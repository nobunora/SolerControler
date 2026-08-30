"""Validation-only Phase 1 v2 hourly PV shadow gate.

The sidecar reads forecast/monitoring evidence from a read-only source
database and writes only to a physically separate shadow database. It never
feeds forecast, energy-plan, SOC, battery, or device-control paths.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable

POLICY_NAME = "gate_per_hour_window21d_margin02pct"
POLICY_VERSION = "phase1-v2"
SELECTOR_WINDOW_DAYS = 21
REQUIRED_MARGIN_FRACTION = 0.02
MIN_HISTORY_DAYS = 3
MIN_CANDIDATE_HISTORY = 2
PRODUCTION_LIKE_SPREAD_KWH = 0.6
FINALIZATION_GRACE_MINUTES = 5
PRIMARY_EVIDENCE_CLASS = "prospective_primary"
DIAGNOSTIC_EVIDENCE_CLASS = "retrospective_diagnostic"
MODEL_NAMES = (
    "baseline",
    "production_like_45d",
    "same_hour_bias_45d_hl7d",
    "same_hour_bias_45d_hl14d",
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _mae(values: Iterable[float]) -> float:
    items = list(values)
    return sum(abs(item) for item in items) / len(items) if items else 0.0


def _value(row: dict[str, Any] | sqlite3.Row, name: str, default: Any = None) -> Any:
    try:
        return row[name]
    except (KeyError, IndexError):
        return default


def _weighted_median(values: list[tuple[float, float]]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    total = sum(max(0.0, weight) for _, weight in ordered)
    if total <= 0.0:
        return median(value for value, _ in ordered)
    threshold = total / 2.0
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += max(0.0, weight)
        if cumulative >= threshold:
            return value
    return ordered[-1][0]


# codebase-memory: keep-separate — frozen Phase 1 diagnostic classification;
# intentionally differs from app.domain.weather.open_meteo_weather_class.
def weather_class(code: Any) -> str:
    """Map weather codes to the frozen diagnostic's coarse classes."""
    try:
        value = int(float(code or 0))
    except (TypeError, ValueError):
        value = 0
    if value <= 3:
        return "clear"
    if 45 <= value <= 48:
        return "fog"
    if 51 <= value <= 67:
        return "rain"
    if 71 <= value <= 77:
        return "snow"
    if 80 <= value <= 99:
        return "shower"
    return "other"


def is_frozen_policy_target(hour: int, baseline_kwh: float) -> bool:
    return 7 <= int(hour) <= 22 and float(baseline_kwh) > 0.0


def production_like_prediction(
    baseline: float,
    residuals: list[float],
    *,
    spread_kwh: float = PRODUCTION_LIKE_SPREAD_KWH,
) -> float:
    if not residuals:
        return max(0.0, baseline)
    center = median(residuals)
    variance = spread_kwh**2 if len(residuals) == 1 else sum((value - center) ** 2 for value in residuals) / len(residuals)
    weight = (len(residuals) / (len(residuals) + 2.0)) * (spread_kwh**2 / (spread_kwh**2 + variance))
    return max(0.0, baseline + weight * center)


def same_hour_bias_prediction(
    baseline: float,
    residuals_and_ages: list[tuple[float, int]],
    *,
    half_life_days: float,
) -> float:
    """Return additive recency-only same-hour bias."""
    if len(residuals_and_ages) < MIN_CANDIDATE_HISTORY:
        return max(0.0, baseline)
    weights = [
        (residual, math.exp(-math.log(2.0) * age / half_life_days))
        for residual, age in residuals_and_ages
    ]
    return max(0.0, baseline + _weighted_median(weights))


def _decision_hard_eligibility(row: dict[str, Any] | sqlite3.Row) -> tuple[bool, str | None]:
    evidence_class = str(_value(row, "evidence_class", PRIMARY_EVIDENCE_CLASS))
    if evidence_class != PRIMARY_EVIDENCE_CLASS:
        return False, "retrospective_diagnostic"
    if str(_value(row, "policy_version", "")) != POLICY_VERSION:
        return False, "superseded_policy_version"
    source_version = str(_value(row, "source_code_version", "") or "").strip().lower()
    if not source_version or source_version == "unknown":
        return False, "source_code_version_missing"
    try:
        decision_at = _parse_timestamp(str(row["decision_at"]))
        target_at = _parse_timestamp(str(row["target_at"]))
        cutoff_at = _parse_timestamp(str(row["cutoff_at"]))
        issued_at = _parse_timestamp(str(row["forecast_issued_at"]))
        target_hour = int(row["target_hour"])
        baseline = float(row["baseline_prediction_kwh"])
    except (KeyError, TypeError, ValueError):
        return False, "invalid_timestamp_or_value"
    if not is_frozen_policy_target(target_hour, baseline):
        return False, "outside_frozen_policy_domain"
    if decision_at >= target_at:
        return False, "decision_after_target"
    if decision_at > cutoff_at:
        return False, "decision_after_cutoff"
    if issued_at > cutoff_at:
        return False, "forecast_issued_after_cutoff"
    return True, None


def open_sqlite_read_only(path: Path) -> sqlite3.Connection:
    """Open an existing SQLite file read-only, without a writable fallback."""
    resolved = path.resolve(strict=True)
    conn = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def validate_separate_db_paths(source_db_path: Path, shadow_db_path: Path) -> tuple[Path, Path]:
    source = source_db_path.resolve(strict=True)
    shadow = shadow_db_path.resolve()
    if source == shadow:
        raise ValueError("source-db-path and shadow-db-path must resolve to different files")
    return source, shadow


def ensure_shadow_schema(conn: sqlite3.Connection) -> None:
    """Create or migrate the writable shadow schema only."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS forecast_shadow_decisions (
            shadow_decision_id TEXT PRIMARY KEY,
            policy_name TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            decision_at TEXT NOT NULL,
            cutoff_at TEXT NOT NULL,
            target_at TEXT NOT NULL,
            target_date TEXT NOT NULL,
            target_hour INTEGER NOT NULL,
            forecast_snapshot_id TEXT NOT NULL,
            forecast_run_id TEXT NOT NULL,
            forecast_issued_at TEXT NOT NULL,
            lead_minutes INTEGER NOT NULL,
            baseline_prediction_kwh REAL NOT NULL,
            forecast_weather_code INTEGER,
            forecast_weather_class TEXT,
            forecast_shortwave_radiation_w_m2 REAL,
            candidate_predictions_json TEXT NOT NULL,
            candidate_score_summary_json TEXT NOT NULL,
            selected_model TEXT NOT NULL,
            selected_prediction_kwh REAL NOT NULL,
            baseline_fallback_reason TEXT,
            selector_window_days INTEGER NOT NULL,
            required_margin_fraction REAL NOT NULL,
            history_window_start TEXT NOT NULL,
            history_window_end TEXT NOT NULL,
            history_sample_counts_json TEXT NOT NULL,
            decision_quality_flags_json TEXT NOT NULL,
            source_code_version TEXT,
            evidence_class TEXT NOT NULL DEFAULT 'prospective_primary',
            primary_eligibility_reason TEXT,
            recorded_at TEXT NOT NULL,
            UNIQUE(forecast_snapshot_id, policy_name, policy_version, cutoff_at)
        );
        CREATE INDEX IF NOT EXISTS idx_shadow_decisions_target ON forecast_shadow_decisions(target_date, target_hour, decision_at);
        CREATE INDEX IF NOT EXISTS idx_shadow_decisions_policy ON forecast_shadow_decisions(policy_name, policy_version, decision_at);
        CREATE INDEX IF NOT EXISTS idx_shadow_decisions_snapshot ON forecast_shadow_decisions(forecast_snapshot_id);
        CREATE TABLE IF NOT EXISTS forecast_shadow_outcomes (
            shadow_decision_id TEXT PRIMARY KEY,
            target_at TEXT NOT NULL,
            actual_pv_kwh REAL NOT NULL,
            baseline_error_kwh REAL NOT NULL,
            selected_error_kwh REAL NOT NULL,
            candidate_errors_json TEXT NOT NULL,
            baseline_absolute_error_kwh REAL NOT NULL,
            selected_absolute_error_kwh REAL NOT NULL,
            selected_minus_baseline_absolute_error_kwh REAL NOT NULL,
            selected_model TEXT NOT NULL,
            correction_applied INTEGER NOT NULL,
            outcome_recorded_at TEXT NOT NULL,
            outcome_quality_flags_json TEXT NOT NULL,
            actual_source TEXT NOT NULL DEFAULT 'monitoring_samples',
            actual_sample_count INTEGER NOT NULL DEFAULT 0,
            expected_sample_count INTEGER,
            first_sample_at TEXT,
            last_sample_at TEXT,
            actual_coverage_status TEXT NOT NULL DEFAULT 'blocked_contract_unproven',
            actual_complete INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_shadow_outcomes_target ON forecast_shadow_outcomes(target_at);
        CREATE TABLE IF NOT EXISTS forecast_shadow_outcome_diagnostics (
            diagnostic_id TEXT PRIMARY KEY,
            shadow_decision_id TEXT NOT NULL,
            target_at TEXT NOT NULL,
            attempted_at TEXT NOT NULL,
            reason TEXT NOT NULL,
            actual_source TEXT NOT NULL,
            actual_sample_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_shadow_outcome_diagnostics_decision ON forecast_shadow_outcome_diagnostics(shadow_decision_id, attempted_at);
        CREATE TABLE IF NOT EXISTS forecast_shadow_decision_diagnostics (
            diagnostic_id TEXT PRIMARY KEY,
            forecast_snapshot_id TEXT NOT NULL,
            target_at TEXT NOT NULL,
            target_date TEXT NOT NULL,
            target_hour INTEGER NOT NULL,
            attempted_at TEXT NOT NULL,
            reason TEXT NOT NULL,
            evidence_class TEXT NOT NULL,
            policy_name TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            source_code_version TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_shadow_decision_diagnostics_target ON forecast_shadow_decision_diagnostics(target_date, target_hour, reason);
        """
    )
    decision_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(forecast_shadow_decisions)").fetchall()}
    for name, definition in (
        ("forecast_weather_code", "INTEGER"),
        ("forecast_weather_class", "TEXT"),
        ("forecast_shortwave_radiation_w_m2", "REAL"),
        ("evidence_class", "TEXT NOT NULL DEFAULT 'prospective_primary'"),
        ("primary_eligibility_reason", "TEXT"),
    ):
        if name not in decision_columns:
            conn.execute(f"ALTER TABLE forecast_shadow_decisions ADD COLUMN {name} {definition}")
    outcome_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(forecast_shadow_outcomes)").fetchall()}
    for name, definition in (
        ("actual_source", "TEXT NOT NULL DEFAULT 'monitoring_samples'"),
        ("actual_sample_count", "INTEGER NOT NULL DEFAULT 0"),
        ("expected_sample_count", "INTEGER"),
        ("first_sample_at", "TEXT"),
        ("last_sample_at", "TEXT"),
        ("actual_coverage_status", "TEXT NOT NULL DEFAULT 'blocked_contract_unproven'"),
        ("actual_complete", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if name not in outcome_columns:
            conn.execute(f"ALTER TABLE forecast_shadow_outcomes ADD COLUMN {name} {definition}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_shadow_decisions_evidence ON forecast_shadow_decisions(evidence_class, target_date, target_hour)")
    conn.commit()


def _snapshot_rows(conn: sqlite3.Connection, cutoff_at: str, start: date | None, end: date | None) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if start is not None:
        clauses.append("date >= ?")
        params.append(start.isoformat())
    if end is not None:
        clauses.append("date <= ?")
        params.append(end.isoformat())
    rows = conn.execute(
        f"SELECT * FROM forecast_hourly_snapshots WHERE {' AND '.join(clauses) if clauses else '1=1'} ORDER BY date, hour",
        params,
    ).fetchall()
    cutoff = _parse_timestamp(cutoff_at)
    latest: dict[tuple[str, int], sqlite3.Row] = {}
    for row in rows:
        if _parse_timestamp(str(row["issued_at"])) > cutoff:
            continue
        key = (str(row["date"]), int(row["hour"]))
        if key not in latest or _parse_timestamp(str(row["issued_at"])) > _parse_timestamp(str(latest[key]["issued_at"])):
            latest[key] = row
    return [dict(row) for row in latest.values()]


def _primary_decision_ids(
    conn: sqlite3.Connection,
    *,
    start: date | None = None,
    end: date | None = None,
    hour: int | None = None,
) -> set[str]:
    clauses = ["policy_name = ?", "policy_version = ?"]
    params: list[Any] = [POLICY_NAME, POLICY_VERSION]
    if start:
        clauses.append("target_date >= ?")
        params.append(start.isoformat())
    if end:
        clauses.append("target_date <= ?")
        params.append(end.isoformat())
    if hour is not None:
        clauses.append("target_hour = ?")
        params.append(hour)
    rows = conn.execute("SELECT * FROM forecast_shadow_decisions WHERE " + " AND ".join(clauses), params).fetchall()
    groups: dict[tuple[str, int], list[sqlite3.Row]] = {}
    for row in rows:
        eligible, _ = _decision_hard_eligibility(row)
        if eligible:
            groups.setdefault((str(row["target_date"]), int(row["target_hour"])), []).append(row)
    selected: set[str] = set()
    for candidates in groups.values():
        winner = max(
            candidates,
            key=lambda row: (
                _parse_timestamp(str(row["decision_at"])),
                _parse_timestamp(str(row["forecast_issued_at"])),
                str(row["shadow_decision_id"]),
            ),
        )
        selected.add(str(winner["shadow_decision_id"]))
    return selected


def _candidate_history(conn: sqlite3.Connection, target_day: date, hour: int) -> list[sqlite3.Row]:
    start = target_day - timedelta(days=45)
    primary_ids = _primary_decision_ids(conn, start=start, end=target_day - timedelta(days=1), hour=hour)
    if not primary_ids:
        return []
    placeholders = ",".join("?" for _ in primary_ids)
    rows = conn.execute(
        f"""
        SELECT d.*, o.actual_pv_kwh, o.candidate_errors_json
        FROM forecast_shadow_decisions d
        JOIN forecast_shadow_outcomes o ON o.shadow_decision_id = d.shadow_decision_id
        WHERE d.shadow_decision_id IN ({placeholders})
          AND o.actual_complete = 1
          AND d.baseline_prediction_kwh > 0
          AND d.target_hour BETWEEN 7 AND 22
        ORDER BY d.target_date, d.shadow_decision_id
        """,
        tuple(primary_ids),
    ).fetchall()
    return list(rows)


def _candidate_predictions(conn: sqlite3.Connection, snapshot: dict[str, Any]) -> dict[str, float]:
    baseline = float(snapshot.get("forecast_pv_kwh") or 0.0)
    target_day = date.fromisoformat(str(snapshot["date"]))
    hour = int(snapshot["hour"])
    target_weather = weather_class(snapshot.get("forecast_weather_code"))
    target_shortwave = float(snapshot.get("forecast_shortwave_radiation_w_m2") or 0.0)
    same_hour_residuals: list[tuple[float, int]] = []
    production_residuals: list[float] = []
    for row in _candidate_history(conn, target_day, hour):
        age = (target_day - date.fromisoformat(str(row["target_date"]))).days
        residual = float(row["actual_pv_kwh"]) - float(row["baseline_prediction_kwh"])
        same_hour_residuals.append((residual, age))
        prior_shortwave = float(row["forecast_shortwave_radiation_w_m2"] or 0.0)
        prior_class = str(row["forecast_weather_class"] or weather_class(row["forecast_weather_code"]))
        if target_shortwave > 0.0 and prior_shortwave > 0.0 and prior_class == target_weather:
            if 0.7 * target_shortwave <= prior_shortwave <= 1.3 * target_shortwave:
                production_residuals.append(residual)
    return {
        "baseline": max(0.0, baseline),
        "production_like_45d": production_like_prediction(baseline, production_residuals),
        "same_hour_bias_45d_hl7d": same_hour_bias_prediction(baseline, same_hour_residuals, half_life_days=7.0),
        "same_hour_bias_45d_hl14d": same_hour_bias_prediction(baseline, same_hour_residuals, half_life_days=14.0),
    }


def _select_model(conn: sqlite3.Connection, target_day: date, hour: int) -> tuple[str, dict[str, Any]]:
    rows = _candidate_history(conn, target_day, hour)
    distinct_days = len({str(row["target_date"]) for row in rows})
    if distinct_days < MIN_HISTORY_DAYS:
        return "baseline", {"reason": "insufficient_shadow_history", "distinct_days": distinct_days, "sample_count": len(rows), "candidate_mae_kwh": {}}
    baseline_mae = _mae(float(row["actual_pv_kwh"]) - float(row["baseline_prediction_kwh"]) for row in rows)
    scores: dict[str, float] = {}
    for model in MODEL_NAMES[1:]:
        scores[model] = _mae(float(json.loads(str(row["candidate_errors_json"]))[model]) for row in rows)
    best = min(scores, key=lambda model: scores[model])
    if scores[best] < baseline_mae * (1.0 - REQUIRED_MARGIN_FRACTION):
        return best, {"reason": "candidate_passed_gate", "distinct_days": distinct_days, "sample_count": len(rows), "baseline_mae_kwh": baseline_mae, "candidate_mae_kwh": scores}
    return "baseline", {"reason": "baseline_fallback", "distinct_days": distinct_days, "sample_count": len(rows), "baseline_mae_kwh": baseline_mae, "candidate_mae_kwh": scores}


def build_shadow_decision(
    source_conn: sqlite3.Connection,
    shadow_conn_or_snapshot: sqlite3.Connection | dict[str, Any],
    snapshot: dict[str, Any] | None = None,
    *,
    decision_at: str,
    cutoff_at: str,
    source_code_version: str | None = None,
    evidence_class: str = PRIMARY_EVIDENCE_CLASS,
) -> dict[str, Any]:
    """Build a decision; the two-connection form is the v2 contract."""
    if snapshot is None:
        shadow_conn = source_conn
        snapshot = shadow_conn_or_snapshot  # type: ignore[assignment]
    else:
        shadow_conn = shadow_conn_or_snapshot  # type: ignore[assignment]
    if not isinstance(snapshot, dict) or not isinstance(shadow_conn, sqlite3.Connection):
        raise TypeError("snapshot and shadow connection are required")
    if evidence_class not in {PRIMARY_EVIDENCE_CLASS, DIAGNOSTIC_EVIDENCE_CLASS}:
        raise ValueError(f"unsupported evidence class: {evidence_class}")
    baseline = float(snapshot.get("forecast_pv_kwh") or 0.0)
    target_hour = int(snapshot["hour"])
    if evidence_class == PRIMARY_EVIDENCE_CLASS:
        if not is_frozen_policy_target(target_hour, baseline):
            raise ValueError("outside_frozen_policy_domain")
        if not str(source_code_version or "").strip() or str(source_code_version).strip().lower() == "unknown":
            raise ValueError("source_code_version is required for phase1-v2 primary decisions")
    decision_at_dt = _parse_timestamp(decision_at)
    cutoff_dt = _parse_timestamp(cutoff_at)
    target_at = str(snapshot["target_at"])
    target_at_dt = _parse_timestamp(target_at)
    issued_at_dt = _parse_timestamp(str(snapshot["issued_at"]))
    eligibility_reason: str | None = None
    if decision_at_dt >= target_at_dt:
        eligibility_reason = "decision_after_target"
    elif decision_at_dt > cutoff_dt:
        eligibility_reason = "decision_after_cutoff"
    elif issued_at_dt > cutoff_dt:
        eligibility_reason = "forecast_issued_after_cutoff"
    if evidence_class == PRIMARY_EVIDENCE_CLASS and eligibility_reason:
        raise ValueError(f"primary shadow decision is not prospectively eligible: {eligibility_reason}")
    candidates = _candidate_predictions(shadow_conn, snapshot)
    target_day = date.fromisoformat(str(snapshot["date"]))
    selected_model, score_summary = _select_model(shadow_conn, target_day, target_hour)
    identity = {"policy_name": POLICY_NAME, "policy_version": POLICY_VERSION, "cutoff_at": _utc_iso(cutoff_dt), "forecast_snapshot_id": str(snapshot["snapshot_id"]), "evidence_class": evidence_class, "selected_model": selected_model, "candidate_predictions": candidates}
    decision_id = hashlib.sha256(_json(identity).encode("utf-8")).hexdigest()[:32]
    history_start = target_day - timedelta(days=SELECTOR_WINDOW_DAYS)
    flags: list[str] = []
    if score_summary["reason"] == "insufficient_shadow_history":
        flags.append("insufficient_shadow_history")
    if eligibility_reason:
        flags.append(eligibility_reason)
    if evidence_class == DIAGNOSTIC_EVIDENCE_CLASS:
        flags.append("retrospective_diagnostic")
    weather_code = snapshot.get("forecast_weather_code")
    return {
        "shadow_decision_id": decision_id,
        "policy_name": POLICY_NAME,
        "policy_version": POLICY_VERSION,
        "decision_at": _utc_iso(decision_at_dt),
        "cutoff_at": _utc_iso(cutoff_dt),
        "target_at": target_at,
        "target_date": target_day.isoformat(),
        "target_hour": target_hour,
        "forecast_snapshot_id": str(snapshot["snapshot_id"]),
        "forecast_run_id": str(snapshot["forecast_run_id"]),
        "forecast_issued_at": str(snapshot["issued_at"]),
        "lead_minutes": int(snapshot["lead_minutes"]),
        "baseline_prediction_kwh": candidates["baseline"],
        "forecast_weather_code": int(weather_code) if weather_code is not None else None,
        "forecast_weather_class": weather_class(weather_code),
        "forecast_shortwave_radiation_w_m2": float(snapshot.get("forecast_shortwave_radiation_w_m2") or 0.0),
        "candidate_predictions_json": _json(candidates),
        "candidate_score_summary_json": _json(score_summary),
        "selected_model": selected_model,
        "selected_prediction_kwh": candidates[selected_model],
        "baseline_fallback_reason": score_summary["reason"] if selected_model == "baseline" else None,
        "selector_window_days": SELECTOR_WINDOW_DAYS,
        "required_margin_fraction": REQUIRED_MARGIN_FRACTION,
        "history_window_start": history_start.isoformat(),
        "history_window_end": (target_day - timedelta(days=1)).isoformat(),
        "history_sample_counts_json": _json({"same_hour": score_summary.get("sample_count", 0), "distinct_days": score_summary.get("distinct_days", 0)}),
        "decision_quality_flags_json": _json(flags),
        "source_code_version": source_code_version,
        "evidence_class": evidence_class,
        "primary_eligibility_reason": eligibility_reason,
        "recorded_at": _utc_iso(datetime.now(timezone.utc)),
    }


def persist_shadow_decisions(conn: sqlite3.Connection, decisions: Iterable[dict[str, Any]]) -> int:
    ensure_shadow_schema(conn)
    normalized = []
    for item in decisions:
        row = dict(item)
        row.setdefault("evidence_class", PRIMARY_EVIDENCE_CLASS)
        eligible, reason = _decision_hard_eligibility(row)
        if row["evidence_class"] == PRIMARY_EVIDENCE_CLASS and not eligible:
            raise ValueError(f"primary shadow decision is not prospectively eligible: {reason}")
        normalized.append(row)
    if not normalized:
        return 0
    names = tuple(normalized[0])
    placeholders = ",".join("?" for _ in names)
    before = conn.total_changes
    conn.executemany(f"INSERT OR IGNORE INTO forecast_shadow_decisions ({','.join(names)}) VALUES ({placeholders})", [[row.get(name) for name in names] for row in normalized])
    conn.commit()
    return conn.total_changes - before


def persist_shadow_decision_diagnostics(conn: sqlite3.Connection, snapshots: Iterable[dict[str, Any]], *, attempted_at: str, reason: str, source_code_version: str | None = None) -> int:
    ensure_shadow_schema(conn)
    attempted = _utc_iso(_parse_timestamp(attempted_at))
    rows = []
    for snapshot in snapshots:
        identity = {"snapshot": snapshot.get("snapshot_id"), "attempted": attempted, "reason": reason}
        diagnostic_id = hashlib.sha256(_json(identity).encode("utf-8")).hexdigest()[:32]
        rows.append((diagnostic_id, str(snapshot["snapshot_id"]), str(snapshot["target_at"]), str(snapshot["date"]), int(snapshot["hour"]), attempted, reason, PRIMARY_EVIDENCE_CLASS, POLICY_NAME, POLICY_VERSION, source_code_version))
    before = conn.total_changes
    conn.executemany("""INSERT OR IGNORE INTO forecast_shadow_decision_diagnostics
        (diagnostic_id,forecast_snapshot_id,target_at,target_date,target_hour,attempted_at,reason,evidence_class,policy_name,policy_version,source_code_version)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""", rows)
    conn.commit()
    return conn.total_changes - before


def _normalize_actual(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        result = dict(value)
        result["actual"] = float(result.get("actual", 0.0))
        result["sample_count"] = int(result.get("sample_count", 0))
        return result
    if isinstance(value, (tuple, list)):
        return {"actual": float(value[0]), "sample_count": int(value[1])}
    return {"actual": float(value), "sample_count": 1}


def persist_shadow_outcomes(
    conn: sqlite3.Connection,
    actuals: dict[tuple[str, int], Any],
    *,
    recorded_at: str,
    actual_source: str = "monitoring_samples",
    finalization_grace_minutes: int = FINALIZATION_GRACE_MINUTES,
    completeness_contract_proven: bool = False,
) -> int:
    ensure_shadow_schema(conn)
    decisions = conn.execute("SELECT * FROM forecast_shadow_decisions WHERE policy_name = ? AND policy_version = ? AND evidence_class = ? ORDER BY target_date, target_hour", (POLICY_NAME, POLICY_VERSION, PRIMARY_EVIDENCE_CLASS)).fetchall()
    rows = []
    diagnostics = []
    recorded_dt = _parse_timestamp(recorded_at)
    for decision in decisions:
        key = (str(decision["target_date"]), int(decision["target_hour"]))
        if key not in actuals:
            continue
        actual_info = _normalize_actual(actuals[key])
        actual = float(actual_info["actual"])
        sample_count = int(actual_info["sample_count"])
        target_end = _parse_timestamp(str(decision["target_at"])) + timedelta(hours=1, minutes=finalization_grace_minutes)
        reason: str | None = None
        if recorded_dt <= target_end:
            reason = "target_hour_not_finalized"
        elif not bool(actual_info.get("completeness_contract_proven", completeness_contract_proven)):
            reason = "actual_completeness_contract_unproven"
        elif actual_info.get("expected_sample_count") is None:
            reason = "actual_completeness_contract_unproven"
        elif sample_count <= 0 or sample_count != int(actual_info["expected_sample_count"]):
            reason = "actual_source_incomplete"
        if reason:
            diagnostic_id = hashlib.sha256(_json({"decision": str(decision["shadow_decision_id"]), "attempted_at": _utc_iso(recorded_dt), "reason": reason}).encode("utf-8")).hexdigest()[:32]
            diagnostics.append((diagnostic_id, str(decision["shadow_decision_id"]), str(decision["target_at"]), _utc_iso(recorded_dt), reason, actual_source, sample_count))
            continue
        candidates = json.loads(str(decision["candidate_predictions_json"]))
        errors = {model: actual - float(prediction) for model, prediction in candidates.items()}
        selected_error = errors[str(decision["selected_model"])]
        rows.append((str(decision["shadow_decision_id"]), str(decision["target_at"]), actual, errors["baseline"], selected_error, _json(errors), abs(errors["baseline"]), abs(selected_error), abs(selected_error) - abs(errors["baseline"]), str(decision["selected_model"]), int(str(decision["selected_model"]) != "baseline"), recorded_at, "[]", actual_source, sample_count, int(actual_info["expected_sample_count"]), actual_info.get("first_sample_at"), actual_info.get("last_sample_at"), "complete", 1))
    before = conn.total_changes
    conn.executemany("""INSERT OR IGNORE INTO forecast_shadow_outcomes
        (shadow_decision_id,target_at,actual_pv_kwh,baseline_error_kwh,selected_error_kwh,candidate_errors_json,
         baseline_absolute_error_kwh,selected_absolute_error_kwh,selected_minus_baseline_absolute_error_kwh,
         selected_model,correction_applied,outcome_recorded_at,outcome_quality_flags_json,actual_source,
         actual_sample_count,expected_sample_count,first_sample_at,last_sample_at,actual_coverage_status,actual_complete)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    inserted_outcomes = conn.total_changes - before
    conn.executemany("""INSERT OR IGNORE INTO forecast_shadow_outcome_diagnostics
        (diagnostic_id,shadow_decision_id,target_at,attempted_at,reason,actual_source,actual_sample_count)
        VALUES (?,?,?,?,?,?,?)""", diagnostics)
    conn.commit()
    return inserted_outcomes


def report_shadow_outcomes(conn: sqlite3.Connection, *, start: date | None = None, end: date | None = None, include_recent_windows: bool = True) -> dict[str, Any]:
    ensure_shadow_schema(conn)
    clauses = ["policy_name = ?", "policy_version = ?"]
    params: list[Any] = [POLICY_NAME, POLICY_VERSION]
    if start:
        clauses.append("target_date >= ?")
        params.append(start.isoformat())
    if end:
        clauses.append("target_date <= ?")
        params.append(end.isoformat())
    decisions = conn.execute("SELECT * FROM forecast_shadow_decisions WHERE " + " AND ".join(clauses) + " ORDER BY target_date,target_hour", params).fetchall()
    decision_by_id = {str(row["shadow_decision_id"]): row for row in decisions}
    primary_ids = _primary_decision_ids(conn, start=start, end=end)
    excluded: dict[str, int] = {}
    valid_ids: set[str] = set()
    for decision in decisions:
        decision_id = str(decision["shadow_decision_id"])
        eligible, reason = _decision_hard_eligibility(decision)
        if not eligible:
            excluded[reason or "invalid_evidence"] = excluded.get(reason or "invalid_evidence", 0) + 1
        elif decision_id not in primary_ids:
            excluded["diagnostic_vintage"] = excluded.get("diagnostic_vintage", 0) + 1
        else:
            valid_ids.add(decision_id)
    v1_params: list[Any] = [POLICY_NAME, "phase1-v1"]
    v1_clause = "policy_name = ? AND policy_version = ?"
    if start:
        v1_clause += " AND target_date >= ?"
        v1_params.append(start.isoformat())
    if end:
        v1_clause += " AND target_date <= ?"
        v1_params.append(end.isoformat())
    superseded_count = int(conn.execute(f"SELECT COUNT(*) FROM forecast_shadow_decisions WHERE {v1_clause}", v1_params).fetchone()[0])
    if superseded_count:
        excluded["superseded_policy_version"] = superseded_count
    all_outcomes = {str(row["shadow_decision_id"]): row for row in conn.execute("SELECT * FROM forecast_shadow_outcomes").fetchall()}
    complete_outcomes = {key: row for key, row in all_outcomes.items() if int(row["actual_complete"]) == 1}
    rows = []
    for decision_id in valid_ids:
        outcome = complete_outcomes.get(decision_id)
        if outcome is None and decision_id not in all_outcomes:
            excluded["missing_finalized_outcome"] = excluded.get("missing_finalized_outcome", 0) + 1
        elif outcome is None:
            excluded["incomplete_outcome"] = excluded.get("incomplete_outcome", 0) + 1
        else:
            rows.append((decision_by_id[decision_id], outcome))
    sample_count = len(rows)
    baseline_abs = [float(outcome["baseline_absolute_error_kwh"]) for _, outcome in rows]
    selected_abs = [float(outcome["selected_absolute_error_kwh"]) for _, outcome in rows]
    baseline_signed = [float(outcome["baseline_error_kwh"]) for _, outcome in rows]
    selected_signed = [float(outcome["selected_error_kwh"]) for _, outcome in rows]
    model_counts: dict[str, int] = {}
    hour_counts: dict[str, dict[str, int]] = {}
    for decision, outcome in rows:
        model = str(outcome["selected_model"])
        model_counts[model] = model_counts.get(model, 0) + 1
        hour_counts.setdefault(str(decision["target_hour"]), {})[model] = hour_counts.setdefault(str(decision["target_hour"]), {}).get(model, 0) + 1
    valid_decisions = [decision_by_id[decision_id] for decision_id in valid_ids]
    decision_count = len(valid_decisions)
    quality_flags: dict[str, int] = {}
    for decision, outcome in rows:
        for field, source in (("decision_quality_flags_json", decision), ("outcome_quality_flags_json", outcome)):
            for flag in json.loads(str(source[field]) or "[]"):
                quality_flags[str(flag)] = quality_flags.get(str(flag), 0) + 1
    outcome_diag_sql = "SELECT reason, COUNT(*) FROM forecast_shadow_outcome_diagnostics WHERE 1=1"
    outcome_diag_params: list[Any] = []
    if start:
        outcome_diag_sql += " AND substr(target_at,1,10) >= ?"
        outcome_diag_params.append(start.isoformat())
    if end:
        outcome_diag_sql += " AND substr(target_at,1,10) <= ?"
        outcome_diag_params.append(end.isoformat())
    for reason, count in conn.execute(outcome_diag_sql + " GROUP BY reason", outcome_diag_params).fetchall():
        excluded[str(reason)] = excluded.get(str(reason), 0) + int(count)
    decision_diag_sql = "SELECT reason, COUNT(*) FROM forecast_shadow_decision_diagnostics WHERE 1=1"
    decision_diag_params: list[Any] = []
    if start:
        decision_diag_sql += " AND target_date >= ?"
        decision_diag_params.append(start.isoformat())
    if end:
        decision_diag_sql += " AND target_date <= ?"
        decision_diag_params.append(end.isoformat())
    for reason, count in conn.execute(decision_diag_sql + " GROUP BY reason", decision_diag_params).fetchall():
        excluded[str(reason)] = excluded.get(str(reason), 0) + int(count)
    dates = sorted({str(row["target_date"]) for row in valid_decisions})
    source_versions: dict[str, int] = {}
    for row in valid_decisions:
        version = str(row["source_code_version"])
        source_versions[version] = source_versions.get(version, 0) + 1
    baseline_mae = _mae(baseline_abs)
    selected_mae = _mae(selected_abs)
    report: dict[str, Any] = {
        "policy_name": POLICY_NAME,
        "policy_version": POLICY_VERSION,
        "first_valid_v2_target_date": dates[0] if dates else None,
        "last_valid_v2_target_date": dates[-1] if dates else None,
        "distinct_valid_v2_target_dates": len(dates),
        "eligible_target_count": decision_count,
        "valid_primary_decision_count": decision_count,
        "finalized_primary_outcome_count": sample_count,
        "sample_count": sample_count,
        "valid_decision_count": decision_count,
        "coverage": sample_count / decision_count if decision_count else 0.0,
        "baseline_mae_kwh": baseline_mae,
        "shadow_selected_mae_kwh": selected_mae,
        "relative_mae_improvement_percent": 100.0 * (baseline_mae - selected_mae) / baseline_mae if baseline_mae else 0.0,
        "baseline_signed_bias_kwh": sum(baseline_signed) / len(baseline_signed) if baseline_signed else 0.0,
        "shadow_selected_signed_bias_kwh": sum(selected_signed) / len(selected_signed) if selected_signed else 0.0,
        "correction_application_rate": sum(int(outcome["correction_applied"]) for _, outcome in rows) / sample_count if sample_count else 0.0,
        "baseline_fallback_rate": sum(str(outcome["selected_model"]) == "baseline" for _, outcome in rows) / sample_count if sample_count else 0.0,
        "selection_count_by_model": dict(sorted(model_counts.items())),
        "selection_count_by_hour": {key: dict(sorted(value.items())) for key, value in sorted(hour_counts.items(), key=lambda item: int(item[0]))},
        "source_code_version_counts": dict(sorted(source_versions.items())),
        "quality_flag_counts": dict(sorted(quality_flags.items())),
        "lead_time_distribution": {"count": decision_count, "min_minutes": min((int(row["lead_minutes"]) for row in valid_decisions), default=None), "max_minutes": max((int(row["lead_minutes"]) for row in valid_decisions), default=None)},
        "forecast_vintage_count": len({str(row["forecast_run_id"]) for row in valid_decisions}),
        "missing_decision_count": int(excluded.get("missing_decision", 0)),
        "missing_finalized_outcome_count": int(excluded.get("missing_finalized_outcome", 0)),
        "excluded_count_by_reason": dict(sorted(excluded.items())),
        "actual_completeness_rule_proven": False,
        "actual_completeness_status": "BLOCKED",
        "prospective_evidence_collection": "NOT_STARTED",
        "production_adoption_eligibility": "NOT_ELIGIBLE",
        "parameters": {"selector_granularity": "per_hour", "selector_window_days": SELECTOR_WINDOW_DAYS, "required_margin_fraction": REQUIRED_MARGIN_FRACTION, "fallback": "baseline", "target_hours": "07-22", "positive_baseline_required": True},
    }
    if include_recent_windows:
        latest = end
        if latest is None and dates:
            latest = date.fromisoformat(dates[-1])
        report["recent_windows"] = {}
        if latest:
            for days in (7, 14, 21):
                report["recent_windows"][f"last_{days}_days"] = report_shadow_outcomes(conn, start=latest - timedelta(days=days - 1), end=latest, include_recent_windows=False)
    return report

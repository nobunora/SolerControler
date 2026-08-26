"""Prospective, side-channel validation for the frozen hourly PV gate.

This module never writes forecast_hourly and never changes a production plan.
Decisions are frozen from forecast snapshots before outcomes are recorded;
outcomes are stored separately and are append-only.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any, Iterable

POLICY_NAME = "gate_per_hour_window21d_margin02pct"
POLICY_VERSION = "phase1-v1"
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
    text = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _mae(values: Iterable[float]) -> float:
    items = list(values)
    return sum(abs(item) for item in items) / len(items) if items else 0.0


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


def production_like_prediction(baseline: float, residuals: list[float], *, spread_kwh: float = PRODUCTION_LIKE_SPREAD_KWH) -> float:
    if not residuals:
        return max(0.0, baseline)
    center = median(residuals)
    variance = spread_kwh**2 if len(residuals) == 1 else sum((value - center) ** 2 for value in residuals) / len(residuals)
    weight = (len(residuals) / (len(residuals) + 2.0)) * (spread_kwh**2 / (spread_kwh**2 + variance))
    return max(0.0, baseline + weight * center)


def same_hour_bias_prediction(baseline: float, residuals_and_ages: list[tuple[float, int, float]], *, half_life_days: float) -> float:
    if len(residuals_and_ages) < MIN_CANDIDATE_HISTORY:
        return max(0.0, baseline)
    weights = [
        (residual, math.exp(-math.log(2.0) * age / half_life_days) * math.exp(-0.5 * (distance / 0.35) ** 2))
        for residual, age, distance in residuals_and_ages
    ]
    return max(0.0, baseline + _weighted_median(weights))


def _decision_hard_eligibility(row: dict[str, Any] | sqlite3.Row) -> tuple[bool, str | None]:
    if str(row.get("evidence_class", PRIMARY_EVIDENCE_CLASS) if isinstance(row, dict) else row["evidence_class"]) != PRIMARY_EVIDENCE_CLASS:
        return False, "retrospective_diagnostic"
    try:
        decision_at = _parse_timestamp(str(row["decision_at"]))
        target_at = _parse_timestamp(str(row["target_at"]))
        cutoff_at = _parse_timestamp(str(row["cutoff_at"]))
        issued_at = _parse_timestamp(str(row["forecast_issued_at"]))
    except (KeyError, TypeError, ValueError):
        return False, "invalid_timestamp"
    if decision_at >= target_at:
        return False, "decision_after_target"
    if decision_at > cutoff_at:
        return False, "decision_after_cutoff"
    if issued_at > cutoff_at:
        return False, "forecast_issued_after_cutoff"
    return True, None


def ensure_shadow_schema(conn: sqlite3.Connection) -> None:
    """Create or migrate the SQLite shadow side-channel idempotently."""
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
        CREATE INDEX IF NOT EXISTS idx_shadow_decisions_target
            ON forecast_shadow_decisions(target_date, target_hour, decision_at);
        CREATE INDEX IF NOT EXISTS idx_shadow_decisions_policy
            ON forecast_shadow_decisions(policy_name, policy_version, decision_at);
        CREATE INDEX IF NOT EXISTS idx_shadow_decisions_snapshot
            ON forecast_shadow_decisions(forecast_snapshot_id);

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
            actual_complete INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_shadow_outcomes_target
            ON forecast_shadow_outcomes(target_at);
        CREATE TABLE IF NOT EXISTS forecast_shadow_outcome_diagnostics (
            diagnostic_id TEXT PRIMARY KEY,
            shadow_decision_id TEXT NOT NULL,
            target_at TEXT NOT NULL,
            attempted_at TEXT NOT NULL,
            reason TEXT NOT NULL,
            actual_source TEXT NOT NULL,
            actual_sample_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_shadow_outcome_diagnostics_decision
            ON forecast_shadow_outcome_diagnostics(shadow_decision_id, attempted_at);
        """
    )
    decision_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(forecast_shadow_decisions)").fetchall()}
    for name, definition in (
        ("evidence_class", "TEXT NOT NULL DEFAULT 'prospective_primary'"),
        ("primary_eligibility_reason", "TEXT"),
    ):
        if name not in decision_columns:
            conn.execute(f"ALTER TABLE forecast_shadow_decisions ADD COLUMN {name} {definition}")
    outcome_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(forecast_shadow_outcomes)").fetchall()}
    for name, definition in (
        ("actual_source", "TEXT NOT NULL DEFAULT 'monitoring_samples'"),
        ("actual_sample_count", "INTEGER NOT NULL DEFAULT 0"),
        ("actual_complete", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if name not in outcome_columns:
            conn.execute(f"ALTER TABLE forecast_shadow_outcomes ADD COLUMN {name} {definition}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_shadow_decisions_evidence ON forecast_shadow_decisions(evidence_class, target_date, target_hour)")
    conn.commit()


def _snapshot_rows(conn: sqlite3.Connection, cutoff_at: str, start: date | None, end: date | None) -> list[dict[str, Any]]:
    params: list[Any] = []
    clauses = ["issued_at <= ?"]
    if start is not None:
        clauses.append("date >= ?")
        params.append(start.isoformat())
    if end is not None:
        clauses.append("date <= ?")
        params.append(end.isoformat())
    # Date predicates reduce the scan; issue-time eligibility is checked after
    # parsing so offsets such as +09:00 and Z cannot be compared lexically.
    clauses = [clause for clause in clauses if clause != "issued_at <= ?"]
    rows = conn.execute(
        f"SELECT * FROM forecast_hourly_snapshots WHERE {' AND '.join(clauses) if clauses else '1=1'} "
        "ORDER BY date, hour",
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
    rows = conn.execute(
        "SELECT * FROM forecast_shadow_decisions WHERE " + " AND ".join(clauses),
        params,
    ).fetchall()
    groups: dict[tuple[str, int], list[sqlite3.Row]] = {}
    for row in rows:
        eligible, _ = _decision_hard_eligibility(row)
        if not eligible:
            continue
        key = (str(row["target_date"]), int(row["target_hour"]))
        groups.setdefault(key, []).append(row)
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


def _candidate_predictions(conn: sqlite3.Connection, snapshot: dict[str, Any]) -> dict[str, float]:
    baseline = float(snapshot["forecast_pv_kwh"] or 0.0)
    target_day = date.fromisoformat(str(snapshot["date"]))
    hour = int(snapshot["hour"])
    target_weather = weather_class(snapshot.get("forecast_weather_code"))
    target_shortwave = float(snapshot.get("forecast_shortwave_radiation_w_m2") or 0.0)
    residuals: list[tuple[float, int, float]] = []
    weather_residuals: list[float] = []
    primary_ids = _primary_decision_ids(conn, start=target_day - timedelta(days=45), end=target_day - timedelta(days=1), hour=hour)
    if not primary_ids:
        return {model: max(0.0, baseline) for model in MODEL_NAMES}
    weather_rows = conn.execute(
        """
        SELECT d.shadow_decision_id, d.target_date, d.baseline_prediction_kwh, o.actual_pv_kwh,
               s.forecast_weather_code, s.forecast_shortwave_radiation_w_m2
        FROM forecast_shadow_decisions d
        JOIN forecast_shadow_outcomes o ON o.shadow_decision_id = d.shadow_decision_id
        JOIN forecast_hourly_snapshots s ON s.snapshot_id = d.forecast_snapshot_id
        WHERE d.policy_name = ? AND d.policy_version = ?
          AND d.target_date >= ? AND d.target_date < ? AND d.target_hour = ?
        ORDER BY d.target_date
        """,
        (POLICY_NAME, POLICY_VERSION, (target_day - timedelta(days=45)).isoformat(), target_day.isoformat(), hour),
    ).fetchall()
    for row in weather_rows:
        if str(row["shadow_decision_id"]) not in primary_ids:
            continue
        age = max(0, (target_day - date.fromisoformat(str(row["target_date"]))).days)
        residual = float(row["actual_pv_kwh"]) - float(row["baseline_prediction_kwh"])
        prior_shortwave = float(row["forecast_shortwave_radiation_w_m2"] or 0.0)
        distance = abs(math.log(prior_shortwave / target_shortwave)) if target_shortwave > 0 and prior_shortwave > 0 else float("inf")
        if weather_class(row["forecast_weather_code"]) == target_weather and target_shortwave > 0 and prior_shortwave > 0:
            if 0.7 * target_shortwave <= prior_shortwave <= 1.3 * target_shortwave:
                residuals.append((residual, age, distance))
                weather_residuals.append(residual)

    return {
        "baseline": max(0.0, baseline),
        "production_like_45d": production_like_prediction(baseline, weather_residuals),
        "same_hour_bias_45d_hl7d": same_hour_bias_prediction(baseline, residuals, half_life_days=7.0),
        "same_hour_bias_45d_hl14d": same_hour_bias_prediction(baseline, residuals, half_life_days=14.0),
    }


def _select_model(conn: sqlite3.Connection, target_day: date, hour: int) -> tuple[str, dict[str, Any]]:
    start = target_day - timedelta(days=SELECTOR_WINDOW_DAYS)
    rows = conn.execute(
        """
        SELECT d.shadow_decision_id, d.target_date, d.selected_model, o.baseline_absolute_error_kwh,
               o.candidate_errors_json
        FROM forecast_shadow_decisions d
        JOIN forecast_shadow_outcomes o ON o.shadow_decision_id = d.shadow_decision_id
        WHERE d.policy_name = ? AND d.policy_version = ?
          AND d.target_date >= ? AND d.target_date < ? AND d.target_hour = ?
          AND o.actual_complete = 1
        """,
        (POLICY_NAME, POLICY_VERSION, start.isoformat(), target_day.isoformat(), hour),
    ).fetchall()
    primary_ids = _primary_decision_ids(conn, start=start, end=target_day - timedelta(days=1), hour=hour)
    rows = [row for row in rows if str(row["shadow_decision_id"]) in primary_ids]
    distinct_days = len({str(row["target_date"]) for row in rows}) if rows and "target_date" in rows[0].keys() else 0
    if distinct_days < MIN_HISTORY_DAYS:
        return "baseline", {"reason": "insufficient_shadow_history", "distinct_days": distinct_days, "sample_count": len(rows), "candidate_mae_kwh": {}}
    baseline_errors = [float(row["baseline_absolute_error_kwh"]) for row in rows]
    scores: dict[str, float] = {}
    for model in MODEL_NAMES[1:]:
        errors = []
        for row in rows:
            parsed = json.loads(str(row["candidate_errors_json"]))
            errors.append(abs(float(parsed[model])))
        scores[model] = _mae(errors)
    baseline_mae = _mae(baseline_errors)
    threshold = baseline_mae * (1.0 - REQUIRED_MARGIN_FRACTION)
    best = min(scores, key=lambda model: scores[model]) if scores else "baseline"
    if scores and scores[best] < threshold:
        return best, {"reason": "candidate_passed_gate", "distinct_days": distinct_days, "sample_count": len(rows), "baseline_mae_kwh": baseline_mae, "candidate_mae_kwh": scores}
    return "baseline", {"reason": "baseline_fallback", "distinct_days": distinct_days, "sample_count": len(rows), "baseline_mae_kwh": baseline_mae, "candidate_mae_kwh": scores}


def build_shadow_decision(
    conn: sqlite3.Connection,
    snapshot: dict[str, Any],
    *,
    decision_at: str,
    cutoff_at: str,
    source_code_version: str | None = None,
    evidence_class: str = PRIMARY_EVIDENCE_CLASS,
) -> dict[str, Any]:
    if evidence_class not in {PRIMARY_EVIDENCE_CLASS, DIAGNOSTIC_EVIDENCE_CLASS}:
        raise ValueError(f"unsupported evidence class: {evidence_class}")
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
    candidates = _candidate_predictions(conn, snapshot)
    target_day = date.fromisoformat(str(snapshot["date"]))
    selected_model, score_summary = _select_model(conn, target_day, int(snapshot["hour"]))
    identity = {
        "policy_name": POLICY_NAME,
        "policy_version": POLICY_VERSION,
        "cutoff_at": _utc_iso(cutoff_dt),
        "forecast_snapshot_id": str(snapshot["snapshot_id"]),
        "evidence_class": evidence_class,
        "selected_model": selected_model,
        "candidate_predictions": candidates,
    }
    decision_id = hashlib.sha256(_json(identity).encode("utf-8")).hexdigest()[:32]
    history_start = target_day - timedelta(days=SELECTOR_WINDOW_DAYS)
    flags: list[str] = []
    if score_summary["reason"] == "insufficient_shadow_history":
        flags.append("insufficient_shadow_history")
    if eligibility_reason:
        flags.append(eligibility_reason)
    if evidence_class == DIAGNOSTIC_EVIDENCE_CLASS:
        flags.append("retrospective_diagnostic")
    return {
        "shadow_decision_id": decision_id,
        "policy_name": POLICY_NAME,
        "policy_version": POLICY_VERSION,
        "decision_at": _utc_iso(decision_at_dt),
        "cutoff_at": _utc_iso(cutoff_dt),
        "target_at": target_at,
        "target_date": target_day.isoformat(),
        "target_hour": int(snapshot["hour"]),
        "forecast_snapshot_id": str(snapshot["snapshot_id"]),
        "forecast_run_id": str(snapshot["forecast_run_id"]),
        "forecast_issued_at": str(snapshot["issued_at"]),
        "lead_minutes": int(snapshot["lead_minutes"]),
        "baseline_prediction_kwh": candidates["baseline"],
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
    columns = tuple(decisions)
    if not columns:
        return 0
    for row in columns:
        candidate = dict(row)
        candidate.setdefault("evidence_class", PRIMARY_EVIDENCE_CLASS)
        eligible, reason = _decision_hard_eligibility(candidate)
        if candidate["evidence_class"] == PRIMARY_EVIDENCE_CLASS and not eligible:
            raise ValueError(f"primary shadow decision is not prospectively eligible: {reason}")
    names = tuple(columns[0])
    placeholders = ",".join("?" for _ in names)
    before = conn.total_changes
    conn.executemany(
        f"INSERT OR IGNORE INTO forecast_shadow_decisions ({','.join(names)}) VALUES ({placeholders})",
        [[row[name] for name in names] for row in columns],
    )
    conn.commit()
    return conn.total_changes - before


def _normalize_actual(value: Any) -> tuple[float, int]:
    if isinstance(value, dict):
        return float(value.get("actual", 0.0)), int(value.get("sample_count", 0))
    if isinstance(value, (tuple, list)):
        return float(value[0]), int(value[1])
    return float(value), 1


def persist_shadow_outcomes(
    conn: sqlite3.Connection,
    actuals: dict[tuple[str, int], Any],
    *,
    recorded_at: str,
    actual_source: str = "monitoring_samples",
    finalization_grace_minutes: int = FINALIZATION_GRACE_MINUTES,
) -> int:
    ensure_shadow_schema(conn)
    decisions = conn.execute("SELECT * FROM forecast_shadow_decisions ORDER BY target_date, target_hour").fetchall()
    rows = []
    diagnostics = []
    recorded_dt = _parse_timestamp(recorded_at)
    for decision in decisions:
        key = (str(decision["target_date"]), int(decision["target_hour"]))
        if key not in actuals:
            continue
        actual, sample_count = _normalize_actual(actuals[key])
        target_end = _parse_timestamp(str(decision["target_at"])) + timedelta(hours=1, minutes=finalization_grace_minutes)
        reason = None
        if recorded_dt <= target_end:
            reason = "target_hour_not_finalized"
        elif sample_count <= 0:
            reason = "actual_source_incomplete"
        if reason:
            diagnostic_id = hashlib.sha256(_json({"decision": str(decision["shadow_decision_id"]), "attempted_at": _utc_iso(recorded_dt), "reason": reason}).encode("utf-8")).hexdigest()[:32]
            diagnostics.append((diagnostic_id, str(decision["shadow_decision_id"]), str(decision["target_at"]), _utc_iso(recorded_dt), reason, actual_source, sample_count))
            continue
        candidates = json.loads(str(decision["candidate_predictions_json"]))
        errors = {model: actual - float(prediction) for model, prediction in candidates.items()}
        selected_error = errors[str(decision["selected_model"])]
        rows.append((
            str(decision["shadow_decision_id"]), str(decision["target_at"]), actual,
            errors["baseline"], selected_error, _json(errors), abs(errors["baseline"]),
            abs(selected_error), abs(selected_error) - abs(errors["baseline"]),
            str(decision["selected_model"]), int(str(decision["selected_model"]) != "baseline"), recorded_at, "[]",
            actual_source, sample_count, 1,
        ))
    before = conn.total_changes
    conn.executemany(
        """INSERT OR IGNORE INTO forecast_shadow_outcomes
        (shadow_decision_id,target_at,actual_pv_kwh,baseline_error_kwh,selected_error_kwh,
         candidate_errors_json,baseline_absolute_error_kwh,selected_absolute_error_kwh,
         selected_minus_baseline_absolute_error_kwh,selected_model,correction_applied,
         outcome_recorded_at,outcome_quality_flags_json,actual_source,actual_sample_count,actual_complete)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    inserted_outcomes = conn.total_changes - before
    before_diagnostics = conn.total_changes
    conn.executemany(
        """INSERT OR IGNORE INTO forecast_shadow_outcome_diagnostics
        (diagnostic_id,shadow_decision_id,target_at,attempted_at,reason,actual_source,actual_sample_count)
        VALUES (?,?,?,?,?,?,?)""",
        diagnostics,
    )
    conn.commit()
    _ = conn.total_changes - before_diagnostics
    return inserted_outcomes


def report_shadow_outcomes(
    conn: sqlite3.Connection,
    *,
    start: date | None = None,
    end: date | None = None,
    include_recent_windows: bool = True,
) -> dict[str, Any]:
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
    all_outcome_rows = conn.execute("SELECT * FROM forecast_shadow_outcomes").fetchall()
    all_outcomes = {str(row["shadow_decision_id"]): row for row in all_outcome_rows}
    outcomes = {decision_id: row for decision_id, row in all_outcomes.items() if int(row["actual_complete"]) == 1}
    rows = []
    for decision_id in valid_ids:
        outcome = outcomes.get(decision_id)
        if outcome is None and decision_id not in all_outcomes:
            excluded["missing_finalized_outcome"] = excluded.get("missing_finalized_outcome", 0) + 1
            continue
        if outcome is None:
            excluded["incomplete_outcome"] = excluded.get("incomplete_outcome", 0) + 1
            continue
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
        key = str(decision["target_hour"])
        hour_counts.setdefault(key, {})[model] = hour_counts.setdefault(key, {}).get(model, 0) + 1
    valid_decisions = [decision_by_id[decision_id] for decision_id in valid_ids]
    decision_count = len(valid_decisions)
    quality_flags: dict[str, int] = {}
    for decision, outcome in rows:
        for field in ("decision_quality_flags_json", "outcome_quality_flags_json"):
            source = decision if field == "decision_quality_flags_json" else outcome
            for flag in json.loads(str(source[field]) or "[]"):
                quality_flags[str(flag)] = quality_flags.get(str(flag), 0) + 1
    diagnostic_query = "SELECT reason, COUNT(*) FROM forecast_shadow_outcome_diagnostics WHERE 1=1"
    diagnostic_params: list[Any] = []
    if start:
        diagnostic_query += " AND substr(target_at,1,10) >= ?"
        diagnostic_params.append(start.isoformat())
    if end:
        diagnostic_query += " AND substr(target_at,1,10) <= ?"
        diagnostic_params.append(end.isoformat())
    diagnostic_query += " GROUP BY reason"
    for reason, count in conn.execute(diagnostic_query, diagnostic_params).fetchall():
        excluded[str(reason)] = excluded.get(str(reason), 0) + int(count)
    report = {
        "policy_name": POLICY_NAME,
        "policy_version": POLICY_VERSION,
        "sample_count": sample_count,
        "valid_decision_count": int(decision_count),
        "coverage": sample_count / decision_count if decision_count else 0.0,
        "baseline_mae_kwh": _mae(baseline_abs),
        "shadow_selected_mae_kwh": _mae(selected_abs),
        "relative_mae_improvement_percent": 100.0 * (_mae(baseline_abs) - _mae(selected_abs)) / _mae(baseline_abs) if baseline_abs and _mae(baseline_abs) else 0.0,
        "baseline_signed_bias_kwh": sum(baseline_signed) / len(baseline_signed) if baseline_signed else 0.0,
        "shadow_selected_signed_bias_kwh": sum(selected_signed) / len(selected_signed) if selected_signed else 0.0,
        "correction_application_rate": sum(int(outcome["correction_applied"]) for _, outcome in rows) / sample_count if sample_count else 0.0,
        "baseline_fallback_rate": sum(str(outcome["selected_model"]) == "baseline" for _, outcome in rows) / sample_count if sample_count else 0.0,
        "selection_count_by_model": dict(sorted(model_counts.items())),
        "selection_count_by_hour": {key: dict(sorted(value.items())) for key, value in sorted(hour_counts.items(), key=lambda item: int(item[0]))},
        "quality_flag_counts": dict(sorted(quality_flags.items())),
        "lead_time_distribution": {
            "count": decision_count,
            "min_minutes": min((int(row["lead_minutes"]) for row in valid_decisions), default=None),
            "max_minutes": max((int(row["lead_minutes"]) for row in valid_decisions), default=None),
        },
        "forecast_vintage_count": len({str(row["forecast_run_id"]) for row in valid_decisions}),
        "excluded_count_by_reason": dict(sorted(excluded.items())),
        "parameters": {"selector_granularity": "per_hour", "selector_window_days": SELECTOR_WINDOW_DAYS, "required_margin_fraction": REQUIRED_MARGIN_FRACTION, "fallback": "baseline"},
    }
    if include_recent_windows:
        latest = end
        if latest is None:
            latest_row = conn.execute(
                "SELECT MAX(d.target_date) FROM forecast_shadow_decisions d WHERE d.policy_name = ? AND d.policy_version = ?",
                (POLICY_NAME, POLICY_VERSION),
            ).fetchone()
            latest = date.fromisoformat(str(latest_row[0])) if latest_row and latest_row[0] else None
        report["recent_windows"] = {}
        if latest:
            for days in (7, 14, 21):
                window_start = latest - timedelta(days=days - 1)
                report["recent_windows"][f"last_{days}_days"] = report_shadow_outcomes(
                    conn,
                    start=window_start,
                    end=latest,
                    include_recent_windows=False,
                )
    return report

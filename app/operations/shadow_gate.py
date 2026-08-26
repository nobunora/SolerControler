"""Prospective, side-channel validation for the frozen hourly PV gate.

This module never writes forecast_hourly and never changes a production plan.
Decisions are frozen from forecast snapshots before outcomes are recorded;
outcomes are stored separately and are append-only.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any, Iterable

POLICY_NAME = "gate_per_hour_window21d_margin02pct"
POLICY_VERSION = "phase1-v1"
SELECTOR_WINDOW_DAYS = 21
REQUIRED_MARGIN_FRACTION = 0.02
MIN_HISTORY_DAYS = 3
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
            outcome_quality_flags_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_shadow_outcomes_target
            ON forecast_shadow_outcomes(target_at);
        """
    )
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


def _candidate_predictions(conn: sqlite3.Connection, snapshot: dict[str, Any]) -> dict[str, float]:
    baseline = float(snapshot["forecast_pv_kwh"] or 0.0)
    target_day = date.fromisoformat(str(snapshot["date"]))
    hour = int(snapshot["hour"])
    target_weather = snapshot.get("forecast_weather_code")
    target_shortwave = float(snapshot.get("forecast_shortwave_radiation_w_m2") or 0.0)
    residuals: list[tuple[float, float]] = []
    weather_residuals: list[tuple[float, float]] = []
    has_history = conn.execute(
        """
        SELECT 1
        FROM forecast_shadow_decisions d
        JOIN forecast_shadow_outcomes o ON o.shadow_decision_id = d.shadow_decision_id
        WHERE d.policy_name = ? AND d.policy_version = ?
          AND d.target_date >= ? AND d.target_date < ? AND d.target_hour = ?
        LIMIT 1
        """,
        (POLICY_NAME, POLICY_VERSION, (target_day - timedelta(days=45)).isoformat(), target_day.isoformat(), hour),
    ).fetchone()
    if not has_history:
        return {model: baseline for model in MODEL_NAMES}
    weather_rows = conn.execute(
        """
        SELECT d.target_date, d.baseline_prediction_kwh, o.actual_pv_kwh,
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
        age = max(0, (target_day - date.fromisoformat(str(row["target_date"]))).days)
        residual = float(row["actual_pv_kwh"]) - float(row["baseline_prediction_kwh"])
        residuals.append((residual, float(age)))
        prior_shortwave = float(row["forecast_shortwave_radiation_w_m2"] or 0.0)
        if target_weather is not None and row["forecast_weather_code"] == target_weather and target_shortwave > 0 and prior_shortwave > 0:
            if 0.7 * target_shortwave <= prior_shortwave <= 1.3 * target_shortwave:
                weather_residuals.append((residual, float(age)))

    def decayed(half_life: float) -> float:
        return _weighted_median([(residual, 0.5 ** (age / half_life)) for residual, age in residuals])

    production_residual = median(value for value, _ in weather_residuals) if weather_residuals else 0.0
    return {
        "baseline": max(0.0, baseline),
        "production_like_45d": max(0.0, baseline + production_residual),
        "same_hour_bias_45d_hl7d": max(0.0, baseline + decayed(7.0)),
        "same_hour_bias_45d_hl14d": max(0.0, baseline + decayed(14.0)),
    }


def _select_model(conn: sqlite3.Connection, target_day: date, hour: int) -> tuple[str, dict[str, Any]]:
    start = target_day - timedelta(days=SELECTOR_WINDOW_DAYS)
    rows = conn.execute(
        """
        SELECT d.target_date, d.selected_model, o.baseline_absolute_error_kwh,
               o.candidate_errors_json
        FROM forecast_shadow_decisions d
        JOIN forecast_shadow_outcomes o ON o.shadow_decision_id = d.shadow_decision_id
        WHERE d.policy_name = ? AND d.policy_version = ?
          AND d.target_date >= ? AND d.target_date < ? AND d.target_hour = ?
        """,
        (POLICY_NAME, POLICY_VERSION, start.isoformat(), target_day.isoformat(), hour),
    ).fetchall()
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


def build_shadow_decision(conn: sqlite3.Connection, snapshot: dict[str, Any], *, decision_at: str, cutoff_at: str, source_code_version: str | None = None) -> dict[str, Any]:
    candidates = _candidate_predictions(conn, snapshot)
    target_day = date.fromisoformat(str(snapshot["date"]))
    selected_model, score_summary = _select_model(conn, target_day, int(snapshot["hour"]))
    decision_at_dt = _parse_timestamp(decision_at)
    cutoff_dt = _parse_timestamp(cutoff_at)
    target_at = str(snapshot["target_at"])
    identity = {
        "policy_name": POLICY_NAME,
        "policy_version": POLICY_VERSION,
        "cutoff_at": _utc_iso(cutoff_dt),
        "forecast_snapshot_id": str(snapshot["snapshot_id"]),
        "selected_model": selected_model,
        "candidate_predictions": candidates,
    }
    decision_id = hashlib.sha256(_json(identity).encode("utf-8")).hexdigest()[:32]
    history_start = target_day - timedelta(days=SELECTOR_WINDOW_DAYS)
    flags: list[str] = []
    if score_summary["reason"] == "insufficient_shadow_history":
        flags.append("insufficient_shadow_history")
    if decision_at_dt > cutoff_dt:
        flags.append("decision_after_cutoff")
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
        "recorded_at": _utc_iso(datetime.now(timezone.utc)),
    }


def persist_shadow_decisions(conn: sqlite3.Connection, decisions: Iterable[dict[str, Any]]) -> int:
    ensure_shadow_schema(conn)
    columns = tuple(decisions)
    if not columns:
        return 0
    names = tuple(columns[0])
    placeholders = ",".join("?" for _ in names)
    before = conn.total_changes
    conn.executemany(
        f"INSERT OR IGNORE INTO forecast_shadow_decisions ({','.join(names)}) VALUES ({placeholders})",
        [[row[name] for name in names] for row in columns],
    )
    conn.commit()
    return conn.total_changes - before


def persist_shadow_outcomes(conn: sqlite3.Connection, actuals: dict[tuple[str, int], float], *, recorded_at: str) -> int:
    ensure_shadow_schema(conn)
    decisions = conn.execute("SELECT * FROM forecast_shadow_decisions ORDER BY target_date, target_hour").fetchall()
    rows = []
    for decision in decisions:
        key = (str(decision["target_date"]), int(decision["target_hour"]))
        if key not in actuals:
            continue
        actual = float(actuals[key])
        candidates = json.loads(str(decision["candidate_predictions_json"]))
        errors = {model: actual - float(prediction) for model, prediction in candidates.items()}
        selected_error = errors[str(decision["selected_model"])]
        rows.append((
            str(decision["shadow_decision_id"]), str(decision["target_at"]), actual,
            errors["baseline"], selected_error, _json(errors), abs(errors["baseline"]),
            abs(selected_error), abs(selected_error) - abs(errors["baseline"]),
            str(decision["selected_model"]), int(str(decision["selected_model"]) != "baseline"), recorded_at, "[]",
        ))
    before = conn.total_changes
    conn.executemany(
        """INSERT OR IGNORE INTO forecast_shadow_outcomes
        (shadow_decision_id,target_at,actual_pv_kwh,baseline_error_kwh,selected_error_kwh,
         candidate_errors_json,baseline_absolute_error_kwh,selected_absolute_error_kwh,
         selected_minus_baseline_absolute_error_kwh,selected_model,correction_applied,
         outcome_recorded_at,outcome_quality_flags_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    return conn.total_changes - before


def report_shadow_outcomes(
    conn: sqlite3.Connection,
    *,
    start: date | None = None,
    end: date | None = None,
    include_recent_windows: bool = True,
) -> dict[str, Any]:
    ensure_shadow_schema(conn)
    params: list[Any] = [POLICY_NAME, POLICY_VERSION]
    clauses = ["d.policy_name = ?", "d.policy_version = ?"]
    if start:
        clauses.append("d.target_date >= ?")
        params.append(start.isoformat())
    if end:
        clauses.append("d.target_date <= ?")
        params.append(end.isoformat())
    rows = conn.execute(
        "SELECT d.*, o.* FROM forecast_shadow_decisions d JOIN forecast_shadow_outcomes o "
        f"ON o.shadow_decision_id=d.shadow_decision_id WHERE {' AND '.join(clauses)} ORDER BY d.target_date,d.target_hour",
        params,
    ).fetchall()
    sample_count = len(rows)
    baseline_abs = [float(row["baseline_absolute_error_kwh"]) for row in rows]
    selected_abs = [float(row["selected_absolute_error_kwh"]) for row in rows]
    baseline_signed = [float(row["baseline_error_kwh"]) for row in rows]
    selected_signed = [float(row["selected_error_kwh"]) for row in rows]
    model_counts: dict[str, int] = {}
    hour_counts: dict[str, dict[str, int]] = {}
    for row in rows:
        model = str(row["selected_model"])
        model_counts[model] = model_counts.get(model, 0) + 1
        key = str(row["target_hour"])
        hour_counts.setdefault(key, {})[model] = hour_counts.setdefault(key, {}).get(model, 0) + 1
    decision_rows = conn.execute(
        "SELECT d.lead_minutes, d.forecast_run_id FROM forecast_shadow_decisions d WHERE " + " AND ".join(clauses),
        params,
    ).fetchall()
    decision_count = len(decision_rows)
    quality_flags: dict[str, int] = {}
    for row in rows:
        for field in ("decision_quality_flags_json", "outcome_quality_flags_json"):
            for flag in json.loads(str(row[field]) or "[]"):
                quality_flags[str(flag)] = quality_flags.get(str(flag), 0) + 1
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
        "correction_application_rate": sum(int(row["correction_applied"]) for row in rows) / sample_count if sample_count else 0.0,
        "baseline_fallback_rate": sum(str(row["selected_model"]) == "baseline" for row in rows) / sample_count if sample_count else 0.0,
        "selection_count_by_model": dict(sorted(model_counts.items())),
        "selection_count_by_hour": {key: dict(sorted(value.items())) for key, value in sorted(hour_counts.items(), key=lambda item: int(item[0]))},
        "quality_flag_counts": dict(sorted(quality_flags.items())),
        "lead_time_distribution": {
            "count": decision_count,
            "min_minutes": min((int(row["lead_minutes"]) for row in decision_rows), default=None),
            "max_minutes": max((int(row["lead_minutes"]) for row in decision_rows), default=None),
        },
        "forecast_vintage_count": len({str(row["forecast_run_id"]) for row in decision_rows}),
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

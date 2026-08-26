from __future__ import annotations

import json
import sqlite3
from datetime import date

from app.operations.shadow_gate import (
    POLICY_NAME,
    POLICY_VERSION,
    build_shadow_decision,
    ensure_shadow_schema,
    persist_shadow_decisions,
    persist_shadow_outcomes,
    report_shadow_outcomes,
)
from app.operations.shadow_gate import _select_model, _snapshot_rows


def _snapshot(snapshot_id: str, day: str = "2026-08-30", hour: int = 10, forecast: float = 10.0) -> dict:
    return {
        "snapshot_id": snapshot_id,
        "forecast_run_id": f"run-{snapshot_id}",
        "issued_at": f"{day}T00:00:00Z",
        "target_at": f"{day}T{hour:02d}:00:00+09:00",
        "date": day,
        "hour": hour,
        "lead_minutes": 540,
        "forecast_pv_kwh": forecast,
        "forecast_weather_code": 1,
        "forecast_shortwave_radiation_w_m2": 500.0,
    }


def _decision_record(snapshot: dict, day: str, hour: int, candidate: float, baseline: float = 10.0) -> dict:
    predictions = {
        "baseline": baseline,
        "production_like_45d": candidate,
        "same_hour_bias_45d_hl7d": candidate,
        "same_hour_bias_45d_hl14d": candidate,
    }
    return {
        "shadow_decision_id": f"decision-{snapshot['snapshot_id']}-{day}-{hour}",
        "policy_name": POLICY_NAME,
        "policy_version": POLICY_VERSION,
        "decision_at": f"{day}T00:00:00Z",
        "cutoff_at": f"{day}T00:00:00Z",
        "target_at": f"{day}T{hour:02d}:00:00+09:00",
        "target_date": day,
        "target_hour": hour,
        "forecast_snapshot_id": snapshot["snapshot_id"],
        "forecast_run_id": snapshot["forecast_run_id"],
        "forecast_issued_at": snapshot["issued_at"],
        "lead_minutes": 540,
        "baseline_prediction_kwh": baseline,
        "candidate_predictions_json": json.dumps(predictions),
        "candidate_score_summary_json": "{}",
        "selected_model": "baseline",
        "selected_prediction_kwh": baseline,
        "baseline_fallback_reason": "test",
        "selector_window_days": 21,
        "required_margin_fraction": 0.02,
        "history_window_start": "2026-08-01",
        "history_window_end": "2026-08-29",
        "history_sample_counts_json": "{}",
        "decision_quality_flags_json": "[]",
        "source_code_version": "test",
        "recorded_at": "2026-08-30T00:00:00Z",
    }


def test_shadow_schema_is_idempotent_and_decisions_are_retry_safe() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_shadow_schema(conn)
    ensure_shadow_schema(conn)
    first = _decision_record(_snapshot("a"), "2026-08-30", 10, 9.5)
    second = _decision_record(_snapshot("b"), "2026-08-30", 10, 9.4)
    assert persist_shadow_decisions(conn, [first]) == 1
    assert persist_shadow_decisions(conn, [first]) == 0
    assert persist_shadow_decisions(conn, [second]) == 1
    assert conn.execute("SELECT COUNT(*) FROM forecast_shadow_decisions").fetchone()[0] == 2


def test_build_decision_falls_back_without_history_and_contains_no_actual() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_shadow_schema(conn)
    decision = build_shadow_decision(
        conn,
        _snapshot("target"),
        decision_at="2026-08-29T15:00:00Z",
        cutoff_at="2026-08-29T15:00:00Z",
    )
    assert decision["selected_model"] == "baseline"
    assert decision["baseline_fallback_reason"] == "insufficient_shadow_history"
    assert "actual_pv_kwh" not in decision
    assert "decision_quality_flags_json" in decision


def test_outcome_is_append_only_and_does_not_mutate_decision() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_shadow_schema(conn)
    decision = _decision_record(_snapshot("target"), "2026-08-30", 10, 9.5)
    persist_shadow_decisions(conn, [decision])
    before = dict(conn.execute("SELECT * FROM forecast_shadow_decisions").fetchone())
    assert persist_shadow_outcomes(conn, {("2026-08-30", 10): 10.2}, recorded_at="2026-08-31T00:00:00Z") == 1
    assert persist_shadow_outcomes(conn, {("2026-08-30", 10): 10.2}, recorded_at="2026-08-31T00:05:00Z") == 0
    after = dict(conn.execute("SELECT * FROM forecast_shadow_decisions").fetchone())
    assert before == after
    assert conn.execute("SELECT COUNT(*) FROM forecast_shadow_outcomes").fetchone()[0] == 1


def test_regenerated_vintage_can_create_a_second_decision() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_shadow_schema(conn)
    first = _decision_record(_snapshot("vintage-a"), "2026-08-30", 10, 9.5)
    second = _decision_record(_snapshot("vintage-b"), "2026-08-30", 10, 9.4)
    assert persist_shadow_decisions(conn, [first, second]) == 2
    assert conn.execute("SELECT COUNT(DISTINCT forecast_snapshot_id) FROM forecast_shadow_decisions").fetchone()[0] == 2


def test_outcome_report_has_required_aggregates() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_shadow_schema(conn)
    decision = _decision_record(_snapshot("report"), "2026-08-30", 10, 9.5)
    persist_shadow_decisions(conn, [decision])
    persist_shadow_outcomes(conn, {("2026-08-30", 10): 10.2}, recorded_at="2026-08-31T00:00:00Z")
    report = report_shadow_outcomes(conn, start=date(2026, 8, 1), end=date(2026, 8, 31))
    assert report["sample_count"] == 1
    assert report["valid_decision_count"] == 1
    assert report["selection_count_by_model"] == {"baseline": 1}
    assert set(report["recent_windows"]) == {"last_7_days", "last_14_days", "last_21_days"}
    assert report["parameters"] == {
        "selector_granularity": "per_hour",
        "selector_window_days": 21,
        "required_margin_fraction": 0.02,
        "fallback": "baseline",
    }


def test_selector_is_strictly_prior_per_hour_and_requires_two_percent_margin() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_shadow_schema(conn)
    conn.execute("CREATE TABLE forecast_hourly_snapshots (snapshot_id TEXT PRIMARY KEY, date TEXT, hour INTEGER, issued_at TEXT, forecast_weather_code INTEGER, forecast_shortwave_radiation_w_m2 REAL)")
    history = []
    for day in ("2026-08-24", "2026-08-25", "2026-08-26"):
        snapshot = _snapshot(f"h-{day}", day=day, hour=10)
        conn.execute("INSERT INTO forecast_hourly_snapshots VALUES (?,?,?,?,?,?)", (snapshot["snapshot_id"], day, 10, snapshot["issued_at"], 1, 500.0))
        history.append(_decision_record(snapshot, day, 10, candidate=10.5))
    # A different hour must not pool its history into hour 10.
    for day in ("2026-08-24", "2026-08-25", "2026-08-26"):
        snapshot = _snapshot(f"h11-{day}", day=day, hour=11)
        conn.execute("INSERT INTO forecast_hourly_snapshots VALUES (?,?,?,?,?,?)", (snapshot["snapshot_id"], day, 11, snapshot["issued_at"], 1, 500.0))
        history.append(_decision_record(snapshot, day, 11, candidate=10.5))
    conn.commit()
    persist_shadow_decisions(conn, history)
    persist_shadow_outcomes(
        conn,
        {(day, hour): 10.5 for day in ("2026-08-24", "2026-08-25", "2026-08-26") for hour in (10, 11)},
        recorded_at="2026-08-27T00:00:00Z",
    )
    assert _select_model(conn, date(2026, 8, 30), 10)[0] != "baseline"
    assert _select_model(conn, date(2026, 8, 30), 12)[0] == "baseline"


def test_snapshot_cutoff_compares_timezone_aware_issue_times() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE forecast_hourly_snapshots (snapshot_id TEXT, date TEXT, hour INTEGER, issued_at TEXT)")
    conn.executemany(
        "INSERT INTO forecast_hourly_snapshots VALUES (?,?,?,?)",
            [("a", "2026-08-30", 10, "2026-08-30T00:00:00+09:00"), ("b", "2026-08-30", 10, "2026-08-29T17:00:00Z")],
    )
    conn.commit()
    selected = _snapshot_rows(conn, "2026-08-30T01:00:00+09:00", date(2026, 8, 30), date(2026, 8, 30))
    assert [row["snapshot_id"] for row in selected] == ["a"]

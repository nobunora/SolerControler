from __future__ import annotations

import hashlib
from importlib import import_module
import json
import math
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from app.operations.shadow_gate import (
    DIAGNOSTIC_EVIDENCE_CLASS,
    POLICY_NAME,
    POLICY_VERSION,
    _select_model,
    _snapshot_rows,
    _candidate_predictions,
    build_shadow_decision,
    ensure_shadow_schema,
    is_frozen_policy_target,
    open_sqlite_read_only,
    persist_shadow_decision_diagnostics,
    persist_shadow_decisions,
    persist_shadow_outcomes,
    production_like_prediction,
    report_shadow_outcomes,
    same_hour_bias_prediction,
    validate_separate_db_paths,
    weather_class,
)
from scripts.diagnose_hourly_pv_correction_limits import (
    Candidate,
    Row,
    predict_variant,
    production_like_prediction as diagnostic_production_like_prediction,
)
diagnostic_same_hour_predict = getattr(import_module("diagnose_hourly_pv_regime_bias"), "predict")
from scripts.forecast_shadow import _actuals


def _snapshot(snapshot_id: str, day: str = "2026-08-30", hour: int = 10, forecast: float = 10.0, *, weather: int = 1, shortwave: float = 500.0) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot_id,
        "forecast_run_id": f"run-{snapshot_id}",
        "issued_at": f"{day}T00:00:00Z",
        "target_at": f"{day}T{hour:02d}:00:00+09:00",
        "date": day,
        "hour": hour,
        "lead_minutes": 540,
        "forecast_pv_kwh": forecast,
        "forecast_weather_code": weather,
        "forecast_shortwave_radiation_w_m2": shortwave,
    }


def _decision_record(snapshot: dict[str, Any], day: str, hour: int, candidate: float, baseline: float = 10.0, *, version: str = "test", policy_version: str = POLICY_VERSION) -> dict[str, Any]:
    predictions = {
        "baseline": baseline,
        "production_like_45d": candidate,
        "same_hour_bias_45d_hl7d": candidate,
        "same_hour_bias_45d_hl14d": candidate,
    }
    return {
        "shadow_decision_id": f"decision-{snapshot['snapshot_id']}-{day}-{hour}",
        "policy_name": POLICY_NAME,
        "policy_version": policy_version,
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
        "forecast_weather_code": snapshot.get("forecast_weather_code"),
        "forecast_weather_class": weather_class(snapshot.get("forecast_weather_code")),
        "forecast_shortwave_radiation_w_m2": snapshot.get("forecast_shortwave_radiation_w_m2"),
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
        "source_code_version": version,
        "evidence_class": "prospective_primary",
        "recorded_at": f"{day}T00:00:00Z",
    }


def _complete_actual(actual: float = 10.2, *, count: int = 2) -> dict[str, Any]:
    return {
        "actual": actual,
        "sample_count": count,
        "expected_sample_count": count,
        "first_sample_at": "2026-08-30T10:00:00+09:00",
        "last_sample_at": "2026-08-30T10:30:00+09:00",
        "completeness_contract_proven": True,
    }


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_shadow_schema(conn)
    return conn


def test_schema_is_idempotent_and_retry_safe() -> None:
    conn = _conn()
    first = _decision_record(_snapshot("a"), "2026-08-30", 10, 9.5)
    assert persist_shadow_decisions(conn, [first]) == 1
    assert persist_shadow_decisions(conn, [first]) == 0
    assert conn.execute("SELECT policy_version FROM forecast_shadow_decisions").fetchone()[0] == "phase1-v2"


def test_frozen_domain_is_exact_and_provenance_is_required() -> None:
    assert [is_frozen_policy_target(hour, 1.0) for hour in (6, 7, 22, 23)] == [False, True, True, False]
    assert is_frozen_policy_target(10, 0.0) is False
    conn = _conn()
    with pytest.raises(ValueError, match="source_code_version"):
        build_shadow_decision(conn, _snapshot("missing-version"), decision_at="2026-08-29T15:00:00Z", cutoff_at="2026-08-30T00:00:00Z")
    with pytest.raises(ValueError, match="outside_frozen_policy_domain"):
        build_shadow_decision(conn, _snapshot("outside", hour=23), decision_at="2026-08-29T15:00:00Z", cutoff_at="2026-08-30T00:00:00Z", source_code_version="abc")


def test_build_decision_falls_back_without_history_and_has_no_actual() -> None:
    conn = _conn()
    decision = build_shadow_decision(conn, _snapshot("target"), decision_at="2026-08-29T15:00:00Z", cutoff_at="2026-08-30T00:00:00Z", source_code_version="abc")
    assert decision["policy_version"] == POLICY_VERSION
    assert decision["selected_model"] == "baseline"
    assert "actual_pv_kwh" not in decision


def test_same_hour_bias_uses_recency_only_and_accepts_two_rows() -> None:
    expected = predict_variant(
        Row(date(2026, 8, 30), 10, 10.0, 10.0, 500.0, "clear"),
        [
            Candidate(Row(date(2026, 8, 29), 10, 11.0, 10.0, 100.0, "rain"), 1, 0.0),
            Candidate(Row(date(2026, 8, 27), 10, 9.5, 10.0, 1000.0, "snow"), 3, 0.0),
        ],
        residual_kind="additive",
        half_life_days=7.0,
        min_candidates=2,
    )
    assert same_hour_bias_prediction(10.0, [(1.0, 1), (-0.5, 3)], half_life_days=7.0) == pytest.approx(expected)
    assert same_hour_bias_prediction(10.0, [(1.0, 1)], half_life_days=7.0) == 10.0


def test_candidate_formulas_match_independent_frozen_diagnostic() -> None:
    target = Row(date(2026, 8, 30), 10, 10.0, 10.0, 500.0, "clear")
    prior = [
        Candidate(Row(date(2026, 8, 29), 10, 11.0, 10.0, 510.0, "clear"), 1, abs(math.log(510.0 / 500.0))),
        Candidate(Row(date(2026, 8, 27), 10, 9.5, 10.0, 480.0, "clear"), 3, abs(math.log(480.0 / 500.0))),
    ]
    assert production_like_prediction(10.0, [1.0, -0.5]) == pytest.approx(diagnostic_production_like_prediction(target, prior))


def test_end_to_end_candidate_parity_uses_independent_oracle_and_separate_histories() -> None:
    conn = _conn()
    target = _snapshot("target", day="2026-08-30", weather=1, shortwave=500.0)
    prior_specs = (
        ("2026-08-29", 11.0, 10.0, 1, 500.0, "clear"),
        ("2026-08-28", 12.0, 10.0, 61, 900.0, "rain"),
        ("2026-08-27", 9.0, 10.0, 1, 0.0, "clear"),
        ("2026-08-26", 8.0, 10.0, 1, 500.0, "clear"),
        ("2026-08-25", 7.0, 0.0, 1, 500.0, "clear"),
    )
    oracle_rows = {
        (date.fromisoformat(day), 10): Row(date.fromisoformat(day), 10, actual, forecast, shortwave, weather)
        for day, actual, forecast, _code, shortwave, weather in prior_specs
    }
    prior_rows = []
    for day, actual, forecast, code, shortwave, _weather in prior_specs:
        if forecast <= 0:
            continue
        snapshot = _snapshot(f"prior-{day}", day=day, forecast=forecast, weather=code, shortwave=shortwave)
        prior = _decision_record(snapshot, day, 10, candidate=forecast, baseline=forecast)
        prior_rows.append(prior)
    persist_shadow_decisions(conn, prior_rows)
    persist_shadow_outcomes(conn, {(row["target_date"], 10): _complete_actual(float(prior_specs[index][1])) for index, row in enumerate(prior_rows)}, recorded_at="2026-08-30T00:00:00Z")
    predictions = _candidate_predictions(conn, target)
    target_row = Row(date(2026, 8, 30), 10, 10.0, 10.0, 500.0, "clear")
    oracle_candidates = [Candidate(row, (target_row.day - row.day).days, abs(math.log(row.shortwave / target_row.shortwave)) if row.shortwave > 0 else float("inf")) for row in oracle_rows.values() if row.forecast > 0]
    assert predictions["production_like_45d"] == pytest.approx(diagnostic_production_like_prediction(target_row, [candidate for candidate in oracle_candidates if candidate.row.weather_class == "clear" and candidate.row.shortwave > 0 and 350 <= candidate.row.shortwave <= 650]))
    oracle_same_hour_rows = {key: row for key, row in oracle_rows.items() if row.forecast > 0}
    assert predictions["same_hour_bias_45d_hl7d"] == pytest.approx(diagnostic_same_hour_predict(target_row, oracle_same_hour_rows, lookback=45, half_life=7.0, residual_kind="additive"))
    assert predictions["same_hour_bias_45d_hl14d"] == pytest.approx(diagnostic_same_hour_predict(target_row, oracle_same_hour_rows, lookback=45, half_life=14.0, residual_kind="additive"))


def test_selector_exact_two_percent_equality_falls_back() -> None:
    conn = _conn()
    records = []
    for day in ("2026-08-24", "2026-08-25", "2026-08-26"):
        records.append(_decision_record(_snapshot(f"eq-{day}", day=day), day, 10, candidate=9.98))
    persist_shadow_decisions(conn, records)
    persist_shadow_outcomes(conn, {(record["target_date"], 10): _complete_actual(9.0) for record in records}, recorded_at="2026-08-27T00:00:00Z")
    assert _select_model(conn, date(2026, 8, 30), 10)[0] == "baseline"


def test_outcome_is_fail_closed_until_contract_is_explicit() -> None:
    conn = _conn()
    decision = _decision_record(_snapshot("target"), "2026-08-30", 10, 9.5)
    persist_shadow_decisions(conn, [decision])
    assert persist_shadow_outcomes(conn, {("2026-08-30", 10): {"actual": 10.2, "sample_count": 2}}, recorded_at="2026-08-31T00:00:00Z") == 0
    assert conn.execute("SELECT reason FROM forecast_shadow_outcome_diagnostics").fetchone()[0] == "actual_completeness_contract_unproven"
    assert persist_shadow_outcomes(conn, {("2026-08-30", 10): _complete_actual()}, recorded_at="2026-08-31T00:00:00Z") == 1
    assert tuple(conn.execute("SELECT actual_complete, expected_sample_count FROM forecast_shadow_outcomes").fetchone()) == (1, 2)
    assert persist_shadow_outcomes(conn, {("2026-08-30", 10): _complete_actual()}, recorded_at="2026-08-31T00:05:00Z") == 0


def test_target_hour_not_finalized_precedes_completeness_block() -> None:
    conn = _conn()
    persist_shadow_decisions(conn, [_decision_record(_snapshot("finalize"), "2026-08-30", 10, 9.5)])
    assert persist_shadow_outcomes(conn, {("2026-08-30", 10): _complete_actual()}, recorded_at="2026-08-30T01:30:00Z") == 0
    assert conn.execute("SELECT reason FROM forecast_shadow_outcome_diagnostics").fetchone()[0] == "target_hour_not_finalized"


def test_selector_is_strictly_prior_per_hour_and_requires_three_dates() -> None:
    conn = _conn()
    history = []
    for index, day in enumerate(("2026-08-24", "2026-08-25", "2026-08-26")):
        snapshot = _snapshot(f"h-{day}", day=day, hour=10)
        history.append(_decision_record(snapshot, day, 10, candidate=10.0))
    persist_shadow_decisions(conn, history)
    persist_shadow_outcomes(conn, {(day, 10): _complete_actual(10.0) for day in ("2026-08-24", "2026-08-25", "2026-08-26")}, recorded_at="2026-08-27T00:00:00Z")
    assert _select_model(conn, date(2026, 8, 30), 10)[0] == "baseline"
    assert _select_model(conn, date(2026, 8, 27), 10)[1]["distinct_days"] == 3


def test_multiple_vintages_have_one_primary_and_v1_is_excluded() -> None:
    conn = _conn()
    first = _decision_record(_snapshot("vintage-1"), "2026-08-30", 10, 9.5)
    second = _decision_record(_snapshot("vintage-2"), "2026-08-30", 10, 9.0)
    second["decision_at"] = "2026-08-30T00:30:00Z"
    second["cutoff_at"] = "2026-08-30T00:30:00Z"
    second["forecast_issued_at"] = "2026-08-29T23:30:00Z"
    v1 = _decision_record(_snapshot("v1"), "2026-08-29", 10, 9.0, policy_version="phase1-v1")
    v1["evidence_class"] = DIAGNOSTIC_EVIDENCE_CLASS
    persist_shadow_decisions(conn, [first, second, v1])
    persist_shadow_outcomes(conn, {("2026-08-30", 10): _complete_actual()}, recorded_at="2026-08-31T00:00:00Z")
    report = report_shadow_outcomes(conn, start=date(2026, 8, 29), end=date(2026, 8, 31))
    assert report["valid_primary_decision_count"] == 1
    assert report["excluded_count_by_reason"]["superseded_policy_version"] == 1


def test_source_readonly_and_shadow_paths_are_physically_separate(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    shadow = tmp_path / "shadow.db"
    source_conn = sqlite3.connect(source)
    source_conn.execute("CREATE TABLE forecast_hourly_snapshots (snapshot_id TEXT PRIMARY KEY, forecast_run_id TEXT, issued_at TEXT, target_at TEXT, date TEXT, hour INTEGER, lead_minutes INTEGER, forecast_pv_kwh REAL, forecast_weather_code INTEGER, forecast_shortwave_radiation_w_m2 REAL)")
    source_conn.execute("CREATE TABLE monitoring_samples (ts TEXT PRIMARY KEY, pv_kwh REAL)")
    source_conn.execute("INSERT INTO forecast_hourly_snapshots VALUES (?,?,?,?,?,?,?,?,?,?)", ("smoke", "run-smoke", "2026-08-29T00:00:00Z", "2026-08-30T10:00:00+09:00", "2026-08-30", 10, 540, 10.0, 1, 500.0))
    source_conn.execute("INSERT INTO monitoring_samples VALUES (?,?)", ("2026-08-30T01:30:00Z", 1.0))
    source_conn.commit()
    source_conn.close()
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    validate_separate_db_paths(source, shadow)
    readonly = open_sqlite_read_only(source)
    with pytest.raises(sqlite3.OperationalError):
        readonly.execute("CREATE TABLE forecast_shadow_should_not_exist (x INTEGER)")
    shadow_conn = sqlite3.connect(shadow)
    shadow_conn.row_factory = sqlite3.Row
    ensure_shadow_schema(shadow_conn)
    snapshots = _snapshot_rows(readonly, "2026-08-30T00:00:00Z", date(2026, 8, 30), date(2026, 8, 30))
    decision = build_shadow_decision(readonly, shadow_conn, snapshots[0], decision_at="2026-08-29T15:00:00Z", cutoff_at="2026-08-30T00:00:00Z", source_code_version="smoke-sha")
    assert persist_shadow_decisions(shadow_conn, [decision]) == 1
    persist_shadow_outcomes(shadow_conn, _actuals(readonly, date(2026, 8, 30), date(2026, 8, 30)), recorded_at="2026-08-30T02:10:00Z")
    report = report_shadow_outcomes(shadow_conn, start=date(2026, 8, 30), end=date(2026, 8, 30))
    assert report["actual_completeness_status"] == "BLOCKED"
    shadow_conn.close()
    readonly.close()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert "forecast_shadow_decisions" not in {row[0] for row in sqlite3.connect(source).execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert sqlite3.connect(shadow).execute("SELECT COUNT(*) FROM forecast_shadow_decisions").fetchone()[0] == 1
    with pytest.raises(ValueError, match="different files"):
        validate_separate_db_paths(source, source)


def test_decision_diagnostics_record_outside_domain() -> None:
    conn = _conn()
    assert persist_shadow_decision_diagnostics(conn, [_snapshot("outside", hour=23)], attempted_at="2026-08-30T00:00:00Z", reason="outside_frozen_policy_domain") == 1
    assert conn.execute("SELECT reason FROM forecast_shadow_decision_diagnostics").fetchone()[0] == "outside_frozen_policy_domain"


def test_actual_aggregation_converts_utc_to_site_timezone() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE monitoring_samples (ts TEXT, pv_kwh REAL)")
    conn.executemany("INSERT INTO monitoring_samples VALUES (?, ?)", [("2026-08-29T15:30:00Z", 1.0), ("2026-08-30T00:30:00Z", 2.0)])
    actuals = _actuals(conn, date(2026, 8, 30), date(2026, 8, 30), timezone_name="Asia/Tokyo")
    assert actuals[("2026-08-30", 0)]["actual"] == pytest.approx(1.0)
    assert actuals[("2026-08-30", 9)]["actual"] == pytest.approx(2.0)

"""Frozen-origin coverage, chronology and economic replay admission."""
from dataclasses import replace
from datetime import date, datetime, timedelta
import json
from zoneinfo import ZoneInfo

import pytest

from app.energy_plan.hourly_pv_bias import (
    PvBiasDay,
    ReplayErrors,
    collect_pv_bias_days,
    forecast_weather_by_hour,
    select_hourly_pv_bias,
)

ZONE = ZoneInfo("Asia/Tokyo")
AS_OF = datetime(2026, 11, 3, 2, 30, tzinfo=ZONE)
TARGET = AS_OF.date().isoformat()


def record(day, *, actual=1.0, forecast=2.0):
    issued = datetime.combine(day, datetime.min.time(), ZONE) + timedelta(hours=2)
    def values(value):
        return dict.fromkeys(range(24), value)
    return PvBiasDay(day, issued, issued + timedelta(hours=23), day.isoformat(),
                     values(forecast), values(3.0), values(1.0), values("clear"),
                     values(actual), values(1.0), values(0.0), values(0.0), values(50.0), (), day.isoformat())


def history(count):
    return tuple(record(AS_OF.date() - timedelta(days=count - index)) for index in range(count))


def select(days, evaluator, **kwargs):
    return select_hourly_pv_bias(days=days, as_of=AS_OF, target_date=TARGET,
                                forecast_pv=dict.fromkeys(range(24), 2.0),
                                maximum=dict.fromkeys(range(24), 3.0),
                                weather_by_hour=dict.fromkeys(range(24), "clear"),
                                evaluate_day=evaluator, **kwargs)


def errors(day, pv):
    error = sum(abs(pv[h] - day.hourly_actual_pv_kwh[h]) for h in range(24)) / 24
    return ReplayErrors(error, error, 30 * error)


def test_7_days_need_forward_validation_before_any_correction():
    pv, diagnostics, model = select(history(7), errors)
    assert pv == dict.fromkeys(range(24), 2.0)
    assert model is None
    assert diagnostics["reason"] == "insufficient_forward_folds"


def test_admitted_after_five_later_folds_uses_exact_distinct_day_shrinkage():
    days = history(12)
    seen = []
    def evaluator(day, pv):
        seen.append((day.day, pv[12]))
        return errors(day, pv)
    pv, diagnostics, model = select(days, evaluator)
    assert model is not None
    assert diagnostics["status"] == "accepted"
    assert len(diagnostics["validation_days"]) == 5
    assert pv[12] == pytest.approx(2 - 12 / 32)
    # Earliest validation has seven earlier days; its own actual is excluded.
    assert seen[1][1] == pytest.approx(2 - 7 / 27)
    assert diagnostics["validation_errors"]["soc_mae"]["candidate"] < 1


@pytest.mark.parametrize("regression", ["buy", "fee", "soc"])
def test_better_pv_error_cannot_override_economic_or_soc_replay_regression(regression):
    def evaluator(_day, pv):
        baseline = pv[12] == 2
        return ReplayErrors(1 if baseline or regression == "soc" else .5,
                            0 if baseline else 1 if regression == "buy" else 0,
                            0 if baseline else 1 if regression == "fee" else 0)
    pv, diagnostics, model = select(history(25), evaluator)
    assert model is None and pv[12] == 2
    assert diagnostics["status"] == "neutral"
    assert diagnostics["reason"] == "replay_rejected"


def test_future_available_actuals_are_not_used_in_earlier_fold():
    days = history(25)
    late = tuple(replace(day, available_at=AS_OF) for day in days)
    pv, diagnostics, model = select(late, errors)
    assert model is None and pv[12] == 2
    assert diagnostics["validation_days"] == []


def test_duplicate_days_cannot_inflate_bin_coverage():
    with pytest.raises(ValueError, match="duplicate"):
        select(history(25) + history(1), errors)


def test_unknown_weather_neutral_and_clipping_bounded():
    from app.energy_plan.hourly_pv_bias import _apply
    pv = dict.fromkeys(range(24), 1.0)
    maximum = dict.fromkeys(range(24), 2.0)
    weather = dict.fromkeys(range(24), "clear")
    result = _apply(pv, maximum, weather, {(0, "clear"): -10, (1, "clear"): 10})
    assert result[0] == 0 and result[1] == 2 and result[2] == 1
    assert pv[0] == 1 and maximum[0] == 2
    assert forecast_weather_by_hour({"weather_code": 0})[0] == "unknown"


def test_bad_or_unavailable_replay_retains_previously_accepted_model():
    _, _, prior = select(history(25), errors)
    pv, diagnostics, model = select(history(25), None, previous_model=prior)
    assert model is prior
    assert pv[12] < 2
    assert diagnostics["status"] == "retained_previous"
    _, _, invalid = select(history(25), None, previous_model=replace(prior, validated_at=AS_OF + timedelta(days=1)))
    assert invalid is None


def raw_sources(day=date(2026, 11, 1)):
    issue = datetime.combine(day, datetime.min.time(), ZONE) + timedelta(hours=2)
    forecasts = [{"date": day.isoformat(), "hour": h, "forecast_run_id": "frozen", "issued_at_source": "plan",
                  "issued_at": issue.isoformat(), "recorded_at": (issue + timedelta(minutes=1)).isoformat(),
                  "forecast_load_kwh": 1.0,
                  "pv_forecast_detail": {"hourly_pv_bias": {"uncorrected_pv_kwh": 1.0, "maximum_kwh": 2.0,
                                                              "model_id": "physical_pv_ideal_max_global_v1",
                                                              "weather_class": "clear"}}} for h in range(24)]
    forecasts[7]["pv_forecast_detail"]["hourly_pv_bias"]["battery_response"] = {
        "schema_version": 1, "charge_soc_per_kwh": 10.0, "discharge_soc_per_kwh": 12.0}
    available = issue + timedelta(days=1)
    actual = [{"dt": datetime.combine(day, datetime.min.time()) + timedelta(minutes=30 * slot),
               "pv": .3, "load": .5, "buy": .1, "sell": 0.0, "soc": 50.0,
               "available_at": available.isoformat()} for slot in range(48)]
    return forecasts, actual


def collect(forecasts, actual):
    return collect_pv_bias_days(target_date=TARGET, as_of=AS_OF, timezone_name="Asia/Tokyo",
                                actual_rows=actual, snapshot_rows=forecasts)


def test_collector_complete_frozen_run_retains_actual_timestamp_and_acquisition():
    forecasts, actual = raw_sources()
    days, provenance = collect(forecasts, actual)
    assert len(days) == 1 and provenance["accepted_days"] == 1
    assert days[0].hourly_actual_pv_kwh[10] == .6
    assert days[0].actual_rows[0]["dt"] == actual[0]["dt"]
    assert len(days[0].source_sha256) == 64


@pytest.mark.parametrize("invalid", ["missing_actual", "duplicate_actual", "future_actual", "missing_availability",
                                      "mixed_run", "late_issue", "negative_actual", "missing_cap", "invalid_soc",
                                      "missing_response", "future_response"])
def test_collector_rejects_partial_or_noncontemporaneous_pairs(invalid):
    forecasts, actual = raw_sources()
    if invalid == "missing_actual":
        actual.pop()
    elif invalid == "duplicate_actual":
        actual.append(actual[0])
    elif invalid == "future_actual":
        actual[0]["available_at"] = (AS_OF + timedelta(days=1)).isoformat()
    elif invalid == "missing_availability":
        actual[0].pop("available_at")
    elif invalid == "mixed_run":
        forecasts[0]["forecast_run_id"] = "other"
    elif invalid == "late_issue":
        forecasts[0]["issued_at"] = "2026-11-01T08:00:00+09:00"
    elif invalid == "negative_actual":
        actual[0]["pv"] = -1.0
    elif invalid == "invalid_soc":
        actual[0]["soc"] = 101.0
    elif invalid == "missing_response":
        forecasts[7]["pv_forecast_detail"]["hourly_pv_bias"].pop("battery_response")
    elif invalid == "future_response":
        forecasts[7]["pv_forecast_detail"]["hourly_pv_bias"]["battery_response_as_of"] = AS_OF.isoformat()
    else:
        forecasts[0]["pv_forecast_detail"]["hourly_pv_bias"].pop("maximum_kwh")
    days, provenance = collect(forecasts, actual)
    assert days == ()
    assert provenance["rejected_days"] == 1


def test_no_actuals_does_not_make_any_database_read(monkeypatch):
    from app.energy_plan import hourly_pv_bias
    def forbidden(**kwargs):
        raise AssertionError("duplicate read")
    monkeypatch.setattr(hourly_pv_bias, "load_pv_bias_snapshots", forbidden)
    assert collect_pv_bias_days(target_date=TARGET, as_of=AS_OF, timezone_name="Asia/Tokyo", actual_rows=[])[0] == ()


def test_existing_snapshot_json_freezes_raw_forecast_cap_and_response_once():
    from app.operations.forecast_snapshot import build_forecast_snapshot_rows
    response = {"schema_version": 1, "charge_soc_per_kwh": 10.0, "discharge_soc_per_kwh": 12.0}
    ideal = {"model_id": "physical_pv_ideal_max_global_v1", "basis_version": "v1", "revision": 4,
             "hourly_uncorrected_pv_kwh": {"7": 1.0, "8": 1.0},
             "hourly_maximum_kwh": {"7": 2.0, "8": 2.0},
             "hourly_weather_class": {"7": "clear", "8": "clear"}}
    plan = {"issued_at": "2026-11-01T02:00:00+09:00", "forecast": {"date": "2026-11-01"},
            "result": {"final_pv_forecast_source": "physical_pv_ideal_max_global_v1"},
            "daytime_soc_optimization": {"hourly_pv_forecast_kwh": {"7": .5, "8": .5},
                                         "hourly_load_forecast_kwh": {"7": 1.0, "8": 1.0}},
            "decision_rationale": {"pv_ideal_max": ideal, "battery_response": response,
                                   "battery_calibration": {"as_of": "2026-11-01T01:59:00+09:00"}}}
    snapshots = build_forecast_snapshot_rows(plan, ingested_at="2026-11-01T02:01:00+09:00", timezone="Asia/Tokyo")
    by_hour = {row["hour"]: row for row in snapshots}
    seven = json.loads(by_hour[7]["pv_forecast_detail_json"])["hourly_pv_bias"]
    eight = json.loads(by_hour[8]["pv_forecast_detail_json"])["hourly_pv_bias"]
    assert by_hour[7]["forecast_pv_kwh"] == .5
    assert seven["uncorrected_pv_kwh"] == 1.0
    assert seven["maximum_kwh"] == 2.0
    assert seven["battery_response"] == response
    assert "battery_response" not in eight
    assert ideal["hourly_uncorrected_pv_kwh"]["7"] == 1.0

"""Load-only correction parity, ownership and callback isolation contracts."""

import hashlib
import json
from copy import deepcopy
from datetime import datetime

import pytest

from app.forecasting import correction, correction_model

# Canonical whole-result snapshots from the pre-extraction implementation at
# d401b15de199ff8f1215935d01080a67a4c95266, evaluated with these offline ports.
# Every PV/load/scenario/diagnostic key participates, including full precision.
_LEGACY_RESULT_SHA256 = {
    "normal/False": "0e3f927858ee00ecc32b703289075febc748a8f564760ae0132bb5c117ea8510",
    "normal/True": "f240b06abaf0a9410d76afdbaa5bb51ca1f0275be7e42254dabe4d7750c8a4f7",
    "heat/False": "cca0660714d7f515e65e15d615645547c65ed81dfcf719f4fea7059527d426e2",
    "heat/True": "a33dba170009c8b328a5c7dba62407e12f6cf7ba05233aa34e540c24475afb80",
    "empty_history/False": "f6e731a0984fd64919a7aa97285d53cb0ce4bdbd48e1d5e2f572874e77d9a6aa",
    "empty_history/True": "cf1aa234d3f0d3428a28bd6318dca3d35c15a2372960909fbbfc527cca169b80",
    "missing/False": "a08cfe4e3fdb2412f9c31c9e69efa93efd79aab6fb9a50a9b5fe84c1766b4f45",
    "missing/True": "62ade62854d01ffc54a6ab3c5af9d7c3e3088298da7db9f3d5990ab3a0124741",
    "zero_temperature/False": "0e3f927858ee00ecc32b703289075febc748a8f564760ae0132bb5c117ea8510",
    "zero_temperature/True": "f240b06abaf0a9410d76afdbaa5bb51ca1f0275be7e42254dabe4d7750c8a4f7",
}


def _policy(*, enabled=True, skip_pv=False):
    return correction.ForecastCorrectionPolicy(
        enabled=enabled, skip_pv_correction=skip_pv, allow_load_safety_floor=True,
        pv_ewma_alpha=0.2, pv_ratio_min=0.9, pv_ratio_max=1.35,
        load_ewma_alpha=0.5, load_ratio_min=0.7, load_ratio_max=1.8,
    )


def _input(case="normal"):
    days = ["2026-05-30", "2026-05-31"]
    rows = [
        {"dt": datetime.fromisoformat(f"{day}T{hour:02}:00:00"),
         "load": 1.2 + index * 0.1, "pv": None if case == "missing" else 0.8}
        for index, day in enumerate(days) for hour in range(24)
    ]
    forecast = {
        "hourly_weather": [
            {"hour": hour, "temp_c": 0.0 if case == "zero_temperature" else
             (35.0 + hour / 12.0 if case == "heat" else 25.0),
             "relative_humidity_percent": 65.0, "dew_point_c": 20.0}
            for hour in range(24)
        ],
    }
    return correction.ForecastCorrectionInput(
        rows=[] if case == "empty_history" else rows,
        hourly_load_forecast={hour: 0.5 + hour * 0.02 for hour in range(24)},
        hourly_pv_forecast={hour: 0.8 if 7 <= hour <= 17 else 0.0 for hour in range(24)},
        target_date="2026-06-01", latitude=35.0, longitude=139.0,
        timezone="Asia/Tokyo", forecast=forecast,
    )


def _ports(monkeypatch, case="normal", comfort=None):
    events = []
    days = ["2026-05-30", "2026-05-31"]
    history = {
        day: {hour: {"pv": 1.0, "load": 1.0} for hour in range(24)}
        for day in days
    } if case != "empty_history" else {}
    weather = {
        day: {hour: {"temp_c": 30.0, "relative_humidity_percent": 70.0,
                    "dew_point_c": 22.0} for hour in range(24)}
        for day in ["2026-05-29", *days, "2026-06-01"]
    }
    temperature = {
        "enabled": True, "applied": case == "heat",
        "multiplier_delta": 0.2 if case == "heat" else 0.0,
        "load_scenarios": [{"label": "temperature", "probability": 1.0, "multiplier": 1.1}],
    }
    comfort = comfort if comfort is not None else {
        "enabled": True, "applied": False, "reason": "insufficient_samples",
    }

    def history_loader(**kwargs):
        events.append("history")
        return history, "sqlite_forecast_hourly"

    def weather_fetch(**kwargs):
        events.append("archive_weather" if kwargs["archive"] else "target_weather")
        return weather

    def temperature_corrector(**kwargs):
        events.append("temperature")
        return temperature

    def comfort_predictor(**kwargs):
        events.append("comfort")
        return comfort

    original_actual = correction_model.actual_hourly_totals_by_day
    original_pairs = correction_model.daily_pairs_for_ratio
    original_ewma = correction_model.ewma_ratio_from_daily_pairs
    original_paired = correction_model._paired_forecast_error_scenarios
    original_assemble = correction_model.assemble_forecast_correction_result

    def actual_totals(*args, **kwargs):
        events.append("actual")
        return original_actual(*args, **kwargs)

    def daily_pairs(**kwargs):
        events.append(f"pairs_{kwargs['key']}")
        return original_pairs(**kwargs)

    def ewma(*args, **kwargs):
        events.append("ewma_" + events[-1].removeprefix("pairs_"))
        return original_ewma(*args, **kwargs)

    def physical_corrector(**kwargs):
        events.append("physical")
        return kwargs["hourly_pv"], {"enabled": True, "reason": "fixture"}

    def paired(**kwargs):
        events.append("paired")
        return original_paired(**kwargs)

    def assemble(**kwargs):
        events.append("assemble")
        return original_assemble(**kwargs)

    monkeypatch.setenv("LOAD_COMFORT_MODEL_ENABLED", "true")
    monkeypatch.setenv("LOAD_TEMPERATURE_CORRECTION_HOURS", "0-23")
    monkeypatch.setattr(correction, "_load_forecast_hourly_history", history_loader)
    monkeypatch.setattr(correction, "fetch_hourly_weather", weather_fetch)
    monkeypatch.setattr(correction, "_evening_temperature_correction", temperature_corrector)
    monkeypatch.setattr(correction, "predict_hourly_comfort_load", comfort_predictor)
    monkeypatch.setattr(correction, "_physical_vector_residual_correction", physical_corrector)
    monkeypatch.setattr(correction_model, "actual_hourly_totals_by_day", actual_totals)
    monkeypatch.setattr(correction_model, "daily_pairs_for_ratio", daily_pairs)
    monkeypatch.setattr(correction_model, "ewma_ratio_from_daily_pairs", ewma)
    monkeypatch.setattr(correction_model, "_paired_forecast_error_scenarios", paired)
    monkeypatch.setattr(correction_model, "assemble_forecast_correction_result", assemble)
    return events, history, weather, temperature, comfort


def _load_only(correction_input, policy):
    return correction.build_load_forecast_correction(
        rows=correction_input.rows, hourly_load_forecast=correction_input.hourly_load_forecast,
        target_date=correction_input.target_date, latitude=correction_input.latitude,
        longitude=correction_input.longitude, timezone=correction_input.timezone,
        forecast=correction_input.forecast, policy=policy,
    )


@pytest.mark.parametrize("case", ["normal", "heat", "empty_history", "missing", "zero_temperature"])
@pytest.mark.parametrize("skip_pv", [False, True])
def test_old_and_load_only_results_match(monkeypatch, case, skip_pv):
    correction_input = _input(case)
    events, *shared = _ports(monkeypatch, case)
    original = deepcopy(shared)
    legacy = correction.build_forecast_correction(correction_input, _policy(skip_pv=skip_pv))
    legacy_bytes = json.dumps(legacy, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    assert hashlib.sha256(legacy_bytes).hexdigest() == _LEGACY_RESULT_SHA256[f"{case}/{skip_pv}"]
    legacy_events = list(events)
    events.clear()
    result = _load_only(correction_input, _policy(skip_pv=skip_pv))
    assert set(result) == {"enabled", "hourly_load_kwh", "load_scenarios", "rationale"}
    assert result["hourly_load_kwh"] == legacy["hourly_load_kwh"]
    assert result["load_scenarios"] == legacy["load_scenarios"]
    assert len(result["hourly_load_kwh"]) == 24
    for key, value in result["rationale"].items():
        assert legacy["rationale"][key] == value
    assert shared == original
    assert events.count("history") == events.count("actual") == events.count("ewma_load") == 1
    assert events.count("archive_weather") == legacy_events.count("archive_weather")
    assert events.count("target_weather") == legacy_events.count("target_weather")
    assert not {"ewma_pv", "physical", "paired", "assemble"} & set(events)


@pytest.mark.parametrize("applied,hour_count", [(True, 24), (False, 24), (True, 23)])
def test_comfort_replaces_values_and_scenarios(monkeypatch, applied, hour_count):
    comfort = {
        "enabled": True, "applied": applied, "confidence": 0.75,
        "hourly_load_kwh": {hour: 0.75 + hour * 0.01 for hour in range(hour_count)},
        "_residual_multipliers": [0.9, 1.0, 1.1],
    }
    original = deepcopy(comfort)
    _ports(monkeypatch, "heat", comfort)
    first = _load_only(_input("heat"), _policy())
    second = _load_only(_input("heat"), _policy())
    assert first == second
    assert comfort == original
    if applied and hour_count == 24:
        assert first["hourly_load_kwh"] == original["hourly_load_kwh"]
        assert len(first["load_scenarios"]) == 5
        assert first["load_scenarios"][2]["multiplier"] == 1.0
    else:
        assert first["hourly_load_kwh"] != original["hourly_load_kwh"]
        assert first["load_scenarios"] == [{"label": "temperature", "probability": 1.0, "multiplier": 1.1}]
    assert "_residual_multipliers" not in first["rationale"]["comfort_load_model"]
    first["rationale"]["comfort_load_model"]["reason"] = "changed_by_reader"
    first["load_scenarios"][0]["multiplier"] = 99.0
    assert first != second
    assert comfort == original


def test_disabled_preserves_raw_load(monkeypatch):
    events, *_ = _ports(monkeypatch)
    correction_input = _input("zero_temperature")
    result = _load_only(correction_input, _policy(enabled=False))
    assert result == {
        "enabled": False, "hourly_load_kwh": correction_input.hourly_load_forecast,
        "load_scenarios": [], "rationale": {"enabled": False, "reason": "disabled"},
    }
    assert result["hourly_load_kwh"] is correction_input.hourly_load_forecast
    assert events == []
    assert correction.build_forecast_correction(correction_input, _policy(enabled=False)) == {
        "enabled": False, "hourly_load_kwh": correction_input.hourly_load_forecast,
        "hourly_pv_kwh": correction_input.hourly_pv_forecast,
        "rationale": {"enabled": False, "reason": "disabled"},
    }


def test_legacy_callback_order_and_single_ewma(monkeypatch):
    events, *_ = _ports(monkeypatch)
    result = correction.build_forecast_correction(_input(), _policy(skip_pv=True))
    assert events == [
        "history", "actual", "pairs_pv", "ewma_pv", "pairs_load", "ewma_load", "archive_weather",
        "temperature", "physical", "comfort", "paired", "assemble",
    ]
    assert set(result) == {
        "enabled", "hourly_load_kwh", "hourly_pv_kwh", "load_scenarios",
        "paired_scenarios", "peak_penalty", "rationale",
    }
    assert len(result["paired_scenarios"]) == 3


def test_load_only_has_no_pv_callbacks_or_extra_io(monkeypatch):
    events, *_ = _ports(monkeypatch)

    def forbidden(**kwargs):
        pytest.fail("load-only path called a PV/paired correction or legacy assembler")

    monkeypatch.setattr(correction_model, "_correct_hourly_pv", forbidden)
    monkeypatch.setattr(correction_model, "_paired_forecast_error_scenarios", forbidden)
    monkeypatch.setattr(correction_model, "assemble_forecast_correction_result", forbidden)
    correction_input = _input()
    correction_input.forecast.clear()
    result = _load_only(correction_input, _policy(skip_pv=True))
    assert len(result["hourly_load_kwh"]) == 24
    assert events == [
        "history", "actual", "pairs_load", "ewma_load", "archive_weather", "target_weather",
        "temperature", "comfort",
    ]

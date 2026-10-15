from datetime import datetime, timedelta

import pytest

from app.forecasting import correction


def _history(days=30):
    start = datetime(2026, 8, 1)
    rows = []
    weather = {}
    for offset in range(days + 1):
        day = start + timedelta(days=offset)
        weather[day.date().isoformat()] = {
            hour: {"temp_c": 20.0, "relative_humidity_percent": 60.0, "dew_point_c": 12.0, "wind_speed_10m": 2.0}
            for hour in range(24)
        }
        if offset < days:
            for slot in range(48):
                hour = slot // 2
                rows.append({"dt": day + timedelta(minutes=30*slot), "load": 1.0 if hour < 7 or hour >= 18 else 0.25, "pv": 0.0})
    return rows, weather, (start + timedelta(days=days)).date().isoformat()


@pytest.mark.parametrize("missing_previous_actual", [False, True])
def test_hourly_learning_uses_full_actual_period_without_saved_forecasts(monkeypatch, missing_previous_actual):
    rows, weather, target = _history()
    if missing_previous_actual:
        rows = rows[:-48]
    requests = []
    def fetch(**kwargs):
        requests.append(kwargs)
        return weather
    monkeypatch.setenv("LOAD_COMFORT_MODEL_ENABLED", "true")
    monkeypatch.setenv("LOAD_COMFORT_MODEL_MIN_SAMPLES", "336")
    monkeypatch.setattr(correction, "_load_forecast_hourly_history", lambda **_: ({}, "empty"))
    monkeypatch.setattr(correction, "fetch_hourly_weather", fetch)
    args = dict(hourly_load_forecast={h: 1.0 for h in range(24)}, target_date=target,
                latitude=35.0, longitude=135.0, timezone="Asia/Tokyo", forecast={},
                policy=correction.ForecastCorrectionPolicy.from_env())
    old = correction.build_load_forecast_correction(rows=rows[-7*48:], **args)
    result = correction.build_load_forecast_correction(rows=rows, **args)
    assert old["rationale"]["comfort_load_model"]["reason"] == "insufficient_history"
    model = result["rationale"]["comfort_load_model"]
    assert model["applied"] is True and model["sample_count"] == (29 if missing_previous_actual else 30)*24
    assert model["training_end"] < target
    assert any(r["archive"] and r["start_date"] < "2026-08-01" for r in requests)
    assert any(r["archive"] and r["end_date"] == "2026-08-30" for r in requests)
    values = result["hourly_load_kwh"]
    assert set(values) == set(range(24))
    assert values[3] == pytest.approx(2.0, abs=0.1)
    assert values[12] == pytest.approx(0.5, abs=0.1)
    assert values[20] == pytest.approx(2.0, abs=0.1)


def test_legacy_pv_correction_keeps_short_history_while_load_receives_training_window(monkeypatch):
    rows, weather, target = _history()
    observed = {}
    monkeypatch.setenv("LOAD_COMFORT_MODEL_ENABLED", "true")
    monkeypatch.setattr(correction, "_load_forecast_hourly_history", lambda **_: ({}, "empty"))
    monkeypatch.setattr(correction, "fetch_hourly_weather", lambda **_: weather)
    def comfort(**kwargs):
        observed["load_days"] = len(kwargs["actual_history"])
        return {"applied": False}
    def physical(**kwargs):
        observed["pv_days"] = len(kwargs["actual_history"])
        return kwargs["hourly_pv"], {"enabled": True}
    monkeypatch.setattr(correction, "predict_hourly_comfort_load", comfort)
    monkeypatch.setattr(correction, "_physical_vector_residual_correction", physical)
    inputs = correction.ForecastCorrectionInput(rows=rows[-7*48:], load_training_rows=rows,
        hourly_load_forecast={h: 1.0 for h in range(24)}, hourly_pv_forecast={h: 0.0 for h in range(24)},
        target_date=target, latitude=35.0, longitude=135.0, timezone="Asia/Tokyo", forecast={})
    correction.build_forecast_correction(inputs, correction.ForecastCorrectionPolicy.from_env(skip_pv_correction=True))
    assert observed == {"load_days": 30, "pv_days": 7}

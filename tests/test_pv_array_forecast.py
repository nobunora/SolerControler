from __future__ import annotations

from datetime import datetime

import pytest

from app.pv_array_forecast import (
    PVArrayConfig,
    build_pv_array_forecast,
    calibrate_performance_ratio,
    forecast_pv_arrays,
    forecast_pv_arrays_forecast_solar,
)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return

    def json(self):
        return self._payload


def test_forecast_pv_arrays_sums_three_orientations() -> None:
    arrays = [
        PVArrayConfig("east", -90, 20, 1.0, performance_ratio=1.0),
        PVArrayConfig("south", 0, 20, 1.0, performance_ratio=1.0),
        PVArrayConfig("west", 90, 20, 1.0, performance_ratio=1.0),
    ]

    def fake_get(url, *, params, timeout):
        assert "global_tilted_irradiance" in params["hourly"]
        azimuth = params["azimuth"]
        if azimuth == -90:
            gti = [1000.0, 0.0, 0.0]
        elif azimuth == 0:
            gti = [0.0, 1000.0, 0.0]
        else:
            gti = [0.0, 0.0, 1000.0]
        return _FakeResponse(
            {
                "hourly": {
                    "time": ["2026-05-18T07:00", "2026-05-18T12:00", "2026-05-18T17:00"],
                    "global_tilted_irradiance": gti,
                    "temperature_2m": [25.0, 25.0, 25.0],
                }
            }
        )

    forecast = forecast_pv_arrays(
        arrays=arrays,
        target_date="2026-05-18",
        lat=35.0,
        lon=139.0,
        timezone="Asia/Tokyo",
        http_get=fake_get,
    )

    assert forecast["totals"]["total_kwh"] == pytest.approx(3.0)
    assert forecast["totals"]["morning_kwh"] == pytest.approx(1.0)
    assert forecast["totals"]["midday_kwh"] == pytest.approx(1.0)
    assert forecast["totals"]["evening_kwh"] == pytest.approx(1.0)


def test_forecast_pv_arrays_forecast_solar_sums_three_orientations() -> None:
    arrays = [
        PVArrayConfig("east", -90, 20, 1.0, performance_ratio=1.0),
        PVArrayConfig("south", 0, 20, 1.0, performance_ratio=1.0),
        PVArrayConfig("west", 90, 20, 1.0, performance_ratio=1.0),
    ]

    def fake_get(url, *, timeout):
        if "/-90.000/" in url:
            values = {
                "2026-05-18 07:00:00": 1000.0,
                "2026-05-18 12:00:00": 0.0,
                "2026-05-18 17:00:00": 0.0,
            }
        elif "/0.000/" in url:
            values = {
                "2026-05-18 07:00:00": 0.0,
                "2026-05-18 12:00:00": 2000.0,
                "2026-05-18 17:00:00": 0.0,
            }
        else:
            values = {
                "2026-05-18 07:00:00": 0.0,
                "2026-05-18 12:00:00": 0.0,
                "2026-05-18 17:00:00": 3000.0,
            }
        return _FakeResponse({"result": {"watt_hours_period": values}})

    forecast = forecast_pv_arrays_forecast_solar(
        arrays=arrays,
        target_date="2026-05-18",
        lat=35.0,
        lon=139.0,
        timezone="Asia/Tokyo",
        http_get=fake_get,
    )

    assert forecast["source"] == "forecast-solar-estimate"
    assert forecast["totals"]["total_kwh"] == pytest.approx(6.0)
    assert forecast["totals"]["morning_kwh"] == pytest.approx(1.0)
    assert forecast["totals"]["midday_kwh"] == pytest.approx(2.0)
    assert forecast["totals"]["evening_kwh"] == pytest.approx(3.0)


def test_calibration_uses_actual_generation_history() -> None:
    arrays = [PVArrayConfig("south", 0, 20, 1.0, performance_ratio=1.0)]
    rows = [
        {"dt": datetime(2026, 5, 15, 12), "pv": 2.0},
        {"dt": datetime(2026, 5, 16, 12), "pv": 2.0},
        {"dt": datetime(2026, 5, 17, 12), "pv": 2.0},
    ]

    def fake_get(url, *, params, timeout):
        return _FakeResponse(
            {
                "hourly": {
                    "time": [
                        "2026-05-15T12:00",
                        "2026-05-16T12:00",
                        "2026-05-17T12:00",
                    ],
                    "global_tilted_irradiance": [1000.0, 1000.0, 1000.0],
                    "temperature_2m": [25.0, 25.0, 25.0],
                }
            }
        )

    calibration = calibrate_performance_ratio(
        arrays=arrays,
        rows=rows,
        target_date="2026-05-18",
        lat=35.0,
        lon=139.0,
        timezone="Asia/Tokyo",
        min_days=3,
        http_get=fake_get,
    )

    assert calibration["factor"] == pytest.approx(2.0)
    assert calibration["sample_days"] == 3


def test_build_pv_array_forecast_applies_weather_multiplier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arrays = [PVArrayConfig("south", 0, 20, 1.0, performance_ratio=1.0)]
    rows = [
        {"dt": datetime(2026, 5, 15, 12), "pv": 1.0},
        {"dt": datetime(2026, 5, 16, 12), "pv": 2.0},
    ]

    monkeypatch.setenv("PV_ARRAY_WEATHER_CALIBRATION_MIN_DAYS", "1")
    monkeypatch.setenv("PV_ARRAY_WEATHER_ADJUSTMENT_MIN_RATIO", "0.5")
    monkeypatch.setenv("PV_ARRAY_WEATHER_ADJUSTMENT_MAX_RATIO", "1.5")
    monkeypatch.setenv("PV_ARRAY_CALIBRATION_MIN_DAYS", "1")
    monkeypatch.setenv("PV_ARRAY_PROVIDER", "open_meteo")

    def fake_get(url, *, params, timeout):
        if "archive-api.open-meteo.com" in url:
            if "weather_code" in str(params.get("daily", "")):
                return _FakeResponse(
                    {
                        "daily": {
                            "time": ["2026-05-15", "2026-05-16"],
                            "weather_code": [0, 3],
                        }
                    }
                )
            return _FakeResponse(
                {
                    "hourly": {
                        "time": ["2026-05-15T12:00", "2026-05-16T12:00"],
                        "global_tilted_irradiance": [1000.0, 1000.0],
                        "temperature_2m": [25.0, 25.0],
                    }
                }
            )
        return _FakeResponse(
            {
                "hourly": {
                    "time": ["2026-05-17T12:00"],
                    "global_tilted_irradiance": [1000.0],
                    "temperature_2m": [25.0],
                }
            }
        )

    forecast = build_pv_array_forecast(
        arrays=arrays,
        rows=rows,
        target_date="2026-05-17",
        lat=35.0,
        lon=139.0,
        timezone="Asia/Tokyo",
        target_weather_class="cloudy",
        http_get=fake_get,
    )
    assert forecast is not None
    assert forecast["totals"]["total_kwh"] == pytest.approx(2.0, rel=1e-3)
    calibration = forecast["calibration"]
    assert calibration["target_weather_class"] == "cloudy"
    assert calibration["weather_multiplier"] == pytest.approx(1.3333, rel=1e-4)


def test_build_pv_array_forecast_prefers_regression_blend_for_cloudy_or_rain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arrays = [PVArrayConfig("south", 0, 20, 1.0, performance_ratio=1.0)]
    rows = [
        {"dt": datetime(2026, 5, 15, 12), "pv": 1.0},
        {"dt": datetime(2026, 5, 16, 12), "pv": 1.5},
        {"dt": datetime(2026, 5, 17, 12), "pv": 2.0},
    ]

    monkeypatch.setenv("PV_ARRAY_CALIBRATION_MIN_DAYS", "1")
    monkeypatch.setenv("PV_ARRAY_WEATHER_CALIBRATION_MIN_DAYS", "1")
    monkeypatch.setenv("PV_ARRAY_WEATHER_REGRESSION_MIN_DAYS", "1")
    monkeypatch.setenv("PV_ARRAY_WEATHER_REGRESSION_BLEND", "1.0")
    monkeypatch.setenv("PV_ARRAY_WEATHER_REGRESSION_RIDGE", "0.01")
    monkeypatch.setenv("PV_ARRAY_PROVIDER", "open_meteo")

    def fake_get(url, *, params, timeout):
        if "archive-api.open-meteo.com" in url:
            if "weather_code" in str(params.get("daily", "")):
                return _FakeResponse(
                    {
                        "daily": {
                            "time": ["2026-05-15", "2026-05-16", "2026-05-17"],
                            "weather_code": [3, 3, 3],
                            "sunshine_duration": [3600.0, 7200.0, 10800.0],
                            "precipitation_sum": [0.0, 0.0, 0.0],
                        }
                    }
                )
            return _FakeResponse(
                {
                    "hourly": {
                        "time": ["2026-05-15T12:00", "2026-05-16T12:00", "2026-05-17T12:00"],
                        "global_tilted_irradiance": [1000.0, 1000.0, 1000.0],
                        "temperature_2m": [25.0, 25.0, 25.0],
                    }
                }
            )
        return _FakeResponse(
            {
                "hourly": {
                    "time": ["2026-05-18T12:00"],
                    "global_tilted_irradiance": [1000.0],
                    "temperature_2m": [25.0],
                }
            }
        )

    forecast = build_pv_array_forecast(
        arrays=arrays,
        rows=rows,
        target_date="2026-05-18",
        lat=35.0,
        lon=139.0,
        timezone="Asia/Tokyo",
        target_weather_class="cloudy",
        target_sun_hours=4.0,
        target_precipitation_sum_mm=0.0,
        http_get=fake_get,
    )
    assert forecast is not None
    assert forecast["totals"]["total_kwh"] > 2.0
    calibration = forecast["calibration"]
    assert calibration["adjustment_strategy"] == "regression_blend"


def test_build_pv_array_forecast_falls_back_to_open_meteo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arrays = [PVArrayConfig("south", 0, 20, 1.0, performance_ratio=1.0)]
    rows = [{"dt": datetime(2026, 5, 17, 12), "pv": 1.0}]

    monkeypatch.setenv("PV_ARRAY_PROVIDER", "forecast_solar,open_meteo")
    monkeypatch.setenv("PV_ARRAY_CALIBRATION_MIN_DAYS", "1")
    monkeypatch.setenv("PV_ARRAY_WEATHER_CALIBRATION_ENABLED", "false")

    def fake_get(url, *, params=None, timeout):
        if "forecast.solar" in url:
            raise RuntimeError("forecast solar unavailable")
        if "archive-api.open-meteo.com" in url:
            return _FakeResponse(
                {
                    "hourly": {
                        "time": ["2026-05-17T12:00"],
                        "global_tilted_irradiance": [1000.0],
                        "temperature_2m": [25.0],
                    }
                }
            )
        return _FakeResponse(
            {
                "hourly": {
                    "time": ["2026-05-18T12:00"],
                    "global_tilted_irradiance": [1000.0],
                    "temperature_2m": [25.0],
                }
            }
        )

    forecast = build_pv_array_forecast(
        arrays=arrays,
        rows=rows,
        target_date="2026-05-18",
        lat=35.0,
        lon=139.0,
        timezone="Asia/Tokyo",
        target_weather_class="clear",
        http_get=fake_get,
    )

    assert forecast is not None
    assert forecast["provider"] == "open_meteo"
    assert forecast["totals"]["total_kwh"] == pytest.approx(1.0)
    assert forecast["provider_attempts"][0]["provider"] == "forecast_solar"
    assert forecast["provider_attempts"][0]["ok"] is False
    assert forecast["provider_attempts"][1]["provider"] == "open_meteo"
    assert forecast["provider_attempts"][1]["ok"] is True

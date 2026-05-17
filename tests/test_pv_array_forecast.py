from __future__ import annotations

from datetime import datetime

import pytest

from app.pv_array_forecast import (
    PVArrayConfig,
    calibrate_performance_ratio,
    forecast_pv_arrays,
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

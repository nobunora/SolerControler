from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.energy_plan.settings import ForecastSettings, HistoricalInputSettings
from app.energy_plan.weather import WeatherHistoryFetchResult
from app.forecasting.pv_array import PVArrayConfig
from app.operations.historical_forecast_reconstruction import build_reconstructed_forecast_rows
from app.operations.historical_forecast_replay import (
    SINGLE_RUN_FORECAST_HOURS,
    _single_run_params,
    build_historical_replay_plan,
    default_single_run_for_target,
    filter_pre_target_history,
    write_historical_replay_plan,
)


# HISTORICAL_FAILURE_LOCK (2026-09-05): historical replay must remain forecast-only
# and immune to target-day/future actual leakage. Single Runs requests must use
# run + forecast_hours; start_date/end_date are rejected by the live endpoint.


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def _times(day: str, count: int = 24) -> list[str]:
    return [f"{day}T{hour:02d}:00" for hour in range(count)]


def _target_from_run_params(params: dict[str, Any]) -> str:
    assert "start_date" not in params
    assert "end_date" not in params
    assert params["forecast_hours"] == SINGLE_RUN_FORECAST_HOURS
    run_day = datetime.fromisoformat(str(params["run"])).date()
    return (run_day + timedelta(days=1)).isoformat()


def _http_get(_url: str, *, params: dict[str, Any], timeout: int) -> _Response:
    del timeout
    day = _target_from_run_params(params)
    times = _times(day)
    if "global_tilted_irradiance" in str(params.get("hourly")):
        gti = [0.0 if hour < 6 or hour > 18 else 500.0 for hour in range(24)]
        return _Response(
            {
                "hourly": {
                    "time": times,
                    "global_tilted_irradiance": gti,
                    "temperature_2m": [25.0] * 24,
                }
            }
        )
    return _Response(
        {
            "hourly": {
                "time": times,
                "temperature_2m": [28.0] * 24,
                "relative_humidity_2m": [60.0] * 24,
                "dew_point_2m": [19.0] * 24,
                "precipitation": [0.0] * 24,
                "weather_code": [1] * 24,
                "cloud_cover": [20.0] * 24,
                "shortwave_radiation": [0.0 if h < 6 or h > 18 else 600.0 for h in range(24)],
                "sunshine_duration": [0.0 if h < 6 or h > 18 else 3600.0 for h in range(24)],
                "wind_speed_10m": [5.0] * 24,
            }
        }
    )


def _incomplete_http_get(_url: str, *, params: dict[str, Any], timeout: int) -> _Response:
    del timeout
    day = _target_from_run_params(params)
    times = _times(day, 23)
    return _Response(
        {
            "hourly": {
                "time": times,
                "temperature_2m": [28.0] * 23,
                "relative_humidity_2m": [60.0] * 23,
                "dew_point_2m": [19.0] * 23,
                "precipitation": [0.0] * 23,
                "weather_code": [1] * 23,
                "cloud_cover": [20.0] * 23,
                "shortwave_radiation": [500.0] * 23,
                "sunshine_duration": [3600.0] * 23,
                "wind_speed_10m": [5.0] * 23,
                "global_tilted_irradiance": [500.0] * 23,
            }
        }
    )


def _history_rows(start: str, days: int) -> list[dict[str, Any]]:
    start_dt = datetime.fromisoformat(f"{start}T00:00:00")
    rows: list[dict[str, Any]] = []
    for day_offset in range(days):
        for slot in range(48):
            dt = start_dt + timedelta(days=day_offset, minutes=30 * slot)
            rows.append(
                {
                    "dt": dt,
                    "load": 0.2 + (dt.hour / 100.0),
                    "pv": 0.0 if dt.hour < 6 or dt.hour > 18 else 0.3,
                    "buy": 0.1,
                    "sell": 0.0,
                    "charge": 0.0,
                    "discharge": 0.0,
                    "soc": 50.0,
                }
            )
    return rows


def _weather_loader(rows: list[dict[str, Any]], **_kwargs: Any) -> WeatherHistoryFetchResult:
    dates = sorted({row["dt"].date().isoformat() for row in rows})
    weather_rows = [
        {
            "date": day,
            "temp": 27.0,
            "weather_code": 1,
            "sunshine_hours": 8.0,
            "precipitation": 0.0,
        }
        for day in dates
    ]
    return WeatherHistoryFetchResult(
        weather_rows,
        dates,
        dates,
        [],
        [],
        dates,
        [],
    )


def _calibration_builder(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return {
        "factor": 1.0,
        "sample_days": 7,
        "source": "test_pre_target_calibration",
        "weather_adjustments": {},
        "weather_regression": {},
    }


def _settings() -> ForecastSettings:
    return ForecastSettings(latitude=35.0, longitude=139.0, timezone="Asia/Tokyo")


def _history_settings(tmp_path: Path) -> HistoricalInputSettings:
    return HistoricalInputSettings(
        artifacts_dir=tmp_path,
        min_training_days=999,
        fallback_window_days=14,
    )


def _array() -> list[PVArrayConfig]:
    return [
        PVArrayConfig(
            name="south",
            azimuth_deg=0.0,
            tilt_deg=20.0,
            capacity_kw=3.0,
            performance_ratio=0.84,
            shading_factor=1.0,
            temp_coeff_per_deg=-0.0035,
        )
    ]


def test_default_replay_run_is_conservatively_available_before_0230_jst() -> None:
    assert default_single_run_for_target("2026-08-30") == "2026-08-29T12:00"


def test_single_runs_request_uses_run_horizon_not_rejected_date_range() -> None:
    params = _single_run_params(
        settings=_settings(),
        target_date="2026-08-30",
        model="jma_msm",
        run="2026-08-29T12:00",
        hourly="temperature_2m",
    )
    assert params["run"] == "2026-08-29T12:00"
    assert params["forecast_hours"] == 48
    assert "start_date" not in params
    assert "end_date" not in params


def test_filter_pre_target_history_rejects_target_day_and_future_rows() -> None:
    rows = _history_rows("2026-08-28", 4)
    filtered = filter_pre_target_history(rows, target_date="2026-08-30")
    assert filtered
    assert all(row["dt"].date().isoformat() < "2026-08-30" for row in filtered)


def test_historical_replay_is_invariant_to_target_and_future_actuals(tmp_path: Path) -> None:
    target = "2026-08-30"
    pre_target = _history_rows("2026-08-20", 10)
    baseline = build_historical_replay_plan(
        pre_target,
        target_date=target,
        forecast_settings=_settings(),
        historical_settings=_history_settings(tmp_path),
        arrays=_array(),
        http_get=_http_get,
        weather_history_loader=_weather_loader,
        calibration_builder=_calibration_builder,
    )

    contaminated = [dict(row) for row in pre_target]
    contaminated.extend(_history_rows(target, 3))
    for row in contaminated:
        if row["dt"].date().isoformat() >= target:
            row.update(
                {
                    "load": 999999.0,
                    "pv": 999999.0,
                    "buy": 999999.0,
                    "sell": 999999.0,
                    "charge": 999999.0,
                    "discharge": 999999.0,
                    "soc": 1.0,
                }
            )
    replay = build_historical_replay_plan(
        contaminated,
        target_date=target,
        forecast_settings=_settings(),
        historical_settings=_history_settings(tmp_path),
        arrays=_array(),
        http_get=_http_get,
        weather_history_loader=_weather_loader,
        calibration_builder=_calibration_builder,
    )

    assert replay.forecast_hash == baseline.forecast_hash
    assert replay.plan["daytime_soc_optimization"] == baseline.plan["daytime_soc_optimization"]
    assert replay.eligible_history_row_count == baseline.eligible_history_row_count
    assert replay.plan["historical_replay"]["eligible_history_end"] < f"{target}T00:00:00"


def test_historical_replay_emits_complete_pair_and_reconstruction_accepts_it(tmp_path: Path) -> None:
    target = "2026-08-30"
    replay = build_historical_replay_plan(
        _history_rows("2026-08-20", 10),
        target_date=target,
        forecast_settings=_settings(),
        historical_settings=_history_settings(tmp_path),
        arrays=_array(),
        http_get=_http_get,
        weather_history_loader=_weather_loader,
        calibration_builder=_calibration_builder,
    )
    optimization = replay.plan["daytime_soc_optimization"]
    assert set(map(int, optimization["hourly_pv_forecast_kwh"])) == set(range(24))
    assert set(map(int, optimization["hourly_load_forecast_kwh"])) == set(range(24))
    assert replay.plan["forecast"]["historical_forecast"]["model"] == "jma_msm"
    assert replay.plan["forecast"]["historical_forecast"]["run"] == "2026-08-29T12:00"
    assert replay.plan["forecast"]["historical_forecast"]["requested_forecast_hours"] == 48

    plan_path = tmp_path / "replay.json"
    write_historical_replay_plan(replay, plan_path)
    rows, summary = build_reconstructed_forecast_rows(
        plan_path=plan_path,
        target_date=target,
        reconstruction_model_version="historical-forecast-replay-v1",
        reconstruction_basis="historical_model_replay",
        input_provenance="jma_msm single-run D-1 12Z; history strictly before target date",
        reconstructed_at="2026-09-05T00:00:00Z",
    )
    assert len(rows) == 24
    assert summary["hourly_row_count"] == 24
    assert all(row["source"] == "historical_reconstructed_estimate" for row in rows)
    assert all("forecast_run_id" not in row for row in rows)
    assert all("forecast_issued_at" not in row for row in rows)


def test_historical_replay_rejects_incomplete_single_run(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="exactly 24 unique hours"):
        build_historical_replay_plan(
            _history_rows("2026-08-20", 10),
            target_date="2026-08-30",
            forecast_settings=_settings(),
            historical_settings=_history_settings(tmp_path),
            arrays=_array(),
            http_get=_incomplete_http_get,
            weather_history_loader=_weather_loader,
            calibration_builder=_calibration_builder,
        )

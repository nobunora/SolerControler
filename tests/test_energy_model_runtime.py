from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from app.consumption_forecast import ConsumptionForecast
from app.energy_model import EnergyModelCoefficients
from energy_model_main import (
    EnergyModelConfig,
    WeatherHistoryFetchResult,
    _build_consumption_forecasts,
    build_energy_plan,
    _load_execution_context,
)


def _coefficients() -> EnergyModelCoefficients:
    return EnergyModelCoefficients(
        soc_per_kwh_charge=10.0,
        soc_per_kwh_discharge=10.0,
        soc_drift_per_slot=0.0,
        battery_round_trip_efficiency=0.9,
        battery_usable_capacity_kwh=10.0,
        pv_self_consumption_ratio=0.7,
        pv_direct_use_ratio=0.5,
        pv_to_battery_ratio=0.2,
        pv_kwh_per_sunhour=2.0,
        pv_temp_coeff_per_deg=-0.01,
        battery_temp_coeff_per_deg=-0.01,
        battery_cycle_capacity_fade_per_cycle=0.001,
    )


def test_energy_model_config_centralizes_runtime_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARTIFACTS_DIR", "saved-artifacts")
    monkeypatch.setenv("FORECAST_LATITUDE", "35.5")
    monkeypatch.setenv("FORECAST_LONGITUDE", "139.5")
    monkeypatch.setenv("CONSUMPTION_MODEL_MIN_TRAINING_DAYS", "12")
    monkeypatch.setenv("BATTERY_TEMP_C", "31.5")
    monkeypatch.setenv("DAYTIME_SOC_OPT_STEP_PERCENT", "2.0")
    monkeypatch.delenv("SOC_COST_OPT_STEP_PERCENT", raising=False)

    config = EnergyModelConfig.from_env()

    assert config.artifacts_dir == Path("saved-artifacts")
    assert config.latitude == pytest.approx(35.5)
    assert config.longitude == pytest.approx(139.5)
    assert config.consumption_min_training_days == 12
    assert config.battery_temp_c == pytest.approx(31.5)
    assert config.cost_soc_step_percent == pytest.approx(2.0)


def test_energy_model_config_preserves_invalid_numeric_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FORECAST_LATITUDE", "invalid")

    with pytest.raises(ValueError):
        EnergyModelConfig.from_env()


def test_load_execution_context_preserves_loaded_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "history.csv"
    coefficients = _coefficients()
    rows = [
        {"dt": datetime(2026, 7, 16, 23, 30), "load": 1.0, "pv": 0.0, "soc": 42.0}
    ]
    forecast = {"date": "2026-07-17", "sun_hours": 8.0, "temp_c": 30.0}
    monkeypatch.setattr(
        "energy_model_main._csv_paths_from_env_or_latest", lambda _: [csv_path]
    )
    monkeypatch.setattr("energy_model_main._read_rows", lambda _: rows)
    monkeypatch.setattr("energy_model_main.fit_coefficients_from_csv", lambda _: coefficients)
    monkeypatch.setattr(
        "energy_model_main._historical_profile",
        lambda _: {"morning_pv_ratio": 0.25, "midday_surplus_ratio": 0.375},
    )
    monkeypatch.setattr("energy_model_main._forecast_from_env_or_api", lambda **_: forecast)
    monkeypatch.setattr("energy_model_main.load_occupancy_events_from_env", lambda: [])

    context = _load_execution_context(EnergyModelConfig.from_env())

    assert context.csv_paths == [csv_path]
    assert context.rows is rows
    assert context.coefficients is coefficients
    assert context.forecast is forecast
    assert context.target_date == "2026-07-17"
    assert context.latest_soc_percent == pytest.approx(42.0)


def test_consumption_bundle_preserves_forecast_and_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    rows = [
        {"dt": datetime(2026, 7, 15, 12, 0), "load": 3.0, "pv": 1.0, "soc": 50.0}
    ]
    monkeypatch.setattr(
        "energy_model_main._csv_paths_from_env_or_latest", lambda _: [tmp_path / "x.csv"]
    )
    monkeypatch.setattr("energy_model_main._read_rows", lambda _: rows)
    monkeypatch.setattr("energy_model_main.fit_coefficients_from_csv", lambda _: _coefficients())
    monkeypatch.setattr(
        "energy_model_main._historical_profile",
        lambda _: {"morning_pv_ratio": 0.25, "midday_surplus_ratio": 0.375},
    )
    monkeypatch.setattr(
        "energy_model_main._forecast_from_env_or_api",
        lambda **_: {"date": "2026-07-17", "sun_hours": 8.0, "temp_c": 30.0},
    )
    monkeypatch.setattr("energy_model_main.load_occupancy_events_from_env", lambda: [])
    monkeypatch.setattr(
        "energy_model_main._archive_weather_history",
        lambda *_, **__: WeatherHistoryFetchResult(
            rows=[{"date": "2026-07-15", "temp": 29.0}],
            requested_dates=["2026-07-15"],
            received_dates=["2026-07-15"],
            missing_dates=[],
            errors=[],
            cache_hit_dates=["2026-07-15"],
            requested_periods=[],
        ),
    )
    expected = ConsumptionForecast(
        target_date=datetime(2026, 7, 17).date(),
        morning_load_kwh=4.0,
        daytime_load_kwh=20.0,
        source="hist_gradient_boosting",
        sample_count=1,
        features=["temp"],
    )
    monkeypatch.setattr("energy_model_main.forecast_daily_consumption", lambda *_, **__: expected)

    bundle = _build_consumption_forecasts(
        _load_execution_context(EnergyModelConfig.from_env())
    )

    assert bundle.daily is expected
    assert bundle.base_daily is expected
    assert bundle.training_diagnostics["joined_training_day_count"] == 1
    assert bundle.training_diagnostics["fallback_reason"] is None
    assert bundle.occupancy_adjustment is None


def test_build_energy_plan_coordinates_stages_without_persisting(monkeypatch, tmp_path) -> None:
    config = EnergyModelConfig.from_env()
    values = [object() for _ in range(7)]
    context, consumption, night_charge, pv, constraints, legacy, decision = values
    output = object()
    calls: list[tuple[str, tuple[object, ...]]] = []

    def stage(name, result):
        def call(*args):
            calls.append((name, args))
            return result
        return call

    monkeypatch.setattr("energy_model_main._load_execution_context", stage("context", context))
    monkeypatch.setattr("energy_model_main._build_consumption_forecasts", stage("consumption", consumption))
    monkeypatch.setattr("energy_model_main._prepare_night_charge", stage("night", night_charge))
    monkeypatch.setattr("energy_model_main._build_selected_pv_forecast", stage("pv", pv))
    monkeypatch.setattr("energy_model_main._build_soc_constraints", stage("constraints", constraints))
    monkeypatch.setattr("energy_model_main._run_legacy_soc_optimization", stage("legacy", legacy))
    monkeypatch.setattr("energy_model_main._run_soc_optimization", stage("decision", decision))
    monkeypatch.setattr("energy_model_main._build_energy_model_output", stage("output", output))

    assert build_energy_plan(config) is output
    assert calls == [
        ("context", (config,)),
        ("consumption", (context,)),
        ("night", (context, consumption)),
        ("pv", (context, consumption, night_charge)),
        ("constraints", (context, pv, night_charge)),
        ("legacy", (context, pv, constraints, night_charge)),
        ("decision", (context, night_charge, pv, constraints, legacy)),
        ("output", (context, consumption, night_charge, pv, constraints, decision)),
    ]

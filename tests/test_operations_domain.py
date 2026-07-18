from __future__ import annotations

from app.operations.domain import (
    extract_final_pv_source_from_plan,
    extract_final_pv_totals_from_plan,
    extract_hourly_forecast_from_plan,
    fetch_open_meteo_daily_actual,
)


def test_plan_domain_builds_backend_neutral_hourly_rows() -> None:
    plan = {
        "forecast": {
            "date": "2026-07-16",
            "hourly_weather": [{"hour": 10, "weather_code": 2, "cloud_cover": 35}],
        },
        "daytime_soc_optimization": {
            "hourly_pv_forecast_kwh": {"10": 2.5},
            "hourly_load_forecast_kwh": {"10": 1.2},
        },
        "result": {"final_pv_forecast_source": "physical"},
    }

    rows = extract_hourly_forecast_from_plan(plan)

    assert rows == [
        {
            "date": "2026-07-16",
            "hour": 10,
            "forecast_pv_kwh": 2.5,
            "forecast_load_kwh": 1.2,
            "forecast_charge_kwh": 1.3,
            "forecast_weather_code": 2,
            "forecast_precipitation_mm": None,
            "forecast_precipitation_probability": None,
            "forecast_cloud_cover": 35.0,
            "forecast_shortwave_radiation_w_m2": None,
        }
    ]
    assert extract_final_pv_totals_from_plan(plan)["total_kwh"] == 2.5
    assert extract_final_pv_source_from_plan(plan) == "physical"


def test_all_database_adapters_use_the_shared_plan_domain() -> None:
    from app import firestore_ops, operations_db, postgres_ops
    from app.operations import domain

    adapters = (operations_db, postgres_ops, firestore_ops)
    for adapter in adapters:
        assert adapter._extract_hourly_forecast_from_plan is domain.extract_hourly_forecast_from_plan
        assert adapter._is_within_window is domain.is_within_window
        assert adapter._parse_hhmm_to_minute is domain.parse_hhmm_to_minute
        assert adapter._tiered_day_increment_cost is domain.tiered_increment_cost


def test_weather_domain_treats_malformed_provider_payload_as_missing(monkeypatch) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[object]:
            return []

    monkeypatch.setattr("app.operations.domain.requests.get", lambda *args, **kwargs: Response())

    actual = fetch_open_meteo_daily_actual(
        lat=35.0,
        lon=139.0,
        date_ymd="2026-07-16",
        timezone="Asia/Tokyo",
    )

    assert all(value is None for value in actual.values())

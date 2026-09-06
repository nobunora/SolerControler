from scripts.validate_dashboard_backend_parity import IGNORED_FIELDS, compare_rows


def test_compare_rows_accepts_float_storage_noise_and_ignored_metadata() -> None:
    errors = compare_rows(
        [{"date": "2026-07-15", "value": 6.087, "updated_at": "local"}],
        [{"date": "2026-07-15", "value": 6.086999999999999, "updated_at": "remote"}],
        ignored_fields={"updated_at"},
    )

    assert errors == []


def test_compare_rows_reports_coverage_and_contract_differences() -> None:
    assert compare_rows([{"date": "2026-07-14"}], []) == [
        "row count differs: sqlite=1, firestore=0"
    ]
    errors = compare_rows(
        [{"date": "2026-07-15", "actual_load_kwh": 1.0}],
        [{"date": "2026-07-15", "forecast_load_kwh": 1.0}],
    )

    assert "fields differ" in errors[0]


def test_display_only_forecast_plan_metadata_does_not_hide_business_value_differences() -> None:
    assert compare_rows(
        [{"date": "2026-09-05", "night_charge_kwh": None}],
        [{"date": "2026-09-05", "night_charge_kwh": 2.0, "plan_display_source": "forecast_plans"}],
        ignored_fields=IGNORED_FIELDS["battery_daily"],
    ) == []
    assert compare_rows(
        [{"date": "2026-09-05", "hour": 0, "forecast_pv_kwh": 0.0}],
        [{
            "date": "2026-09-05",
            "hour": 0,
            "forecast_pv_kwh": 0.0,
            "forecast_target_soc_percent": 80.0,
            "forecast_night_charge_kwh": 3.0,
        }],
        ignored_fields=IGNORED_FIELDS["forecast_hourly"],
    ) == []

    errors = compare_rows(
        [{"date": "2026-09-05", "hour": 0, "forecast_pv_kwh": 0.0}],
        [{"date": "2026-09-05", "hour": 0, "forecast_pv_kwh": 0.1}],
        ignored_fields=IGNORED_FIELDS["forecast_hourly"],
    )

    assert "values differ" in errors[0]

    errors = compare_rows(
        [{"date": "2026-09-05", "setting_soc_target_percent": 70.0}],
        [{
            "date": "2026-09-05",
            "setting_soc_target_percent": 80.0,
            "plan_display_source": "forecast_plans",
        }],
        ignored_fields=IGNORED_FIELDS["battery_daily"],
    )

    assert "values differ" in errors[0]

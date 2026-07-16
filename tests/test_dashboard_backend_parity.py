from scripts.validate_dashboard_backend_parity import compare_rows


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

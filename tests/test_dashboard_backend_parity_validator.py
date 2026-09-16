from scripts.validate_dashboard_backend_parity import compare_rows


def test_compare_rows_ignores_backend_return_order() -> None:
    sqlite_rows = [
        {"date": "2026-09-15", "value": 1.0},
        {"date": "2026-09-16", "value": 2.0},
    ]
    firestore_rows = list(reversed(sqlite_rows))

    assert compare_rows(sqlite_rows, firestore_rows) == []


def test_compare_rows_matches_hourly_identity() -> None:
    sqlite_rows = [
        {"date": "2026-09-16", "hour": 3, "value": 1.0},
        {"date": "2026-09-16", "hour": 4, "value": 2.0},
    ]
    firestore_rows = [
        {"date": "2026-09-16", "hour": "4", "value": 2.0},
        {"date": "2026-09-16", "hour": "3", "value": 1.0},
    ]

    assert compare_rows(sqlite_rows, firestore_rows) == []


def test_compare_rows_reports_missing_identity_instead_of_index_mismatch() -> None:
    errors = compare_rows(
        [{"date": "2026-09-15", "value": 1.0}],
        [{"date": "2026-09-16", "value": 1.0}],
    )

    assert "missing from firestore: date=2026-09-15" in errors
    assert "missing from sqlite: date=2026-09-16" in errors


def test_compare_rows_rejects_duplicate_identity() -> None:
    errors = compare_rows(
        [
            {"date": "2026-09-16", "value": 1.0},
            {"date": "2026-09-16", "value": 1.0},
        ],
        [{"date": "2026-09-16", "value": 1.0}],
    )

    assert errors == ["sqlite duplicate row identity: date=2026-09-16"]


def test_compare_rows_treats_flattened_null_and_omitted_field_as_equivalent() -> None:
    sqlite_rows = [
        {
            "date": "2026-09-14",
            "setting_soc_target_percent": 100.0,
            "night_charge_kwh": 9.5,
            "pv_charge_end_soc_percent": None,
        }
    ]
    firestore_rows = [
        {
            "date": "2026-09-14",
            "setting_soc_target_percent": 100.0,
            "night_charge_kwh": 9.5,
        }
    ]

    assert compare_rows(sqlite_rows, firestore_rows) == []


def test_compare_rows_does_not_hide_non_null_field_drift() -> None:
    errors = compare_rows(
        [{"date": "2026-09-14", "setting_soc_target_percent": None}],
        [{"date": "2026-09-14", "setting_soc_target_percent": 100.0}],
    )

    assert errors == [
        "date=2026-09-14 fields differ: sqlite_only=[], "
        "firestore_only=['setting_soc_target_percent']"
    ]

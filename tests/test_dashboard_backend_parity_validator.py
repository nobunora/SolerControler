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

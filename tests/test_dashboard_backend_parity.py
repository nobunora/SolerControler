import copy

from scripts.validate_dashboard_backend_parity import IGNORED_FIELDS, compare_rows, partition_forecast_metadata


def test_compare_rows_accepts_float_storage_noise_and_ignored_metadata() -> None:
    errors = compare_rows(
        [{"date": "2026-07-15", "value": 6.087, "updated_at": "local"}],
        [{"date": "2026-07-15", "value": 6.086999999999999, "updated_at": "remote"}],
        ignored_fields={"updated_at"},
    )

    assert errors == []


def test_compare_rows_reports_coverage_and_contract_differences() -> None:
    assert compare_rows([{"date": "2026-07-14"}], []) == [
        "missing from firestore: date=2026-07-14"
    ]
    errors = compare_rows(
        [{"date": "2026-07-15", "actual_load_kwh": 1.0}],
        [{"date": "2026-07-15", "forecast_load_kwh": 1.0}],
    )

    assert "fields differ" in errors[0]


def test_display_only_forecast_plan_metadata_does_not_hide_business_value_differences() -> None:
    errors = compare_rows(
        [{"date": "2026-09-05", "night_charge_kwh": None}],
        [{"date": "2026-09-05", "night_charge_kwh": 2.0, "plan_display_source": "forecast_plans"}],
        ignored_fields=IGNORED_FIELDS["battery_daily"],
    )
    assert errors == [
        "date=2026-09-05 fields differ: sqlite_only=[], "
        "firestore_only=['night_charge_kwh']"
    ]

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


def test_existing_48_rows_split_only_after_evidence_pass() -> None:
    left, right, proofs = [], [], {}
    for day in ("2026-10-10", "2026-10-11"):
        identity = {"forecast_run_id": f"run-{day}", "forecast_issued_at": "2026-10-09T21:00:00+09:00"}
        proofs[day] = {"status": "PASS", "original_verified": True, "capacity_verified": True,
                       "soc_points_verified": 17, "forecast_identity": identity}
        for hour in range(24):
            row = {"date": day, "hour": hour, "forecast_pv_kwh": 0.0, **identity}
            left.append(dict(row))
            right.append({**row, "forecast_capacity_kwh": 10.0,
                          **({"forecast_soc_percent": 0.0} if hour >= 7 else {})})
    before = copy.deepcopy((left, right))
    assert len(compare_rows(left, right)) == 48
    filtered_left, filtered_right, errors = partition_forecast_metadata(
        sqlite_rows=left, firestore_rows=right, day_results=proofs)
    assert errors == []
    assert compare_rows(filtered_left, filtered_right) == []
    assert (left, right) == before
    proofs["2026-10-11"]["status"] = "INCONCLUSIVE"
    _, retained, errors = partition_forecast_metadata(sqlite_rows=left, firestore_rows=right, day_results=proofs)
    assert errors
    assert retained[-1]["forecast_soc_percent"] == 0.0


def test_partition_preserves_common_and_unknown_fields_and_both_present_values() -> None:
    identity = {"forecast_run_id": "same", "forecast_issued_at": "2026-10-09T21:00:00+09:00"}
    row = {"date": "2026-10-10", "hour": 7, **identity, "forecast_capacity_kwh": 9.0}
    other = {**row, "forecast_capacity_kwh": 10.0, "unknown_business": 1, "forecast_load_kwh": 0.1}
    proof = {"status": "PASS", "original_verified": True, "capacity_verified": True,
             "soc_points_verified": 17, "forecast_identity": identity}
    left, right, errors = partition_forecast_metadata(sqlite_rows=[row], firestore_rows=[other],
                                                       day_results={row["date"]: proof})
    assert errors == []
    assert right[0]["forecast_capacity_kwh"] == 10.0
    assert "unknown_business" in right[0]
    assert compare_rows(left, right)


def test_partition_rejects_proof_for_a_different_run() -> None:
    row = {"date": "2026-10-10", "hour": 7, "forecast_run_id": "wrong"}
    proof = {"status": "PASS", "original_verified": True, "capacity_verified": True,
             "soc_points_verified": 17, "forecast_identity": {"forecast_run_id": "right"}}
    _, right, errors = partition_forecast_metadata(sqlite_rows=[row],
        firestore_rows=[{**row, "forecast_capacity_kwh": 10.0}], day_results={row["date"]: proof})
    assert errors
    assert right[0]["forecast_capacity_kwh"] == 10.0

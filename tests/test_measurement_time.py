from datetime import datetime

from app.domain.measurement_time import csv_measurement_window, hourly_time_contract


def test_energy_is_an_interval_and_soc_is_its_end_state():
    start, end = csv_measurement_window(datetime(2026, 11, 4, 3))
    assert start.isoformat() == "2026-11-04T03:00:00+09:00"
    assert end.isoformat() == "2026-11-04T03:30:00+09:00"
    row = hourly_time_contract({"date": "2026-11-04", "hour": 3,
        "actual_soc_at_hour_start_percent": 20, "actual_soc_at_half_hour_percent": 42})
    assert row["interval_start"] == start.isoformat()
    assert row["interval_end"] == "2026-11-04T04:00:00+09:00"
    assert row["forecast_soc_at"] == start.isoformat()
    assert row["actual_soc_points"] == [
        {"at": end.isoformat(), "percent": 20.0, "csv_label_at": start.isoformat()},
        {"at": "2026-11-04T04:00:00+09:00", "percent": 42.0, "csv_label_at": end.isoformat()},
    ]


def test_midnight_rollover_valid_zero_and_missing_are_distinct():
    row = hourly_time_contract({"date": "2026-11-04", "hour": 23,
        "actual_soc_at_hour_start_percent": None, "actual_soc_at_half_hour_percent": 0})
    assert row["actual_soc_points"][0]["percent"] is None
    assert row["actual_soc_points"][1]["at"] == "2026-11-05T00:00:00+09:00"
    assert row["actual_soc_points"][1]["percent"] == 0

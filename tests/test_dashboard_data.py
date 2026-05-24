from pathlib import Path

import pytest

from app.dashboard_data import load_dashboard_slice
from app.operations_db import ensure_schema, open_db


def test_dashboard_slice_includes_energy_daily(tmp_path: Path) -> None:
    db_path = tmp_path / "solar.db"
    conn = open_db(db_path)
    try:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO model_parameters(name, mean_value, variance, sample_count, hit_rate, updated_at)
            VALUES
              ('pv_kwh_per_sunhour', 2.0, 0.0, 1, NULL, '2026-05-02T00:00:00'),
              ('pv_temp_coeff_per_deg', -0.01, 0.0, 1, NULL, '2026-05-02T00:00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO sunshine_daily(date, forecast_hours, actual_hours, forecast_temp_c, actual_temp_c, source, updated_at)
            VALUES ('2026-05-02', 3.0, 2.8, 30.0, 29.0, 'test', '2026-05-02T00:00:00')
            """
        )
        conn.executemany(
            """
            INSERT INTO monitoring_samples(ts, pv_kwh, load_kwh, ingested_at)
            VALUES (?, ?, ?, '2026-05-02T00:00:00')
            """,
            [
                ("2026-05-01T07:00:00", 1.0, 0.8),
                ("2026-05-01T07:30:00", 1.0, 1.2),
                ("2026-05-02T07:00:00", 2.0, 1.5),
                ("2026-05-02T07:30:00", 3.0, 2.5),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    sliced = load_dashboard_slice(db_path, end_date="2026-05-02", window_days=2, include_static=True)
    row = next(x for x in sliced.data.energy_daily if x["date"] == "2026-05-02")

    assert row["forecast_pv_kwh"] == pytest.approx(5.7)
    assert row["actual_pv_kwh"] == pytest.approx(5.0)
    assert row["forecast_load_kwh"] == pytest.approx(2.0)
    assert row["actual_load_kwh"] == pytest.approx(4.0)


def test_dashboard_uses_pv_array_forecast_when_present(tmp_path: Path) -> None:
    db_path = tmp_path / "solar.db"
    conn = open_db(db_path)
    try:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO sunshine_daily(
                date, forecast_hours, actual_hours, forecast_temp_c, actual_temp_c,
                forecast_pv_total_kwh, source, updated_at
            )
            VALUES ('2026-05-02', 3.0, 2.8, 30.0, 29.0, 8.4, 'test', '2026-05-02T00:00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO monitoring_samples(ts, pv_kwh, load_kwh, ingested_at)
            VALUES ('2026-05-02T07:00:00', 2.0, 1.5, '2026-05-02T00:00:00')
            """
        )
        conn.commit()
    finally:
        conn.close()

    sliced = load_dashboard_slice(db_path, end_date="2026-05-02", window_days=1, include_static=True)
    row = next(x for x in sliced.data.energy_daily if x["date"] == "2026-05-02")

    assert row["forecast_pv_kwh"] == pytest.approx(8.4)


def test_dashboard_monthly_cost_uses_configurable_close_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_AGGREGATION_CLOSE_DAY", "14")
    db_path = tmp_path / "solar.db"
    conn = open_db(db_path)
    try:
        ensure_schema(conn)
        conn.executemany(
            """
            INSERT INTO cost_daily(date, self_consumption_kwh, savings_yen, cumulative_kwh, cumulative_yen, updated_at)
            VALUES (?, ?, ?, ?, ?, '2026-05-15T00:00:00')
            """,
            [
                ("2026-04-14", 1.0, 10.0, 1.0, 10.0),
                ("2026-04-15", 2.0, 20.0, 3.0, 30.0),
                ("2026-05-14", 3.0, 30.0, 6.0, 60.0),
                ("2026-05-15", 4.0, 40.0, 10.0, 100.0),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    sliced = load_dashboard_slice(db_path, end_date="2026-05-15", window_days=40, include_static=True)
    by_month = {row["month"]: row for row in sliced.data.cost_monthly}

    assert by_month["2026-04"]["self_consumption_kwh"] == pytest.approx(1.0)
    assert by_month["2026-05"]["period_start"] == "2026-04-15"
    assert by_month["2026-05"]["period_end"] == "2026-05-14"
    assert by_month["2026-05"]["self_consumption_kwh"] == pytest.approx(5.0)
    assert by_month["2026-06"]["self_consumption_kwh"] == pytest.approx(4.0)
    assert sliced.meta["aggregation_close_day"] == 14

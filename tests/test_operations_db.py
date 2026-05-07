from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.operations_db import (
    ensure_schema,
    ingest_monitoring_csvs,
    open_db,
    recalc_battery_end_of_day_soc,
    recalc_model_hit_rates,
    recalc_cost_daily,
    upsert_battery_daily_metrics,
    upsert_model_parameters_from_plan,
)


def test_ingest_and_cost_daily(tmp_path: Path) -> None:
    db_path = tmp_path / "solar.db"
    conn = open_db(db_path)
    try:
        ensure_schema(conn)
        csv_path = tmp_path / "m.csv"
        csv_path.write_text(
            "\n".join(
                [
                    "年月日,時刻,発電電力量[kWh],消費電力量[kWh],売電電力量[kWh],買電電力量[kWh],放電電力量[kWh],充電電力量[kWh],蓄電残量(SOC)[%]",
                    "2026/05/01,07:00,1.2,0.8,0.1,0.2,0.2,0.5,55",
                    "2026/05/01,07:30,1.0,0.7,0.0,0.1,0.1,0.4,56",
                ]
            ),
            encoding="utf-8",
        )
        count = ingest_monitoring_csvs(conn, csv_paths=[csv_path], ingested_at="2026-05-02T00:00:00Z")
        assert count == 2
        recalc_cost_daily(conn, day_rate_yen_per_kwh=31.0, updated_at="2026-05-02T00:00:00Z")
        row = conn.execute("SELECT self_consumption_kwh, savings_yen FROM cost_daily WHERE date='2026-05-01'").fetchone()
        assert row is not None
        # self = (load-buy) = (0.8-0.2)+(0.7-0.1)=1.2
        assert float(row[0]) == pytest.approx(1.2)
        assert float(row[1]) == pytest.approx(37.2)
    finally:
        conn.close()


def test_recalc_cost_daily_night8_tiered(tmp_path: Path) -> None:
    db_path = tmp_path / "solar.db"
    conn = open_db(db_path)
    try:
        ensure_schema(conn)
        csv_path = tmp_path / "m_tiered.csv"
        csv_path.write_text(
            "\n".join(
                [
                    "年月日,時刻,発電電力量[kWh],消費電力量[kWh],売電電力量[kWh],買電電力量[kWh],放電電力量[kWh],充電電力量[kWh],蓄電残量(SOC)[%]",
                    "2026/05/01,07:00,0,50,0,50,0,0,50",
                    "2026/05/01,08:00,0,100,0,0,0,0,50",
                    "2026/05/01,23:30,0,10,0,2,0,0,50",
                    "2026/05/02,07:00,0,100,0,20,0,0,50",
                    "2026/05/02,23:30,0,0,0,10,0,0,50",
                ]
            ),
            encoding="utf-8",
        )
        count = ingest_monitoring_csvs(conn, csv_paths=[csv_path], ingested_at="2026-05-02T00:00:00Z")
        assert count == 5

        recalc_cost_daily(
            conn,
            day_rate_yen_per_kwh=31.0,
            updated_at="2026-05-02T00:00:00Z",
            tariff_mode="night8_tiered",
            night8_day_start_hhmm="07:00",
            night8_day_end_hhmm="23:00",
            night8_day_tier1_upper_kwh=90.0,
            night8_day_tier2_upper_kwh=230.0,
            night8_day_rate_tier1_yen=31.80,
            night8_day_rate_tier2_yen=39.10,
            night8_day_rate_tier3_yen=43.62,
            night8_night_rate_yen=28.85,
        )

        rows = conn.execute(
            "SELECT date, self_consumption_kwh, savings_yen, cumulative_yen FROM cost_daily ORDER BY date"
        ).fetchall()
        assert len(rows) == 2

        assert rows[0][0] == "2026-05-01"
        assert float(rows[0][1]) == pytest.approx(108.0)
        assert float(rows[0][2]) == pytest.approx(3848.8, abs=1e-6)
        assert float(rows[0][3]) == pytest.approx(3848.8, abs=1e-6)

        assert rows[1][0] == "2026-05-02"
        assert float(rows[1][1]) == pytest.approx(80.0)
        assert float(rows[1][2]) == pytest.approx(3364.4, abs=1e-6)
        assert float(rows[1][3]) == pytest.approx(7213.2, abs=1e-6)
    finally:
        conn.close()


def test_upsert_battery_daily_metrics_fallbacks_to_night_plan_result(tmp_path: Path) -> None:
    db_path = tmp_path / "solar.db"
    conn = open_db(db_path)
    try:
        ensure_schema(conn)
        summary_path = tmp_path / "kpnet_summary.json"
        summary_path.write_text(
            """
            {
              "night_charge_plan": {
                "forecast_date": "2026-05-03",
                "target_soc_7_percent_raw": 15.4,
                "required_night_charge_kwh": 0.0
              }
            }
            """.strip(),
            encoding="utf-8",
        )
        night_plan_path = tmp_path / "night_charge_plan.json"
        night_plan_path.write_text(
            """
            {
              "forecast": {"date": "2026-05-03"},
              "result": {
                "required_night_charge_kwh": 0.7668890711637568,
                "predicted_midday_surplus_kwh": 4.7620196164713535,
                "target_soc_7_percent": 10.0
              }
            }
            """.strip(),
            encoding="utf-8",
        )

        upsert_battery_daily_metrics(
            conn,
            summary_path=summary_path,
            updated_at="2026-05-03T00:00:00Z",
            night_plan_path=night_plan_path,
        )
        row = conn.execute(
            """
            SELECT date, setting_soc_target_percent, night_charge_kwh, pv_max_charge_kwh, end_of_day_soc_percent
            FROM battery_daily_metrics
            WHERE date='2026-05-03'
            """
        ).fetchone()
        assert row is not None
        # summary値が優先される
        assert float(row[1]) == pytest.approx(15.4)
        assert float(row[2]) == pytest.approx(0.0)
        # summaryに無い項目は night_charge_plan.result から補完される
        assert float(row[3]) == pytest.approx(4.7620196164713535)
        # 日終SOCは実測CSVから再計算するため、ここでは未設定
        assert row[4] is None
    finally:
        conn.close()


def test_recalc_battery_end_of_day_soc_uses_latest_sample_per_day(tmp_path: Path) -> None:
    db_path = tmp_path / "solar.db"
    conn = open_db(db_path)
    try:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO battery_daily_metrics(date, setting_soc_target_percent, night_charge_kwh, pv_max_charge_kwh, end_of_day_soc_percent, updated_at)
            VALUES
              ('2026-05-01', 20, 1.0, 2.0, NULL, '2026-05-02T00:00:00Z'),
              ('2026-05-02', 10, 0.5, 1.5, NULL, '2026-05-02T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO monitoring_samples(ts, soc_percent, ingested_at)
            VALUES
              ('2026-05-01T22:00:00', 40, 'x'),
              ('2026-05-01T23:30:00', 55, 'x'),
              ('2026-05-02T21:00:00', 35, 'x'),
              ('2026-05-02T23:59:00', 25, 'x')
            """
        )
        conn.commit()

        updated = recalc_battery_end_of_day_soc(conn, updated_at="2026-05-03T00:00:00Z")
        assert updated == 2
        rows = conn.execute(
            "SELECT date, end_of_day_soc_percent FROM battery_daily_metrics ORDER BY date"
        ).fetchall()
        assert float(rows[0][1]) == pytest.approx(55.0)
        assert float(rows[1][1]) == pytest.approx(25.0)
    finally:
        conn.close()


def test_recalc_model_hit_rates_updates_all_params(tmp_path: Path) -> None:
    db_path = tmp_path / "solar.db"
    conn = open_db(db_path)
    try:
        ensure_schema(conn)
        night_plan_path = tmp_path / "night_charge_plan.json"
        night_plan_path.write_text(
            """
            {
              "coefficients": {
                "pv_kwh_per_sunhour": 1.45,
                "battery_round_trip_efficiency": 0.93
              }
            }
            """.strip(),
            encoding="utf-8",
        )
        upsert_model_parameters_from_plan(conn, night_plan_path=night_plan_path, updated_at="2026-05-03T00:00:00Z")
        conn.execute(
            """
            INSERT INTO sunshine_daily(date, forecast_hours, actual_hours, source, updated_at)
            VALUES
              ('2026-05-01', 5.0, 4.0, 'test', '2026-05-03T00:00:00Z'),
              ('2026-05-02', 6.0, 6.0, 'test', '2026-05-03T00:00:00Z')
            """
        )
        conn.commit()

        hit = recalc_model_hit_rates(conn, updated_at="2026-05-03T00:01:00Z")
        assert hit is not None
        assert 0.0 <= hit <= 1.0
        assert hit == pytest.approx(0.9444444444)

        rows = conn.execute("SELECT hit_rate FROM model_parameters ORDER BY name").fetchall()
        assert len(rows) == 2
        assert rows[0][0] == pytest.approx(hit)
        assert rows[1][0] == pytest.approx(hit)
    finally:
        conn.close()


def test_recalc_model_hit_rates_does_not_collapse_on_low_actual_days(tmp_path: Path) -> None:
    db_path = tmp_path / "solar.db"
    conn = open_db(db_path)
    try:
        ensure_schema(conn)
        night_plan_path = tmp_path / "night_charge_plan.json"
        night_plan_path.write_text(
            """
            {
              "coefficients": {
                "pv_kwh_per_sunhour": 1.45
              }
            }
            """.strip(),
            encoding="utf-8",
        )
        upsert_model_parameters_from_plan(conn, night_plan_path=night_plan_path, updated_at="2026-05-03T00:00:00Z")
        conn.execute(
            """
            INSERT INTO sunshine_daily(date, forecast_hours, actual_hours, source, updated_at)
            VALUES
              ('2026-05-01', 1.0, 0.2, 'test', '2026-05-03T00:00:00Z'),
              ('2026-05-02', 1.2, 0.3, 'test', '2026-05-03T00:00:00Z')
            """
        )
        conn.commit()

        hit = recalc_model_hit_rates(conn, updated_at="2026-05-03T00:01:00Z")
        assert hit is not None
        assert hit == pytest.approx(0.3666666667)
        assert hit > 0.0
    finally:
        conn.close()

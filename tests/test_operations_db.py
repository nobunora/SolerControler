from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.operations_db import (
    ensure_schema,
    ingest_monitoring_csvs,
    open_db,
    recalc_cost_daily,
    upsert_battery_daily_metrics,
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
            SELECT date, setting_soc_target_percent, night_charge_kwh, pv_max_charge_kwh
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
    finally:
        conn.close()

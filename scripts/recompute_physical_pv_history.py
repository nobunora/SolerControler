"""Recompute archived physical PV forecasts without modifying production forecasts."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from google.cloud import storage

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.operations.firestore import open_firestore
from app.forecasting.correction import load_forecast_hourly_history_from_firestore
from app.night_plan_archive import load_night_plan_detail_from_firestore_doc
from app.forecasting.pv_physical import build_physical_pv_candidate


def monitoring_rows(conn: sqlite3.Connection, target_date: str) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for ts, pv, load in conn.execute(
        "SELECT ts, pv_kwh, load_kwh FROM monitoring_samples WHERE substr(ts,1,10) < ? ORDER BY ts",
        (target_date,),
    ):
        try:
            result.append({"dt": datetime.fromisoformat(str(ts)), "pv": float(pv or 0), "load": float(load or 0)})
        except ValueError:
            continue
    return result


def existing_hourly(plan: dict[str, object]) -> dict[int, float]:
    rows = (plan.get("pv_array_forecast") or {}).get("hourly", []) if isinstance(plan.get("pv_array_forecast"), dict) else []
    output: dict[int, float] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            output[int(str(row.get("time", ""))[11:13])] = max(0.0, float(row.get("total_kwh") or 0.0))
        except (TypeError, ValueError):
            continue
    return output


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS physical_pv_reforecast_hourly (
            forecast_date TEXT NOT NULL, hour INTEGER NOT NULL,
            archived_physical_pv_kwh REAL NOT NULL, recomputed_physical_pv_kwh REAL NOT NULL,
            weather_code INTEGER, shortwave_w_m2 REAL, method TEXT NOT NULL, recomputed_at TEXT NOT NULL,
            PRIMARY KEY (forecast_date, hour, method))"""
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-07-06")
    parser.add_argument("--end", default="2026-07-22")
    parser.add_argument("--db-path", default="artifacts/solar_monitor.db")
    args = parser.parse_args()
    conn = sqlite3.connect(args.db_path)
    ensure_table(conn)
    client, store = open_firestore(), storage.Client()
    docs = {snap.id: snap.to_dict() or {} for snap in client.collection("night_charge_plans").stream()}
    recorded = 0
    for target_date in sorted(day for day in docs if args.start <= day <= args.end):
        summary = docs[target_date]
        if (summary.get("result") or {}).get("final_pv_forecast_source") != "physical_pv_forecast":
            continue
        plan = load_night_plan_detail_from_firestore_doc(summary, storage_client=store) or {}
        forecast = plan.get("forecast") if isinstance(plan.get("forecast"), dict) else {}
        old = (plan.get("daytime_soc_optimization") or {}).get("hourly_pv_forecast_kwh", {}) if isinstance(plan.get("daytime_soc_optimization"), dict) else {}
        candidate = build_physical_pv_candidate(
            rows=monitoring_rows(conn, target_date), forecast_history=load_forecast_hourly_history_from_firestore(target_date=target_date),
            existing_hourly_pv=existing_hourly(plan), forecast=forecast, target_date=target_date,
            lat=35.67452, lon=139.48216, timezone="Asia/Tokyo",
        )
        weather = {int(row.get("hour")): row for row in forecast.get("hourly_weather", []) if isinstance(row, dict) and row.get("hour") is not None}
        now = datetime.now().isoformat(timespec="seconds")
        for hour, value in candidate.hourly_pv_kwh.items():
            row = weather.get(hour, {})
            conn.execute(
                "INSERT OR REPLACE INTO physical_pv_reforecast_hourly VALUES (?,?,?,?,?,?,?,?)",
                (target_date, hour, float(old.get(str(hour), 0.0) or 0.0), float(value), row.get("weather_code"), row.get("shortwave_radiation_w_m2"), "recomputed_current_model", now),
            )
            recorded += 1
        conn.commit()
        print({"forecast_date": target_date, "rows_recorded": recorded}, flush=True)
    conn.close(); print({"rows_recorded": recorded})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

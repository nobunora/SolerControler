from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.energy_plan.monitoring_history import (
    load_monitoring_history, merge_monitoring_rows, recent_forecast_rows,
    validate_current_billing_history,
)
from app.energy_plan.monthly_projection import monthly_day_buy_kwh_before_target
from app.energy_plan.workflow import _read_rows, _load_execution_context, EnergyModelConfig


def day_rows(day: str, **changes):
    start = datetime.fromisoformat(day)
    return [{"dt": start + timedelta(minutes=30 * slot), "pv": 0.0, "load": 0.2, "buy": 0.1, "sell": 0.0, "charge": 0.0, "discharge": 0.0, "soc": 20.0, **changes} for slot in range(48)]


def test_seven_observed_days_cross_months_years_and_missing_calendar_days():
    days = ["2025-11-01", "2025-12-29", "2025-12-30", "2025-12-31", "2026-01-01", "2026-01-04", "2026-01-06", "2026-01-07"]
    rows = [row for day in days for row in day_rows(day)] + day_rows("2026-01-08")
    selected = recent_forecast_rows(rows, target_date="2026-01-08")
    assert sorted({row["dt"].date().isoformat() for row in selected}) == days[-7:]
    assert len(selected) == 336
    assert all(row["pv"] == 0.0 for row in selected)  # measured zero remains valid


@pytest.mark.parametrize("invalid", [None, -0.1, float("nan"), float("inf")])
@pytest.mark.parametrize("key", ["pv", "load"])
def test_invalid_day_is_skipped_without_turning_missing_into_zero(key, invalid):
    rows = [row for day in range(1, 9) for row in day_rows(f"2026-10-{day:02d}")]
    rows[-1][key] = invalid
    selected = recent_forecast_rows(rows, target_date="2026-10-09")
    assert sorted({row["dt"].day for row in selected}) == list(range(1, 8))


def test_partial_day_and_duplicate_intervals_do_not_qualify():
    rows = day_rows("2026-10-07")[:-1]
    rows.append(dict(rows[0]))
    assert recent_forecast_rows(rows, target_date="2026-10-08") == []


def test_older_days_are_available_without_calendar_month_limit(tmp_path, monkeypatch):
    db = tmp_path / "history.db"
    monkeypatch.setenv("DATA_BACKEND", "sqlite")
    monkeypatch.setenv("DATA_DB_PATH", str(db))
    rows = [row for day in range(1, 8) for row in day_rows(f"2025-12-{day:02d}")]
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE monitoring_samples (ts TEXT, pv_kwh REAL, load_kwh REAL, buy_kwh REAL, sell_kwh REAL, charge_kwh REAL, discharge_kwh REAL, soc_percent REAL)")
        conn.executemany("INSERT INTO monitoring_samples VALUES (?,?,?,?,?,?,?,?)", [(r["dt"].isoformat(), r["pv"], r["load"], r["buy"], r["sell"], r["charge"], r["discharge"], r["soc"]) for r in rows])
    selected = recent_forecast_rows(load_monitoring_history(target_date="2026-10-08"), target_date="2026-10-08")
    assert len(selected) == 336
    assert selected[0]["dt"].date().isoformat() == "2025-12-01"


def test_csv_and_storage_billing_aggregate_crosses_months_without_double_count(tmp_path, monkeypatch):
    stored = [row for day in range(15, 31) for row in day_rows(f"2026-09-{day:02d}")]
    path = tmp_path / "october.csv"
    headers = ["年月日", "時刻", "発電電力量[kWh]", "消費電力量[kWh]", "買電電力量[kWh]", "売電電力量[kWh]", "充電電力量[kWh]", "放電電力量[kWh]", "蓄電残量(SOC)[%]"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for day in range(1, 8):
            for r in day_rows(f"2026-10-{day:02d}"):
                writer.writerow([r["dt"].strftime("%Y/%m/%d"), r["dt"].strftime("%H:%M"), r["pv"], r["load"], r["buy"], r["sell"], r["charge"], r["discharge"], r["soc"]])
    fresh = _read_rows([path])
    combined = merge_monitoring_rows(stored + fresh, fresh)
    monkeypatch.setenv("SOC_MONTHLY_TIER_CLOSE_DAY", "14")
    validate_current_billing_history(combined, target_date="2026-10-08")
    billed = monthly_day_buy_kwh_before_target(combined, target_date="2026-10-08")
    assert billed["kwh"] == pytest.approx(23 * 32 * 0.1)
    assert billed["sample_day_count"] == 23
    assert len(recent_forecast_rows(combined, target_date="2026-10-08")) == 336
    with pytest.raises(RuntimeError, match="2026-09-15"):
        validate_current_billing_history(fresh, target_date="2026-10-08")


def test_csv_missing_measurement_is_preserved(tmp_path: Path):
    path = tmp_path / "missing.csv"
    path.write_text("年月日,時刻,発電電力量[kWh],消費電力量[kWh],買電電力量[kWh],売電電力量[kWh]\n2026/10/07,12:00,,,1.2,0\n", encoding="utf-8")
    row = _read_rows([path])[0]
    assert row["pv"] is None and row["load"] is None
    assert row["buy"] == 1.2


def test_billing_coverage_uses_configured_day_window(monkeypatch):
    monkeypatch.setenv("SOC_MONTHLY_TIER_CLOSE_DAY", "14")
    monkeypatch.setenv("NIGHT8_DAY_START_HHMM", "08:00")
    monkeypatch.setenv("NIGHT8_DAY_END_HHMM", "09:00")
    rows = [{"dt": datetime(2026, 10, day, 7), "buy": 0.1} for day in range(15, 16)]
    with pytest.raises(RuntimeError, match="2026-10-15"):
        validate_current_billing_history(rows, target_date="2026-10-16")
    rows[0]["dt"] = datetime(2026, 10, 15, 8)
    validate_current_billing_history(rows, target_date="2026-10-16")


def test_default_context_uses_stored_previous_month_and_seven_days_for_fitting(tmp_path, monkeypatch):
    from app.energy_plan.energy_model import EnergyModelCoefficients

    stored = [row for day in range(15, 31) for row in day_rows(f"2026-09-{day:02d}")]
    fresh = [row for day in range(1, 8) for row in day_rows(f"2026-10-{day:02d}")]
    fresh += day_rows("2026-10-08", soc=25.0)[:3]
    monkeypatch.setattr("app.energy_plan.workflow._csv_paths_from_env_or_latest", lambda _: [tmp_path / "fresh.csv"])
    monkeypatch.setattr("app.energy_plan.workflow._read_rows", lambda _: fresh)
    monkeypatch.setattr("app.energy_plan.workflow.load_monitoring_history", lambda **_: stored)
    monkeypatch.setattr("app.energy_plan.workflow._forecast_from_env_or_api", lambda **_: {"date": "2026-10-08", "sun_hours": 10.0, "temp_c": 20.0})
    monkeypatch.setattr("app.energy_plan.workflow.load_occupancy_events_from_env", lambda: [])
    monkeypatch.setenv("SOC_MONTHLY_TIER_CLOSE_DAY", "14")
    fitted = []

    def fit(rows):
        fitted.extend(rows)
        return EnergyModelCoefficients(10, 10, 0, 0.9, 9, 0.9, 0.5, 0.4, 1.45, -0.0035, -0.005, 0.0003)

    monkeypatch.setattr("app.energy_plan.workflow.fit_coefficients_from_rows", fit)
    context = _load_execution_context(EnergyModelConfig.from_env())
    assert context.latest_soc_percent == 25.0
    assert context.history_selection["days"] == [f"2026-10-{day:02d}" for day in range(1, 8)]
    assert len(fitted) == 336 and len(context.rows) == 336
    assert all(row["dt"].date().isoformat() < "2026-10-08" for row in context.rows)
    assert monthly_day_buy_kwh_before_target(context.billing_rows, target_date="2026-10-08")["kwh"] == pytest.approx(73.6)

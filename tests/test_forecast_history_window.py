import sqlite3

from app.forecasting.correction_history_io import _load_forecast_hourly_history_from_sqlite


def test_default_history_includes_3650_days_and_excludes_target_day(monkeypatch, tmp_path):
    monkeypatch.delenv("FORECAST_HOURLY_HISTORY_LOOKBACK_DAYS", raising=False)
    db_path = tmp_path / "history.db"
    monkeypatch.setenv("DATA_DB_PATH", str(db_path))
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE forecast_hourly (date TEXT, hour INTEGER, forecast_pv_kwh REAL, "
            "forecast_load_kwh REAL, forecast_shortwave_radiation_w_m2 REAL, forecast_weather_code REAL)"
        )
        conn.executemany(
            "INSERT INTO forecast_hourly VALUES (?, 12, 1, 2, 100, 3)",
            [(day,) for day in ("2016-10-03", "2016-10-04", "2026-10-01", "2026-10-02")],
        )

    history = _load_forecast_hourly_history_from_sqlite(target_date="2026-10-02")

    assert set(history) == {"2016-10-04", "2026-10-01"}

    monkeypatch.setenv("FORECAST_HOURLY_HISTORY_LOOKBACK_DAYS", "1")
    assert set(_load_forecast_hourly_history_from_sqlite(target_date="2026-10-02")) == {"2026-10-01"}

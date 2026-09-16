from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.dashboard.history_reconstruction import firestore_forecast_hourly_with_reconstruction
from app.dashboard.slice_assembler import merge_forecast_history_into_battery_daily
from app.operations import sqlite as sqlite_ops
from app.operations.firestore import open_firestore


def _open_firestore_client(*, project_id: str | None, database_id: str) -> Any:
    if project_id:
        from google.cloud import firestore

        return firestore.Client(project=project_id, database=database_id)
    return open_firestore()


def _projection_candidates(
    existing_rows: list[dict[str, Any]],
    forecast_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return only forecast-plan display values that add evidence to SQLite.

    The production Firestore dashboard enriches original forecast rows with same-vintage
    ``forecast_plans`` metadata before assembling ``battery_daily``. SQLite is a flattened
    validation read model and historically dropped that display-only metadata. This
    projection gives validation the same evidence without changing Firestore or inferring
    configured SOC from actual SOC/reconstructed forecasts.
    """
    existing_by_date = {
        str(row.get("date") or ""): row
        for row in existing_rows
        if str(row.get("date") or "").strip()
    }
    merged = merge_forecast_history_into_battery_daily(existing_rows, forecast_rows)
    candidates: list[dict[str, Any]] = []
    for row in merged:
        if row.get("plan_display_source") != "forecast_plans":
            continue
        day = str(row.get("date") or "").strip()
        if not day:
            continue
        before = existing_by_date.get(day, {})
        target = row.get("setting_soc_target_percent")
        night_charge = row.get("night_charge_kwh")
        adds_target = before.get("setting_soc_target_percent") is None and target is not None
        adds_night = before.get("night_charge_kwh") is None and night_charge is not None
        if adds_target or adds_night:
            candidates.append(
                {
                    "date": day,
                    "setting_soc_target_percent": target if adds_target else None,
                    "night_charge_kwh": night_charge if adds_night else None,
                }
            )
    return candidates


def project_forecast_plan_battery_rows(
    *,
    sqlite_path: Path,
    project_id: str | None = None,
    database_id: str = "(default)",
) -> int:
    """Materialize display-only forecast-plan SOC evidence into local validation SQLite.

    This mutates only the local validation database. It never writes Firestore and never
    touches KP-NET/device control. Existing battery metrics remain authoritative because
    only NULL/missing configured-SOC and night-charge fields are filled.
    """
    conn = sqlite_ops.open_db(sqlite_path)
    sqlite_ops.ensure_schema(conn)
    try:
        bounds = conn.execute(
            "SELECT MIN(date) AS min_date, MAX(date) AS max_date FROM forecast_hourly"
        ).fetchone()
        if bounds is None or not bounds["min_date"] or not bounds["max_date"]:
            return 0
        existing_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT date, setting_soc_target_percent, night_charge_kwh
                FROM battery_daily_metrics
                ORDER BY date
                """
            ).fetchall()
        ]
        client = _open_firestore_client(project_id=project_id, database_id=database_id)
        forecast_rows = firestore_forecast_hourly_with_reconstruction(
            client,
            start_date=str(bounds["min_date"]),
            end_date_iso=str(bounds["max_date"]),
        )
        candidates = _projection_candidates(existing_rows, forecast_rows)
        projected_at = datetime.now(timezone.utc).isoformat()
        with conn:
            for row in candidates:
                day = row["date"]
                current = conn.execute(
                    """
                    SELECT setting_soc_target_percent, night_charge_kwh
                    FROM battery_daily_metrics WHERE date=?
                    """,
                    (day,),
                ).fetchone()
                if current is None:
                    conn.execute(
                        """
                        INSERT INTO battery_daily_metrics (
                            date, setting_soc_target_percent, night_charge_kwh, updated_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            day,
                            row.get("setting_soc_target_percent"),
                            row.get("night_charge_kwh"),
                            projected_at,
                        ),
                    )
                    continue
                conn.execute(
                    """
                    UPDATE battery_daily_metrics
                    SET setting_soc_target_percent=COALESCE(setting_soc_target_percent, ?),
                        night_charge_kwh=COALESCE(night_charge_kwh, ?)
                    WHERE date=?
                    """,
                    (
                        row.get("setting_soc_target_percent"),
                        row.get("night_charge_kwh"),
                        day,
                    ),
                )
        return len(candidates)
    finally:
        conn.close()

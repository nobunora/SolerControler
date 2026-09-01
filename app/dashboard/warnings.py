from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.dashboard.schedule import SETTINGS_COMPLETED_STATUSES
from app.parsing.numbers import to_float


_JST = ZoneInfo("Asia/Tokyo")


def _latest_row_by_date(rows: list[dict[str, Any]], *, date_key: str = "date") -> dict[str, Any] | None:
    dated = [row for row in rows if row.get(date_key)]
    if not dated:
        return None
    return max(dated, key=lambda row: str(row.get(date_key)))


def _morning_target_soc_observation(
    forecast_hourly: list[dict[str, Any]],
    *,
    date_iso: str,
) -> tuple[float, str] | None:
    for row in forecast_hourly:
        if str(row.get("date") or "") != date_iso or to_float(row.get("hour")) != 7:
            continue
        opening_soc = to_float(row.get("opening_soc_percent"))
        if opening_soc is None or not 0.0 <= opening_soc <= 100.0:
            continue
        timestamp_text = str(row.get("first_sample_at") or "").strip()
        if not timestamp_text:
            continue
        try:
            timestamp = datetime.fromisoformat(timestamp_text.replace("Z", "+00:00"))
        except ValueError:
            continue
        if timestamp.tzinfo is not None:
            timestamp = timestamp.astimezone(_JST)
        if timestamp.date().isoformat() != date_iso:
            continue
        if (timestamp.hour, timestamp.minute, timestamp.second, timestamp.microsecond) != (7, 0, 0, 0):
            continue
        return opening_soc, timestamp_text
    return None


def _has_plan_specific_settings_event(latest_schedule: dict[str, Any]) -> bool:
    if any(
        str(latest_schedule.get(field) or "").strip()
        for field in ("recorded_at", "settings_completed_source_doc_id", "settings_completed_run_id")
    ):
        return True
    return str(latest_schedule.get("schedule_source") or "").strip() in {
        "03-monitor",
        "03-dynamic",
        "03-no-charge",
    }


# readable-code-audit: skip STRUCT-04 — the warning list intentionally evaluates schedule, completion, and freshness together so related user-visible warnings are not suppressed independently
def build_dashboard_warnings(
    *,
    latest_schedule: dict[str, Any],
    battery_daily: list[dict[str, Any]],
    energy_daily: list[dict[str, Any]],
    forecast_hourly: list[dict[str, Any]] | None = None,
    end_date_iso: str,
    today_jst_iso: str,
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []

    def add(
        code: str,
        severity: str,
        title: str,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        warnings.append(
            {
                "code": code,
                "severity": severity,
                "title": title,
                "message": message,
                "detail": detail or {},
            }
        )

    latest_battery = _latest_row_by_date(battery_daily)
    has_plan_specific_settings_event = _has_plan_specific_settings_event(latest_schedule)
    if latest_battery:
        target_soc = to_float(latest_battery.get("setting_soc_target_percent"))
        morning_observation = _morning_target_soc_observation(
            forecast_hourly or [],
            date_iso=str(latest_battery.get("date") or ""),
        )
        if target_soc is not None and morning_observation is not None and morning_observation[0] < target_soc:
            add(
                "soc_target_unreached",
                "warning",
                "朝7時目標SOC未達",
                f"{latest_battery.get('date')} 07:00のSOCが目標より低いです。",
                {
                    "date": latest_battery.get("date"),
                    "target_soc_percent": target_soc,
                    "observed_soc_percent": morning_observation[0],
                    "observed_at": morning_observation[1],
                    "source": "monitoring-sample-07:00",
                },
            )
        schedule_plan_date = str(latest_schedule.get("plan_date") or "")
        schedule_battery = next(
            (row for row in battery_daily if str(row.get("date") or "") == schedule_plan_date),
            None,
        )
        night_charge = to_float(schedule_battery.get("night_charge_kwh")) if schedule_battery else 0.0
        source = str(latest_schedule.get("schedule_source") or "")
        charge_start_time = str(latest_schedule.get("charge_start_time") or "").strip()
        if has_plan_specific_settings_event and (night_charge or 0.0) > 0.1 and source not in {"03-monitor", "03-dynamic"} and not charge_start_time:
            add(
                "monitor_schedule_missing",
                "warning",
                "03実行計画が未記録",
                "夜間充電が必要な日に、03ジョブが決めた実開始時刻を確認できません。",
                {
                    "date": schedule_plan_date,
                    "night_charge_kwh": night_charge or 0.0,
                    "schedule_source": source or None,
                },
            )

    actual_dates = [
        str(row.get("date"))
        for row in energy_daily
        if row.get("date")
        and (
            to_float(row.get("actual_pv_kwh")) is not None
            or to_float(row.get("actual_load_kwh")) is not None
        )
    ]
    latest_actual = max(actual_dates) if actual_dates else None
    if end_date_iso < today_jst_iso and (latest_actual is None or latest_actual < end_date_iso):
        add(
            "csv_actual_stale",
            "info",
            "CSV実績未更新",
            "表示終了日の実績CSVがまだ反映されていない可能性があります。",
            {"latest_actual_date": latest_actual, "display_end_date": end_date_iso},
        )

    completed = bool(latest_schedule.get("settings_completed"))
    status = str(latest_schedule.get("settings_completed_status") or latest_schedule.get("status") or "")
    if has_plan_specific_settings_event and not completed and status not in SETTINGS_COMPLETED_STATUSES:
        add(
            "settings_completion_unconfirmed",
            "warning",
            "設定完了未確認",
            "記録された設定イベントに正常完了を確認できません。",
            {
                "plan_date": latest_schedule.get("plan_date"),
                "status": status or None,
                "schedule_source": latest_schedule.get("schedule_source"),
            },
        )
    return warnings

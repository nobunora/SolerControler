from __future__ import annotations

import os
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.parsing.numbers import to_float


_RECONSTRUCTED_FORECAST_SOURCE = "historical_reconstructed_estimate"
_LEGACY_LOAD_SOURCE = "legacy_rolling_14d_estimate"


def _to_date_or_none(raw: str | None) -> date | None:
    if raw is None:
        return None
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError:
        return None


def _date_add_iso(date_text: str, delta_days: int) -> str | None:
    d = _to_date_or_none(date_text)
    if d is None:
        return None
    return (d + timedelta(days=delta_days)).isoformat()


def _today_jst_iso() -> str:
    return datetime.now(timezone(timedelta(hours=9))).date().isoformat()


def _aggregation_close_day() -> int:
    # 契約月の締め日を既定14日とし、暦月ではなく料金請求期間で集計をそろえる。
    raw = os.getenv("DASHBOARD_AGGREGATION_CLOSE_DAY", "14").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 14
    return max(1, min(31, value))


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    index = (year * 12) + (month - 1) + delta
    return index // 12, (index % 12) + 1


def _month_end_day(year: int, month: int, close_day: int) -> int:
    return min(close_day, monthrange(year, month)[1])


def _accounting_month_label(day_text: str, *, close_day: int) -> str | None:
    day = _to_date_or_none(day_text)
    if day is None:
        return None
    effective_close = _month_end_day(day.year, day.month, close_day)
    year = day.year
    month = day.month
    if day.day > effective_close:
        year, month = _add_months(year, month, 1)
    return f"{year:04d}-{month:02d}"


def _accounting_period_bounds(month_label: str, *, close_day: int) -> tuple[str, str] | None:
    try:
        year_text, month_text = month_label.split("-", 1)
        year = int(year_text)
        month = int(month_text)
    except Exception:
        return None
    if month < 1 or month > 12:
        return None
    end_day = _month_end_day(year, month, close_day)
    end = date(year, month, end_day)
    prev_year, prev_month = _add_months(year, month, -1)
    prev_end_day = _month_end_day(prev_year, prev_month, close_day)
    start = date(prev_year, prev_month, prev_end_day) + timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _build_cost_monthly(cost_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    close_day = _aggregation_close_day()
    by_month: dict[str, dict[str, float]] = {}
    for row in cost_rows:
        label = _accounting_month_label(str(row.get("date", "")), close_day=close_day)
        if label is None:
            continue
        acc = by_month.setdefault(label, {"self_consumption_kwh": 0.0, "savings_yen": 0.0})
        acc["self_consumption_kwh"] += float(row.get("self_consumption_kwh") or 0.0)
        acc["savings_yen"] += float(row.get("savings_yen") or 0.0)

    out: list[dict[str, Any]] = []
    for month, values in sorted(by_month.items()):
        bounds = _accounting_period_bounds(month, close_day=close_day)
        period_start, period_end = bounds if bounds is not None else (None, None)
        out.append(
            {
                "month": month,
                "period_start": period_start,
                "period_end": period_end,
                "self_consumption_kwh": values["self_consumption_kwh"],
                "savings_yen": values["savings_yen"],
            }
        )
    return out


def _forecast_pv_kwh(
    sunshine_row: dict[str, Any] | None,
) -> float | None:
    if not sunshine_row:
        return None
    array_forecast = to_float(sunshine_row.get("forecast_pv_total_kwh"))
    if array_forecast is not None:
        return max(0.0, array_forecast)
    return None


def _rolling_load_forecast(
    day: str,
    actual_by_day: dict[str, dict[str, Any]],
    *,
    lookback_days: int = 14,
) -> float | None:
    day_obj = _to_date_or_none(day)
    if day_obj is None:
        return None
    values: list[float] = []
    for prev_day in sorted(actual_by_day):
        prev_obj = _to_date_or_none(prev_day)
        if prev_obj is None or prev_obj >= day_obj:
            continue
        if (day_obj - prev_obj).days > lookback_days:
            continue
        value = to_float(actual_by_day[prev_day].get("actual_load_kwh"))
        if value is not None:
            values.append(value)
    if not values:
        return None
    return sum(values) / len(values)


def _forecast_provenance_kind(
    *,
    pv_source: str | None,
    load_source: str | None,
) -> str:
    if _RECONSTRUCTED_FORECAST_SOURCE in {pv_source, load_source}:
        return "reconstructed"
    pv_original = bool(pv_source)
    load_original = bool(load_source) and load_source != _LEGACY_LOAD_SOURCE
    load_legacy = load_source == _LEGACY_LOAD_SOURCE
    if pv_original and load_original:
        return "original"
    if pv_original and load_legacy:
        return "mixed_original_legacy"
    if pv_original or load_original:
        return "partial_original"
    if load_legacy:
        return "legacy_derived"
    return "missing"


def _build_energy_daily(
    *,
    start_date: str,
    end_date_iso: str,
    pv_daily: list[dict[str, Any]],
    monitoring_daily: list[dict[str, Any]],
    forecast_hourly: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    pv_by_day = {str(row.get("date")): row for row in pv_daily if row.get("date")}
    actual_by_day = {str(row.get("date")): row for row in monitoring_daily if row.get("date")}
    hourly_load_by_day: dict[str, float] = {}
    hourly_pv_by_day: dict[str, float] = {}
    hourly_source_by_day: dict[str, str] = {}
    hourly_run_id_by_day: dict[str, str] = {}
    hourly_issued_at_by_day: dict[str, str] = {}
    reconstruction_id_by_day: dict[str, str] = {}
    reconstructed_at_by_day: dict[str, str] = {}
    reconstruction_model_version_by_day: dict[str, str] = {}
    reconstruction_basis_by_day: dict[str, str] = {}
    for row in forecast_hourly or []:
        day = str(row.get("date") or "")
        value = to_float(row.get("forecast_load_kwh"))
        if day and value is not None:
            hourly_load_by_day[day] = hourly_load_by_day.get(day, 0.0) + max(0.0, value)
        pv_value = to_float(row.get("forecast_pv_kwh"))
        if day and pv_value is not None:
            hourly_pv_by_day[day] = hourly_pv_by_day.get(day, 0.0) + max(0.0, pv_value)
        if day and row.get("source"):
            hourly_source_by_day[day] = str(row["source"])
        if day and row.get("forecast_run_id"):
            hourly_run_id_by_day[day] = str(row["forecast_run_id"])
        if day and row.get("forecast_issued_at"):
            hourly_issued_at_by_day[day] = str(row["forecast_issued_at"])
        if day and row.get("forecast_reconstruction_id"):
            reconstruction_id_by_day[day] = str(row["forecast_reconstruction_id"])
        if day and row.get("forecast_reconstructed_at"):
            reconstructed_at_by_day[day] = str(row["forecast_reconstructed_at"])
        if day and row.get("forecast_reconstruction_model_version"):
            reconstruction_model_version_by_day[day] = str(
                row["forecast_reconstruction_model_version"]
            )
        if day and row.get("forecast_reconstruction_basis"):
            reconstruction_basis_by_day[day] = str(row["forecast_reconstruction_basis"])
    dates = {
        d
        for d in set(pv_by_day) | set(actual_by_day) | set(hourly_load_by_day) | set(hourly_pv_by_day)
        if start_date <= d <= end_date_iso
    }
    out: list[dict[str, Any]] = []
    for day in sorted(dates):
        actual = actual_by_day.get(day, {})
        pv = pv_by_day.get(day)
        sunshine_forecast_pv = _forecast_pv_kwh(pv)
        hourly_source = hourly_source_by_day.get(day, "forecast_hourly")
        paired_hourly = (
            hourly_source in {"forecast_hourly_snapshot", _RECONSTRUCTED_FORECAST_SOURCE}
            and day in hourly_pv_by_day
            and day in hourly_load_by_day
        )
        forecast_pv = hourly_pv_by_day.get(day) if paired_hourly else sunshine_forecast_pv
        if forecast_pv is None:
            forecast_pv = hourly_pv_by_day.get(day)
        pv_source = (
            hourly_source
            if paired_hourly
            else "sunshine_daily"
            if sunshine_forecast_pv is not None
            else hourly_source_by_day.get(day)
            if day in hourly_pv_by_day
            else None
        )
        rolling_load = None if day in hourly_load_by_day else _rolling_load_forecast(day, actual_by_day)
        forecast_load = hourly_load_by_day.get(day, rolling_load)
        load_source = (
            hourly_source
            if day in hourly_load_by_day
            else _LEGACY_LOAD_SOURCE
            if rolling_load is not None
            else None
        )
        is_reconstructed = _RECONSTRUCTED_FORECAST_SOURCE in {pv_source, load_source}
        provenance_kind = _forecast_provenance_kind(
            pv_source=pv_source,
            load_source=load_source,
        )
        out.append(
            {
                "date": day,
                "forecast_pv_kwh": forecast_pv,
                "forecast_pv_source": pv_source,
                "forecast_pv_morning_kwh": (pv or {}).get("forecast_pv_morning_kwh"),
                "forecast_pv_midday_kwh": (pv or {}).get("forecast_pv_midday_kwh"),
                "forecast_pv_evening_kwh": (pv or {}).get("forecast_pv_evening_kwh"),
                "forecast_pv_calibration_factor": (pv or {}).get("forecast_pv_calibration_factor"),
                "actual_pv_kwh": actual.get("actual_pv_kwh"),
                "forecast_load_kwh": forecast_load,
                "forecast_load_source": load_source,
                "forecast_run_id": hourly_run_id_by_day.get(day),
                "forecast_issued_at": hourly_issued_at_by_day.get(day),
                "forecast_provenance_kind": provenance_kind,
                "forecast_is_reconstructed": is_reconstructed,
                "forecast_reconstruction_id": reconstruction_id_by_day.get(day),
                "forecast_reconstructed_at": reconstructed_at_by_day.get(day),
                "forecast_reconstruction_model_version": reconstruction_model_version_by_day.get(day),
                "forecast_reconstruction_basis": reconstruction_basis_by_day.get(day),
                "actual_load_kwh": actual.get("actual_load_kwh"),
            }
        )
    return out

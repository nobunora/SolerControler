"""Provider ordering and candidate selection for PV array forecasts."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Callable

from app.configuration.environment import env_float_clamped
from app.forecasting.pv_array_adapters import aggregate_hourly, parse_provider_time, round_finite
from app.parsing.numbers import parse_csv_float


ForecastProvider = Callable[[], dict[str, Any]]


def provider_order_from_env() -> list[str]:
    raw = os.getenv("PV_ARRAY_PROVIDER", "forecast_solar,open_meteo").strip()
    aliases = {
        "forecast.solar": "forecast_solar", "forecast-solar": "forecast_solar",
        "forecast_solar": "forecast_solar", "open-meteo": "open_meteo",
        "open_meteo": "open_meteo", "openmeteo": "open_meteo",
    }
    providers: list[str] = []
    for part in (raw or "forecast_solar,open_meteo").split(","):
        provider = aliases.get(part.strip().lower())
        if provider and provider not in providers:
            providers.append(provider)
    return providers or ["forecast_solar", "open_meteo"]


def select_provider_forecasts(
    providers: dict[str, ForecastProvider], *, mode: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run configured candidates in order and retain success/failure provenance."""
    forecasts: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    for provider in provider_order_from_env():
        candidate = providers.get(provider)
        if candidate is None:
            continue
        try:
            forecasts.append(candidate())
            attempts.append({"provider": provider, "ok": True})
            if mode in {"first", "first_success", "fallback"}:
                break
        except Exception as exc:
            attempts.append({"provider": provider, "ok": False, "error": str(exc)})
    return forecasts, attempts


def forecast_hourly_map(forecast: dict[str, Any]) -> dict[datetime, float]:
    rows = forecast.get("hourly", [])
    if not isinstance(rows, list):
        return {}
    result = {}
    for row in rows:
        if isinstance(row, dict) and (dt := parse_provider_time(row.get("time"), forecast_solar=True)) is not None:
            result[dt] = max(0.0, parse_csv_float(row.get("total_kwh"), default=0.0))
    return result


def ensemble_hourly_value(*, hour: int, forecast_solar_kwh: float, open_meteo_kwh: float) -> tuple[float, str]:
    if 7 <= hour < 10:
        return max(forecast_solar_kwh, open_meteo_kwh), "morning_max"
    env_key, default, method = ("PV_ENSEMBLE_OPEN_METEO_WEIGHT_MIDDAY", 0.35, "midday_blend") if 10 <= hour < 16 else (("PV_ENSEMBLE_OPEN_METEO_WEIGHT_EVENING", 0.25, "evening_blend") if 16 <= hour < 23 else ("PV_ENSEMBLE_OPEN_METEO_WEIGHT_OTHER", 0.50, "other_blend"))
    weight = env_float_clamped(env_key, default, max_val=1.0)
    return forecast_solar_kwh * (1.0 - weight) + open_meteo_kwh * weight, method


def ensemble_pv_forecasts(*, forecasts: list[dict[str, Any]], target_date: str, timezone: str, calibration_factor: float) -> dict[str, Any]:
    providers = {str(item.get("provider") or ""): item for item in forecasts if isinstance(item, dict)}
    forecast_solar, open_meteo = providers.get("forecast_solar"), providers.get("open_meteo")
    if forecast_solar is None or open_meteo is None:
        raise RuntimeError("PV ensemble requires forecast_solar and open_meteo forecasts")
    fs_rows, om_rows = forecast_hourly_map(forecast_solar), forecast_hourly_map(open_meteo)
    hourly, totals_rows = [], []
    for dt in sorted(set(fs_rows) | set(om_rows)):
        fs_kwh, om_kwh = fs_rows.get(dt), om_rows.get(dt)
        if fs_kwh is None:
            total, method = max(0.0, om_kwh or 0.0), "open_meteo_only"
        elif om_kwh is None:
            total, method = max(0.0, fs_kwh), "forecast_solar_only"
        else:
            total, method = ensemble_hourly_value(hour=dt.hour, forecast_solar_kwh=fs_kwh, open_meteo_kwh=om_kwh)
        hourly.append({"time": dt.isoformat(timespec="minutes"), "total_kwh": round_finite(total), "total_kw": round_finite(total), "forecast_solar_kwh": round_finite(fs_kwh), "open_meteo_kwh": round_finite(om_kwh), "ensemble_method": method})
        totals_rows.append({"time": dt, "kwh": total})
    totals = aggregate_hourly(totals_rows)
    return {"enabled": True, "source": "ensemble-forecast-solar-open-meteo", "provider": "ensemble", "target_date": target_date, "timezone": timezone, "calibration_factor": round_finite(calibration_factor), "totals": {key: round_finite(value) for key, value in totals.items()}, "arrays": forecast_solar.get("arrays") or open_meteo.get("arrays") or [], "hourly": hourly, "provider_forecasts": {"forecast_solar": forecast_solar, "open_meteo": open_meteo}, "ensemble": {"method": "morning_max_midday_weighted_blend", "morning_hours": [5, 6, 7, 8, 9], "open_meteo_weight_midday": env_float_clamped("PV_ENSEMBLE_OPEN_METEO_WEIGHT_MIDDAY", 0.35, max_val=1.0), "open_meteo_weight_evening": env_float_clamped("PV_ENSEMBLE_OPEN_METEO_WEIGHT_EVENING", 0.25, max_val=1.0), "open_meteo_weight_other": env_float_clamped("PV_ENSEMBLE_OPEN_METEO_WEIGHT_OTHER", 0.50, max_val=1.0)}}

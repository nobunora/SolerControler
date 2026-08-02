"""Provider ordering and candidate selection for PV array forecasts."""

from __future__ import annotations

import os
from typing import Any, Callable


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

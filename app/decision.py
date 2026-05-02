from __future__ import annotations

from app.config import AppConfig
from app.models import DesiredBatterySetting, ForecastResult, MonitoringMetrics


def decide_battery_setting(
    forecast: ForecastResult,
    metrics: MonitoringMetrics,
    cfg: AppConfig,
) -> DesiredBatterySetting:
    latest_soc = metrics.latest_soc

    if forecast.hours_12h >= cfg.forecast_high_hours:
        if latest_soc is not None and latest_soc <= cfg.low_soc_threshold:
            return DesiredBatterySetting(
                charge_limit_percent=cfg.charge_limit_high,
                mode=cfg.mode_high_sun,
                reason=(
                    f"日射予報が高い({forecast.hours_12h:.2f}h) かつ "
                    f"SOCが低め({latest_soc:.1f}%)"
                ),
            )
        return DesiredBatterySetting(
            charge_limit_percent=cfg.charge_limit_mid,
            mode=cfg.mode_mid_sun,
            reason=f"日射予報が高い({forecast.hours_12h:.2f}h) ため通常運転",
        )

    if forecast.hours_12h <= cfg.forecast_low_hours:
        return DesiredBatterySetting(
            charge_limit_percent=cfg.charge_limit_low,
            mode=cfg.mode_low_sun,
            reason=f"日射予報が低い({forecast.hours_12h:.2f}h)ため節電優先",
        )

    if latest_soc is not None and latest_soc >= cfg.high_soc_threshold:
        return DesiredBatterySetting(
            charge_limit_percent=cfg.charge_limit_low,
            mode=cfg.mode_low_sun,
            reason=(
                f"日射予報は中間({forecast.hours_12h:.2f}h)だが "
                f"SOCが高め({latest_soc:.1f}%)"
            ),
        )

    return DesiredBatterySetting(
        charge_limit_percent=cfg.charge_limit_mid,
        mode=cfg.mode_mid_sun,
        reason=f"日射予報が中間({forecast.hours_12h:.2f}h)のため標準運転",
    )

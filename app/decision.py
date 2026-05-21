from __future__ import annotations

from app.config import AppConfig
from app.models import DesiredBatterySetting, ForecastResult, MonitoringMetrics


def decide_battery_setting(
    forecast: ForecastResult,
    metrics: MonitoringMetrics,
    cfg: AppConfig,
) -> DesiredBatterySetting:
    latest_soc = metrics.latest_soc
    near_zero_sun_threshold_h = max(0.05, min(0.5, cfg.forecast_low_hours * 0.2))

    decision: DesiredBatterySetting
    if forecast.hours_12h >= cfg.forecast_high_hours:
        if latest_soc is not None and latest_soc <= cfg.low_soc_threshold:
            decision = DesiredBatterySetting(
                charge_limit_percent=cfg.charge_limit_high,
                mode=cfg.mode_high_sun,
                reason=(
                    f"日射予報が高い({forecast.hours_12h:.2f}h) かつ "
                    f"SOCが低め({latest_soc:.1f}%)"
                ),
            )
        else:
            decision = DesiredBatterySetting(
                charge_limit_percent=cfg.charge_limit_mid,
                mode=cfg.mode_mid_sun,
                reason=f"日射予報が高い({forecast.hours_12h:.2f}h) ため通常運転",
            )
    elif forecast.hours_12h <= cfg.forecast_low_hours:
        near_zero_sun = forecast.hours_12h <= near_zero_sun_threshold_h
        charge_limit = cfg.charge_limit_high if near_zero_sun else cfg.charge_limit_low
        reason_tail = (
            f"日射予報がほぼ0({forecast.hours_12h:.2f}h)のため夜間充電を優先"
            if near_zero_sun
            else f"日射予報が低い({forecast.hours_12h:.2f}h)ため節電優先"
        )
        decision = DesiredBatterySetting(
            charge_limit_percent=charge_limit,
            mode=cfg.mode_low_sun,
            reason=reason_tail,
        )
    elif latest_soc is not None and latest_soc >= cfg.high_soc_threshold:
        decision = DesiredBatterySetting(
            charge_limit_percent=cfg.charge_limit_low,
            mode=cfg.mode_low_sun,
            reason=(
                f"日射予報は中間({forecast.hours_12h:.2f}h)だが "
                f"SOCが高め({latest_soc:.1f}%)"
            ),
        )
    else:
        decision = DesiredBatterySetting(
            charge_limit_percent=cfg.charge_limit_mid,
            mode=cfg.mode_mid_sun,
            reason=f"日射予報が中間({forecast.hours_12h:.2f}h)のため標準運転",
        )

    if decision.charge_limit_percent >= cfg.green_mode_max_charge_percent:
        return DesiredBatterySetting(
            charge_limit_percent=decision.charge_limit_percent,
            mode=cfg.mode_force_charge,
            reason=(
                f"{decision.reason} / 必要充電量が{cfg.green_mode_max_charge_percent:.0f}%以上のため"
                " 強制充電モードを選択"
            ),
        )
    return decision

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.decision import decide_battery_setting
from app.models import ForecastResult, MonitoringMetrics


def _cfg(**overrides):
    base = dict(
        forecast_high_hours=6.0,
        forecast_low_hours=2.0,
        low_soc_threshold=30.0,
        high_soc_threshold=80.0,
        charge_limit_high=95,
        charge_limit_mid=70,
        charge_limit_low=40,
        mode_high_sun="high",
        mode_mid_sun="mid",
        mode_low_sun="low",
        mode_force_charge="forced",
        green_mode_max_charge_percent=50.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _forecast(hours: float) -> ForecastResult:
    return ForecastResult(hours_12h=hours, captured_at=datetime(2026, 5, 1, 0, 0), source_text="test")


def _metrics(soc: float | None) -> MonitoringMetrics:
    return MonitoringMetrics(
        row_count=1,
        latest_soc=soc,
        avg_soc=soc,
        total_charge=0.0,
        total_discharge=0.0,
    )


def test_decide_battery_setting_switches_to_forced_when_required_charge_is_high() -> None:
    cfg = _cfg(charge_limit_mid=70, green_mode_max_charge_percent=50.0)
    result = decide_battery_setting(_forecast(7.0), _metrics(60.0), cfg)
    assert result.charge_limit_percent == 70
    assert result.mode == "forced"


def test_decide_battery_setting_keeps_non_forced_when_charge_target_is_below_threshold() -> None:
    cfg = _cfg(charge_limit_mid=45, green_mode_max_charge_percent=50.0)
    result = decide_battery_setting(_forecast(7.0), _metrics(60.0), cfg)
    assert result.charge_limit_percent == 45
    assert result.mode == "mid"

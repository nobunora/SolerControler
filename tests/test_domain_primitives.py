from __future__ import annotations

from datetime import datetime, time
from pathlib import Path

import pytest

from app.domain.monitoring import MonitoringPoint, validated_soc_percent
from app.operations.monitoring_csv import iter_monitoring_points
from app.energy_plan.night_plan import parse_night_plan
from app.domain.tariff import TieredTariff
from app.domain.constants import clamp_percent
from app.domain.time_windows import DailyWindow, parse_hhmm


def test_daily_window_uses_half_open_boundaries_and_supports_midnight() -> None:
    daytime = DailyWindow(parse_hhmm("08:00"), parse_hhmm("22:00"))
    assert daytime.contains(time(8, 0)) is True
    assert daytime.contains(time(22, 0)) is False

    overnight = DailyWindow(parse_hhmm("23:00"), parse_hhmm("07:00"))
    assert overnight.contains(time(23, 30)) is True
    assert overnight.contains(time(6, 59)) is True
    assert overnight.contains(time(7, 0)) is False


def test_percent_boundary_keeps_legacy_helper_behavior() -> None:
    assert clamp_percent(-1.0) == 0.0
    assert clamp_percent(34.5) == 34.5
    assert clamp_percent(101.0) == 100.0


def test_tiered_tariff_increment_crosses_each_boundary() -> None:
    tariff = TieredTariff(120.0, 300.0, 20.0, 30.0, 40.0)
    assert tariff.incremental_cost(119.0, 2.0) == pytest.approx(50.0)
    assert tariff.incremental_cost(299.0, 2.0) == pytest.approx(70.0)
    assert tariff.incremental_cost(-10.0, -2.0) == 0.0


def test_monitoring_csv_keeps_missing_and_rejects_invalid_soc(tmp_path: Path) -> None:
    csv_path = tmp_path / "monitor.csv"
    csv_path.write_text(
        "年月日,時刻,消費電力量[kWh],蓄電残量(SOC)[%]\n"
        "2026/07/15,12:00,1.5,55\n"
        "2026/07/15,12:30,1.7,101\n",
        encoding="utf-8",
    )
    points = list(iter_monitoring_points(csv_path))
    assert [point.load_kwh for point in points] == [1.5, 1.7]
    assert [point.soc_percent for point in points] == [55.0, None]


def test_monitoring_domain_value_keeps_storage_shape() -> None:
    point = MonitoringPoint(
        timestamp=datetime(2026, 7, 15, 12, 0),
        pv_kwh=1.0,
        load_kwh=2.0,
        sell_kwh=None,
        buy_kwh=1.0,
        charge_kwh=None,
        discharge_kwh=None,
        soc_percent=validated_soc_percent("55"),
    )
    assert point.as_storage_row()["soc_percent"] == 55.0


def test_night_plan_typed_view_validates_business_fields() -> None:
    plan = parse_night_plan(
        {
            "forecast": {"date": "2026-07-16"},
            "result": {
                "target_soc_7_percent": 80,
                "required_night_charge_kwh": 4.2,
                "predicted_midday_surplus_kwh": 7.1,
            },
            "plan_quality": {"should_apply": True},
        }
    )
    assert plan.forecast_date.isoformat() == "2026-07-16"
    assert plan.result.required_night_charge_kwh == 4.2
    assert plan.should_apply is True

    with pytest.raises(ValueError, match="out of range"):
        parse_night_plan(
            {
                "forecast": {"date": "2026-07-16"},
                "result": {"target_soc_7_percent": 120, "required_night_charge_kwh": 1},
            }
        )


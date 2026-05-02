from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.kpnet_workflow import (
    KpNetConfig,
    _build_dynamic_forced_profile,
    _in_time_window,
    _parse_hhmm,
    _validate_base_url,
)


def _build_cfg(*, plan_path: Path) -> KpNetConfig:
    return KpNetConfig(
        base_url="https://ctrl.kp-net.com/settingcontrol",
        username="user",
        password="pass",
        dry_run=True,
        timeout_sec=60.0,
        csv_output_format="太陽光発電＋蓄電池",
        csv_aggr_type="30分データ",
        csv_target_months=["2026-04", "2026-05"],
        download_latest_month=True,
        workflow_mode="settings",
        settings_sequence="forced-only",
        force_settings_profile="auto",
        dynamic_forced_profile=True,
        dynamic_mode_switch_by_time=True,
        night_plan_path=plan_path,
        default_charge_power_kw=1.8,
        night_charge_window_start="23:00",
        night_charge_window_end="07:00",
        day_discharge_window_start="07:00",
        day_discharge_window_end="23:00",
        timezone_name="Asia/Tokyo",
        use_har_credentials=False,
        har_path=Path("dummy.har"),
        artifacts_dir=Path("artifacts"),
        enforce_https=True,
        allowed_hosts=["ctrl.kp-net.com"],
    )


def test_parse_hhmm_valid_and_invalid() -> None:
    assert _parse_hhmm("07:30", name="X") == (7, 30)
    assert _parse_hhmm(" 23:00 ", name="X") == (23, 0)
    with pytest.raises(RuntimeError):
        _parse_hhmm("24:00", name="X")
    with pytest.raises(RuntimeError):
        _parse_hhmm("abc", name="X")


def test_in_time_window_cross_midnight() -> None:
    start = 23 * 60
    end = 7 * 60
    assert _in_time_window(23 * 60 + 30, start, end)
    assert _in_time_window(6 * 60 + 59, start, end)
    assert not _in_time_window(12 * 60, start, end)


def test_validate_base_url_enforces_https_and_host() -> None:
    _validate_base_url(
        base_url="https://ctrl.kp-net.com/settingcontrol",
        enforce_https=True,
        allowed_hosts=["ctrl.kp-net.com"],
    )
    with pytest.raises(RuntimeError):
        _validate_base_url(
            base_url="http://ctrl.kp-net.com/settingcontrol",
            enforce_https=True,
            allowed_hosts=["ctrl.kp-net.com"],
        )
    with pytest.raises(RuntimeError):
        _validate_base_url(
            base_url="https://evil.example.com/path",
            enforce_https=True,
            allowed_hosts=["ctrl.kp-net.com"],
        )


def test_build_dynamic_forced_profile_uses_plan_and_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(
        "\n".join(
            [
                "年月日,時刻,充電電力量[kWh]",
                "2026/05/01,23:30,0.5",
                "2026/05/02,00:00,0.5",
                "2026/05/02,08:00,0.2",
            ]
        ),
        encoding="utf-8",
    )
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "forecast": {"date": "2026-05-03"},
                "result": {
                    "required_night_charge_kwh": 2.2,
                    "target_soc_7_percent": 35.0,
                },
                "csv_paths": [str(csv_path)],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cfg = _build_cfg(plan_path=plan_path)
    value_maps = {
        "BatteryOperatingMode": {"1": "グリーンモード", "2": "経済モード", "3": "強制充電モード"},
        "SocSafetyMode": {"0": "0%", "50": "50%", "100": "100%"},
        "SocEconomyMode": {"0": "0%", "20": "20%"},
        "SocContactInput": {"0": "0%", "100": "100%"},
        "SocChargeMode": {"10": "10%", "30": "30%", "50": "50%", "80": "80%"},
    }
    summary: dict[str, object] = {}

    profile = _build_dynamic_forced_profile(cfg=cfg, value_maps=value_maps, summary=summary)

    assert profile.battery_operating_mode == "2"
    assert profile.soc_safety_mode == "100"
    assert profile.soc_economy_mode == "0"
    assert profile.soc_contact_input == "100"
    assert profile.soc_charge_mode == "50"
    # 2.2kWh / 1.0kW = 132min -> 07:00 終了の132分前 = 04:48
    assert profile.charge_start_h == "4"
    assert profile.charge_start_m == "48"
    assert profile.charge_end_h == "7"
    assert profile.charge_end_m == "0"

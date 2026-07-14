from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import pytest

from app.kpnet_workflow import (
    KpNetConfig,
    NightChargePlan,
    ProfileOverrides,
    _build_payload,
    _build_dynamic_forced_profile,
    _default_csv_target_months,
    _extract_simple_visualization_soc_percent,
    _in_time_window,
    _load_night_charge_plan,
    _parse_hhmm,
    _pick_battery_operating_mode_code,
    _pick_night_mode_preference,
    _run_settings_phase,
    _validate_base_url,
)


def _build_cfg(*, plan_path: Path) -> KpNetConfig:
    conditions_path = plan_path.parent / "operation_conditions.json"
    if not conditions_path.exists():
        conditions_path.write_text(
            json.dumps(
                {
                    "fixed": [
                        {
                            "id": "forbid_cross_midnight",
                            "enabled": True,
                            "priority": 1000,
                            "target": "charge",
                            "min_duration_minutes": 30,
                        },
                        {
                            "id": "forbid_same_start_end",
                            "enabled": True,
                            "priority": 990,
                            "target": "charge",
                            "min_duration_minutes": 30,
                        },
                    ],
                    "variable": [
                        {
                            "id": "night_charge_end_time",
                            "enabled": True,
                            "priority": 500,
                            "value": "06:00",
                        },
                        {
                            "id": "day_charge_window",
                            "enabled": True,
                            "priority": 400,
                            "start": "00:00",
                            "end": "06:00",
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
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
        green_mode_max_charge_percent=50.0,
        night_charge_window_start="23:00",
        night_charge_window_end="07:00",
        day_discharge_window_start="07:00",
        day_discharge_window_end="23:00",
        operation_conditions_path=conditions_path,
        timezone_name="Asia/Tokyo",
        use_har_credentials=False,
        har_path=Path("dummy.har"),
        artifacts_dir=Path("artifacts"),
        enforce_https=True,
        allowed_hosts=["ctrl.kp-net.com"],
    )


def _value_maps() -> dict[str, dict[str, str]]:
    return {
        "BatteryOperatingMode": {"0": "待機モード", "1": "グリーンモード", "2": "経済モード", "3": "強制充電モード"},
        "SocSafetyMode": {"0": "0%", "50": "50%", "100": "100%"},
        "SocEconomyMode": {"0": "0%", "20": "20%"},
        "SocContactInput": {"0": "0%", "100": "100%"},
        "SocChargeMode": {"0": "0%", "10": "10%", "30": "30%", "50": "50%"},
        "OnPowerOutageChargePowerW": {"0": "0W", "65535": "最大"},
        "AgreementAmpere": {"50": "50A"},
    }


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


def test_default_csv_target_months_includes_previous_and_current_month() -> None:
    assert _default_csv_target_months(datetime(2026, 7, 1, 4, 0)) == ["2026-06", "2026-07"]


def test_extract_simple_visualization_soc_percent_from_battery_table() -> None:
    html = """
    <table class="data_table01 data_table_bt">
      <tr>
        <th class="l_cell" rowspan="2"><i class="fas fa-battery-three-quarters"></i><br>蓄電池</th>
        <th>運転状態</th><th class="rt_cell">蓄電残量</th>
      </tr>
      <tr><td>充電</td><td class="rb_cell"> 78 <span>%</span></td></tr>
    </table>
    """

    assert _extract_simple_visualization_soc_percent(html) == 78.0
    assert _default_csv_target_months(datetime(2026, 1, 1, 4, 0)) == ["2025-12", "2026-01"]


@pytest.mark.parametrize(
    "icon",
    ["full", "three-quarters", "half", "quarter", "empty"],
)
def test_extract_simple_visualization_soc_supports_battery_icons(icon: str) -> None:
    html = f"""
    <table class="data_table_bt">
      <tr><th rowspan="2"><i class="fa fa-battery-{icon}"></i></th><th>運転状態</th><th>蓄電残量</th></tr>
      <tr><td class="rb_cell">3</td><td class="rb_cell">25 <span>%</span></td></tr>
    </table>
    """

    assert _extract_simple_visualization_soc_percent(html) == 25.0


def test_extract_simple_visualization_soc_uses_matching_header_and_battery_table() -> None:
    html = """
    <table class="data_table_bt">
      <tr><th>発電量</th></tr><tr><td class="rb_cell">99 <span>%</span></td></tr>
    </table>
    <table class="data_table_bt">
      <tr><th rowspan="2"><i class="fa fa-battery-quarter"></i></th><th>蓄電残量</th><th>運転状態</th></tr>
      <tr><td class="rb_cell">24 <span>%</span></td><td class="rb_cell">1</td></tr>
    </table>
    """

    assert _extract_simple_visualization_soc_percent(html) == 24.0


@pytest.mark.parametrize("value", ["0", "100", "78.5"])
def test_extract_simple_visualization_soc_accepts_valid_bounds(value: str) -> None:
    html = f"""
    <table class="data_table_bt"><tr><th><i class="fa fa-battery-half"></i></th><th>蓄電残量</th></tr>
    <tr><td class="rb_cell">{value} <span>%</span></td></tr></table>
    """

    assert _extract_simple_visualization_soc_percent(html) == float(value)


@pytest.mark.parametrize("value", ["-0.1", "100.1", "780"])
def test_extract_simple_visualization_soc_rejects_out_of_range(value: str) -> None:
    html = f"""
    <table class="data_table_bt"><tr><th><i class="fa fa-battery-half"></i></th><th>蓄電残量</th></tr>
    <tr><td class="rb_cell">{value} <span>%</span></td></tr></table>
    """

    with pytest.raises(ValueError, match="SOC out of range"):
        _extract_simple_visualization_soc_percent(html)


@pytest.mark.parametrize("value", ["--", "20 80", "nan", "inf"])
def test_extract_simple_visualization_soc_rejects_unparseable_values(value: str) -> None:
    html = f"""
    <table class="data_table_bt"><tr><th><i class="fa fa-battery-half"></i></th><th>蓄電残量</th></tr>
    <tr><td class="rb_cell">{value} <span>%</span></td></tr></table>
    """

    assert _extract_simple_visualization_soc_percent(html) is None


def test_pick_battery_operating_mode_code_supports_standby() -> None:
    assert (
        _pick_battery_operating_mode_code(
            {"0": "待機モード", "1": "グリーンモード", "3": "強制充電モード"},
            prefer="standby",
        )
        == "0"
    )


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


def test_build_payload_detects_outage_only_drift() -> None:
    current = {
        "batteryOperatingMode": "1",
        "socSafetyMode": "0",
        "socEconomyMode": "0",
        "socContactInput": "0",
        "socChargeMode": "0",
        "onPowerOutageMode": "0",
        "onPowerOutageChargePowerW": "0",
        "chargeStartTimeH": "23",
        "chargeStartTimeM": "0",
        "chargeEndTimeH": "7",
        "chargeEndTimeM": "0",
        "dischargeStartTimeH": "7",
        "dischargeStartTimeM": "0",
        "dischargeEndTimeH": "23",
        "dischargeEndTimeM": "0",
        "agreementAmpere": "50",
    }
    overrides = ProfileOverrides(
        name="outage-check",
        battery_operating_mode="1",
        soc_safety_mode="0",
        soc_economy_mode="0",
        soc_contact_input="0",
        soc_charge_mode="0",
        charge_start_h="23",
        charge_start_m="0",
        charge_end_h="7",
        charge_end_m="0",
        discharge_start_h="7",
        discharge_start_m="0",
        discharge_end_h="23",
        discharge_end_m="0",
        agreement_ampere="50",
        on_power_outage_mode="1",
        on_power_outage_charge_power_w="65535",
    )

    _payload, changed_fields = _build_payload(
        csrf_setting="csrf",
        pcsid="pcsid",
        current=current,
        overrides=overrides,
        value_maps=_value_maps(),
    )

    assert changed_fields == ["onPowerOutageMode", "onPowerOutageChargePowerW"]


def test_run_settings_phase_raises_after_confirm_failed(tmp_path: Path) -> None:
    class FakeClient:
        csrf_setting = "csrf"
        pcsid = "pcsid"

        def open_settings_page(self) -> None:
            return None

        def read_current_settings(self) -> dict[str, str]:
            return {}

        def collect_candidate_maps(self) -> dict[str, dict[str, str]]:
            return _value_maps()

        def confirm_setting(self, payload: dict[str, str]) -> tuple[bool, str, str, str]:
            return False, "confirm title", "confirm error", "<html>failed</html>"

    base_cfg = _build_cfg(plan_path=tmp_path / "missing_night_charge_plan.json")
    cfg = KpNetConfig(
        **{
            **base_cfg.__dict__,
            "dynamic_forced_profile": False,
            "force_settings_profile": "forced",
        }
    )
    summary: dict[str, object] = {"setting_results": []}

    with pytest.raises(RuntimeError, match="confirmation failed"):
        _run_settings_phase(client=FakeClient(), cfg=cfg, run_dir=tmp_path, summary=summary)

    assert summary["setting_results"] == [
        {
            "profile": "night-green",
            "changed_fields": [
                "batteryOperatingMode",
                "socSafetyMode",
                "socEconomyMode",
                "socContactInput",
                "socChargeMode",
                "onPowerOutageMode",
                "onPowerOutageChargePowerW",
                "chargeStartTimeH",
                "chargeStartTimeM",
                "chargeEndTimeH",
                "chargeEndTimeM",
                "dischargeStartTimeH",
                "dischargeStartTimeM",
                "dischargeEndTimeH",
                "dischargeEndTimeM",
                "agreementAmpere",
            ],
            "status": "confirm-failed",
            "title": "confirm title",
            "error": "confirm error",
            "confirm_path": str(tmp_path / "confirm_night-green.html"),
        }
    ]


def test_night_mode_preference_uses_target_soc_above_green_ceiling(tmp_path: Path) -> None:
    plan = NightChargePlan(
        plan_path=tmp_path / "night_charge_plan.json",
        forecast_date="2026-06-02",
        required_night_charge_kwh=3.7,
        target_soc_7_percent=80.0,
        soc_now_percent=39.0,
        effective_capacity_kwh=9.0,
        csv_paths=[],
    )

    preference, required_pct, force_charge = _pick_night_mode_preference(
        plan=plan,
        green_mode_max_charge_percent=50.0,
    )

    assert preference == "forced"
    assert required_pct == 41.0
    assert force_charge is True


def test_night_mode_preference_uses_forced_for_adjust03_charge_need(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUD_JOB_SLOT", "03")
    plan = NightChargePlan(
        plan_path=tmp_path / "night_charge_plan.json",
        forecast_date="2026-06-02",
        required_night_charge_kwh=0.2,
        target_soc_7_percent=20.0,
        soc_now_percent=18.0,
        effective_capacity_kwh=9.0,
        csv_paths=[],
    )

    preference, required_pct, force_charge = _pick_night_mode_preference(
        plan=plan,
        green_mode_max_charge_percent=50.0,
    )

    assert preference == "forced"
    assert required_pct == 2.0
    assert force_charge is True


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

    assert profile.battery_operating_mode == "1"
    assert profile.soc_safety_mode == "100"
    assert profile.soc_economy_mode == "0"
    assert profile.soc_contact_input == "100"
    assert profile.soc_charge_mode == "50"
    # 2.2kWh / 1.0kW = 132min -> 06:00 終了の132分前 = 03:48
    assert profile.charge_start_h == "3"
    assert profile.charge_start_m == "48"
    assert profile.charge_end_h == "6"
    assert profile.charge_end_m == "0"
    assert profile.discharge_start_h == "7"
    assert profile.discharge_start_m == "0"
    assert profile.discharge_end_h == "23"
    assert profile.discharge_end_m == "0"


def test_build_dynamic_forced_profile_applies_slot23_discharge_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUD_JOB_SLOT", "23")
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text("年月日,時刻,充電電力量[kWh]\n2026/05/01,23:30,0.0\n", encoding="utf-8")
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "forecast": {"date": "2026-05-03"},
                "result": {
                    "required_night_charge_kwh": 0.0,
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
        "BatteryOperatingMode": {"1": "グリーンモード", "3": "強制充電モード"},
        "SocSafetyMode": {"0": "0%", "50": "50%", "100": "100%"},
        "SocEconomyMode": {"0": "0%", "20": "20%"},
        "SocContactInput": {"0": "0%", "100": "100%"},
        "SocChargeMode": {"0": "0%", "40": "40%", "80": "80%"},
    }
    summary: dict[str, object] = {}

    profile = _build_dynamic_forced_profile(cfg=cfg, value_maps=value_maps, summary=summary)

    assert profile.soc_contact_input == "100"
    assert profile.soc_charge_mode == "0"
    assert summary["night_charge_plan"]["slot23_discharge_guard"]["applied"] is True


def test_build_dynamic_forced_profile_times_rounded_soc_limit_by_raw_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADJUST03_FORCE_CHARGE_RATE_FALLBACK_PERCENT_PER_HOUR", "40")
    monkeypatch.setenv("ADJUST03_FORCE_CHARGE_RATE_MIN_PERCENT_PER_HOUR", "25")
    monkeypatch.setenv("ADJUST03_FORCE_CHARGE_RATE_MAX_PERCENT_PER_HOUR", "50")
    monkeypatch.setenv("ADJUST03_FORCE_CHARGE_SAMPLE_MIN_KWH", "0.8")
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(
        "\n".join(
            [
                "年月日,時刻,充電電力量[kWh],蓄電残量(SOC)[%]",
                "2026/06/18,05:00,0.0,0",
                "2026/06/18,05:30,1.0,20",
                "2026/06/18,06:00,1.0,40",
            ]
        ),
        encoding="utf-8",
    )
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "forecast": {"date": "2026-06-19"},
                "inputs": {"soc_now_percent": 0.0},
                "result": {
                    "required_night_charge_kwh": 3.25,
                    "effective_capacity_kwh": 8.9,
                    "target_soc_7_percent": 34.0,
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
        "SocChargeMode": {"0": "0%", "10": "10%", "20": "20%", "30": "30%", "40": "40%"},
    }
    summary: dict[str, object] = {}

    profile = _build_dynamic_forced_profile(cfg=cfg, value_maps=value_maps, summary=summary)

    assert profile.soc_charge_mode == "40"
    # Raw target is 34%, but KP-NET upper code is 40%. 34% / 40%/h = 51min,
    # so start should be 05:09 for a 06:00 end instead of the earlier kWh-based time.
    assert profile.charge_start_h == "5"
    assert profile.charge_start_m == "9"
    night_plan_summary = summary.get("night_charge_plan", {})
    assert isinstance(night_plan_summary, dict)
    assert night_plan_summary.get("duration_source") == "soc-rate-rounded-target"
    assert night_plan_summary.get("duration_minutes") == 51
    assert night_plan_summary.get("duration_minutes_kwh") == 98
    assert night_plan_summary.get("charge_rate_percent_per_hour") == pytest.approx(40.0)


def test_build_dynamic_forced_profile_uses_fixed_discharge_start_and_charge_end(
    tmp_path: Path,
) -> None:
    conditions_path = tmp_path / "operation_conditions.json"
    conditions_path.write_text(
        json.dumps(
            {
                "fixed": [
                    {
                        "id": "forbid_cross_midnight",
                        "enabled": True,
                        "priority": 1000,
                        "target": "charge",
                        "min_duration_minutes": 30,
                    },
                    {
                        "id": "forbid_same_start_end",
                        "enabled": True,
                        "priority": 990,
                        "target": "charge",
                        "min_duration_minutes": 30,
                    },
                ],
                "variable": [
                    {
                        "id": "night_charge_end_time",
                        "enabled": True,
                        "priority": 500,
                        "value": "07:00",
                    },
                    {
                        "id": "day_charge_window",
                        "enabled": True,
                        "priority": 400,
                        "start": "00:00",
                        "end": "07:00",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(
        "\n".join(
            [
                "年月日,時刻,充電電力量[kWh]",
                "2026/05/01,23:30,0.2",
            ]
        ),
        encoding="utf-8",
    )
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "forecast": {"date": "2026-05-03", "sun_hours": 8.0},
                "result": {
                    "required_night_charge_kwh": 0.0,
                    "target_soc_7_percent": 0.0,
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
        "SocChargeMode": {"0": "0%", "10": "10%", "30": "30%"},
    }
    summary: dict[str, object] = {}
    profile = _build_dynamic_forced_profile(cfg=cfg, value_maps=value_maps, summary=summary)

    assert profile.discharge_start_h == "7"
    assert profile.discharge_start_m == "0"
    assert profile.charge_start_h == "7"
    assert profile.charge_start_m == "0"
    assert profile.charge_end_h == "7"


@pytest.mark.parametrize(
    "plan_payload",
    [
        {"forecast": {"date": "2026-05-03"}, "result": {"target_soc_7_percent": 10.0}, "csv_paths": ["x.csv"]},
        {"forecast": {"date": "2026-05-03"}, "result": {"required_night_charge_kwh": 0.0}, "csv_paths": ["x.csv"]},
        {
            "forecast": {"date": ""},
            "result": {"required_night_charge_kwh": 0.0, "target_soc_7_percent": 10.0},
            "csv_paths": ["x.csv"],
        },
        {
            "forecast": {"date": "2026-05-03"},
            "plan_quality": {"should_apply": False},
            "result": {"required_night_charge_kwh": 0.0, "target_soc_7_percent": 10.0},
            "csv_paths": ["x.csv"],
        },
        {
            "forecast": {"date": "2026-05-03"},
            "result": {"required_night_charge_kwh": 0.0, "target_soc_7_percent": 101.0},
            "csv_paths": ["x.csv"],
        },
    ],
)
def test_load_night_charge_plan_rejects_unsafe_plan(tmp_path: Path, plan_payload: dict[str, object]) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text(json.dumps(plan_payload), encoding="utf-8")

    with pytest.raises(RuntimeError):
        _load_night_charge_plan(plan_path)


def test_build_dynamic_forced_profile_switches_to_forced_mode_when_charge_need_is_high(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(
        "\n".join(
            [
                "年月日,時刻,充電電力量[kWh]",
                "2026/05/01,23:30,0.5",
            ]
        ),
        encoding="utf-8",
    )
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "forecast": {"date": "2026-05-03"},
                "inputs": {"soc_now_percent": 10.0},
                "result": {
                    "required_night_charge_kwh": 4.0,
                    "effective_capacity_kwh": 8.0,
                    "target_soc_7_percent": 70.0,
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

    assert profile.battery_operating_mode == "3"
    night_plan_summary = summary.get("night_charge_plan", {})
    assert isinstance(night_plan_summary, dict)
    assert night_plan_summary.get("force_charge_mode") is True

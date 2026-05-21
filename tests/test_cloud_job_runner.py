from __future__ import annotations

from cloud_job_runner import (
    _forecast_changed,
    _mask_env_updates,
    _required_charge_percent_from_plan,
    _should_stage_partial_forced,
)


def test_mask_env_updates_hides_secrets() -> None:
    masked = _mask_env_updates(
        {
            "KP_MONITOR_PASSWORD": "plain-password",
            "API_TOKEN": "plain-token",
            "KP_WORKFLOW_MODE": "settings",
        }
    )
    assert masked["KP_MONITOR_PASSWORD"] == "***"
    assert masked["API_TOKEN"] == "***"
    assert masked["KP_WORKFLOW_MODE"] == "settings"


def test_mask_env_updates_none() -> None:
    assert _mask_env_updates(None) == {}


def test_forecast_changed_threshold() -> None:
    base = ("2026-05-04", 5.00, 18.0)
    same = ("2026-05-04", 5.03, 18.1)
    changed = ("2026-05-04", 5.12, 18.1)
    changed_date = ("2026-05-05", 5.00, 18.0)

    assert not _forecast_changed(base, same, sun_epsilon_h=0.05, temp_epsilon_c=0.2)
    assert _forecast_changed(base, changed, sun_epsilon_h=0.05, temp_epsilon_c=0.2)
    assert _forecast_changed(base, changed_date, sun_epsilon_h=0.05, temp_epsilon_c=0.2)


def test_required_charge_percent_from_plan_uses_soc_delta() -> None:
    pct = _required_charge_percent_from_plan(
        {
            "target_soc_7_percent": 80.0,
            "soc_now_percent": 25.0,
            "effective_capacity_kwh": 9.0,
            "required_night_charge_kwh": 2.0,
        }
    )
    assert pct == 55.0


def test_stage_partial_forced_enabled_for_51_to_99(monkeypatch) -> None:
    monkeypatch.setenv("KP_FORCE_PARTIAL_SOC_MIN_PERCENT", "51")
    monkeypatch.setenv("KP_FORCE_PARTIAL_SOC_MAX_PERCENT", "99")
    staged, required_pct, target_soc = _should_stage_partial_forced(
        plan_meta={
            "target_soc_7_percent": 80.0,
            "soc_now_percent": 20.0,
            "effective_capacity_kwh": 9.0,
            "required_night_charge_kwh": 5.0,
        },
        green_mode_max_charge_percent=50.0,
    )
    assert staged is True
    assert required_pct == 60.0
    assert target_soc == 80.0

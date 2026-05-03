from __future__ import annotations

from cloud_job_runner import _forecast_changed, _mask_env_updates


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

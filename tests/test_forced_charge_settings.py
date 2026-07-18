from __future__ import annotations

from datetime import time

from app.settings.forced_charge import ForcedChargeSettings


def test_forced_charge_settings_preserve_runner_defaults(monkeypatch) -> None:
    keys = (
        "ADJUST03_FORCE_MONITOR_CUTOFF_HHMM",
        "ADJUST03_FORCE_MONITOR_POLL_SECONDS",
        "ADJUST03_SOC_RETRY_ATTEMPTS",
        "ADJUST03_SOC_RETRY_DELAY_SECONDS",
        "ADJUST03_FORCE_STOP_SOC_MARGIN_PERCENT",
        "ADJUST03_MAX_CONSECUTIVE_SOC_FAILURES",
        "ADJUST03_FORCE_REAPPLY_IF_SOC_NOT_INCREASING",
        "ADJUST03_FORCE_REAPPLY_AFTER_POLLS",
        "ADJUST03_FORCE_REAPPLY_MIN_SOC_DELTA_PERCENT",
        "ADJUST03_COMPLETION_CONFIRM_BEFORE_MINUTES",
    )
    for key in keys:
        monkeypatch.delenv(key, raising=False)

    settings = ForcedChargeSettings.from_env()

    assert settings == ForcedChargeSettings(
        cutoff=time(7, 0),
        poll_interval_seconds=180,
        retry_attempts=3,
        retry_delay_seconds=5.0,
        stop_soc_margin_percent=1.0,
        max_consecutive_soc_failures=3,
        reapply_if_soc_not_increasing=True,
        reapply_after_polls=2,
        reapply_min_soc_delta_percent=0.1,
        completion_confirm_before_minutes=5,
    )


def test_forced_charge_settings_preserve_runner_bounds(monkeypatch) -> None:
    monkeypatch.setenv("ADJUST03_FORCE_MONITOR_POLL_SECONDS", "1")
    monkeypatch.setenv("ADJUST03_FORCE_STOP_SOC_MARGIN_PERCENT", "-1")
    monkeypatch.setenv("ADJUST03_MAX_CONSECUTIVE_SOC_FAILURES", "0")
    monkeypatch.setenv("ADJUST03_FORCE_REAPPLY_IF_SOC_NOT_INCREASING", "off")
    monkeypatch.setenv("ADJUST03_FORCE_REAPPLY_AFTER_POLLS", "0")
    monkeypatch.setenv("ADJUST03_FORCE_REAPPLY_MIN_SOC_DELTA_PERCENT", "-1")
    monkeypatch.setenv("ADJUST03_COMPLETION_CONFIRM_BEFORE_MINUTES", "-1")

    settings = ForcedChargeSettings.from_env()

    assert settings.poll_interval_seconds == 60
    assert settings.stop_soc_margin_percent == 0.0
    assert settings.max_consecutive_soc_failures == 1
    assert settings.reapply_if_soc_not_increasing is False
    assert settings.reapply_after_polls == 1
    assert settings.reapply_min_soc_delta_percent == 0.0
    assert settings.completion_confirm_before_minutes == 0

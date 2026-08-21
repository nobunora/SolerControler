"""Safe, reversible KP-NET settings probe helpers.

The probe deliberately validates the planned forced-charge SOC guard before it
touches the device, then makes one reversible setting change and restores the
complete controlled-settings snapshot.
"""

from __future__ import annotations

from dataclasses import replace
import time
from typing import Any, Mapping

from app.kpnet.client import KpNetClient
from app.kpnet.config import KpNetConfig
from app.kpnet.profile_builder import _build_payload
from app.kpnet.profiles import ProfileOverrides
from app.runtime.night_soc_controller import (
    CONTROLLED_SETTING_FIELDS,
    DeviceSocGuard,
    build_device_soc_guard,
    compare_setting_readback,
)


def profile_from_current_settings(current: Mapping[str, Any]) -> ProfileOverrides:
    """Make an exact restore profile from the provider's current setting values."""
    required = (*CONTROLLED_SETTING_FIELDS, "agreementAmpere")
    missing = [field for field in required if field not in current or str(current[field]).strip() == ""]
    if missing:
        raise RuntimeError(f"KP-NET current settings missing restore fields: {', '.join(missing)}")
    return ProfileOverrides(
        name="post-deploy-restore",
        battery_operating_mode=str(current["batteryOperatingMode"]),
        soc_safety_mode=str(current["socSafetyMode"]),
        soc_economy_mode=str(current["socEconomyMode"]),
        soc_contact_input=str(current["socContactInput"]),
        soc_charge_mode=str(current["socChargeMode"]),
        charge_start_h=str(current["chargeStartTimeH"]),
        charge_start_m=str(current["chargeStartTimeM"]),
        charge_end_h=str(current["chargeEndTimeH"]),
        charge_end_m=str(current["chargeEndTimeM"]),
        discharge_start_h=str(current["dischargeStartTimeH"]),
        discharge_start_m=str(current["dischargeStartTimeM"]),
        discharge_end_h=str(current["dischargeEndTimeH"]),
        discharge_end_m=str(current["dischargeEndTimeM"]),
        agreement_ampere=str(current["agreementAmpere"]),
        on_power_outage_mode=str(current.get("onPowerOutageMode", "0")),
        on_power_outage_charge_power_w=str(current.get("onPowerOutageChargePowerW", "65535")),
    )


def validate_forced_target(*, value_maps: Mapping[str, Mapping[str, str]], target_soc_percent: float) -> DeviceSocGuard:
    """Fail before mutation when the actual device has no safe upper guard."""
    return build_device_soc_guard(
        value_maps.get("SocChargeMode", {}),
        raw_target_soc_percent=target_soc_percent,
        stop_margin_percent=0.0,
    )


def make_reversible_probe_profile(
    *,
    restore_profile: ProfileOverrides,
    value_maps: Mapping[str, Mapping[str, str]],
) -> ProfileOverrides:
    """Change exactly one safe field; prefer standby mode during the brief probe."""
    modes = value_maps.get("BatteryOperatingMode", {})
    standby_code = next(
        (
            str(code)
            for code, label in modes.items()
            if "待機" in str(label) or "standby" in str(label).lower()
        ),
        "0" if "0" in modes else None,
    )
    if standby_code is not None and standby_code != restore_profile.battery_operating_mode:
        return replace(
            restore_profile,
            name="post-deploy-standby-probe",
            battery_operating_mode=standby_code,
        )

    candidates = [str(code) for code in value_maps.get("SocChargeMode", {})]
    alternate_soc_code = next((code for code in candidates if code != restore_profile.soc_charge_mode), None)
    if alternate_soc_code is None:
        raise RuntimeError("KP-NET has no reversible setting candidate for the post-deploy probe")
    return replace(
        restore_profile,
        name="post-deploy-soc-probe",
        soc_charge_mode=alternate_soc_code,
    )


def _apply_and_verify(
    *,
    client: KpNetClient,
    current: dict[str, Any],
    value_maps: dict[str, dict[str, str]],
    profile: ProfileOverrides,
) -> tuple[dict[str, Any], list[str]]:
    payload, changed_fields = _build_payload(
        csrf_setting=client.csrf_setting,
        pcsid=client.pcsid,
        current=current,
        overrides=profile,
        value_maps=value_maps,
    )
    if not changed_fields:
        raise RuntimeError(f"KP-NET round-trip profile made no setting change: {profile.name}")
    ok, title, error, confirm_html = client.confirm_setting(payload)
    if not ok:
        raise RuntimeError(f"KP-NET setting confirmation failed for {profile.name}: {error or title}")
    client.write_setting(confirm_html)
    readback = client.read_current_settings()
    matched, mismatches = compare_setting_readback(payload, readback, CONTROLLED_SETTING_FIELDS)
    if not matched:
        raise RuntimeError(f"KP-NET read-back mismatch for {profile.name}: {', '.join(mismatches)}")
    return readback, changed_fields


def run_settings_roundtrip(
    *,
    target_soc_percent: float,
    hold_seconds: int = 60,
) -> dict[str, object]:
    """Execute a live, one-minute setting probe and restore its exact snapshot.

    The caller must expose this only as an explicit test mode.  A target/candidate
    mismatch is reported after restoration, so the test proves both device writes
    and the separate forced-charge feasibility gate in one execution.
    """
    if hold_seconds != 60:
        raise ValueError("settings round-trip hold_seconds must be exactly 60")
    cfg = KpNetConfig.from_env()
    if cfg.dry_run:
        raise RuntimeError("settings round-trip test execution requires DRY_RUN=false")
    client = KpNetClient(cfg)
    summary: dict[str, object] = {
        "target_soc_percent": target_soc_percent,
        "hold_seconds": hold_seconds,
        "status": "failed",
    }
    current: dict[str, Any] | None = None
    restore_profile: ProfileOverrides | None = None
    restored_verified = False
    try:
        client.login()
        client.open_settings_page()
        current = client.read_current_settings()
        value_maps = client.collect_candidate_maps()
        try:
            guard = validate_forced_target(value_maps=value_maps, target_soc_percent=target_soc_percent)
            summary["forced_guard_ceiling_percent"] = guard.device_soc_ceiling_percent
            summary["forced_target_compatible"] = True
        except ValueError as guard_error:
            summary["forced_target_compatible"] = False
            summary["forced_target_error"] = str(guard_error)

        restore_profile = profile_from_current_settings(current)
        probe_profile = make_reversible_probe_profile(restore_profile=restore_profile, value_maps=value_maps)
        summary["probe_profile"] = probe_profile.name
        _, probe_changed = _apply_and_verify(
            client=client, current=current, value_maps=value_maps, profile=probe_profile
        )
        hold_started = time.monotonic()
        time.sleep(hold_seconds)
        elapsed = time.monotonic() - hold_started
        if elapsed < hold_seconds:
            time.sleep(hold_seconds - elapsed)
        restored, restore_changed = _apply_and_verify(
            client=client,
            current=client.read_current_settings(),
            value_maps=value_maps,
            profile=restore_profile,
        )
        snapshot_ok, snapshot_mismatches = compare_setting_readback(current, restored, CONTROLLED_SETTING_FIELDS)
        if not snapshot_ok:
            raise RuntimeError(f"KP-NET restore did not match initial snapshot: {', '.join(snapshot_mismatches)}")
        restored_verified = True
        summary.update({"probe_changed_fields": probe_changed, "restore_changed_fields": restore_changed})
        if not summary["forced_target_compatible"]:
            raise RuntimeError(f"forced-charge target is not device-representable: {summary['forced_target_error']}")
        summary["status"] = "passed"
        return summary
    finally:
        if not restored_verified and current is not None and restore_profile is not None:
            try:
                current_after_failure = client.read_current_settings()
                value_maps_after_failure = client.collect_candidate_maps()
                _apply_and_verify(
                    client=client,
                    current=current_after_failure,
                    value_maps=value_maps_after_failure,
                    profile=restore_profile,
                )
                summary["restore_after_failure"] = "passed"
            except Exception as restore_error:
                summary["restore_after_failure_error"] = type(restore_error).__name__
        try:
            client.logout()
        finally:
            summary.setdefault("status", "failed")

"""Reversible live verification of the KP-NET forced-charge command path."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import time
from typing import Any, Mapping

from app.kpnet.client import KpNetClient
from app.kpnet.config import KpNetConfig
from app.kpnet.profile_builder import _build_payload
from app.kpnet.profiles import ProfileOverrides
from app.kpnet.rules import _parse_hhmm
from app.runtime.night_soc_controller import (
    CONTROLLED_SETTING_FIELDS,
    DeviceSocGuard,
    build_device_soc_guard,
    compare_setting_readback,
)


ROUNDTRIP_SETTING_FIELDS: tuple[str, ...] = (*CONTROLLED_SETTING_FIELDS, "agreementAmpere")


class SettingsRoundtripError(RuntimeError):
    """Failure carrying the machine-readable round-trip audit summary."""

    def __init__(self, message: str, *, summary: dict[str, object]) -> None:
        super().__init__(message)
        self.summary = summary


def profile_from_current_settings(current: Mapping[str, Any]) -> ProfileOverrides:
    """Make an exact restore profile from the provider's current setting values."""
    required = ROUNDTRIP_SETTING_FIELDS
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
    """Map the test target to the actual device's forced-mode candidate."""
    return build_device_soc_guard(
        value_maps.get("SocChargeMode", {}),
        raw_target_soc_percent=target_soc_percent,
        stop_margin_percent=0.0,
    )


def make_reversible_probe_profile(
    *,
    restore_profile: ProfileOverrides,
    value_maps: Mapping[str, Mapping[str, str]],
    target_soc_percent: float,
    test_charge_start_hhmm: str | None = None,
    test_charge_end_hhmm: str | None = None,
) -> ProfileOverrides:
    """Build the required 50%-candidate forced-charge test profile.

    The test confirms the command path that matters for the 03 job: set the
    device's maximum supported SocChargeMode and enter forced charge.  It keeps
    every other controlled setting from the initial snapshot and restores that
    snapshot after exactly 60 seconds.
    """
    modes = value_maps.get("BatteryOperatingMode", {})
    forced_code = next(
        (
            str(code)
            for code, label in modes.items()
            if "強制充電" in str(label) or "forced charge" in str(label).lower()
        ),
        "3" if "3" in modes else None,
    )
    if forced_code is None:
        raise RuntimeError("KP-NET has no forced-charge operating-mode candidate for the post-deploy probe")
    guard = validate_forced_target(value_maps=value_maps, target_soc_percent=target_soc_percent)
    probe = replace(
        restore_profile,
        name="post-deploy-forced-charge-50-probe",
        battery_operating_mode=forced_code,
        soc_charge_mode=guard.device_soc_code,
    )
    if test_charge_start_hhmm is not None:
        start_h, start_m = _parse_hhmm(test_charge_start_hhmm, name="test_charge_start_hhmm")
        probe = replace(probe, charge_start_h=str(start_h), charge_start_m=str(start_m))
    if test_charge_end_hhmm is not None:
        end_h, end_m = _parse_hhmm(test_charge_end_hhmm, name="test_charge_end_hhmm")
        probe = replace(probe, charge_end_h=str(end_h), charge_end_m=str(end_m))
    return probe


def _apply_and_verify(
    *,
    client: KpNetClient,
    current: dict[str, Any],
    value_maps: dict[str, dict[str, str]],
    profile: ProfileOverrides,
    require_change: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    payload, changed_fields = _build_payload(
        csrf_setting=client.csrf_setting,
        pcsid=client.pcsid,
        current=current,
        overrides=profile,
        value_maps=value_maps,
    )
    if require_change and not changed_fields:
        raise RuntimeError(f"KP-NET round-trip profile made no setting change: {profile.name}")
    if changed_fields:
        ok, title, error, confirm_html = client.confirm_setting(payload)
        if not ok:
            raise RuntimeError(f"KP-NET setting confirmation failed for {profile.name}: {error or title}")
        client.write_setting(confirm_html)
    readback = client.read_current_settings()
    matched, mismatches = compare_setting_readback(payload, readback, ROUNDTRIP_SETTING_FIELDS)
    if not matched:
        raise RuntimeError(f"KP-NET read-back mismatch for {profile.name}: {', '.join(mismatches)}")
    return readback, changed_fields


# HISTORICAL_FAILURE_LOCK (ee84e43, bf48f42, 5e46ff8): this live probe must remain
# explicit, exactly 60 seconds, and restore/read back the original snapshot.
def run_settings_roundtrip(
    *,
    target_soc_percent: float,
    hold_seconds: int = 60,
    test_charge_start_hhmm: str | None = None,
    test_charge_end_hhmm: str | None = None,
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
        "test_charge_start_hhmm": test_charge_start_hhmm,
        "test_charge_end_hhmm": test_charge_end_hhmm,
        "status": "failed",
    }
    current: dict[str, Any] | None = None
    restore_profile: ProfileOverrides | None = None
    restored_verified = False
    try:
        client.login()
        client.open_settings_page()
        current = client.read_current_settings()
        snapshot_values = {field: str(current[field]) for field in ROUNDTRIP_SETTING_FIELDS}
        summary["snapshot_hash"] = hashlib.sha256(
            json.dumps(snapshot_values, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        summary["snapshot_field_count"] = len(ROUNDTRIP_SETTING_FIELDS)
        value_maps = client.collect_candidate_maps()
        guard = validate_forced_target(value_maps=value_maps, target_soc_percent=target_soc_percent)
        summary["forced_guard_ceiling_percent"] = guard.device_soc_ceiling_percent
        summary["forced_target_compatible"] = True

        restore_profile = profile_from_current_settings(current)
        probe_profile = make_reversible_probe_profile(
            restore_profile=restore_profile,
            value_maps=value_maps,
            target_soc_percent=target_soc_percent,
            test_charge_start_hhmm=test_charge_start_hhmm,
            test_charge_end_hhmm=test_charge_end_hhmm,
        )
        summary["probe_profile"] = probe_profile.name
        summary["probe_settings"] = {
            "battery_operating_mode": probe_profile.battery_operating_mode,
            "soc_charge_mode": probe_profile.soc_charge_mode,
            "charge_start_hhmm": f"{int(probe_profile.charge_start_h):02d}:{int(probe_profile.charge_start_m):02d}",
            "charge_end_hhmm": f"{int(probe_profile.charge_end_h):02d}:{int(probe_profile.charge_end_m):02d}",
        }
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
            require_change=False,
        )
        snapshot_ok, snapshot_mismatches = compare_setting_readback(current, restored, ROUNDTRIP_SETTING_FIELDS)
        if not snapshot_ok:
            raise RuntimeError(f"KP-NET restore did not match initial snapshot: {', '.join(snapshot_mismatches)}")
        restored_verified = True
        summary.update(
            {
                "probe_changed_fields": probe_changed,
                "restore_changed_fields": restore_changed,
                "restore_verified": True,
            }
        )
        summary["status"] = "passed"
        return summary
    except Exception as exc:
        summary["error_type"] = type(exc).__name__
        raise SettingsRoundtripError(str(exc), summary=summary) from exc
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
                    require_change=False,
                )
                summary["restore_after_failure"] = "passed"
            except Exception as restore_error:
                summary["restore_after_failure_error"] = type(restore_error).__name__
        try:
            client.logout()
        finally:
            summary.setdefault("status", "failed")
            print(f"[settings_roundtrip] {json.dumps(summary, ensure_ascii=False, sort_keys=True)}", flush=True)

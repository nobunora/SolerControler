"""Reversible live verification of the scheduled 03 forced and 07 green paths."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
import time
from typing import Any, Mapping

from app.kpnet.client import KpNetClient, KpNetUnknownWriteError
from app.kpnet.config import KpNetConfig
from app.kpnet.profile_builder import (
    _build_payload,
    _pick_battery_operating_mode_code,
)
from app.kpnet.profiles import ProfileOverrides
from app.runtime.night_soc_controller import (
    CONTROLLED_SETTING_FIELDS,
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


def _sparse_candidate_maps(
    *,
    battery_operating_mode: Mapping[str, str],
    soc_economy_mode: Mapping[str, str] | None = None,
) -> dict[str, dict[str, str]]:
    """Build sparse candidate maps while preserving current names for unchanged fields."""
    return {
        "BatteryOperatingMode": dict(battery_operating_mode),
        "SocSafetyMode": {},
        "SocEconomyMode": dict(soc_economy_mode or {}),
        "SocContactInput": {},
        "SocChargeMode": {},
        "OnPowerOutageChargePowerW": {},
        "AgreementAmpere": {},
    }


def _forced_probe_candidate_maps(client: KpNetClient) -> dict[str, dict[str, str]]:
    """Fetch exactly the candidate list required by scheduled 03 forced mode."""
    return _sparse_candidate_maps(
        battery_operating_mode=client.candidate_map(
            "BatteryOperatingMode",
            "remotesetting/pcssetting/valueList/batteryoperatingmode",
        )
    )


def _green_probe_candidate_maps(client: KpNetClient) -> dict[str, dict[str, str]]:
    """Fetch exactly the candidate list required by scheduled 07 green mode."""
    return _sparse_candidate_maps(
        battery_operating_mode=client.candidate_map(
            "BatteryOperatingMode",
            "remotesetting/pcssetting/valueList/batteryoperatingmode",
        ),
    )


def make_forced_probe_profile(
    *,
    current_profile: ProfileOverrides,
    value_maps: Mapping[str, dict[str, str]],
) -> ProfileOverrides:
    """Build the scheduled-03-equivalent probe: operating mode only."""
    return replace(
        current_profile,
        name="post-deploy-03-forced-probe",
        battery_operating_mode=_pick_battery_operating_mode_code(
            value_maps["BatteryOperatingMode"],
            prefer="forced",
        ),
    )


def make_green_probe_profile(
    *,
    current_profile: ProfileOverrides,
    value_maps: Mapping[str, dict[str, str]],
) -> ProfileOverrides:
    """Build the scheduled-07-equivalent probe while preserving other settings."""
    return replace(
        current_profile,
        name="post-deploy-07-green-probe",
        battery_operating_mode=_pick_battery_operating_mode_code(
            value_maps["BatteryOperatingMode"],
            prefer="green",
        ),
    )


def _assert_preserved_fields(
    *,
    baseline: Mapping[str, Any],
    observed: Mapping[str, Any],
    allowed_changes: set[str],
    phase: str,
) -> None:
    mismatches = [
        field
        for field in ROUNDTRIP_SETTING_FIELDS
        if field not in allowed_changes
        and str(baseline.get(field, "")) != str(observed.get(field, ""))
    ]
    if mismatches:
        raise RuntimeError(
            f"KP-NET {phase} changed unrelated writable fields: {', '.join(mismatches)}"
        )

def _apply_and_verify(
    *,
    client: KpNetClient,
    current: dict[str, Any],
    value_maps: dict[str, dict[str, str]],
    profile: ProfileOverrides,
    require_change: bool = True,
    required_readback_fields: tuple[str, ...] | None = None,
) -> tuple[dict[str, Any], list[str], dict[str, str]]:
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
    readback_fields = required_readback_fields or tuple(changed_fields)
    matched, mismatches = compare_setting_readback(payload, readback, readback_fields)
    if not matched:
        raise RuntimeError(f"KP-NET read-back mismatch for {profile.name}: {', '.join(mismatches)}")
    return readback, changed_fields, payload

def _read_only_snapshot_after_unknown(
    *,
    client: KpNetClient,
    initial: dict[str, Any],
    summary: dict[str, object],
) -> None:
    """Collect evidence after UNKNOWN_WRITE without crossing a second mutation boundary."""
    summary["post_failure_readback"] = "attempted_read_only_unknown_write"
    try:
        observed = client.read_current_settings()
        matches, mismatches = compare_setting_readback(initial, observed, ROUNDTRIP_SETTING_FIELDS)
        summary["post_failure_snapshot_matches_initial"] = matches
        summary["post_failure_snapshot_mismatches"] = list(mismatches)
    except Exception as exc:
        summary["post_failure_readback"] = "failed_read_only_unknown_write"
        summary["post_failure_readback_error"] = type(exc).__name__
    # A mismatch immediately after an uncertain mutation is not proof of either
    # acceptance or rejection because provider/device consistency latency is not
    # bounded. Never issue a cleanup SET from this state.
    summary["restore_after_failure"] = "suppressed_unknown_write"


# HISTORICAL_FAILURE_LOCK (ee84e43, bf48f42, 5e46ff8): this live probe must remain
# explicit, exactly 60 seconds, and restore/read back the original snapshot.
def run_settings_roundtrip(
    *,
    target_soc_percent: float = 50.0,
    hold_seconds: int = 60,
    test_charge_start_hhmm: str | None = None,
    test_charge_end_hhmm: str | None = None,
) -> dict[str, object]:
    """Prove real 03 forced and 07 green writes, then restore the exact snapshot.

    The legacy target/window arguments remain accepted for entrypoint compatibility
    only. They are intentionally not mapped into the device settings because the
    approved scheduled 03 forced contract changes BatteryOperatingMode only.
    """
    if hold_seconds != 60:
        raise ValueError("settings round-trip hold_seconds must be exactly 60")
    if test_charge_start_hhmm is not None or test_charge_end_hhmm is not None:
        raise ValueError("dual-profile release probe must not alter charge/discharge windows")

    cfg = KpNetConfig.from_env()
    if cfg.dry_run:
        raise RuntimeError("settings round-trip test execution requires DRY_RUN=false")
    client = KpNetClient(cfg)
    summary: dict[str, object] = {
        "target_soc_percent_compatibility_only": target_soc_percent,
        "hold_seconds": hold_seconds,
        "out_of_window_authorized": os.getenv(
            "LIVE_PROBE_OUT_OF_WINDOW_AUTHORIZED", ""
        ).strip().lower() in {"1", "true", "yes", "on"},
        "status": "failed",
        "forced_proof": "not_started",
        "green_proof": "not_started",
    }
    current: dict[str, Any] | None = None
    restore_profile: ProfileOverrides | None = None
    restore_maps: dict[str, dict[str, str]] | None = None
    restored_verified = False
    unknown_write = False
    phase = "initial_read"
    try:
        client.login()
        client.open_settings_page()
        current = client.read_current_settings()
        snapshot_values = {field: str(current[field]) for field in ROUNDTRIP_SETTING_FIELDS}
        summary["snapshot_hash"] = hashlib.sha256(
            json.dumps(snapshot_values, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        summary["snapshot_field_count"] = len(ROUNDTRIP_SETTING_FIELDS)
        restore_profile = profile_from_current_settings(current)

        phase = "forced_candidate_fetch"
        forced_maps = _forced_probe_candidate_maps(client)
        summary["forced_candidate_maps_fetched"] = ["BatteryOperatingMode"]
        forced_profile = make_forced_probe_profile(
            current_profile=restore_profile,
            value_maps=forced_maps,
        )
        phase = "forced_write"
        forced_readback, forced_changed, forced_payload = _apply_and_verify(
            client=client,
            current=current,
            value_maps=forced_maps,
            profile=forced_profile,
            required_readback_fields=("batteryOperatingMode",),
        )
        _assert_preserved_fields(
            baseline=current,
            observed=forced_readback,
            allowed_changes={"batteryOperatingMode"},
            phase="03 forced proof",
        )
        summary["forced_proof"] = "passed"
        summary["forced_changed_fields"] = forced_changed
        summary["forced_readback_fields"] = ["batteryOperatingMode"]
        summary["forced_requested"] = {
            "batteryOperatingMode": forced_payload["batteryOperatingMode"],
        }
        summary["forced_observed"] = {
            "batteryOperatingMode": str(forced_readback.get("batteryOperatingMode", "")),
        }
        summary["forced_operation_id"] = getattr(client, "operation_id", None)

        phase = "hold"
        hold_started = time.monotonic()
        time.sleep(hold_seconds)
        elapsed = time.monotonic() - hold_started
        if elapsed < hold_seconds:
            time.sleep(hold_seconds - elapsed)

        phase = "green_candidate_fetch"
        green_maps = _green_probe_candidate_maps(client)
        restore_maps = green_maps
        summary["green_candidate_maps_fetched"] = ["BatteryOperatingMode"]
        green_base = profile_from_current_settings(forced_readback)
        green_profile = make_green_probe_profile(
            current_profile=green_base,
            value_maps=green_maps,
        )
        phase = "green_write"
        green_readback, green_changed, green_payload = _apply_and_verify(
            client=client,
            current=forced_readback,
            value_maps=green_maps,
            profile=green_profile,
            required_readback_fields=("batteryOperatingMode",),
        )
        _assert_preserved_fields(
            baseline=current,
            observed=green_readback,
            allowed_changes={"batteryOperatingMode"},
            phase="07 green proof",
        )
        summary["green_proof"] = "passed"
        summary["green_changed_fields"] = green_changed
        summary["green_readback_fields"] = ["batteryOperatingMode"]
        summary["green_requested"] = {"batteryOperatingMode": green_payload["batteryOperatingMode"]}
        summary["green_observed"] = {"batteryOperatingMode": str(green_readback.get("batteryOperatingMode", ""))}
        summary["green_operation_id"] = getattr(client, "operation_id", None)

        phase = "restore_write"
        restored, restore_changed, _ = _apply_and_verify(
            client=client,
            current=green_readback,
            value_maps=green_maps,
            profile=restore_profile,
            require_change=False,
        )
        phase = "restore_readback"
        snapshot_ok, snapshot_mismatches = compare_setting_readback(
            current, restored, ROUNDTRIP_SETTING_FIELDS
        )
        if not snapshot_ok:
            raise RuntimeError(
                f"KP-NET restore did not match initial snapshot: {', '.join(snapshot_mismatches)}"
            )
        restored_verified = True
        summary.update(
            {
                "restore_changed_fields": restore_changed,
                "restore_verified": True,
                "status": "passed",
            }
        )
        return summary
    except KpNetUnknownWriteError as exc:
        unknown_write = True
        summary["error_type"] = type(exc).__name__
        summary["failed_phase"] = phase
        summary["mutation_outcome"] = "unknown"
        raise SettingsRoundtripError(str(exc), summary=summary) from exc
    except Exception as exc:
        summary["error_type"] = type(exc).__name__
        summary["failed_phase"] = phase
        raise SettingsRoundtripError(str(exc), summary=summary) from exc
    finally:
        if not restored_verified and current is not None and restore_profile is not None:
            if unknown_write:
                _read_only_snapshot_after_unknown(client=client, initial=current, summary=summary)
            else:
                try:
                    summary["post_failure_readback"] = "attempted"
                    current_after_failure = client.read_current_settings()
                    if restore_maps is None:
                        restore_maps = _green_probe_candidate_maps(client)
                    snapshot_ok, snapshot_mismatches = compare_setting_readback(
                        current, current_after_failure, ROUNDTRIP_SETTING_FIELDS
                    )
                    summary["post_failure_snapshot_matches_initial"] = snapshot_ok
                    summary["post_failure_snapshot_mismatches"] = list(snapshot_mismatches)
                    if snapshot_ok:
                        summary["restore_after_failure"] = "not_needed_snapshot_matches"
                    else:
                        phase = "post_failure_restore_write"
                        restored_after_failure, _, _ = _apply_and_verify(
                            client=client,
                            current=current_after_failure,
                            value_maps=restore_maps,
                            profile=restore_profile,
                            require_change=False,
                        )
                        restored_ok, restored_mismatches = compare_setting_readback(
                            current, restored_after_failure, ROUNDTRIP_SETTING_FIELDS
                        )
                        if not restored_ok:
                            raise RuntimeError(
                                "KP-NET failure cleanup restore did not match initial snapshot: "
                                + ", ".join(restored_mismatches)
                            )
                        summary["restore_after_failure"] = "passed"
                        summary["restore_after_failure_mismatches"] = list(restored_mismatches)
                except KpNetUnknownWriteError as restore_unknown:
                    summary["restore_after_failure"] = "unknown_write"
                    summary["restore_after_failure_error"] = type(restore_unknown).__name__
                    _read_only_snapshot_after_unknown(client=client, initial=current, summary=summary)
                except Exception as restore_error:
                    summary["restore_after_failure_error"] = type(restore_error).__name__
        try:
            close_client = getattr(client, "close", client.logout)
            close_client()
            summary["logout_outcome"] = "local_close"
        except Exception as close_error:
            summary["logout_outcome"] = "failed"
            summary["logout_exception_type"] = type(close_error).__name__
        finally:
            summary.setdefault("status", "failed")
            print(f"[settings_roundtrip] {json.dumps(summary, ensure_ascii=False, sort_keys=True)}", flush=True)

"""Explicit daytime SOC +1 point acceptance probe for the deployed control image.

Run through run_cloud_job_from_env.ps1 -Slot settings-roundtrip -TestExecution
-SocCycleProbe. This diagnostic does not replace the protected 03 controller or
change its clock, canonical plan, SOC settings, or fixed 60-second release probe.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
import math
import os
import time
from typing import Any
from zoneinfo import ZoneInfo

from app.kpnet.client import KpNetClient, KpNetUnknownWriteError
from app.kpnet.config import KpNetConfig
from app.kpnet.profile_builder import _pick_battery_operating_mode_code
from app.kpnet.profiles import ProfileOverrides
from app.kpnet.realtime_soc_parser import extract_realtime_soc_percent_resilient
from app.kpnet.settings_roundtrip import (
    ROUNDTRIP_SETTING_FIELDS,
    _apply_and_verify,
    _assert_preserved_fields,
    _forced_probe_candidate_maps,
    _read_only_snapshot_after_unknown,
    make_forced_probe_profile,
    profile_from_current_settings,
)
from app.runtime.night_soc_controller import compare_setting_readback
from app.separated_runtime.control import _validate_current_plan
from app.separated_runtime.plan_store import FirestorePlanStore


MAX_MONITOR_SECONDS = 600
RESTORE_RESERVE_SECONDS = 240
POLL_SECONDS = 20


def _valid_soc(value: object) -> float | None:
    if not isinstance(value, (int, float, str)) or isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) and 0 <= numeric <= 100 else None


def run_soc_cycle_probe() -> dict[str, object]:
    if os.getenv("SOC_CYCLE_PROBE_AUTHORIZED") != "true":
        raise RuntimeError("SOC cycle probe requires explicit one-shot authorization")
    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    finish = now + timedelta(seconds=MAX_MONITOR_SECONDS + RESTORE_RESERVE_SECONDS)
    if not (7 * 60 + 15 <= now.hour * 60 + now.minute and finish.hour * 60 + finish.minute < 22 * 60 + 30):
        raise RuntimeError("SOC probe and restoration must fit the daytime probe window")
    cfg = KpNetConfig.from_env()
    if cfg.dry_run:
        raise RuntimeError("SOC cycle acceptance requires a live execution")
    started = time.monotonic()
    cutoff = started + MAX_MONITOR_SECONDS
    plan = _validate_current_plan(FirestorePlanStore().fetch(now.date().isoformat()), now, require_evidence=True)
    client = KpNetClient(cfg, deadline_monotonic=cutoff + RESTORE_RESERVE_SECONDS)
    summary: dict[str, object] = {
        "message": "soc-cycle-probe", "status": "failed", "scope": "daytime_physical_cycle",
        "producer_source_revision": plan.producer_source_revision,
        "canonical_plan_sha256": hashlib.sha256(plan.raw_json.encode("utf-8")).hexdigest(),
        "max_monitor_seconds": MAX_MONITOR_SECONDS, "restore_verified": False,
        "forced_readback_verified": False, "standby_readback_verified": False,
        "soc_target_settings_changed": False, "samples": [],
    }
    current: dict[str, Any] | None = None
    restore_profile: ProfileOverrides | None = None
    maps: dict[str, dict[str, str]] | None = None
    mutated = False
    restored = False
    unknown = False
    standby_verified = False
    phase = "initial_read"
    temporary_window = os.getenv("SOC_CYCLE_PROBE_TEMPORARY_WINDOW") == "true"
    allowed_changes = {"batteryOperatingMode"}
    if temporary_window:
        allowed_changes.update({"chargeStartTimeH", "chargeStartTimeM", "chargeEndTimeH", "chargeEndTimeM"})

    def read_soc() -> float | None:
        # Use the deployed direct/semantic parsers and a bounded read request;
        # reserve the full restoration budget even if SOC requests time out.
        previous_deadline = client.deadline_monotonic
        if previous_deadline is None:
            raise RuntimeError("SOC probe requires a bounded device deadline")
        client.deadline_monotonic = min(previous_deadline, cutoff, time.monotonic() + 45)
        try:
            if time.monotonic() >= client.deadline_monotonic:
                return None
            value = _valid_soc(client.read_realtime_soc_percent())
            if value is None and time.monotonic() < client.deadline_monotonic:
                response = client._get("remotevisualization/simplevisualization/enduser", stage="soc-cycle-semantic-fallback")
                value = _valid_soc(extract_realtime_soc_percent_resilient(response.text))
            return value
        except Exception:
            return None
        finally:
            client.deadline_monotonic = previous_deadline

    def emit_sample(value: float | None) -> None:
        sample = {"elapsed_seconds": round(time.monotonic() - started, 2), "soc_percent": value}
        samples = summary["samples"]
        if isinstance(samples, list):
            samples.append(sample)
        print(json.dumps({"message": "soc-cycle-sample", **sample}, sort_keys=True), flush=True)

    def stop_and_verify() -> None:
        nonlocal standby_verified
        if current is None or maps is None:
            raise RuntimeError("probe stop requires the initial settings and candidate map")
        observed = client.read_current_settings()
        base = profile_from_current_settings(observed)
        stop = replace(base, name="one-percent-probe-stop", battery_operating_mode=
                       _pick_battery_operating_mode_code(maps["BatteryOperatingMode"], prefer="standby"))
        stopped, changed, _ = _apply_and_verify(client=client, current=observed, value_maps=maps,
                                               profile=stop, require_change=False,
                                               required_readback_fields=("batteryOperatingMode",))
        _assert_preserved_fields(baseline=current, observed=stopped, allowed_changes=allowed_changes, phase="probe stop")
        if any(field != "batteryOperatingMode" for field in changed):
            raise RuntimeError("probe stop changed unrelated settings")
        standby_verified = True
        summary["standby_readback_verified"] = True
        summary["standby_observed_mode"] = str(stopped["batteryOperatingMode"])

    try:
        client.login()
        client.open_settings_page()
        current = client.read_current_settings()
        restore_profile = profile_from_current_settings(current)
        summary["initial_charge_window"] = {field: str(current[field]) for field in
                                            ("chargeStartTimeH", "chargeStartTimeM", "chargeEndTimeH", "chargeEndTimeM")}
        maps = _forced_probe_candidate_maps(client)
        forced = make_forced_probe_profile(current_profile=restore_profile, value_maps=maps)
        if str(current["batteryOperatingMode"]) == forced.battery_operating_mode:
            raise RuntimeError("refusing to interrupt an existing forced-charge operation")
        phase = "initial_soc"
        baseline = read_soc()
        if baseline is None:
            raise RuntimeError("initial realtime SOC unavailable; no settings mutation")
        if baseline > 99:
            raise RuntimeError("insufficient headroom for SOC +1 point")
        target = baseline + 1
        summary.update({"initial_soc_percent": baseline, "target_soc_percent": target})
        emit_sample(baseline)
        if temporary_window:
            # Explicit diagnostic-only exception: the mode-only production owner
            # and fixed 60-second probe stay unchanged. A daylight physical cycle
            # needs an active device charging window, which is restored afterward.
            window_now = datetime.now(ZoneInfo("Asia/Tokyo"))
            forced = replace(forced, charge_start_h=str(window_now.hour), charge_start_m="0",
                             charge_end_h=str(window_now.hour + 1), charge_end_m="0")
            summary["temporary_charge_window"] = {"start_hour": window_now.hour, "end_hour": window_now.hour + 1}
        if time.monotonic() >= cutoff - 120:
            raise RuntimeError("not enough bounded probe budget to begin charging")
        phase = "forced_write"
        # Mark before sending: a known rejection can be reconciled; UNKNOWN must
        # suppress every subsequent mutation, including automatic restoration.
        mutated = True
        forced_readback, changed, _ = _apply_and_verify(client=client, current=current, value_maps=maps,
                                                       profile=forced, required_readback_fields=("batteryOperatingMode",))
        _assert_preserved_fields(baseline=current, observed=forced_readback,
                                allowed_changes=allowed_changes, phase="probe forced")
        summary.update({"forced_readback_verified": True, "forced_changed_fields": changed,
                        "forced_observed_mode": str(forced_readback["batteryOperatingMode"])})
        phase = "soc_monitor"
        failures = 0
        reached = False
        while time.monotonic() < cutoff:
            soc = read_soc()
            emit_sample(soc)
            if soc is None:
                failures += 1
                if failures >= 3:
                    summary["stop_reason"] = "soc_unavailable"
                    break
            else:
                failures = 0
                summary["last_soc_percent"] = soc
                if soc >= target:
                    reached = True
                    summary["soc_increase_percent"] = soc - baseline
                    summary["stop_reason"] = "target_reached"
                    break
            time.sleep(min(POLL_SECONDS, max(0, cutoff - time.monotonic())))
        if not reached and "stop_reason" not in summary:
            summary["stop_reason"] = "probe_timeout"
        phase = "standby_write"
        stop_and_verify()
        phase = "restore_write"
        observed = client.read_current_settings()
        restored_values, _, _ = _apply_and_verify(client=client, current=observed, value_maps=maps,
                                                 profile=restore_profile, require_change=False)
        restored, mismatches = compare_setting_readback(current, restored_values, ROUNDTRIP_SETTING_FIELDS)
        if not restored:
            raise RuntimeError(f"probe restoration mismatch: {','.join(mismatches)}")
        summary["restore_verified"] = True
        summary["status"] = "passed" if reached else "inconclusive"
    except KpNetUnknownWriteError as exc:
        unknown = True
        summary.update({"error_type": type(exc).__name__, "failed_phase": phase, "mutation_outcome": "unknown"})
    except Exception as exc:
        summary.update({"error_type": type(exc).__name__, "failed_phase": phase})
    finally:
        if mutated and not restored and current is not None and maps is not None and restore_profile is not None:
            if unknown:
                _read_only_snapshot_after_unknown(client=client, initial=current, summary=summary)
            else:
                try:
                    if not standby_verified:
                        stop_and_verify()
                    observed = client.read_current_settings()
                    restored_values, _, _ = _apply_and_verify(client=client, current=observed, value_maps=maps,
                                                             profile=restore_profile, require_change=False)
                    restored, _ = compare_setting_readback(current, restored_values, ROUNDTRIP_SETTING_FIELDS)
                    summary["restore_verified"] = restored
                except Exception as exc:
                    summary["restore_error_type"] = type(exc).__name__
        try:
            client.logout()
        except Exception:
            pass
        summary["elapsed_seconds"] = round(time.monotonic() - started, 2)
        print(json.dumps(summary, sort_keys=True), flush=True)
    return summary


if __name__ == "__main__":
    raise SystemExit(0 if run_soc_cycle_probe()["status"] == "passed" else 1)

from __future__ import annotations

import csv
import json
import logging
import math
import os
import statistics
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict
from zoneinfo import ZoneInfo

import matplotlib

from app.domain.constants import SOCBounds, validate_soc_percent
from app.kpnet import build_settings_intent
from app.kpnet.monitoring_history import iter_charge_soc_points
from app.kpnet.rules import (
    NightWindowContract,
    _in_time_window,
    _is_night_window_now,
    _minutes_to_hm,
    _night_window_contract,
    _now_in_timezone,
    _parse_hhmm,
)
from app.kpnet.csv_visualization import (
    _default_csv_target_months,
    _month_key,
    _parse_csv_points,
    _plot_csvs,
    _resolve_months,
)
from app.kpnet.client_support import validate_base_url as _validate_base_url
from app.kpnet.config import LOGGER, KpNetConfig
from app.kpnet.profile_builder import (
    _apply_fixed_time_rules,
    _build_dynamic_forced_profile,
    _build_dynamic_green_profile,
    _build_payload,
    _candidate_int_values,
    _enabled_sorted_rules,
    _estimate_charge_power_kw,
    _estimate_charge_soc_rate_percent_per_hour,
    _extract_simple_visualization_soc_percent,
    _load_operation_conditions,
    _pick_battery_operating_mode_code,
    _pick_ceil_code,
    _pick_max_code,
    _pick_min_code,
    _pick_night_mode_preference,
    _required_charge_percent,
    _resolve_day_discharge_start_hhmm,
    _resolve_hhmm,
    _resolve_night_charge_end_hhmm,
    _variable_rule,
)
from app.kpnet.plan import NightChargePlan, load_night_charge_plan
from app.kpnet.profiles import FORCED_CHARGE_PROFILE, GREEN_MODE_PROFILE, STANDBY_PROFILE, ProfileOverrides
from app.configuration.environment import load_dotenv_if_present
from app.parsing.numbers import parse_csv_float, to_float

__all__ = ["KpNetConfig", "run_kpnet_workflow", "main"]

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

# readable-code-audit: skip STRUCT-04 — profile fields are resolved together so the KP-NET command cannot mix settings from different rule versions
from app.kpnet.client import KpNetClient

_load_night_charge_plan = load_night_charge_plan


def _run_csv_phase(
    client: KpNetClient,
    cfg: KpNetConfig,
    run_dir: Path,
    summary: dict[str, Any],
) -> None:
    csv_dir = run_dir / "csv"
    plot_path = run_dir / "kpi_plot.png"

    available_months, pcsclass = client.open_csv_measure_page()
    target_months = _resolve_months(
        requested=cfg.csv_target_months,
        available=available_months,
        include_latest=cfg.download_latest_month,
    )
    LOGGER.info("Available months: %s", available_months)
    LOGGER.info("Target months: %s", target_months)

    csv_paths: list[Path] = []
    for month in target_months:
        csv_path = client.download_csv(month=month, pcsclass=pcsclass, out_dir=csv_dir)
        csv_paths.append(csv_path)
        summary["csv_downloads"].append({"month": month, "path": str(csv_path)})

    summary["plot"] = _plot_csvs(csv_paths, plot_path)
    LOGGER.info("Plot generated: %s", plot_path)


# readable-code-audit: skip STRUCT-04 — profile command, confirmation, and summary use one device-operation result boundary
def _apply_settings_profile(
    *,
    client: KpNetClient,
    cfg: KpNetConfig,
    run_dir: Path,
    summary: dict[str, Any],
    current: dict[str, Any],
    value_maps: dict[str, dict[str, str]],
    profile: ProfileOverrides,
) -> dict[str, Any]:
    payload, changed_fields = _build_payload(
        csrf_setting=client.csrf_setting,
        pcsid=client.pcsid,
        current=current,
        overrides=profile,
        value_maps=value_maps,
    )
    intent = build_settings_intent(
        profile_name=profile.name,
        current_values=current,
        desired_values=payload,
        changed_fields=changed_fields,
        dry_run=cfg.dry_run,
    )
    payload = dict(intent.desired_values)
    changed_fields = [change.field for change in intent.expected_changes]
    if not intent.has_changes:
        summary["setting_results"].append(
            {
                "profile": profile.name,
                "changed_fields": [],
                "status": "skipped-no-change",
            }
        )
        return current

    ok, title, err, confirm_html = client.confirm_setting(payload)
    confirm_path = run_dir / f"confirm_{profile.name}.html"
    confirm_path.write_text(confirm_html, encoding="utf-8")

    if not ok:
        summary["setting_results"].append(
            {
                "profile": profile.name,
                "changed_fields": changed_fields,
                "status": "confirm-failed",
                "title": title,
                "error": err,
                "confirm_path": str(confirm_path),
            }
        )
        raise RuntimeError(f"KP-NET setting confirmation failed for profile={profile.name}: {err or title}")

    if intent.dry_run:
        summary["setting_results"].append(
            {
                "profile": profile.name,
                "changed_fields": changed_fields,
                "status": "dry-run-confirmed",
                "title": title,
                "confirm_path": str(confirm_path),
            }
        )
        return current

    write_result = client.write_setting(confirm_html)
    summary["setting_results"].append(
        {
            "profile": profile.name,
            "changed_fields": changed_fields,
            "status": "applied",
            "write_result": write_result,
            "confirm_path": str(confirm_path),
        }
    )
    return client.read_current_settings()


# readable-code-audit: skip STRUCT-04 — command execution, confirmation, and durable result recording form one device-operation boundary and must retain their failure order
def _run_settings_phase(
    client: KpNetClient,
    cfg: KpNetConfig,
    run_dir: Path,
    summary: dict[str, Any],
) -> None:
    client.open_settings_page()
    current = client.read_current_settings()
    maps = client.collect_candidate_maps()
    conditions = _load_operation_conditions(cfg.operation_conditions_path)
    summary["operation_conditions"] = {
        "source": str(cfg.operation_conditions_path),
        "fixed": [
            {
                "id": str(rule.get("id", "")),
                "priority": int(rule.get("priority", 0)),
                "target": str(rule.get("target", "all")),
            }
            for rule in _enabled_sorted_rules(conditions, "fixed")
        ],
        "variable": [
            {
                "id": str(rule.get("id", "")),
                "priority": int(rule.get("priority", 0)),
            }
            for rule in _enabled_sorted_rules(conditions, "variable")
        ],
    }

    if cfg.dynamic_forced_profile:
        forced_profile = _build_dynamic_forced_profile(cfg=cfg, value_maps=maps, summary=summary)
    else:
        mode_preference = "green"
        required_charge_percent = None
        force_charge_mode = False
        try:
            plan = _load_night_charge_plan(cfg.night_plan_path)
            mode_preference, required_charge_percent, force_charge_mode = _pick_night_mode_preference(
                plan=plan,
                green_mode_max_charge_percent=cfg.green_mode_max_charge_percent,
            )
        except Exception as exc:
            LOGGER.warning("Night charge plan unavailable while selecting legacy forced profile mode: %s", exc)

        forced_profile = replace(
            FORCED_CHARGE_PROFILE,
            battery_operating_mode=_pick_battery_operating_mode_code(
                maps["BatteryOperatingMode"],
                prefer=mode_preference,
            ),
        )
        summary["night_charge_plan"] = {
            "status": "dynamic-profile-disabled",
            "legacy_mode_preference": mode_preference,
            "required_charge_percent": required_charge_percent,
            "green_mode_max_charge_percent": cfg.green_mode_max_charge_percent,
            "force_charge_mode": force_charge_mode,
        }

    if cfg.dynamic_forced_profile:
        green_profile = _build_dynamic_green_profile(
            cfg=cfg,
            value_maps=maps,
            forced_profile=forced_profile,
            summary=summary,
        )
    else:
        green_profile = GREEN_MODE_PROFILE
    profiles: tuple[ProfileOverrides, ...]
    if cfg.force_settings_profile == "forced":
        profiles = (forced_profile,)
        summary["time_based_mode_selection"] = {
            "enabled": False,
            "forced_profile": forced_profile.name,
            "selected_profile": forced_profile.name,
        }
        LOGGER.info("Forced settings profile selected: %s", forced_profile.name)
    elif cfg.force_settings_profile == "green":
        profiles = (green_profile,)
        summary["time_based_mode_selection"] = {
            "enabled": False,
            "forced_profile": "green-mode",
            "selected_profile": "green-mode",
        }
        LOGGER.info("Forced settings profile selected: green-mode")
    elif cfg.force_settings_profile == "standby":
        standby_profile = replace(
            STANDBY_PROFILE,
            battery_operating_mode=_pick_battery_operating_mode_code(
                maps["BatteryOperatingMode"],
                prefer="standby",
            ),
        )
        profiles = (standby_profile,)
        summary["time_based_mode_selection"] = {
            "enabled": False,
            "forced_profile": "standby-mode",
            "selected_profile": "standby-mode",
        }
        LOGGER.info("Forced settings profile selected: standby-mode")
    elif cfg.dynamic_mode_switch_by_time:
        night_window_start = _parse_hhmm(cfg.night_charge_window_start, name="KP_NIGHT_CHARGE_WINDOW_START")
        night_window_end = _parse_hhmm(cfg.night_charge_window_end, name="KP_NIGHT_CHARGE_WINDOW_END")
        is_night = _is_night_window_now(
            timezone_name=cfg.timezone_name,
            night_window_start=night_window_start,
            night_window_end=night_window_end,
        )
        current_phase = "night" if is_night else "day"
        profiles = (forced_profile,) if is_night else (green_profile,)
        summary["time_based_mode_selection"] = {
            "enabled": True,
            "timezone": cfg.timezone_name,
            "phase": current_phase,
            "selected_profile": profiles[0].name,
        }
        LOGGER.info("Time-based mode switch phase=%s timezone=%s profile=%s", current_phase, cfg.timezone_name, profiles[0].name)
    elif cfg.settings_sequence == "forced-only":
        profiles = (forced_profile,)
    else:
        profiles = (forced_profile, green_profile)

    LOGGER.info(
        "Settings sequence: %s force_settings_profile=%s dynamic_forced_profile=%s dynamic_mode_switch_by_time=%s",
        cfg.settings_sequence,
        cfg.force_settings_profile,
        cfg.dynamic_forced_profile,
        cfg.dynamic_mode_switch_by_time,
    )

    for profile in profiles:
        current = _apply_settings_profile(
            client=client,
            cfg=cfg,
            run_dir=run_dir,
            summary=summary,
            current=current,
            value_maps=maps,
            profile=profile,
        )


def run_kpnet_workflow() -> int:
    load_dotenv_if_present()
    _setup_logging()
    cfg = KpNetConfig.from_env()

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = cfg.artifacts_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    client = KpNetClient(cfg)

    summary: dict[str, Any] = {
        "run_id": run_id,
        "workflow_mode": cfg.workflow_mode,
        "settings_sequence": cfg.settings_sequence,
        "dry_run": cfg.dry_run,
        "csv_downloads": [],
        "setting_results": [],
        "plot": {},
    }

    try:
        client.login()
        if cfg.workflow_mode in {"all", "csv"}:
            _run_csv_phase(client=client, cfg=cfg, run_dir=run_dir, summary=summary)
        if cfg.workflow_mode in {"all", "settings"}:
            _run_settings_phase(client=client, cfg=cfg, run_dir=run_dir, summary=summary)

        return_code = 0
    except Exception as exc:
        LOGGER.exception("KP-NET workflow failed")
        summary["error"] = str(exc)
        return_code = 1
    finally:
        try:
            client.logout()
        except Exception:
            LOGGER.exception("Logout failed")

        summary_path = run_dir / "kpnet_summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        LOGGER.info("Summary saved: %s", summary_path)

    return return_code


def main() -> int:
    return run_kpnet_workflow()

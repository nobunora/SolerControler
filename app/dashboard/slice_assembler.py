"""Backend-neutral dashboard slice assembly."""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeGuard

from app.dashboard.aggregation import _aggregation_close_day, _build_cost_monthly, _build_energy_daily, _today_jst_iso
from app.dashboard.models import DashboardData, DashboardRawData, DashboardSlice
from app.dashboard.repositories import DashboardQuerySnapshot
from app.dashboard.schedule import _build_latest_schedule_from_events, _default_latest_schedule
from app.dashboard.service import assemble_dashboard_slice
from app.dashboard.warnings import build_dashboard_warnings
from app.parsing.numbers import to_float


def _build_pv_model_summary(
    data: dict[str, Any], *, producer_identity: dict[str, str] | None = None,
    forecast_run_id: str | None = None,
) -> dict[str, Any]:
    """Expose adopted display metadata without importing a forecasting model."""
    new_model = "physical_clear_ghi_annual1"
    new_basis = "noaa_geometric_1min_midpoint_annual1_v1"
    legacy_sources = {"physical_pv_forecast", "corrected_pv_forecast"}
    legacy_methods = {"existing", "physical_unscaled", "physical_global", "physical_daypart", "physical_altitude_shortwave"}

    def mapping(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def text(value: Any, maximum: int, *, pattern: str | None = None) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            if len(value.encode("utf-8")) > maximum:
                return None
        except UnicodeEncodeError:
            return None
        if pattern and re.fullmatch(pattern, value) is None:
            return None
        return value

    def day(value: Any) -> str | None:
        value = text(value, 10)
        try:
            return value if value and date.fromisoformat(value).isoformat() == value else None
        except (ValueError, OverflowError):
            return None

    def instant(value: Any) -> datetime | None:
        value = text(value, 64)
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
            return parsed.astimezone(timezone.utc) if parsed is not None and parsed.utcoffset() is not None else None
        except (ValueError, OverflowError):
            return None

    def number(value: Any) -> TypeGuard[int | float]:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        try:
            return math.isfinite(value)
        except OverflowError:
            return False

    identifier = r"[A-Za-z0-9_.:-]+"
    sha = r"[a-f0-9]{64}"
    rationale = mapping(data.get("decision_rationale"))
    diagnostics_source = rationale if isinstance(data.get("decision_rationale"), dict) else mapping(data.get("daytime_soc_optimization"))
    physical = mapping(diagnostics_source.get("pv_physical_forecast"))
    trace = mapping(rationale.get("pv_clear_ghi"))
    forecast = mapping(data.get("forecast"))
    source = mapping(data.get("result")).get("final_pv_forecast_source")
    selected = physical.get("selected_method")
    trace_model = trace.get("model_id")
    model = source if source is not None and source != "" else selected if selected is not None and selected != "" else trace_model
    identity = mapping(producer_identity)
    generated = identity.get("generated_at", data.get("generated_at"))
    summary: dict[str, Any] = {
        "schema_version": 1, "status": "unsupported", "reason_code": "unsupported_model",
        "model_id": text(model, 128, pattern=identifier),
        "plan_date": day(identity.get("target_date", forecast.get("date"))),
        "generated_at": generated if instant(generated) is not None else None,
        "forecast_run_id": text(forecast_run_id, 256, pattern=identifier),
        "raw_plan_sha256": text(identity.get("raw_plan_sha256"), 64, pattern=sha),
        "control_version_id": text(identity.get("control_version_id"), 256, pattern=identifier),
    }
    common = dict(summary)

    def finish(status: str, reason: str | None) -> dict[str, Any]:
        result = {**(summary if status == "valid" else common), "status": status, "reason_code": reason}
        if len(json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")) > 4096:
            result = {**common, "status": "invalid", "reason_code": "display_summary_too_large"}
            if len(json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")) > 4096:
                result = {key: (None if key not in {"schema_version", "status", "reason_code"} else value) for key, value in result.items()}
                if len(json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")) > 4096:
                    raise ValueError("bounded display summary serialization failed")
        return result

    # Source selection is an allowlist, never a catch-all legacy fallback.
    if source is not None and source != "" and (not isinstance(source, str) or source not in legacy_sources | {new_model}):
        return finish("unsupported", "unsupported_model")
    new_indicated = new_model in (source, selected, trace_model)
    if new_indicated and any(value is not None and value != "" and value != new_model for value in (source, selected, trace_model)):
        return finish("invalid", "display_metadata_invalid")
    if not new_indicated:
        if ((isinstance(source, str) and source in legacy_sources)
                or (not source and ((isinstance(selected, str) and selected in legacy_methods) or physical.get("scales") or physical.get("data_quality")))):
            return finish("legacy", None)
        return finish("unsupported", "unsupported_model")
    if producer_identity is None or not all(identity.get(key) for key in ("target_date", "generated_at", "raw_plan_sha256", "control_version_id")):
        return finish("unsupported", "producer_identity_unavailable")
    if summary["forecast_run_id"] is None:
        return finish("unsupported", "forecast_run_unavailable")
    if (summary["plan_date"] is None or summary["generated_at"] is None or summary["raw_plan_sha256"] is None
            or summary["control_version_id"] is None or summary["plan_date"] != day(forecast.get("date"))
            or instant(generated) != instant(data.get("generated_at"))):
        return finish("invalid", "plan_identity_mismatch")
    if not trace:
        return finish("invalid", "clear_ghi_trace_missing")

    pack = mapping(trace.get("calibration_pack"))
    training_dates = pack.get("training_dates")
    theta = pack.get("theta4")
    totals = mapping(trace.get("totals"))
    total_keys = ("full_day_pv_kwh", "decision_day_pv_kwh", "morning_pv_kwh", "midday_pv_kwh", "evening_pv_kwh")
    stamps = {key: instant(pack.get(key)) for key in ("training_as_of", "created_at", "usable_from")}
    stamps.update({key: instant(trace.get(key)) for key in ("as_of", "retrieved_at")})
    provider = trace.get("provider_issued_at")
    provider_stamp = instant(provider)
    repaired = trace.get("repaired_hours")
    samples, rank, condition = pack.get("training_samples"), pack.get("rank"), pack.get("condition_number")
    if not isinstance(training_dates, list) or not training_dates or not isinstance(theta, list) or not isinstance(repaired, list):
        return finish("invalid", "display_metadata_invalid")
    metadata_valid = (
        type(trace.get("schema_version")) is int and trace["schema_version"] == 1
        and type(pack.get("schema_version")) is int and pack["schema_version"] == 1
        and trace_model == new_model and pack.get("model_id") == new_model
        and trace.get("basis_version") == new_basis and pack.get("basis_version") == new_basis
        and day(trace.get("target_date")) == summary["plan_date"]
        and text(trace.get("calibration_sha256"), 64, pattern=sha) is not None
        and text(trace.get("forecast_sha256"), 64, pattern=sha) is not None
        and isinstance(training_dates, list) and bool(training_dates)
        and all(day(value) is not None and value < summary["plan_date"] for value in training_dates)
        and training_dates == sorted(set(training_dates))
        and type(samples) is int and samples == 13 * len(training_dates)
        and pack.get("terms") == ["1", "x", "cos_phi", "sin_phi"]
        and isinstance(theta, list) and len(theta) == 4 and all(number(value) for value in theta)
        and type(rank) is int and rank == 4 and number(condition) and condition >= 1
        and isinstance(repaired, list) and len(repaired) <= 24
        and all(type(hour) is int and 0 <= hour <= 23 for hour in repaired)
        and repaired == sorted(set(repaired))
        and all(number(totals.get(key)) and totals[key] >= 0 for key in total_keys)
        and all(value is not None for value in stamps.values())
        and (provider is None or provider_stamp is not None)
    )
    if not metadata_valid:
        return finish("invalid", "display_metadata_invalid")
    # Compare aware instants only; forecast retrieval and pack creation have
    # independent clocks and no order relative to one another.
    valid_stamps = {key: value for key, value in stamps.items() if value is not None}
    training_at, created, usable, as_of, retrieved = (valid_stamps[key] for key in ("training_as_of", "created_at", "usable_from", "as_of", "retrieved_at"))
    if (not (training_at <= created <= usable <= as_of) or retrieved > as_of
            or (provider_stamp is not None and provider_stamp > retrieved)):
        return finish("invalid", "display_metadata_invalid")
    summary.update({
        "basis_version": new_basis, "forecast_sha256": trace["forecast_sha256"], "calibration_sha256": trace["calibration_sha256"],
        "retrieved_at": trace["retrieved_at"], "as_of": trace["as_of"], "provider_issued_at": provider,
        "training_day_count": len(training_dates), "training_sample_count": samples,
        "training_start_date": training_dates[0], "training_end_date": training_dates[-1],
        "training_as_of": pack["training_as_of"], "created_at": pack["created_at"], "usable_from": pack["usable_from"],
        "terms": list(pack["terms"]), "theta4": list(theta), "rank": rank, "condition_number": condition,
        "repaired_hours": list(repaired), "totals": {key: totals[key] for key in total_keys},
    })
    return finish("valid", None)


def extract_pv_forecast_diagnostics(
    data: dict[str, Any], *, producer_identity: dict[str, str] | None = None,
    forecast_run_id: str | None = None,
) -> dict[str, Any]:
    rationale = data.get("decision_rationale")
    optimization = data.get("daytime_soc_optimization")
    source = rationale if isinstance(rationale, dict) else optimization if isinstance(optimization, dict) else {}
    forecast = data.get("forecast")
    def mapping(key: str) -> dict[str, Any]:
        value = source.get(key) if isinstance(source, dict) else None
        return value if isinstance(value, dict) else {}
    return {"plan_date": forecast.get("date") if isinstance(forecast, dict) else None, "physical": mapping("pv_physical_forecast"), "forecast_correction": mapping("forecast_correction"), "hourly_weather_pv_shape": mapping("hourly_weather_pv_shape"), "overnight_load_forecast": mapping("overnight_discharge_guard"), "pv_model": _build_pv_model_summary(data, producer_identity=producer_identity, forecast_run_id=forecast_run_id)}


def read_latest_pv_forecast_diagnostics() -> dict[str, Any]:
    path = Path(os.getenv("NIGHT_CHARGE_PLAN_PATH", "artifacts/night_charge_plan.json"))
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as error:
        print(json.dumps({"event": "dashboard_plan_diagnostics_unavailable", "stage": "local_plan",
                          "reason_code": "invalid_json" if isinstance(error, ValueError) else "read_failed"}), flush=True)
        return {}
    if not isinstance(data, dict):
        print(json.dumps({"event": "dashboard_plan_diagnostics_unavailable", "stage": "local_plan",
                          "reason_code": "non_object"}), flush=True)
        return {}
    return extract_pv_forecast_diagnostics(data) if isinstance(data, dict) else {}


def merge_latest_plan_into_schedule(schedule: dict[str, Any], plan: dict[str, Any] | None) -> dict[str, Any]:
    """Attach matching persisted plan values to a schedule response."""
    merged = dict(schedule)
    if not plan:
        return merged
    forecast = plan.get("forecast")
    plan_date = str(plan.get("date") or (forecast.get("date") if isinstance(forecast, dict) else "") or "").strip()
    if merged.get("plan_date") and plan_date != str(merged["plan_date"]):
        return merged
    raw_result = plan.get("result")
    result = raw_result if isinstance(raw_result, dict) else {}
    target_soc = to_float(result.get("target_soc_7_percent"))
    night_charge = to_float(result.get("required_night_charge_kwh"))
    if target_soc is not None:
        merged["planned_target_soc_percent"] = target_soc
    if night_charge is not None:
        merged["planned_night_charge_kwh"] = night_charge
    if (updated_at := str(plan.get("updated_at") or "").strip()):
        merged["plan_updated_at"] = updated_at
    return merged


def _unique_forecast_value(
    rows: list[dict[str, Any]],
    field: str,
    *,
    minimum: float,
    maximum: float | None = None,
) -> float | None:
    values: set[float] = set()
    for row in rows:
        value = to_float(row.get(field))
        if value is None or value < minimum or (maximum is not None and value > maximum):
            continue
        values.add(value)
    return next(iter(values)) if len(values) == 1 else None


def merge_forecast_metadata_into_schedule(
    schedule: dict[str, Any],
    forecast_hourly: list[dict[str, Any]],
    *,
    plan_date: str,
) -> dict[str, Any]:
    """Recover display-only SOC planning metadata from the forecast-only evidence path.

    Existing control-plan or applied-setting evidence keeps priority. Forecast metadata is
    accepted only when every matching hourly row that carries the field agrees on one
    finite value, so inconsistent evidence fails closed instead of fabricating an anchor.
    """
    merged = dict(schedule)
    matching = [row for row in forecast_hourly if str(row.get("date") or "") == plan_date]

    if to_float(merged.get("planned_target_soc_percent")) is None:
        target_soc = _unique_forecast_value(
            matching,
            "forecast_target_soc_percent",
            minimum=0.0,
            maximum=100.0,
        )
        if target_soc is not None:
            merged["planned_target_soc_percent"] = target_soc
            merged["planned_target_soc_source"] = "forecast_plans"
    if to_float(merged.get("planned_night_charge_kwh")) is None:
        night_charge = _unique_forecast_value(
            matching,
            "forecast_night_charge_kwh",
            minimum=0.0,
        )
        if night_charge is not None:
            merged["planned_night_charge_kwh"] = night_charge
    return merged


def merge_forecast_history_into_battery_daily(
    battery_daily: list[dict[str, Any]],
    forecast_hourly: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Recover configured-SOC chart history from same-vintage forecast-plan evidence.

    ``history_reconstruction`` attaches forecast-plan metadata only when it can prove the
    forecast vintage. Reconstructed forecasts intentionally carry no plan SOC metadata.
    This function therefore fills only missing dashboard fields, never overwrites stronger
    applied/control evidence, and never invents a target from actual SOC.
    """
    merged_by_date = {
        str(row.get("date")): dict(row)
        for row in battery_daily
        if str(row.get("date") or "").strip()
    }
    forecast_by_date: dict[str, list[dict[str, Any]]] = {}
    for row in forecast_hourly:
        day = str(row.get("date") or "").strip()
        if not day:
            continue
        forecast_by_date.setdefault(day, []).append(row)

    for day, rows in forecast_by_date.items():
        # Defensive fail-closed guard: historical reconstruction must never fabricate
        # plan SOC.  If a reconstructed row somehow gains metadata, ignore that day.
        if any(
            bool(row.get("is_reconstructed"))
            or str(row.get("source") or "") == "historical_reconstructed_estimate"
            for row in rows
        ):
            continue
        target_soc = _unique_forecast_value(
            rows,
            "forecast_target_soc_percent",
            minimum=0.0,
            maximum=100.0,
        )
        night_charge = _unique_forecast_value(
            rows,
            "forecast_night_charge_kwh",
            minimum=0.0,
        )
        if target_soc is None and night_charge is None:
            continue
        target = merged_by_date.setdefault(day, {"date": day})
        recovered = False
        if target.get("setting_soc_target_percent") is None and target_soc is not None:
            target["setting_soc_target_percent"] = target_soc
            recovered = True
        if target.get("night_charge_kwh") is None and night_charge is not None:
            target["night_charge_kwh"] = night_charge
            recovered = True
        if recovered and not target.get("plan_display_source"):
            target["plan_display_source"] = "forecast_plans"

    return [merged_by_date[day] for day in sorted(merged_by_date)]


def merge_display_plan_into_battery_daily(
    battery_daily: list[dict[str, Any]],
    schedule: dict[str, Any],
) -> list[dict[str, Any]]:
    """Expose the latest forecast-only plan in the battery planning chart.

    Existing applied/control evidence always wins. Forecast-only values are display
    metadata and never create a control-plan or settings write.
    """
    plan_date = str(schedule.get("plan_date") or "").strip()
    target_soc = to_float(schedule.get("planned_target_soc_percent"))
    night_charge = to_float(schedule.get("planned_night_charge_kwh"))
    if not plan_date or (target_soc is None and night_charge is None):
        return battery_daily
    merged_rows = [dict(row) for row in battery_daily]
    row: dict[str, Any] | None = next(
        (item for item in merged_rows if str(item.get("date")) == plan_date), None
    )
    if row is None:
        row = {"date": plan_date}
        merged_rows.append(row)
    if row.get("setting_soc_target_percent") is None and target_soc is not None:
        row["setting_soc_target_percent"] = target_soc
        row["plan_display_source"] = "forecast_plans"
    if row.get("night_charge_kwh") is None and night_charge is not None:
        row["night_charge_kwh"] = night_charge
        row["plan_display_source"] = "forecast_plans"
    merged_rows.sort(key=lambda item: str(item.get("date") or ""))
    return merged_rows


def empty_dashboard_slice(*, window_days: int, schedule: dict[str, Any], global_oldest: str | None = None, global_newest: str | None = None) -> DashboardSlice:
    return DashboardSlice(data=DashboardData([], [], [], [], [], latest_schedule=schedule), meta={"window_days": window_days, "oldest_loaded_date": None, "newest_loaded_date": None, "global_oldest_date": global_oldest, "global_newest_date": global_newest, "has_more_before": False})


def dashboard_warnings(*, latest_schedule: dict[str, Any], battery_daily: list[dict[str, Any]], energy_daily: list[dict[str, Any]], forecast_hourly: list[dict[str, Any]], end_date_iso: str, today_jst_iso: str | None = None) -> list[dict[str, Any]]:
    return build_dashboard_warnings(latest_schedule=latest_schedule, battery_daily=battery_daily, energy_daily=energy_daily, forecast_hourly=forecast_hourly, end_date_iso=end_date_iso, today_jst_iso=today_jst_iso or _today_jst_iso())


def dashboard_meta(*, window_days: int, global_oldest_date: str | None, global_newest_date: str | None, pv_daily: list[dict[str, Any]], cost_daily: list[dict[str, Any]], battery_daily: list[dict[str, Any]], energy_daily: list[dict[str, Any]] | None = None, forecast_hourly: list[dict[str, Any]] | None = None, battery_flow_daily: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [*pv_daily, *cost_daily, *battery_daily, *(energy_daily or []), *(forecast_hourly or []), *(battery_flow_daily or [])]
    dates = [str(row["date"]) for row in rows if row.get("date")]
    oldest, newest = (min(dates), max(dates)) if dates else (None, None)
    return {"window_days": window_days, "aggregation_close_day": _aggregation_close_day(), "oldest_loaded_date": oldest, "newest_loaded_date": newest, "global_oldest_date": global_oldest_date, "global_newest_date": global_newest_date, "has_more_before": bool(global_oldest_date and oldest and global_oldest_date < oldest)}


def build_dashboard_slice(raw: DashboardRawData, *, end_date_iso: str, window_days: int, pv_forecast_diagnostics: dict[str, Any] | None = None, daily_review: dict[str, Any] | None = None, daily_reviews: list[dict[str, Any]] | None = None, today_jst_iso: str | None = None) -> DashboardSlice:
    # Restore same-vintage forecast-plan display metadata for every historical day,
    # not just the current end date. This keeps the configured-SOC line continuous
    # whenever trustworthy plan evidence exists.
    battery_daily = merge_forecast_history_into_battery_daily(
        raw.battery_daily,
        raw.forecast_hourly,
    )
    latest_schedule = merge_forecast_metadata_into_schedule(raw.latest_schedule, raw.forecast_hourly, plan_date=end_date_iso)
    battery_daily = merge_display_plan_into_battery_daily(battery_daily, latest_schedule)
    if latest_schedule != raw.latest_schedule or battery_daily != raw.battery_daily:
        raw = replace(raw, latest_schedule=latest_schedule, battery_daily=battery_daily)
    meta = dashboard_meta(window_days=window_days, global_oldest_date=raw.global_oldest, global_newest_date=raw.global_newest, pv_daily=raw.pv_daily, cost_daily=raw.cost_daily, battery_daily=raw.battery_daily, energy_daily=raw.energy_daily, forecast_hourly=raw.forecast_hourly, battery_flow_daily=raw.battery_flow_daily)
    return assemble_dashboard_slice(raw, meta=meta, warnings=dashboard_warnings(latest_schedule=raw.latest_schedule, battery_daily=raw.battery_daily, energy_daily=raw.energy_daily, forecast_hourly=raw.forecast_hourly, end_date_iso=end_date_iso, today_jst_iso=today_jst_iso), pv_forecast_diagnostics=pv_forecast_diagnostics, daily_review=daily_review, daily_reviews=daily_reviews)


def build_slice_from_query_snapshot(snapshot: DashboardQuerySnapshot, *, window_days: int, include_static: bool, pv_forecast_diagnostics: dict[str, Any] | None = None, today_jst_iso: str | None = None) -> DashboardSlice:
    end_date_iso = snapshot.resolved_end_date
    try:
        end_obj = date.fromisoformat(end_date_iso) if end_date_iso else None
    except ValueError:
        end_obj = None
    if end_obj is None:
        return empty_dashboard_slice(window_days=window_days, schedule=_default_latest_schedule(), global_oldest=snapshot.global_oldest_date, global_newest=snapshot.global_newest_date)
    assert end_date_iso is not None
    start_date = (end_obj - timedelta(days=max(1, window_days) - 1)).isoformat()
    energy_daily = _build_energy_daily(start_date=start_date, end_date_iso=end_date_iso, pv_daily=snapshot.pv_daily, monitoring_daily=snapshot.monitoring_daily, forecast_hourly=snapshot.forecast_hourly)
    schedule = _default_latest_schedule(plan_date=end_date_iso)
    if include_static:
        schedule = _build_latest_schedule_from_events(event_rows=snapshot.settings_events, battery_row=snapshot.latest_battery, plan_date=end_date_iso)
    raw = DashboardRawData(pv_daily=snapshot.pv_daily, cost_daily=snapshot.cost_daily, cost_monthly=_build_cost_monthly(snapshot.all_cost_daily) if include_static else [], battery_daily=snapshot.battery_daily, model_parameters=snapshot.model_parameters, battery_flow_daily=snapshot.battery_flow_daily, energy_daily=energy_daily, forecast_hourly=snapshot.forecast_hourly, latest_schedule=schedule, global_oldest=snapshot.global_oldest_date, global_newest=snapshot.global_newest_date)
    diagnostics = pv_forecast_diagnostics if pv_forecast_diagnostics is not None else (read_latest_pv_forecast_diagnostics() if include_static else {})
    return build_dashboard_slice(raw, end_date_iso=end_date_iso, window_days=window_days, pv_forecast_diagnostics=diagnostics, today_jst_iso=today_jst_iso)

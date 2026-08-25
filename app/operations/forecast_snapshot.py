from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.operations.domain import extract_final_pv_source_from_plan, extract_hourly_forecast_from_plan
from app.parsing.numbers import to_float


def _parse_timestamp(value: str, *, timezone: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
    return parsed


def _issued_at_from_plan(data: dict[str, Any], *, ingested_at: str) -> tuple[str, str]:
    forecast = data.get("forecast") if isinstance(data.get("forecast"), dict) else {}
    candidates = (
        data.get("issued_at"),
        data.get("generated_at"),
        data.get("created_at"),
        forecast.get("issued_at"),
        forecast.get("generated_at"),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip(), "plan"
    return ingested_at, "ingested_at_fallback"


def _model_version(data: dict[str, Any], section_name: str) -> str | None:
    section = data.get(section_name)
    if not isinstance(section, dict):
        return None
    for key in ("model_version", "version", "model"):
        value = section.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _quality_flags(row: dict[str, Any], *, issued_at_source: str) -> list[str]:
    flags: list[str] = []
    if issued_at_source != "plan":
        flags.append("issued_at_ingested_fallback")
    pv = to_float(row.get("forecast_pv_kwh")) or 0.0
    shortwave = to_float(row.get("forecast_shortwave_radiation_w_m2"))
    if shortwave is None:
        flags.append("missing_shortwave")
    elif pv > 0.0 and shortwave <= 0.0:
        flags.append("nonpositive_shortwave_with_positive_pv")
    if row.get("forecast_cloud_cover") is None:
        flags.append("missing_cloud_cover")
    if row.get("forecast_weather_code") is None:
        flags.append("missing_weather_code")
    return flags


def build_forecast_snapshot_rows(
    data: dict[str, Any],
    *,
    ingested_at: str,
    timezone: str,
) -> list[dict[str, Any]]:
    """Build append-only forecast evidence rows without changing the latest-value contract."""
    hourly_rows = extract_hourly_forecast_from_plan(data)
    if not hourly_rows:
        return []
    issued_at, issued_at_source = _issued_at_from_plan(data, ingested_at=ingested_at)
    try:
        issued_dt = _parse_timestamp(issued_at, timezone=timezone)
        local_zone = ZoneInfo(timezone)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        issued_at = ingested_at
        issued_at_source = "ingested_at_fallback"
        local_zone = ZoneInfo("UTC")
        issued_dt = _parse_timestamp(ingested_at, timezone="UTC")
    issued_local = issued_dt.astimezone(local_zone)
    forecast_source = extract_final_pv_source_from_plan(data)
    forecast = data.get("forecast") if isinstance(data.get("forecast"), dict) else {}
    weather_provider = str(forecast.get("source") or "").strip() or None
    pv_model_version = _model_version(data, "pv_array_forecast")
    weather_model_version = _model_version(data, "forecast")
    pv_forecast = data.get("pv_array_forecast")
    calibration = pv_forecast.get("calibration") if isinstance(pv_forecast, dict) else None
    calibration_factor = None
    if isinstance(calibration, dict):
        calibration_factor = to_float(calibration.get("effective_factor"))
        if calibration_factor is None:
            calibration_factor = to_float(calibration.get("factor"))

    identity_payload = {
        "issued_at": issued_at,
        "forecast_date": hourly_rows[0]["date"],
        "source": forecast_source,
        "rows": hourly_rows,
    }
    forecast_run_id = hashlib.sha256(
        json.dumps(identity_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]

    result: list[dict[str, Any]] = []
    for row in hourly_rows:
        target_local = datetime.fromisoformat(f"{row['date']}T{int(row['hour']):02d}:00:00").replace(tzinfo=local_zone)
        lead_minutes = int(round((target_local - issued_local).total_seconds() / 60.0))
        result.append(
            {
                "snapshot_id": f"{forecast_run_id}-{row['date']}-{int(row['hour']):02d}",
                "forecast_run_id": forecast_run_id,
                "issued_at": issued_at,
                "issued_at_source": issued_at_source,
                "target_at": target_local.isoformat(),
                "lead_minutes": lead_minutes,
                **row,
                "forecast_provider": weather_provider,
                "pv_model_source": forecast_source,
                "pv_model_version": pv_model_version,
                "weather_model_version": weather_model_version,
                "forecast_pv_calibration_factor": calibration_factor,
                "quality_flags_json": json.dumps(
                    _quality_flags(row, issued_at_source=issued_at_source),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "source": "night-charge-plan-hourly-snapshot",
                "recorded_at": ingested_at,
            }
        )
    return result

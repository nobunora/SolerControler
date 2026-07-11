from __future__ import annotations

import gzip
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def _json_bytes(plan: dict[str, Any]) -> bytes:
    return json.dumps(plan, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _parse_gs_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "gs" or not parsed.netloc:
        raise ValueError(f"invalid GCS URI: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def _join_gs_uri(prefix_uri: str, *parts: str) -> str:
    bucket, prefix = _parse_gs_uri(prefix_uri)
    clean = [prefix.strip("/")] if prefix.strip("/") else []
    clean.extend(part.strip("/") for part in parts if part.strip("/"))
    return f"gs://{bucket}/{'/'.join(clean)}"


def night_plan_archive_prefix() -> str:
    explicit = os.getenv("NIGHT_PLAN_ARCHIVE_GCS_PREFIX", "").strip()
    if explicit:
        return explicit.rstrip("/")
    daily_prefix = os.getenv("DATA_GCS_DAILY_PREFIX", "").strip()
    if daily_prefix:
        return _join_gs_uri(daily_prefix, "night_charge_plans").rstrip("/")
    return ""


def night_plan_inline_detail_days() -> int:
    raw = os.getenv("NIGHT_PLAN_FIRESTORE_INLINE_DETAIL_DAYS", "0").strip() or "0"
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def night_plan_gcs_uri(*, forecast_date: str, prefix_uri: str | None = None) -> str:
    prefix = (prefix_uri or night_plan_archive_prefix()).strip()
    if not prefix:
        return ""
    year, month, *_ = forecast_date.split("-")
    return _join_gs_uri(prefix, year, month, f"{forecast_date}.json.gz")


def _detail_inline_enabled(*, forecast_date: str, now: datetime | None = None) -> bool:
    days = night_plan_inline_detail_days()
    if days <= 0:
        return False
    try:
        plan_day = datetime.strptime(forecast_date, "%Y-%m-%d").date()
    except ValueError:
        return True
    today = (now or datetime.now(timezone.utc)).date()
    return plan_day >= today - timedelta(days=days)


def _summary_for_firestore(plan: dict[str, Any]) -> dict[str, Any]:
    pv_array = plan.get("pv_array_forecast") if isinstance(plan.get("pv_array_forecast"), dict) else {}
    soc_opt = plan.get("daytime_soc_optimization") if isinstance(plan.get("daytime_soc_optimization"), dict) else {}
    forecast = plan.get("forecast") if isinstance(plan.get("forecast"), dict) else {}
    result = plan.get("result") if isinstance(plan.get("result"), dict) else {}
    return {
        "forecast": {
            key: forecast.get(key)
            for key in (
                "date",
                "sun_hours",
                "temp_c",
                "weather_code",
                "precipitation_sum_mm",
                "precipitation_probability_mean",
                "shortwave_radiation_sum_mj_m2",
            )
            if key in forecast
        },
        "result": {
            key: result.get(key)
            for key in (
                "target_soc_7_percent",
                "required_night_charge_kwh",
                "final_predicted_pv_kwh",
                "final_pv_forecast_source",
                "predicted_midday_surplus_kwh",
                "effective_capacity_kwh",
                "soc_expected_day_buy_kwh",
                "soc_expected_sell_kwh",
                "soc_expected_peak_unmet_kwh",
                "soc_expected_peak_unmet_cost_yen",
                "total_expected_cost_yen",
            )
            if key in result
        },
        "inputs_summary": {
            "soc_now_percent": (plan.get("inputs") or {}).get("soc_now_percent")
            if isinstance(plan.get("inputs"), dict) else None,
        },
        "plan_quality": plan.get("plan_quality", {}),
        "decision_rationale": plan.get("decision_rationale", {}),
        "daytime_soc_optimization": {
            key: soc_opt.get(key)
            for key in (
                "cost_model",
                "forecast_correction",
                "soc_decision_prior",
                "source",
                "target_soc_7_percent",
                "max_target_soc_percent_after_guards",
            )
            if key in soc_opt
        },
        "pv_array_forecast_summary": {
            "source": pv_array.get("source"),
            "provider": pv_array.get("provider"),
            "totals": pv_array.get("totals"),
        },
        "physical_pv_forecast_summary": plan.get("physical_pv_forecast_summary", {}),
    }


def build_night_plan_firestore_document(
    plan: dict[str, Any],
    *,
    source: str,
    updated_at: str,
    force_inline_detail: bool = False,
    archive_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    forecast = plan.get("forecast", {}) if isinstance(plan.get("forecast"), dict) else {}
    forecast_date = str(forecast.get("date", "")).strip()
    raw = _json_bytes(plan)
    gzip_bytes = gzip.compress(raw, compresslevel=9, mtime=0)
    detail_sha256 = hashlib.sha256(raw).hexdigest()
    doc = {
        "date": forecast_date,
        "updated_at": updated_at,
        "source": source,
        "detail_storage": "firestore",
        "detail_uri": None,
        "detail_gcs_uri": None,
        "detail_sha256": detail_sha256,
        "detail_size_bytes": len(raw),
        "detail_gzip_size_bytes": len(gzip_bytes),
        "detail_retention_policy": "indefinite",
        "detail_retention_delete_after": None,
        "detail_inline_until_days": night_plan_inline_detail_days(),
        **_summary_for_firestore(plan),
    }
    if archive_info:
        doc.update(
            {
                "detail_storage": "gcs",
                "detail_uri": archive_info.get("detail_uri"),
                "detail_gcs_uri": archive_info.get("detail_uri"),
                "detail_generation": archive_info.get("generation"),
                "detail_archived_at": archive_info.get("archived_at"),
            }
        )
    if force_inline_detail or not forecast_date or _detail_inline_enabled(forecast_date=forecast_date):
        doc["plan_json"] = raw.decode("utf-8")
    else:
        doc["plan_json"] = None
    return doc


def upload_night_plan_to_gcs(
    plan: dict[str, Any],
    *,
    forecast_date: str,
    prefix_uri: str | None = None,
    storage_client: Any | None = None,
) -> dict[str, Any]:
    uri = night_plan_gcs_uri(forecast_date=forecast_date, prefix_uri=prefix_uri)
    if not uri:
        return {}
    if storage_client is None:
        from google.cloud import storage

        storage_client = storage.Client()
    bucket_name, blob_name = _parse_gs_uri(uri)
    raw = _json_bytes(plan)
    gzip_bytes = gzip.compress(raw, compresslevel=9, mtime=0)
    blob = storage_client.bucket(bucket_name).blob(blob_name)
    blob.cache_control = "private, max-age=31536000"
    blob.content_encoding = "gzip"
    blob.upload_from_string(gzip_bytes, content_type="application/json")
    return {
        "detail_uri": uri,
        "generation": str(getattr(blob, "generation", "") or ""),
        "archived_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def load_night_plan_detail_from_gcs(doc: dict[str, Any], *, storage_client: Any | None = None) -> dict[str, Any] | None:
    uri = str(doc.get("detail_gcs_uri") or doc.get("detail_uri") or "").strip()
    if not uri:
        return None
    if storage_client is None:
        from google.cloud import storage

        storage_client = storage.Client()
    bucket_name, blob_name = _parse_gs_uri(uri)
    payload = storage_client.bucket(bucket_name).blob(blob_name).download_as_bytes()
    try:
        raw = gzip.decompress(payload)
    except OSError:
        raw = payload
    sha = hashlib.sha256(raw).hexdigest()
    expected = str(doc.get("detail_sha256") or "").strip()
    if expected and sha != expected:
        raise ValueError(f"night plan detail checksum mismatch: {uri}")
    data = json.loads(raw.decode("utf-8"))
    return data if isinstance(data, dict) else None


def load_night_plan_detail_from_firestore_doc(
    doc: dict[str, Any],
    *,
    storage_client: Any | None = None,
) -> dict[str, Any] | None:
    plan_text = str(doc.get("plan_json") or "").strip()
    if plan_text:
        data = json.loads(plan_text)
        return data if isinstance(data, dict) else None
    return load_night_plan_detail_from_gcs(doc, storage_client=storage_client)


def read_plan_file(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"night plan must be a JSON object: {path}")
    return data

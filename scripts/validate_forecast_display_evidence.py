"""Bounded, read-only originals collector and independent forecast display checks.

The parity script owns the CLI, the SQL/Firestore pair and the retry deadline.
Original bytes and native documents never appear in the public result.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
import zlib
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from google.api_core.exceptions import GoogleAPICallError

from app.energy_plan.soc_cost import simulate_daytime_soc_percent
from app.operations.forecast_snapshot import build_forecast_snapshot_rows
from app.separated_runtime.plan import PublishedPlan

MAX_ORIGINAL_BYTES = 750_000
MAX_LEGACY_ARCHIVE_CANDIDATES = 32
_FORECAST_FIELDS = ("forecast_pv_kwh", "forecast_load_kwh", "forecast_charge_kwh")
_SOC_FIELDS = {"actual_soc_percent", "opening_soc_percent", "soc_percent", "forecast_soc_percent", "setting_soc_target_percent"}
_COVERAGE_COUNTS = ("selected_forecast_days", "new_required_days", "new_verified_days", "legacy_required_days",
                    "legacy_verified_days", "legacy_unavailable_days", "legacy_no_metadata_days", "actual_only_rows",
                    "capacity_points_verified", "soc_points_verified", "daily_metadata_fields_verified")


def _empty_window_result(*, start_date: str = "", end_date: str = "") -> dict[str, Any]:
    coverage: dict[str, Any] = dict.fromkeys(_COVERAGE_COUNTS, 0)
    coverage.update(sql_capacity_supported=False, sql_soc_supported=False)
    return {"schema_version": 1, "status": "PASS", "required_evidence_status": "PASS",
            "start_date": start_date, "end_date": end_date,
            "days": {}, "coverage": coverage, "read_count": 0, "read_bytes": 0, "failure_reasons": []}


def _number(value: Any, *, maximum: float | None = None, positive: bool = False) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and (value > 0 if positive else value >= 0)
            and (maximum is None or value <= maximum))


def _hour(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        if not value.isdigit():
            return None
        value = int(value)
    if not isinstance(value, int) or not 0 <= value <= 23:
        return None
    return value


def has_forecast_evidence(row: dict[str, Any]) -> bool:
    return (any(row.get(key) is not None for key in _FORECAST_FIELDS)
            or any(row.get(key) for key in ("forecast_run_id", "forecast_issued_at", "issued_at",
                                           "source", "updated_at", "control_version_id")))


def _new_marker(rows: list[dict[str, Any]]) -> bool:
    return any(row.get("control_version_id") or row.get("raw_plan_sha256")
               or row.get("_control_version_id") or row.get("_raw_plan_sha256")
               or "clear_ghi" in str(row.get("pv_input_source") or row.get("pv_model_source") or "")
               for row in rows)


def _issued(row: dict[str, Any]) -> Any:
    return row.get("forecast_issued_at") or row.get("issued_at")


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("invalid_timestamp")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("invalid_timestamp")
    return stamp.astimezone(timezone.utc)


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if hasattr(value, "path"):
        return {"reference_path": value.path}
    raise ValueError("unsupported_native_value")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False, default=_json_default).encode("utf-8")


def _json_object(raw: bytes, *, maximum: int = MAX_ORIGINAL_BYTES) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result

    def invalid_constant(_value: str) -> None:
        raise ValueError("nonfinite_json")

    if len(raw) > maximum:
        raise ValueError("original_size_limit")
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError("original_not_object")
    return value


def _business_errors(rows: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for row in rows:
        if "hour" in row and _hour(row["hour"]) is None:
            errors.append("hour:invalid_numeric")
        for key, value in row.items():
            if value is None:
                continue
            if key.endswith("_kwh") or key in _SOC_FIELDS or key == "forecast_target_soc_percent":
                limit = 100.0 if key in _SOC_FIELDS or key == "forecast_target_soc_percent" else None
                if not _number(value, maximum=limit):
                    errors.append(f"{key}:invalid_numeric")
    return errors


def _indexed(rows: list[dict[str, Any]], *, complete: bool) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        hour = _hour(row.get("hour"))
        if hour is None or hour in result:
            raise ValueError("hour:duplicate_or_invalid")
        result[hour] = row
    if complete and set(result) != set(range(24)):
        raise ValueError("hour:incomplete_24")
    return result


def _close(left: Any, right: Any, *, tolerance: float = 1e-9) -> bool:
    return _number(left) and _number(right) and abs(float(left) - float(right)) <= tolerance


def _original_hourly(plan: dict[str, Any], target_date: str) -> dict[int, dict[str, float]]:
    forecast = plan.get("forecast")
    optimization = plan.get("daytime_soc_optimization")
    if not isinstance(forecast, dict) or forecast.get("date") != target_date or not isinstance(optimization, dict):
        raise ValueError("original:target_date")
    pv = optimization.get("hourly_pv_forecast_kwh")
    load = optimization.get("hourly_load_forecast_kwh")
    if not isinstance(pv, dict) or not isinstance(load, dict):
        raise ValueError("original:hourly_maps")
    hours = {_hour(key) for mapping in (pv, load) for key in mapping}
    weather = forecast.get("hourly_weather", [])
    if not isinstance(weather, list):
        raise ValueError("original:hourly_weather")
    hours.update(_hour(row.get("hour")) for row in weather if isinstance(row, dict))
    if None in hours or hours != set(range(24)):
        raise ValueError("original:incomplete_24")
    pv_start = 5 if any(str(hour) in pv or hour in pv for hour in (5, 6)) else 7
    result: dict[int, dict[str, float]] = {}
    for hour in range(24):
        pv_value = pv.get(str(hour), pv.get(hour))
        if pv and str(hour) not in pv and hour not in pv and (hour < pv_start or hour > 22):
            pv_value = 0.0
        load_value = load.get(str(hour), load.get(hour))
        if pv_value is None or load_value is None or not _number(pv_value) or not _number(load_value):
            raise ValueError("original:missing_or_invalid_energy")
        pv_value, load_value = float(pv_value), float(load_value)
        result[hour] = {"forecast_pv_kwh": round(pv_value, 4),
                        "forecast_load_kwh": round(load_value, 4),
                        "forecast_charge_kwh": round(max(0.0, pv_value - load_value), 4)}
    return result


def _day_result(target_date: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {"status": "PASS", "target_date": target_date,
            "evidence_required": True,
            "evidence_kind": evidence.get("evidence_kind", "LEGACY_UNAVAILABLE"),
            "metadata_verified": False, "original_verified": False, "capacity_verified": False,
            "soc_points_verified": 0, "required_soc_points": 0,
            "daily_metadata_verified": False, "daily_metadata_fields_verified": 0,
            "failure_reasons": [], "original_sha256": evidence.get("original_sha256"),
            "forecast_identity": evidence.get("forecast_identity", {}),
            "read_count": evidence.get("read_count", 0), "read_bytes": evidence.get("read_bytes", 0)}


def validate_forecast_day_evidence(
    *, target_date: str, evidence: dict[str, Any], displayed_hourly: list[dict[str, Any]],
    displayed_battery_daily: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate one selected vintage without invoking the production display mapper."""
    result = _day_result(target_date, evidence)
    errors = list(evidence.get("failure_reasons", [])) + _business_errors(displayed_hourly)
    if isinstance(displayed_battery_daily, dict):
        errors.extend(_business_errors([displayed_battery_daily]))
    if evidence.get("revisions_before") != evidence.get("revisions_after"):
        errors.append("snapshot_drift")
    metadata = evidence.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        errors.append("metadata:invalid_type")
    exists = evidence.get("metadata_exists")
    new = (bool(metadata.get("control_version_id")) or evidence.get("evidence_kind") == "NEW_ADOPTED"
           or _new_marker(displayed_hourly + evidence.get("stored_hourly", [])))
    if new:
        result["evidence_kind"] = "NEW_ADOPTED"
    legacy_old_shape = (exists is True and not new
                        and "planned_capacity_kwh" not in metadata
                        and "planned_hourly_soc_percent" not in metadata
                        and all(row.get("forecast_capacity_kwh") is None
                                and row.get("forecast_soc_percent") is None
                                for row in displayed_hourly + evidence.get("stored_hourly", [])))
    if new or (exists is True and not legacy_old_shape):
        result["required_soc_points"] = 17
    uncertain = {"snapshot_drift", "metadata:read_unknown", "battery_daily:read_unknown",
                 "original:unavailable_legacy", "collector:read_unknown", "collector:deadline"}
    if (errors and all(error in uncertain for error in errors)
            and set(errors) != {"original:unavailable_legacy"}):
        result.update(status="INCONCLUSIVE", failure_reasons=sorted(set(errors)))
        return result
    if exists is None:
        errors.append("metadata:read_unknown")
    elif exists is False:
        if new or any(row.get("forecast_capacity_kwh") is not None or row.get("forecast_soc_percent") is not None
                      for row in displayed_hourly):
            errors.append("metadata:missing_required")
        else:
            result["evidence_kind"] = "LEGACY_NO_PLAN_METADATA"
    else:
        try:
            if metadata.get("date") != target_date:
                raise ValueError("metadata:target_date")
            displayed = _indexed(displayed_hourly, complete=True)
            stored = _indexed(evidence.get("stored_hourly", []), complete=True)
            for rows in (displayed_hourly, list(stored.values())):
                errors.extend(_business_errors(rows))
                runs = {row.get("forecast_run_id") for row in rows if row.get("forecast_run_id")}
                issued = {_timestamp(_issued(row)) for row in rows if _issued(row)}
                if len(runs) > 1 or len(issued) > 1:
                    raise ValueError("identity:mixed_run")
                if metadata.get("forecast_run_id"):
                    if runs != {metadata["forecast_run_id"]}:
                        raise ValueError("identity:run_mismatch")
                elif runs or new:
                    raise ValueError("identity:missing_run")
                elif any(row.get("updated_at") != metadata.get("updated_at") for row in rows):
                    raise ValueError("identity:legacy_timestamp")
                if metadata.get("forecast_issued_at"):
                    if issued != {_timestamp(metadata["forecast_issued_at"])}:
                        raise ValueError("identity:issued_mismatch")
                elif new:
                    raise ValueError("identity:missing_issued")
                for row in rows:
                    for key in ("raw_plan_sha256", "control_version_id"):
                        if row.get(key) is not None and row[key] != metadata.get(key):
                            raise ValueError("identity:adopted_version_mismatch")
            metadata_fields: tuple[str, ...] = ("planned_target_soc_percent", "planned_night_charge_kwh")
            if not legacy_old_shape:
                metadata_fields += ("planned_capacity_kwh",)
            for field in metadata_fields:
                if not _number(metadata.get(field), maximum=100 if field == "planned_target_soc_percent" else None,
                               positive=field == "planned_capacity_kwh"):
                    raise ValueError(f"{field}:invalid_numeric")
            soc = metadata.get("planned_hourly_soc_percent")
            if not legacy_old_shape:
                if not isinstance(soc, dict) or set(soc) != {str(hour) for hour in range(7, 24)}:
                    raise ValueError("planned_hourly_soc_percent:incomplete_17")
                if any(not _number(value, maximum=100) for value in soc.values()):
                    raise ValueError("planned_hourly_soc_percent:invalid_numeric")
            result["metadata_verified"] = True
            raw = evidence.get("original")
            if not isinstance(raw, bytes):
                raise ValueError("original:missing_new" if new else "original:unavailable_legacy")
            original = _json_object(raw)
            sha = hashlib.sha256(raw).hexdigest()
            if sha != evidence.get("original_sha256") or (new and sha != metadata.get("raw_plan_sha256")):
                raise ValueError("original:sha_mismatch")
            expected = _original_hourly(original, target_date)
            generated = _timestamp(original.get("generated_at"))
            if metadata.get("forecast_issued_at") and generated != _timestamp(metadata["forecast_issued_at"]):
                raise ValueError("original:issued_mismatch")
            # Reuse only the existing immutable identity construction. Numeric
            # projection above is independently derived from raw maps.
            identity_rows = build_forecast_snapshot_rows(original, ingested_at=original["generated_at"], timezone="UTC")
            if metadata.get("forecast_run_id") and identity_rows[0]["forecast_run_id"] != metadata["forecast_run_id"]:
                raise ValueError("original:run_mismatch")
            values = original.get("result", {})
            if not isinstance(values, dict):
                raise ValueError("original:invalid_result")
            for original_field, stored_field, positive, maximum in (
                ("effective_capacity_kwh", "planned_capacity_kwh", True, None),
                ("target_soc_7_percent", "planned_target_soc_percent", False, 100),
                ("required_night_charge_kwh", "planned_night_charge_kwh", False, None),
            ):
                if not _number(values.get(original_field), positive=positive, maximum=maximum):
                    raise ValueError(f"{original_field}:invalid_numeric")
                if (stored_field != "planned_capacity_kwh" or not legacy_old_shape) and not _close(
                        values[original_field], metadata[stored_field]):
                    raise ValueError(f"{stored_field}:original_mismatch")
            if new:
                published_doc = evidence.get("published_plan_document")
                if not isinstance(published_doc, dict) or isinstance(published_doc.get("schema_version"), bool):
                    raise ValueError("published_plan:missing")
                published = PublishedPlan.from_document(published_doc, target_date=target_date)
                if published.raw_json.encode("utf-8") != raw or published.version_id != metadata.get("control_version_id"):
                    raise ValueError("published_plan:identity_mismatch")
            for hour in range(24):
                for field in _FORECAST_FIELDS:
                    for row in (stored[hour], displayed[hour]):
                        if not _close(row.get(field), expected[hour][field]):
                            raise ValueError(f"{field}:original_mismatch")
                if not legacy_old_shape and not _close(
                        displayed[hour].get("forecast_capacity_kwh"), metadata["planned_capacity_kwh"]):
                    raise ValueError("forecast_capacity_kwh:display_mismatch")
                for display_field, metadata_field in (("forecast_target_soc_percent", "planned_target_soc_percent"),
                                                     ("forecast_night_charge_kwh", "planned_night_charge_kwh")):
                    if not _close(displayed[hour].get(display_field), metadata[metadata_field]):
                        raise ValueError(f"{display_field}:display_mismatch")
                if hour < 7 and displayed[hour].get("forecast_soc_percent") is not None:
                    raise ValueError("forecast_soc_percent:unexpected_early_point")
            if not legacy_old_shape:
                assert isinstance(soc, dict)
                expected_soc = simulate_daytime_soc_percent(
                    target_soc_percent=values["target_soc_7_percent"], capacity_kwh=values["effective_capacity_kwh"],
                    hourly_load_kwh={hour: row["forecast_load_kwh"] for hour, row in expected.items()},
                    hourly_pv_kwh={hour: row["forecast_pv_kwh"] for hour, row in expected.items()},
                )
                for hour, expected_value in expected_soc.items():
                    if not _close(soc[str(hour)], expected_value, tolerance=1e-7):
                        raise ValueError("planned_hourly_soc_percent:original_mismatch")
                    if not _close(displayed[hour].get("forecast_soc_percent"), expected_value, tolerance=1e-7):
                        raise ValueError("forecast_soc_percent:display_mismatch")
            if evidence.get("stored_battery_daily_exists") is None:
                raise ValueError("battery_daily:read_unknown")
            saved_daily = evidence.get("stored_battery_daily") or {}
            errors.extend(_business_errors([saved_daily]))
            if not isinstance(displayed_battery_daily, dict):
                raise ValueError("battery_daily:display_missing")
            for display_field, metadata_field in (("setting_soc_target_percent", "planned_target_soc_percent"),
                                                 ("night_charge_kwh", "planned_night_charge_kwh")):
                before = saved_daily.get(display_field)
                expected_value = metadata[metadata_field] if before is None else before
                if not _close(displayed_battery_daily.get(display_field), expected_value):
                    raise ValueError(f"{display_field}:daily_mismatch")
                result["daily_metadata_fields_verified"] += int(before is None)
            result.update(original_verified=True, capacity_verified=not legacy_old_shape,
                          soc_points_verified=0 if legacy_old_shape else 17,
                          daily_metadata_verified=True)
        except (ValueError, TypeError, KeyError, IndexError, UnicodeError) as exc:
            reason = str(exc) if isinstance(exc, ValueError) and ":" in str(exc) else "original:invalid_evidence"
            # Only our fixed reason codes may enter the public result.
            if not reason.replace("_", "").replace(":", "").isalnum():
                reason = "original:invalid_evidence"
            errors.append(reason)
    result["failure_reasons"] = sorted(set(errors))
    if errors:
        result["status"] = "INCONCLUSIVE" if all(error in uncertain for error in errors) else "FAIL"
    if (legacy_old_shape and evidence.get("native_reads_complete") is True
            and isinstance(evidence.get("revisions_before"), dict) and evidence["revisions_before"]
            and evidence["revisions_before"] == evidence.get("revisions_after")
            and (not errors or set(errors) == {"original:unavailable_legacy"})):
        result["evidence_required"] = False
    return result


def validate_forecast_window_evidence(
    *, start_date: str, end_date: str, evidence_by_date: dict[str, dict[str, Any]],
    displayed_hourly: list[dict[str, Any]], displayed_battery_daily: list[dict[str, Any]],
) -> dict[str, Any]:
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    if not 0 <= (end - start).days < 31:
        raise ValueError("invalid_window")
    grouped: dict[str, list[dict[str, Any]]] = {}
    actual_only = 0
    for row in displayed_hourly:
        day = str(row.get("date") or "")
        if not start_date <= day <= end_date:
            raise ValueError("row_outside_window")
        grouped.setdefault(day, []).append(row)
        actual_only += int(not has_forecast_evidence(row))
    daily: dict[str, dict[str, Any]] = {}
    for row in displayed_battery_daily:
        day = str(row.get("date") or "")
        if day in daily:
            raise ValueError("duplicate_daily")
        daily[day] = row
    days: dict[str, dict[str, Any]] = {}
    for day in sorted(grouped.keys() | evidence_by_date.keys()):
        if not start_date <= day <= end_date:
            raise ValueError("evidence_outside_window")
        evidence = evidence_by_date.get(day)
        if evidence is None:
            raise ValueError("missing_day_evidence")
        grouped.setdefault(day, [])
        rows = [row for row in grouped[day] if has_forecast_evidence(row)]
        days[day] = validate_forecast_day_evidence(target_date=day, evidence=evidence,
                                                  displayed_hourly=rows, displayed_battery_daily=daily.get(day))
        if _business_errors(grouped[day]):
            days[day]["failure_reasons"] = sorted(set(days[day]["failure_reasons"] + _business_errors(grouped[day])))
            days[day]["status"] = "FAIL"
            days[day]["evidence_required"] = True
    summary = _empty_window_result(start_date=start_date, end_date=end_date)
    coverage = summary["coverage"]
    coverage["actual_only_rows"] = actual_only
    for day, result in days.items():
        forecast = any(has_forecast_evidence(row) for row in grouped[day])
        coverage["selected_forecast_days"] += int(forecast)
        kind = result["evidence_kind"]
        verified = result["status"] == "PASS" and result["original_verified"]
        if kind == "NEW_ADOPTED":
            coverage["new_required_days"] += 1
            coverage["new_verified_days"] += int(verified)
        elif kind == "LEGACY_NO_PLAN_METADATA":
            coverage["legacy_no_metadata_days"] += int(forecast)
        else:
            coverage["legacy_required_days"] += int(forecast)
            coverage["legacy_verified_days"] += int(verified)
            coverage["legacy_unavailable_days"] += int(kind == "LEGACY_UNAVAILABLE")
        coverage["capacity_points_verified"] += 24 * int(result["capacity_verified"] and result["status"] == "PASS")
        coverage["soc_points_verified"] += result["soc_points_verified"] if result["status"] == "PASS" else 0
        coverage["daily_metadata_fields_verified"] += result["daily_metadata_fields_verified"] if result["status"] == "PASS" else 0
    status = "FAIL" if any(item["status"] == "FAIL" for item in days.values()) else (
        "INCONCLUSIVE" if any(item["status"] == "INCONCLUSIVE" for item in days.values()) else "PASS")
    required = [item for item in days.values() if item["evidence_required"]]
    required_status = "FAIL" if any(item["status"] == "FAIL" for item in required) else (
        "INCONCLUSIVE" if any(item["status"] == "INCONCLUSIVE" for item in required) else "PASS")
    summary.update(status=status, required_evidence_status=required_status,
                   days=days, read_count=sum(item["read_count"] for item in days.values()),
                   read_bytes=sum(item["read_bytes"] for item in days.values()),
                   failure_reasons=[f"{day}:{reason}" for day, item in days.items() for reason in item["failure_reasons"]])
    return summary


def _timeout(deadline: float, maximum: float = 3.0) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("collector:deadline")
    return min(maximum, remaining)


def _native(snapshot: Any) -> tuple[dict[str, Any], str, int]:
    if not snapshot.exists:
        return {}, "absent", 0
    value = snapshot.to_dict()
    if not isinstance(value, dict) or snapshot.update_time is None:
        raise ValueError("native_revision_missing")
    stamp = snapshot.update_time
    if isinstance(stamp, datetime):
        if stamp.tzinfo is None:
            raise ValueError("native_revision_missing")
        # Firestore's DatetimeWithNanoseconds.rfc3339 retains sub-microsecond
        # revisions which datetime.__str__ can lose.
        stamp = stamp.rfc3339() if hasattr(stamp, "rfc3339") else stamp.isoformat()
    elif isinstance(stamp, str):
        _timestamp(stamp)
    else:
        raise ValueError("native_revision_missing")
    raw = _canonical(value)
    return value, f"{stamp}|{hashlib.sha256(raw).hexdigest()}", len(raw)


def _read_documents(client: Any, keys: list[tuple[str, str]], *, deadline: float) -> dict[str, Any]:
    """Batch exact references without changing the bounded evidence document set."""
    references = {f"{collection}/{key}": client.collection(collection).document(key)
                  for collection, key in keys}
    if len(references) > 93:
        raise ValueError("native_reference_limit")
    if not references:
        return {}
    snapshots = client.get_all(list(references.values()), timeout=_timeout(deadline), retry=None)
    result: dict[str, Any] = {}
    for snapshot in snapshots:
        path = snapshot.reference.path
        if path not in references or path in result:
            raise ValueError("native_reference_identity")
        result[path] = snapshot
    if set(result) != set(references):
        raise ValueError("native_reference_missing")
    return result


def _read_state(client: Any, *, start_date: str, end_date: str, grouped: dict[str, list[dict[str, Any]]],
                deadline: float) -> dict[str, dict[str, Any]]:
    documents = list(client.collection("forecast_plans").where("date", ">=", start_date)
                     .where("date", "<=", end_date).limit(32).stream(timeout=_timeout(deadline), retry=None))
    if len(documents) > 31:
        raise ValueError("metadata_limit")
    metadata_docs: dict[str, Any] = {}
    for snapshot in documents:
        if snapshot.id in metadata_docs:
            raise ValueError("duplicate_metadata")
        row = snapshot.to_dict() or {}
        if row.get("date") != snapshot.id or not start_date <= snapshot.id <= end_date:
            raise ValueError("metadata_document_identity")
        metadata_docs[snapshot.id] = snapshot
        grouped.setdefault(snapshot.id, [])
    keys = [("battery_daily_metrics", day) for day in grouped]
    keys.extend(("forecast_plans", day) for day in grouped if day not in metadata_docs)
    fetched = _read_documents(client, keys, deadline=deadline)
    states: dict[str, dict[str, Any]] = {}

    def record(state: dict[str, Any], collection: str, snapshot: Any) -> dict[str, Any]:
        value, revision, count = _native(snapshot)
        state["revisions_before"][f"{collection}/{snapshot.id}"] = revision
        state["read_count"] += 1
        state["read_bytes"] += count
        return value

    for day, selected in grouped.items():
        state: dict[str, Any] = {"revisions_before": {}, "read_count": 0, "read_bytes": 0}
        meta_snapshot = metadata_docs.get(day) or fetched[f"forecast_plans/{day}"]
        metadata = record(state, "forecast_plans", meta_snapshot)
        daily_snapshot = fetched[f"battery_daily_metrics/{day}"]
        daily = record(state, "battery_daily_metrics", daily_snapshot)
        forecast_rows = [row for row in selected if has_forecast_evidence(row)]
        sources = {str(row.get("source") or "") for row in forecast_rows}
        collection = "forecast_hourly_snapshots" if "forecast_hourly_snapshot" in sources else (
            "forecast_hourly_reconstructed" if "historical_reconstructed_estimate" in sources else "forecast_hourly")
        query = client.collection(collection).where("date", "==", day)
        runs = {row.get("forecast_run_id") for row in forecast_rows if row.get("forecast_run_id")}
        if collection == "forecast_hourly_snapshots" and len(runs) == 1:
            query = query.where("forecast_run_id", "==", next(iter(runs)))
        if collection == "forecast_hourly_reconstructed":
            reconstruction_ids = {row.get("forecast_reconstruction_id") for row in forecast_rows
                                  if row.get("forecast_reconstruction_id")}
            if len(reconstruction_ids) != 1:
                raise ValueError("reconstruction_selection_unknown")
            query = query.where("forecast_reconstruction_id", "==", next(iter(reconstruction_ids)))
        raw_snapshots = list(query.limit(25).stream(timeout=_timeout(deadline), retry=None)) if forecast_rows else []
        if len(raw_snapshots) > 24:
            raise ValueError("hourly_limit")
        stored = [record(state, collection, snapshot) for snapshot in raw_snapshots]
        new = bool(metadata.get("control_version_id")) or _new_marker(forecast_rows + stored)
        state.update(target_date=day, metadata_exists=bool(meta_snapshot.exists), metadata=metadata,
                     stored_hourly=stored, stored_battery_daily_exists=bool(daily_snapshot.exists),
                     stored_battery_daily=daily if daily_snapshot.exists else None,
                     published_plan_document=None,
                     forecast_identity={"target_date": day, "forecast_run_id": metadata.get("forecast_run_id"),
                         "forecast_issued_at": metadata.get("forecast_issued_at"), "raw_plan_sha256": metadata.get("raw_plan_sha256"),
                         "control_version_id": metadata.get("control_version_id")},
                     evidence_kind="NEW_ADOPTED" if new else (
                         "LEGACY_UNAVAILABLE" if meta_snapshot.exists else "LEGACY_NO_PLAN_METADATA"),
                     _decision={})
        states[day] = state
    keys = []
    for day, state in states.items():
        metadata = state["metadata"]
        if state["evidence_kind"] == "NEW_ADOPTED":
            sha, version = metadata.get("raw_plan_sha256"), metadata.get("control_version_id")
            if isinstance(sha, str) and len(sha) == 64:
                keys.append(("night_plan_decisions", f"{day}--{sha}"))
            if isinstance(version, str) and version:
                keys.append(("control_plan_versions", version))
        elif state["metadata_exists"]:
            keys.append(("night_charge_plans", day))
    decisions = _read_documents(client, keys, deadline=deadline)
    for day, state in states.items():
        metadata = state["metadata"]
        new = state["evidence_kind"] == "NEW_ADOPTED"
        if new:
            sha = metadata.get("raw_plan_sha256")
            if isinstance(sha, str) and len(sha) == 64:
                snapshot = decisions[f"night_plan_decisions/{day}--{sha}"]
                state["_decision"] = record(state, "night_plan_decisions", snapshot)
            version = metadata.get("control_version_id")
            if isinstance(version, str) and version:
                snapshot = decisions[f"control_plan_versions/{version}"]
                document = record(state, "control_plan_versions", snapshot)
                payload = document.get("payload")
                state["published_plan_document"] = (_json_object(payload.encode("utf-8"), maximum=900_000)
                                                    if isinstance(payload, str) else payload)
        elif state["metadata_exists"]:
            decision = record(state, "night_charge_plans", decisions[f"night_charge_plans/{day}"])
            issued = metadata.get("forecast_issued_at")
            archived_at = decision.get("generated_at")
            if issued and archived_at and _timestamp(issued) != _timestamp(archived_at):
                candidates = list(client.collection("night_plan_decisions").where("date", "==", day)
                                  .limit(MAX_LEGACY_ARCHIVE_CANDIDATES + 1)
                                  .stream(timeout=_timeout(deadline), retry=None))
                if len(candidates) > MAX_LEGACY_ARCHIVE_CANDIDATES:
                    raise ValueError("legacy_archive_candidate_limit")
                matching = []
                for snapshot in candidates:
                    candidate = record(state, "night_plan_decisions", snapshot)
                    if candidate.get("date") != day:
                        raise ValueError("legacy_archive_target_date")
                    stamp = candidate.get("generated_at")
                    if stamp and _timestamp(stamp) == _timestamp(issued):
                        matching.append(candidate)
                if len(matching) > 1:
                    raise ValueError("legacy_archive_ambiguous")
                decision = matching[0] if matching else {}
            state["_decision"] = decision
    return states


def _decoded(raw: bytes) -> bytes:
    if raw[:2] == b"\x1f\x8b":
        decoder = zlib.decompressobj(31)
        value = decoder.decompress(raw, MAX_ORIGINAL_BYTES + 1)
        if len(value) > MAX_ORIGINAL_BYTES or not decoder.eof or decoder.unused_data:
            raise ValueError("original_size_or_gzip")
        return value
    if len(raw) > MAX_ORIGINAL_BYTES:
        raise ValueError("original_size_limit")
    return raw


def _frozen_originals(directory: Path | None) -> dict[str, list[bytes]]:
    result: dict[str, list[bytes]] = {}
    if directory is None:
        return result
    files = []
    for path in directory.iterdir():
        if path.is_file() and (path.name.endswith(".json") or path.name.endswith(".json.gz")):
            files.append(path)
            if len(files) > 31:
                raise ValueError("frozen_original_limit")
    for path in files:
        with path.open("rb") as stream:
            raw = _decoded(stream.read(MAX_ORIGINAL_BYTES + 1025))
        plan = _json_object(raw)
        day = str(plan.get("forecast", {}).get("date") or "")
        result.setdefault(day, []).append(raw)
    return result


def load_forecast_evidence_for_window(
    *, client: Any, storage: Any, start_date: str, end_date: str, selected_hourly: list[dict[str, Any]],
    original_dir: Path | None = None, deadline: float,
) -> dict[str, dict[str, Any]]:
    """Pin native originals and re-read the same selected-vintage evidence set."""
    if not 0 <= (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days < 31:
        raise ValueError("invalid_window")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in selected_hourly:
        day = str(row.get("date") or "")
        if not start_date <= day <= end_date:
            raise ValueError("row_outside_window")
        grouped.setdefault(day, []).append(row)
    frozen = _frozen_originals(original_dir)
    states: dict[str, dict[str, Any]] = {}
    blobs: dict[str, Any] = {}
    stage = "native_before"
    try:
        states = _read_state(client, start_date=start_date, end_date=end_date, grouped=grouped, deadline=deadline)
        for day, state in states.items():
            state.update(original=None, original_sha256=None, original_generation=None,
                         native_reads_complete=False, failure_reasons=[])
            decision = state.pop("_decision")
            if state["evidence_kind"] == "LEGACY_NO_PLAN_METADATA":
                continue
            sha = decision.get("detail_sha256")
            if not isinstance(sha, str) or len(sha) != 64 or decision.get("date") != day:
                state["failure_reasons"].append("original:missing_new" if state["evidence_kind"] == "NEW_ADOPTED"
                                                 else "original:unavailable_legacy")
                continue
            candidates = [raw for raw in frozen.get(day, []) if hashlib.sha256(raw).hexdigest() == sha]
            if len(candidates) > 1:
                raise ValueError("ambiguous_frozen_original")
            raw = candidates[0] if candidates else None
            if raw is None and isinstance(decision.get("plan_json"), str):
                raw = decision["plan_json"].encode("utf-8")
            uri = decision.get("detail_uri") or decision.get("detail_gcs_uri")
            if raw is None and isinstance(uri, str) and uri.startswith("gs://") and storage is not None:
                bucket, separator, name = uri[5:].partition("/")
                if not separator or not bucket or not name:
                    raise ValueError("invalid_original_reference")
                declared_generation = decision.get("detail_generation")
                blob = (storage.bucket(bucket).blob(name, generation=int(declared_generation))
                        if declared_generation else storage.bucket(bucket).blob(name))
                stage = "original_reload"
                blob.reload(timeout=_timeout(deadline, 4), retry=None)
                generation = int(blob.generation)
                if decision.get("detail_generation") and generation != int(decision["detail_generation"]):
                    raise ValueError("original_generation_mismatch")
                stage = "original_download"
                compressed = blob.download_as_bytes(if_generation_match=generation, raw_download=True,
                    start=0, end=MAX_ORIGINAL_BYTES + 1023, timeout=_timeout(deadline, 4), retry=None)
                raw = _decoded(compressed)
                state["original_generation"] = str(generation)
                state["revisions_before"]["original_generation"] = str(generation)
                blobs[day] = blob
            if raw is not None:
                if len(raw) > MAX_ORIGINAL_BYTES or hashlib.sha256(raw).hexdigest() != sha:
                    raise ValueError("original_checksum_or_size")
                if state["evidence_kind"] != "NEW_ADOPTED":
                    original = _json_object(raw)
                    metadata = state["metadata"]
                    issued = metadata.get("forecast_issued_at")
                    generated = original.get("generated_at")
                    if not isinstance(generated, str):
                        raise ValueError("original_generated_at_invalid")
                    identity_rows = build_forecast_snapshot_rows(
                        original, ingested_at=generated, timezone="UTC")
                    same_issued = not issued or _timestamp(original.get("generated_at")) == _timestamp(issued)
                    run = metadata.get("forecast_run_id")
                    same_run = not run or identity_rows[0]["forecast_run_id"] == run
                    if not same_issued or not same_run:
                        state["failure_reasons"].append("original:unavailable_legacy")
                        state["read_count"] += 1
                        state["read_bytes"] += len(raw)
                        continue
                state.update(original=raw, original_sha256=sha)
                state["read_count"] += 1
                state["read_bytes"] += len(raw)
                if state["evidence_kind"] != "NEW_ADOPTED":
                    state["evidence_kind"] = "LEGACY_ORIGINAL"
        stage = "native_after"
        after = _read_state(client, start_date=start_date, end_date=end_date, grouped=grouped, deadline=deadline)
        for day, state in states.items():
            state["revisions_after"] = after[day]["revisions_before"]
            state["read_count"] += after[day]["read_count"]
            state["read_bytes"] += after[day]["read_bytes"]
            if day in blobs:
                stage = "generation_after"
                blobs[day].reload(timeout=_timeout(deadline, 4), retry=None)
                state["revisions_after"]["original_generation"] = str(blobs[day].generation)
            state["native_reads_complete"] = True
    except (GoogleAPICallError, TimeoutError, OSError) as exc:
        for day in grouped:
            state = states.setdefault(day, {"target_date": day, "metadata_exists": None, "metadata": {},
                "evidence_kind": "NEW_ADOPTED" if _new_marker(grouped[day]) else "LEGACY_UNAVAILABLE",
                "forecast_identity": {}, "read_count": 0, "read_bytes": 0})
            state.setdefault("failure_reasons", []).append("collector:read_unknown")
            state["collector_error"] = {"exception_type": type(exc).__name__, "stage": stage}
            state["native_reads_complete"] = False
            state.setdefault("revisions_before", {})
            state["revisions_after"] = state["revisions_before"]
    return states

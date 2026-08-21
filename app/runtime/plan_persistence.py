"""Firestore persistence boundaries for Cloud Job night-plan decisions."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.energy_plan.decision_feedback import build_soc_decision_feedback


FirestoreOpener = Callable[[], Any | None]
PlanMeta = dict[str, float | str | None]


def _transaction_snapshot(transaction: Any, ref: Any) -> Any | None:
    """Normalize Firestore transaction.get across supported client versions."""
    result = transaction.get(ref)
    if result is None or hasattr(result, "exists"):
        return result
    try:
        return next(iter(result), None)
    except TypeError:
        return None


def _run_firestore_transaction(transaction: Any, operation: Callable[[Any], bool]) -> bool:
    """Run a Firestore operation through the SDK's transaction lifecycle."""
    try:
        from google.cloud.firestore_v1.transaction import transactional
    except ImportError:
        begin = getattr(transaction, "_begin", None) or getattr(transaction, "begin", None)
        if callable(begin):
            begin()
        result = operation(transaction)
        commit = getattr(transaction, "commit", None) or getattr(transaction, "_commit", None)
        if callable(commit):
            commit()
        return result
    return bool(transactional(operation)(transaction))


def persist_night_soc_execution(
    *,
    plan_meta: Mapping[str, Any],
    state: str,
    owner: str = "03-monitor",
    open_firestore: FirestoreOpener,
    **values: Any,
) -> bool:
    """Persist the immutable plan identity and the current single-owner state."""
    client = open_firestore()
    plan_date = str(plan_meta.get("date") or "").strip()
    plan_id = str(plan_meta.get("plan_id") or "").strip()
    if client is None or not plan_date or not plan_id:
        return False
    now_utc = datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds").replace("+00:00", "Z")
    payload: dict[str, Any] = {
        "plan_id": plan_id,
        "plan_date": plan_date,
        "plan_revision": plan_meta.get("plan_revision"),
        "plan_hash": plan_meta.get("plan_hash"),
        "raw_target_soc_percent": plan_meta.get("target_soc_7_percent"),
        "required_night_charge_kwh": plan_meta.get("required_night_charge_kwh"),
        "state": state,
        "owner": owner,
        "updated_at_utc": now_utc,
    }
    payload.update(values)
    try:
        client.collection("night_soc_execution").document(plan_date).set(payload, merge=True)
        return True
    except Exception as exc:
        print(f"[cloud_job_runner] night SOC execution persistence failed: {exc}", flush=True)
        return False


def acquire_night_soc_lease(
    *,
    plan_meta: Mapping[str, Any],
    owner: str,
    lease_seconds: int,
    open_firestore: FirestoreOpener,
) -> bool:
    """Acquire the 03:00 owner lease using a Firestore transaction when available."""
    client = open_firestore()
    plan_date = str(plan_meta.get("date") or "").strip()
    plan_id = str(plan_meta.get("plan_id") or "").strip()
    if client is None or not plan_date or not plan_id or lease_seconds <= 0:
        return False
    now = datetime.now(ZoneInfo("UTC"))
    expiry = now + timedelta(seconds=lease_seconds)
    ref = client.collection("night_soc_execution").document(plan_date)
    payload = {
        "plan_id": plan_id,
        "plan_date": plan_date,
        "owner": owner,
        "lease_id": f"{plan_id}:{owner}",
        "lease_acquired_at_utc": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "lease_expires_at_utc": expiry.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "state": "LEASE_ACQUIRED",
    }
    try:
        transaction_factory = getattr(client, "transaction", None)
        if transaction_factory is not None:
            transaction = transaction_factory()

            def acquire_in_transaction(active_transaction: Any) -> bool:
                snapshot = _transaction_snapshot(active_transaction, ref)
                if snapshot is not None and snapshot.exists:
                    current = snapshot.to_dict() or {}
                    current_owner = str(current.get("owner") or "")
                    current_plan_id = str(current.get("plan_id") or "")
                    current_expiry = str(current.get("lease_expires_at_utc") or "")
                    if current_plan_id != plan_id or (
                        current_owner and current_owner != owner and current_expiry > now.isoformat()
                    ):
                        return False
                active_transaction.set(ref, payload, merge=True)
                return True

            return _run_firestore_transaction(transaction, acquire_in_transaction)
        snapshot = ref.get()
        if snapshot.exists:
            current = snapshot.to_dict() or {}
            if str(current.get("owner") or "") not in {"", owner}:
                return False
        ref.set(payload, merge=True)
        return True
    except Exception as exc:
        print(f"[cloud_job_runner] night SOC lease acquisition failed: {exc}", flush=True)
        return False


def can_apply_day_transition(*, plan_date: str, open_firestore: FirestoreOpener) -> bool:
    client = open_firestore()
    if client is None:
        return False
    try:
        snapshot = client.collection("night_soc_execution").document(plan_date).get()
        if not snapshot.exists:
            return False
        state = str((snapshot.to_dict() or {}).get("state") or "")
        return state in {"STANDBY_ACKED", "COMPLETED_NO_CHARGE", "SAFE_TERMINATED", "VERIFIED"}
    except Exception as exc:
        print(f"[cloud_job_runner] day transition lease check failed: {exc}", flush=True)
        return False


def _plan_date_from_json(plan: dict[str, Any]) -> str:
    forecast = plan.get("forecast", {}) if isinstance(plan.get("forecast"), dict) else {}
    return str(forecast.get("date", "")).strip()


def persist_night_plan_to_firestore(
    plan_path: Path, *, source: str, open_firestore: FirestoreOpener
) -> bool:
    if not plan_path.exists():
        print(f"[cloud_job_runner] plan persistence skipped; missing: {plan_path}", flush=True)
        return False
    client = open_firestore()
    if client is None:
        return False
    try:
        from app.backup.night_plan_archive import (
            build_night_plan_firestore_document,
            read_plan_file,
            upload_night_plan_to_gcs,
        )

        plan = read_plan_file(plan_path)
        plan_date = _plan_date_from_json(plan)
        if not plan_date:
            print("[cloud_job_runner] plan persistence skipped; forecast.date missing", flush=True)
            return False
        now = datetime.now(ZoneInfo("UTC")).isoformat()
        archive_info = upload_night_plan_to_gcs(plan, forecast_date=plan_date)
        doc = build_night_plan_firestore_document(plan, source=source, updated_at=now, archive_info=archive_info)
        collection = client.collection("night_charge_plans")
        collection.document(plan_date).set(doc, merge=True)
        latest_doc = build_night_plan_firestore_document(
            plan, source=source, updated_at=now, force_inline_detail=True, archive_info=archive_info
        )
        collection.document("latest").set(latest_doc, merge=True)
        print(f"[cloud_job_runner] persisted night plan to Firestore date={plan_date}", flush=True)
        return True
    except Exception as exc:
        print(f"[cloud_job_runner] plan persistence failed: {exc}", flush=True)
        return False


def restore_night_plan_from_firestore(
    plan_path: Path, *, target_date: str, open_firestore: FirestoreOpener
) -> bool:
    client = open_firestore()
    if client is None:
        return False
    try:
        from app.backup.night_plan_archive import load_night_plan_detail_from_firestore_doc

        candidates = [target_date] if target_date else []
        candidates.append("latest")
        for doc_id in candidates:
            snapshot = client.collection("night_charge_plans").document(doc_id).get()
            if not snapshot.exists:
                continue
            plan = load_night_plan_detail_from_firestore_doc(snapshot.to_dict() or {})
            if not plan:
                continue
            plan_date = _plan_date_from_json(plan)
            if target_date and plan_date and plan_date != target_date:
                continue
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[cloud_job_runner] restored night plan from Firestore date={plan_date}", flush=True)
            return True
    except Exception as exc:
        print(f"[cloud_job_runner] plan restore failed: {exc}", flush=True)
    return False


def persist_previous_day_soc_feedback(
    *, target_date: str, csv_paths: list[Path], open_firestore: FirestoreOpener
) -> bool:
    enabled = os.getenv("SOC_DECISION_FEEDBACK_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return False
    client = open_firestore()
    if client is None:
        return False
    try:
        previous_date = (datetime.fromisoformat(target_date).date() - timedelta(days=1)).isoformat()
    except ValueError:
        print(f"[cloud_job_runner] SOC feedback skipped: invalid target_date={target_date}", flush=True)
        return False
    try:
        snapshot = client.collection("night_charge_plans").document(previous_date).get()
        if not snapshot.exists:
            print(f"[cloud_job_runner] SOC feedback skipped: previous plan missing date={previous_date}", flush=True)
            return False
        data = snapshot.to_dict() or {}
        plan_text = str(data.get("plan_json") or "").strip()
        plan = json.loads(plan_text) if plan_text else data
        feedback = build_soc_decision_feedback(
            plan=plan,
            csv_paths=csv_paths,
            target_date=previous_date,
            created_at=datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds").replace("+00:00", "Z"),
        )
        if not feedback:
            print(f"[cloud_job_runner] SOC feedback skipped: insufficient actual data date={previous_date}", flush=True)
            return False
        client.collection("soc_decision_feedback").document(previous_date).set(feedback, merge=True)
        print(
            "[cloud_job_runner] persisted SOC decision feedback "
            f"date={previous_date} best={feedback.get('best_target_soc_percent')}%",
            flush=True,
        )
        return True
    except Exception as exc:
        print(f"[cloud_job_runner] SOC feedback persistence failed: {exc}", flush=True)
        return False


# readable-code-audit: skip STRUCT-04 — monitor schedule fields and the Firestore document write must remain one durable event boundary
def persist_03_monitor_schedule_to_firestore(
    *, plan_meta: PlanMeta, charge_start_time: str, charge_end_time: str, target_soc: float,
    latest_soc: float | None, required_kwh: float, estimated_charge_minutes: int,
    default_power_kw: float, open_firestore: FirestoreOpener,
    charge_rate_info: Mapping[str, float | int | str | None] | None = None,
    soc_source: str = "unknown", soc_error: str | None = None, monitor_start_reason: str = "soc_available",
) -> bool:
    client = open_firestore()
    if client is None:
        return False
    plan_date = str(plan_meta.get("date") or "").strip()
    if not plan_date:
        return False
    now_utc = datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds").replace("+00:00", "Z")
    event_id = f"{plan_date}-03-monitor-schedule"
    day_start = os.getenv("KP_DAY_DISCHARGE_WINDOW_START", "07:00").strip() or "07:00"
    day_end = os.getenv("KP_DAY_DISCHARGE_WINDOW_END", "23:00").strip() or "23:00"
    detail: dict[str, Any] = {
        "plan_date": plan_date, "charge_start_time": charge_start_time, "charge_end_time": charge_end_time,
        "plan_id": plan_meta.get("plan_id"),
        "plan_revision": plan_meta.get("plan_revision"),
        "plan_hash": plan_meta.get("plan_hash"),
        "target_soc_7_percent": target_soc,
        "night_window_start": os.getenv("KP_NIGHT_CHARGE_WINDOW_START", "23:00").strip() or "23:00",
        "night_window_end": os.getenv("KP_NIGHT_CHARGE_WINDOW_END", "07:00").strip() or "07:00",
        "day_discharge_window_start": day_start, "day_discharge_window_end": day_end,
        "discharge_fixed_window": f"{day_start}-{day_end}",
        "soc_charge_mode": plan_meta.get("device_soc_code"),
        "mode": "forced", "battery_operating_mode": "forced", "estimated_charge_power_kw": default_power_kw,
        "latest_soc_percent_at_schedule": latest_soc, "soc_source": soc_source, "soc_error": soc_error,
        "monitor_start_reason": monitor_start_reason, "required_night_charge_kwh_at_schedule": required_kwh,
        "estimated_charge_minutes": estimated_charge_minutes, "schedule_source": "03-monitor",
    }
    if charge_rate_info:
        detail.update({
            "estimated_charge_rate_percent_per_hour": charge_rate_info.get("percent_per_hour"),
            "charge_rate_source": charge_rate_info.get("source"),
            "charge_rate_sample_count": charge_rate_info.get("sample_count"),
            "required_charge_percent_at_schedule": charge_rate_info.get("required_charge_percent"),
        })
    try:
        client.collection("settings_events").document(event_id).set({
            "event_id": event_id, "run_id": event_id, "slot": "03", "profile": "forced-monitor",
            "status": "forced-started", "changed_fields_json": [], "detail_json": detail, "recorded_at": now_utc,
        }, merge=True)
        collection = client.collection("night_charge_plans")
        collection.document(plan_date).set({"monitor_schedule": detail, "monitor_schedule_updated_at": now_utc}, merge=True)
        collection.document("latest").set({"monitor_schedule": detail, "monitor_schedule_updated_at": now_utc}, merge=True)
        print(f"[cloud_job_runner] persisted 03-monitor schedule date={plan_date} start={charge_start_time} end={charge_end_time}", flush=True)
        return True
    except Exception as exc:
        print(f"[cloud_job_runner] 03-monitor schedule persistence failed: {exc}", flush=True)
        return False


def persist_03_no_charge_decision_to_firestore(
    *, plan_meta: PlanMeta, target_soc: float, latest_soc: float | None, required_kwh: float,
    open_firestore: FirestoreOpener, soc_source: str = "unknown",
) -> bool:
    client = open_firestore()
    if client is None:
        return False
    plan_date = str(plan_meta.get("date") or "").strip()
    if not plan_date:
        return False
    now_utc = datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds").replace("+00:00", "Z")
    event_id = f"{plan_date}-03-no-charge"
    detail = {
        "plan_date": plan_date,
        "plan_id": plan_meta.get("plan_id"),
        "target_soc_7_percent": target_soc,
        "charge_end_time": os.getenv("KP_NIGHT_CHARGE_WINDOW_END", "07:00").strip() or "07:00",
        "soc_charge_mode": None,
        "mode": "standby", "battery_operating_mode": "standby",
        "latest_soc_percent_at_schedule": latest_soc, "soc_source": soc_source,
        "required_night_charge_kwh_at_schedule": required_kwh, "schedule_source": "03-no-charge",
    }
    try:
        client.collection("settings_events").document(event_id).set({
            "event_id": event_id, "run_id": event_id, "slot": "03", "profile": "standby",
            "status": "skipped-no-charge", "changed_fields_json": [], "detail_json": detail, "recorded_at": now_utc,
        }, merge=True)
        collection = client.collection("night_charge_plans")
        collection.document(plan_date).set({"monitor_decision": detail, "monitor_decision_updated_at": now_utc}, merge=True)
        collection.document("latest").set({"monitor_decision": detail, "monitor_decision_updated_at": now_utc}, merge=True)
        print(f"[cloud_job_runner] persisted 03 no-charge decision date={plan_date}", flush=True)
        return True
    except Exception as exc:
        print(f"[cloud_job_runner] 03 no-charge decision persistence failed: {exc}", flush=True)
        return False


def persist_03_monitor_stop_reason(
    plan_meta: PlanMeta, reason: str, *, open_firestore: FirestoreOpener,
    soc_source: str | None = None, soc_error: str | None = None,
) -> bool:
    client = open_firestore()
    plan_date = str(plan_meta.get("date") or "").strip()
    if client is None or not plan_date:
        return False
    now_utc = datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds").replace("+00:00", "Z")
    try:
        payload: dict[str, Any] = {"monitor_stop_reason": reason, "monitor_stopped_at": now_utc}
        if soc_source is not None:
            payload.update({"soc_source": soc_source, "soc_error": soc_error})
        client.collection("settings_events").document(f"{plan_date}-03-monitor-schedule").set(payload, merge=True)
        return True
    except Exception as exc:
        print(f"[cloud_job_runner] 03-monitor stop reason persistence failed: {exc}", flush=True)
        return False

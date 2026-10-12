"""Bounded Firestore REST access; the control image needs no database SDK."""
from __future__ import annotations

import json
import os
from typing import Any, cast
from datetime import datetime
from urllib.parse import quote

from app.separated_runtime.plan import PublishedPlan


def _provenance(plan: PublishedPlan) -> dict[str, Any]:
    data = json.loads(plan.raw_json)
    rationale = data.get("decision_rationale")
    trace = rationale.get("pv_clear_ghi") if isinstance(rationale, dict) else None
    return trace if isinstance(trace, dict) else {}


def _provenance_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("plan publication provenance timestamp is missing")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.utcoffset() is None:
        raise ValueError("plan publication provenance requires timezone")
    return stamp


def _check_provenance(incoming: PublishedPlan, adopted: PublishedPlan) -> None:
    new, old = _provenance(incoming), _provenance(adopted)
    if not old:
        return  # Preserve legacy plans without inventing provider evidence.
    identity = ("model_id", "basis_version", "site_fingerprint")
    if not new or any(not old.get(key) or new.get(key) != old[key] for key in identity):
        raise ValueError("plan publication provenance model/site/basis mismatch")
    old_issued, new_issued = old.get("provider_issued_at"), new.get("provider_issued_at")
    if old_issued is not None:
        if new_issued is None or _provenance_time(new_issued) < _provenance_time(old_issued):
            raise ValueError("plan publication provider vintage cannot regress")
    elif new_issued is None:
        if _provenance_time(new.get("retrieved_at")) < _provenance_time(old.get("retrieved_at")):
            raise ValueError("plan publication retrieval order cannot regress")
        print(json.dumps({"event": "plan_publication_freshness_unknown", "stage": "control_cas",
                          "target_date": incoming.target_date, "control_version_id": incoming.version_id,
                          "reason_code": "provider_freshness_unknown"}), flush=True)
    old_pack, new_pack = old.get("calibration_pack"), new.get("calibration_pack")
    if not isinstance(old_pack, dict) or not isinstance(new_pack, dict):
        raise ValueError("plan publication calibration provenance missing")
    if _provenance_time(new_pack.get("training_as_of")) < _provenance_time(old_pack.get("training_as_of")):
        raise ValueError("plan publication calibration training vintage cannot regress")


def _validate_incoming_provenance(plan: PublishedPlan) -> None:
    trace = _provenance(plan)
    if not trace:
        return
    if any(not isinstance(trace.get(key), str) or not trace[key] for key in ("model_id", "basis_version", "site_fingerprint")):
        raise ValueError("plan publication provenance identity missing")
    generated = _provenance_time(json.loads(plan.raw_json)["generated_at"])
    if _provenance_time(trace.get("retrieved_at")) > generated:
        raise ValueError("plan publication retrieval is later than generation")
    pack = trace.get("calibration_pack")
    if not isinstance(pack, dict) or _provenance_time(pack.get("training_as_of")) > generated:
        raise ValueError("plan publication calibration provenance invalid")
    if trace.get("provider_issued_at") is not None:
        _provenance_time(trace["provider_issued_at"])


class FirestorePlanStore:
    def __init__(self, *, session: Any = None) -> None:
        project = os.environ["FIRESTORE_PROJECT_ID"]
        database = os.environ.get("FIRESTORE_DATABASE_ID", "(default)")
        self.root = f"projects/{project}/databases/{database}/documents"
        self.base = "https://firestore.googleapis.com/v1/" + self.root
        if session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession
            credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/datastore"])
            # google-auth's transport constructor has no complete type metadata.
            session = cast(Any, AuthorizedSession)(credentials, refresh_timeout=30)
        self.session = session

    def fetch(self, target_date: str, *, timeout_seconds: float = 60) -> PublishedPlan:
        response = self.session.get(self.base + "/control_plan_current/" + quote(target_date, safe=""),
                                    timeout=(10, timeout_seconds), max_allowed_time=timeout_seconds)
        if response.status_code != 200:
            raise RuntimeError("published control plan could not be read")
        document = json.loads(response.json()["fields"]["payload"]["stringValue"])
        return PublishedPlan.from_document(document, target_date=target_date)

    def publish(self, plan: PublishedPlan) -> None:
        _validate_incoming_provenance(plan)
        payload = json.dumps(plan.to_document(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(payload.encode("utf-8")) > 900_000:
            raise ValueError("encoded control plan exceeds the Firestore document budget")
        fields = {"payload": {"stringValue": payload}}
        # Both documents become visible together. A failed publication never
        # removes the last valid plan or mutates any prior content-addressed version.
        pointer = "/control_plan_current/" + plan.target_date
        incoming_time = datetime.fromisoformat(json.loads(plan.raw_json)["generated_at"].replace("Z", "+00:00"))
        for _ in range(3):
            current = self.session.get(self.base + pointer, timeout=(10, 60), max_allowed_time=60)
            if current.status_code == 404:
                precondition = {"exists": False}
            elif current.status_code == 200:
                stored = current.json()
                old = PublishedPlan.from_document(json.loads(stored["fields"]["payload"]["stringValue"]), target_date=plan.target_date)
                old_time = datetime.fromisoformat(json.loads(old.raw_json)["generated_at"].replace("Z", "+00:00"))
                if old_time > incoming_time or (old_time == incoming_time and old.version_id != plan.version_id):
                    raise ValueError("older or conflicting plan cannot replace the adopted plan")
                _check_provenance(plan, old)
                precondition = {"updateTime": stored["updateTime"]}
            else:
                raise RuntimeError("control plan publication could not read the adopted plan")
            writes = [{"update": {"name": self.root + "/control_plan_versions/" + plan.version_id, "fields": fields}},
                      {"update": {"name": self.root + pointer, "fields": fields}, "currentDocument": precondition}]
            response = self.session.post(self.base + ":commit", json={"writes": writes},
                                         timeout=(10, 60), max_allowed_time=60)
            if response.status_code == 200:
                return
            conflict = response.status_code in {409, 412} or (
                response.status_code == 400 and response.json().get("error", {}).get("status") == "FAILED_PRECONDITION")
            if not conflict:
                raise RuntimeError("control plan publication failed")
        raise RuntimeError("concurrent control plan publication did not converge")

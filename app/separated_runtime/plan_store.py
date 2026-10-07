"""Bounded Firestore REST access; the control image needs no database SDK."""
from __future__ import annotations

import json
import os
from typing import Any, cast
from datetime import datetime
from urllib.parse import quote

from app.separated_runtime.plan import PublishedPlan


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

"""Start the fixed, non-control actual-import job; web never receives KP credentials."""
from __future__ import annotations

import os
import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import google.auth
from google.auth.transport.requests import AuthorizedSession

from app.dashboard.firestore_repository import _open_dashboard_firestore_client


def import_job() -> tuple[str, str, str]:
    value = os.getenv("DASHBOARD_ACTUAL_IMPORT_JOB", "")
    match = re.fullmatch(r"projects/([a-z0-9-]+)/locations/([a-z0-9-]+)/jobs/(solar-actual-import)", value)
    if match is None:
        raise ValueError("actual import is not configured")
    return match.group(1), match.group(2), match.group(3)


def resolve_target_date(value: object = None) -> str:
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    target = today if value is None else date.fromisoformat(str(value))
    if target > today or (today - target).days > 1095:
        raise ValueError("import date is outside the supported past window")
    return target.isoformat()


def start_actual_import(target_date: str | None = None) -> dict[str, str]:
    project, region, job = import_job()
    target_date = resolve_target_date(target_date)
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    # Only the validated date override; no automatic HTTP/auth retry. An unknown submission must
    # be reconciled, never silently submitted a second time.
    session_factory: Any = AuthorizedSession
    with session_factory(credentials, max_refresh_attempts=0) as session:
        response = session.post(
            f"https://{region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/{project}/jobs/{job}:run",
            json={"overrides": {"containerOverrides": [{"env": [
                {"name": "ACTUAL_IMPORT_TARGET_DATE", "value": target_date},
            ]}]}}, timeout=20, allow_redirects=False,
        )
        response.raise_for_status()
        execution = response.json().get("metadata", {}).get("name")
    if not isinstance(execution, str) or not re.fullmatch(job + r"-[a-z0-9]+", execution):
        raise ValueError("actual import submission outcome is unknown")
    return {"status": "accepted", "execution": execution, "target_date": target_date}


def actual_import_status(execution: str) -> dict[str, Any]:
    _, _, job = import_job()
    if not re.fullmatch(job + r"-[a-z0-9]+", execution):
        raise ValueError("invalid import execution")
    row = (_open_dashboard_firestore_client().collection("dashboard_actual_import_runs")
           .document(execution).get(timeout=4, retry=None).to_dict() or {})
    return {key: row[key] for key in ("status", "completed_at", "reason", "stage", "actuals_saved", "target_date", "pending_dates", "missing_review") if key in row} or {"status": "pending"}

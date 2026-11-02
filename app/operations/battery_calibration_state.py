"""Bounded, read-only retrieval of the immediately preceding accepted plan state."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Callable

from app.backup.night_plan_archive import load_night_plan_detail_from_firestore_doc

LOGGER = logging.getLogger(__name__)
READ_TIMEOUT_SECONDS = 4.0


class _BoundedStorage:
    """Supply the canonical detail loader with a single bounded blob read."""
    def __init__(self, client: Any):
        self.client = client

    def bucket(self, name: str) -> _BoundedStorage:
        return _BoundedStorage(self.client.bucket(name))

    def blob(self, name: str) -> _BoundedStorage:
        return _BoundedStorage(self.client.blob(name))

    def download_as_bytes(self) -> bytes:
        payload = self.client.download_as_bytes(timeout=READ_TIMEOUT_SECONDS, retry=None)
        if not isinstance(payload, bytes):
            raise TypeError("previous plan download must return bytes")
        return payload


def _stamp(value: Any) -> datetime:
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.utcoffset() is None:
        raise ValueError("calibration state requires aware timestamps")
    return stamp


def load_previous_battery_calibration(
    *, target_date: str, as_of: datetime,
    client_factory: Callable[[], Any] | None = None,
    detail_loader: Callable[..., Any] = load_night_plan_detail_from_firestore_doc,
    storage_client: Any | None = None,
) -> dict[str, Any] | None:
    """Read at most one exact date document; never query latest or mutate state."""
    if os.getenv("DATA_BACKEND", "").strip().lower() != "firestore":
        return None
    try:
        cutoff = _stamp(as_of)
        previous_date = (date.fromisoformat(target_date) - timedelta(days=1)).isoformat()
        if client_factory is None:
            from app.operations.firestore import open_firestore
            client_factory = open_firestore
        snapshot = client_factory().collection("night_charge_plans").document(previous_date).get(
            timeout=READ_TIMEOUT_SECONDS, retry=None,
        )
        if not snapshot.exists:
            LOGGER.info("previous_calibration_unavailable reason=document_missing")
            return None
        document = snapshot.to_dict() or {}
        if document.get("date") != previous_date or _stamp(document.get("updated_at")) > cutoff:
            raise ValueError("previous document date or availability invalid")
        expected_sha = str(document.get("detail_sha256") or "")
        if len(expected_sha) != 64 or any(char not in "0123456789abcdef" for char in expected_sha):
            raise ValueError("previous plan checksum missing or invalid")
        if not document.get("plan_json") and storage_client is None:
            from google.cloud.storage import Client
            storage_client = Client()
        payload = detail_loader(document, storage_client=_BoundedStorage(storage_client) if storage_client is not None else None)
        if not isinstance(payload, dict):
            raise ValueError("previous plan payload missing")
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if hashlib.sha256(raw).hexdigest() != expected_sha:
            raise ValueError("previous plan checksum mismatch")
        if not isinstance(payload.get("forecast"), dict) or payload["forecast"].get("date") != previous_date:
            raise ValueError("previous source date mismatch")
        rationale = payload.get("decision_rationale")
        if not isinstance(rationale, dict):
            raise ValueError("previous rationale missing")
        state = {key: rationale[key] for key in ("battery_calibration", "hourly_pv_bias_model")
                 if isinstance(rationale.get(key), dict)}
        if not state:
            LOGGER.info("previous_calibration_unavailable reason=model_missing")
            return None
        for key, value in state.items():
            diagnostics = value.get("diagnostics", value)
            stamp = diagnostics.get("as_of", diagnostics.get("validated_at"))
            if _stamp(stamp) > cutoff:
                raise ValueError(f"previous {key} is from future")
        for key in ("generated_at", "created_at", "as_of"):
            if payload.get(key) is not None and _stamp(payload[key]) > cutoff:
                raise ValueError("previous payload is from future")
        return state
    except Exception as exc:
        # Never print cloud resource identifiers or exception messages.
        LOGGER.warning("previous_calibration_unavailable reason=%s", type(exc).__name__)
        return None

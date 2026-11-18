"""Bounded receipt checkpoint for publishing already committed actuals."""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
from typing import Any

MAX_RAW_BYTES = 8 * 1024 * 1024


def encode_checkpoint(captured: dict[str, Any], target_date: str) -> dict[str, str]:
    value = {key: captured[key] for key in ("payloads", "identity", "target_plan_version", "outside_epoch") if key in captured}
    raw = json.dumps({"target_date": target_date, "captured": value}, sort_keys=True, allow_nan=False).encode()
    encoded = base64.b64encode(gzip.compress(raw, mtime=0)).decode()
    if len(raw) > MAX_RAW_BYTES or len(encoded) > 700_000:
        raise ValueError("actual publication checkpoint exceeds receipt budget")
    return {"sha256": hashlib.sha256(raw).hexdigest(), "gzip_base64": encoded}


def decode_checkpoint(checkpoint: dict[str, str], target_date: str) -> dict[str, Any]:
    encoded = checkpoint["gzip_base64"]
    if len(encoded) > 700_000:
        raise ValueError("actual publication checkpoint exceeds receipt budget")
    with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(encoded, validate=True))) as stream:
        raw = stream.read(MAX_RAW_BYTES + 1)
    if len(raw) > MAX_RAW_BYTES or hashlib.sha256(raw).hexdigest() != checkpoint["sha256"]:
        raise ValueError("actual publication checkpoint checksum mismatch")
    value = json.loads(raw)
    captured = value["captured"]
    if value["target_date"] != target_date or not isinstance(captured, dict) or not all(
        key in captured for key in ("payloads", "identity", "target_plan_version", "outside_epoch")
    ):
        raise ValueError("actual publication checkpoint target or scope mismatch")
    return dict(captured)

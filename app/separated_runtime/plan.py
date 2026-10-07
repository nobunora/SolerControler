"""Lossless, validated transfer of a calculated plan to the control worker."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PublishedPlan:
    target_date: str
    raw_json: str
    sha256: str
    charge_rate_info: dict[str, Any]
    producer_source_revision: str = ""
    inputs_verified: bool = False

    @classmethod
    def from_bytes(cls, raw: bytes, *, target_date: str, charge_rate_info: dict[str, Any], producer_source_revision: str = "", inputs_verified: bool = False) -> PublishedPlan:
        # HISTORICAL_FAILURE_LOCK: preserve the original calculated bytes and
        # values. Missing/NaN inputs are not zero; a previous day is not a fallback.
        date.fromisoformat(target_date)
        if len(raw) > 750_000:
            raise ValueError("control plan exceeds the bounded document size")
        plan = json.loads(raw)
        # Reject non-finite values anywhere before publication; JSON nulls remain null.
        json.dumps(plan, allow_nan=False)
        issued = datetime.fromisoformat(plan["generated_at"].replace("Z", "+00:00"))
        if issued.tzinfo is None:
            raise ValueError("control plan generation time must include timezone")
        if plan.get("forecast", {}).get("date") != target_date:
            raise ValueError("control plan target date mismatch")
        result = plan["result"]
        for key in ("target_soc_7_percent", "required_night_charge_kwh", "effective_capacity_kwh"):
            value = result.get(key)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                raise ValueError(f"control plan invalid {key}")
        if not 0 <= result["target_soc_7_percent"] <= 100:
            raise ValueError("control plan target SOC outside 0..100")
        if result["required_night_charge_kwh"] < 0 or result["effective_capacity_kwh"] <= 0:
            raise ValueError("control plan invalid charge/capacity")
        rate = charge_rate_info.get("percent_per_hour")
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate <= 0:
            raise ValueError("control plan invalid charging rate")
        text = raw.decode("utf-8")
        return cls(target_date, text, hashlib.sha256(raw).hexdigest(), dict(charge_rate_info), producer_source_revision, inputs_verified)

    def to_document(self) -> dict[str, Any]:
        return {"schema_version": 1, "target_date": self.target_date, "raw_json": self.raw_json,
                "sha256": self.sha256, "charge_rate_info": self.charge_rate_info,
                "producer_source_revision": self.producer_source_revision, "inputs_verified": self.inputs_verified}

    @property
    def version_id(self) -> str:
        content = json.dumps(self.to_document(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @classmethod
    def from_document(cls, document: dict[str, Any], *, target_date: str) -> PublishedPlan:
        if document.get("schema_version") != 1 or document.get("target_date") != target_date:
            raise ValueError("unsupported or stale control plan")
        published = cls.from_bytes(document["raw_json"].encode("utf-8"), target_date=target_date,
                                   charge_rate_info=document["charge_rate_info"],
                                   producer_source_revision=document.get("producer_source_revision", ""),
                                   inputs_verified=document.get("inputs_verified") is True)
        if document.get("sha256") != published.sha256:
            raise ValueError("control plan checksum mismatch")
        return published

    def restore(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".part")
        temporary.write_bytes(self.raw_json.encode("utf-8"))
        temporary.replace(path)

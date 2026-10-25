from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from .models import WriteResult


class AuditSink(Protocol):
    def record(self, result: WriteResult) -> None: ...


class NullAuditSink:
    def record(self, result: WriteResult) -> None:
        return None


class JsonlAuditSink:
    """Append-only local audit for semantic ECHONET write results."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def record(self, result: WriteResult) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(result)
        payload["timestamp"] = datetime.now(UTC).isoformat()
        payload["outcome"] = result.outcome.value
        payload["target"] = result.target.target
        payload["epc"] = f"{result.epc:02X}"
        with self._path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str))
            handle.write("\n")

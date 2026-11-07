"""Pure dependency selection for current-environment recovery captures."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit


@dataclass(frozen=True)
class ObjectReference:
    bucket: str
    name: str
    generation: str | None = None
    reason: str = field(default="saved_reference", compare=False)
    decoded_sha256: str | None = field(default=None, compare=False)


def decode_fields(fields: dict[str, Any]) -> dict[str, Any]:
    def decode(value: dict[str, Any]) -> Any:
        if "mapValue" in value:
            return decode_fields(value["mapValue"].get("fields", {}))
        if "arrayValue" in value:
            return [decode(item) for item in value["arrayValue"].get("values", [])]
        for key in ("stringValue", "integerValue", "doubleValue", "booleanValue", "timestampValue", "referenceValue"):
            if key in value:
                return value[key]
        return None
    return {key: decode(value) for key, value in fields.items()}


def object_references(value: Any, *, reason: str, depth: int = 0) -> list[ObjectReference]:
    if depth > 40:
        raise ValueError("Dependency nesting exceeds the source contract")
    result: list[ObjectReference] = []
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("{", "[")):
            try:
                nested = json.loads(text)
            except ValueError:
                return []
            return object_references(nested, reason=reason, depth=depth + 1)
        if text.startswith("gs://"):
            return object_references({"uri": text}, reason=reason, depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            result.extend(object_references(item, reason=reason, depth=depth + 1))
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str) and item.startswith("gs://"):
                effective_key = str(value.get("name", key)) if key == "value" else str(key)
                parsed = urlsplit(item)
                if not parsed.netloc or parsed.fragment:
                    raise ValueError("Unsupported object reference")
                # Namespace configuration is not an object; all live objects in
                # the captured buckets are already roots of the required set.
                if effective_key.lower().endswith("prefix") or not parsed.path.strip("/"):
                    continue
                query = parse_qs(parsed.query)
                if set(query) - {"generation"} or len(query.get("generation", [])) > 1:
                    raise ValueError("Unsupported object reference query")
                stem = effective_key.removesuffix("_gcs_uri").removesuffix("_uri")
                generation = (query.get("generation", [None])[0] or value.get(stem + "_generation")
                              or value.get(effective_key + "_generation") or value.get("generation"))
                if generation is not None and not re.fullmatch(r"[0-9]+", str(generation)):
                    raise ValueError("Invalid object generation reference")
                digest = value.get(stem + "_sha256") or value.get(effective_key + "_sha256")
                if digest is None and effective_key in {"uri", "gcs_uri"}:
                    digest = value.get("sha256")
                if digest is not None and (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)):
                    raise ValueError("Invalid original content hash")
                result.append(ObjectReference(parsed.netloc, parsed.path.lstrip("/"),
                                              str(generation) if generation is not None else None, reason,
                                              digest.lower() if digest is not None else None))
            else:
                result.extend(object_references(item, reason=reason, depth=depth + 1))
    return result


def pointer_references(bucket: str, name: str, value: Any) -> list[ObjectReference]:
    if not name.endswith("/current.json") or not isinstance(value, dict):
        return []
    prefix = name.rsplit("/", 1)[0]
    if "/pv_ideal_max/" in "/" + name:
        digest = value.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid ideal-maximum pointer")
        return [ObjectReference(bucket, prefix + "/snapshots/" + digest + ".sqlite.gz",
                                reason="current_ideal_maximum_ledger", decoded_sha256=digest)]
    if "calibration_sha256" in value and "clear_days" in value:
        result = [ObjectReference(bucket, prefix + "/calibrations/" + value["calibration_sha256"] + ".json",
                                  reason="current_calibration")]
        for record in value["clear_days"]:
            for source in ("actual", "certification"):
                result.append(ObjectReference(bucket, f"{prefix}/originals/{source}/{record['date']}/{record[source + '_sha256']}.json",
                                              reason="current_calibration_original"))
        return result
    return []

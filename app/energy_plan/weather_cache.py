"""Small same-target forecast cache using existing local/Firestore storage."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone as utc
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any
from zoneinfo import ZoneInfo

from app.forecasting.pv_clear_ghi import clear_ghi_hour, normalize_radiation_intervals, _repair_short_gaps


def log_failure(stage: str, error: Exception) -> None:
    print(json.dumps({"event": "weather_capability_unavailable", "stage": stage,
                      "error_type": type(error).__name__}), flush=True)


def validate_forecast(forecast: dict[str, Any], *, target: str, latitude: float, longitude: float,
                      timezone: str, as_of: datetime | None = None) -> None:
    now = as_of or datetime.now(utc.utc)
    retrieved = datetime.fromisoformat(forecast["pv_forecast_retrieved_at"])
    if (forecast.get("date") != target or retrieved.utcoffset() is None or retrieved > now):
        raise ValueError("weather target or retrieval time is invalid")
    issued = forecast.get("pv_provider_issued_at")
    if issued is not None:
        stamp = datetime.fromisoformat(issued)
        if stamp.utcoffset() is None or stamp > retrieved:
            raise ValueError("weather issuance is invalid")
    rows = forecast["pv_radiation_rows"]
    raw = json.dumps({"target_date": target, "timezone": timezone, "source_rows": rows},
                     sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    if hashlib.sha256(raw).hexdigest() != forecast.get("pv_forecast_input_sha256"):
        raise ValueError("weather radiation checksum mismatch")
    day = date.fromisoformat(target)
    energy, _ = normalize_radiation_intervals(tuple(rows), target_date=day, timezone=timezone)
    clear = tuple(clear_ghi_hour(day, hour, latitude=latitude, longitude=longitude, timezone=timezone)
                  for hour in range(24))
    _repair_short_gaps(clear, energy)


def _site(latitude: float, longitude: float, timezone: str) -> dict[str, Any]:
    return {"latitude": latitude, "longitude": longitude, "timezone": timezone}


def _key(site: dict[str, Any], target: str, vintage: str) -> str:
    digest = hashlib.sha256(json.dumps(site, sort_keys=True).encode()).hexdigest()
    return f"{digest}-{target}-{vintage}"


def _local_path(key: str) -> Path | None:
    directory = os.environ.get("ARTIFACTS_DIR")
    return Path(directory) / "weather_forecast_cache" / f"{key}.json" if directory else None


def _firestore() -> Any:
    if os.environ.get("DATA_BACKEND", "").lower() != "firestore":
        return None
    from app.operations.firestore import open_firestore
    return open_firestore()


def _keep_newer(existing: dict[str, Any], forecast: dict[str, Any], site: dict[str, Any]) -> bool:
    try:
        old = existing["forecast"]
        validate_forecast(old, target=forecast["date"], **site)
        return bool(datetime.fromisoformat(old["pv_forecast_retrieved_at"]) >=
                    datetime.fromisoformat(forecast["pv_forecast_retrieved_at"]))
    except (KeyError, TypeError, ValueError):
        return False


def save_forecast(forecast: dict[str, Any], *, latitude: float, longitude: float, timezone: str) -> None:
    site = _site(latitude, longitude, timezone)
    target = str(forecast["date"])
    validate_forecast(forecast, target=target, **site)
    acquired = datetime.fromisoformat(forecast["pv_forecast_retrieved_at"])
    vintage = acquired.astimezone(ZoneInfo(timezone)).date().isoformat()
    key = _key(site, target, vintage)
    value = {"site": site, "forecast": forecast}
    path = _local_path(key)
    if path is not None:
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                existing = json.loads(path.read_text()) if path.exists() else {}
            except (OSError, ValueError) as error:
                log_failure("local_cache_read", error)
                existing = {}
            if not _keep_newer(existing, forecast, site):
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    json.dump(value, stream, allow_nan=False)
                temporary.replace(path)
        except Exception as error:
            log_failure("local_cache_save", error)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    try:
        client = _firestore()
        if client is not None:
            document = client.collection("weather_forecast_cache").document(key)
            existing = document.get(timeout=4, retry=None)
            if not _keep_newer(existing.to_dict() or {}, forecast, site):
                if existing.exists:
                    document.update(value, option=client.write_option(last_update_time=existing.update_time),
                                    timeout=4, retry=None)
                else:
                    document.create(value, timeout=4, retry=None)
    except Exception as error:
        log_failure("persistent_cache_save", error)


def load_forecast(*, target: str, latitude: float, longitude: float, timezone: str) -> dict[str, Any] | None:
    site = _site(latitude, longitude, timezone)
    try:
        client = _firestore()
    except Exception as error:
        log_failure("persistent_cache_open", error)
        client = None
    # Retain an already acquired newer forecast before falling back to yesterday.
    # Selecting an older vintage would violate same-day plan publication ordering.
    for vintage in (target, (date.fromisoformat(target) - timedelta(days=1)).isoformat()):
        key = _key(site, target, vintage)
        candidates = []
        path = _local_path(key)
        if path is not None and path.exists():
            try:
                candidates.append(json.loads(path.read_text()))
            except Exception as error:
                log_failure("local_cache_read", error)
        if client is not None:
            try:
                candidates.append(client.collection("weather_forecast_cache").document(key).get(
                    timeout=4, retry=None).to_dict() or {})
            except Exception as error:
                log_failure("persistent_cache_read", error)
        valid = []
        for value in candidates:
            if not value:
                continue
            try:
                if value.get("site") != site:
                    raise ValueError("weather cache site mismatch")
                forecast = value["forecast"]
                validate_forecast(forecast, target=target, **site)
                acquired = datetime.fromisoformat(forecast["pv_forecast_retrieved_at"])
                if acquired.astimezone(ZoneInfo(timezone)).date().isoformat() != vintage:
                    raise ValueError("weather cache vintage mismatch")
                valid.append(forecast)
            except Exception as error:
                log_failure("cache_validation", error)
        if valid:
            return dict(max(valid, key=lambda item: datetime.fromisoformat(item["pv_forecast_retrieved_at"])))
    return None

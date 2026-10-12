"""Observed satellite inputs and immutable per-day calibration provenance."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import requests

from app.forecasting.pv_clear_ghi import BASIS_VERSION, solar_altitude

SATELLITE_ENDPOINT = "https://satellite-api.open-meteo.com/v1/archive"
SATELLITE_MODEL = "satellite_radiation_seamless"
CERTIFICATION_RULE_VERSION = "satellite_core_alt10_sun55_ghi08_v1"


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def site_fingerprint(*, latitude: float, longitude: float, timezone_name: str, arrays: object) -> str:
    return hashlib.sha256(canonical_bytes({"latitude": latitude, "longitude": longitude, "timezone": timezone_name, "arrays": arrays})).hexdigest()


def _number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
        raise ValueError("invalid observed nonnegative number")
    return float(value)


def fetch_satellite_original(*, start_date: date, end_date: date, latitude: float, longitude: float, timezone_name: str,
                             timeout_seconds: float = 30.0) -> tuple[dict[str, Any], datetime]:
    """One bounded non-control request; completion time is conservative availability."""
    if end_date < start_date or (end_date - start_date).days > 1095:
        raise ValueError("satellite range exceeds explicit three-year bootstrap budget")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("satellite acquisition deadline expired")
    params: dict[str, str | float] = {
        "latitude": latitude, "longitude": longitude, "timezone": timezone_name,
        "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
        "models": SATELLITE_MODEL, "hourly": "shortwave_radiation,sunshine_duration",
    }
    connect_timeout = min(5.0, timeout_seconds / 2)
    response = requests.get(SATELLITE_ENDPOINT, params=params, timeout=(connect_timeout, min(25.0, timeout_seconds - connect_timeout)))
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("hourly"), dict):
        raise ValueError("satellite response has no hourly object")
    # Do not persist/log response coordinates, URLs, or secret-bearing request metadata.
    selected = {"hourly": payload["hourly"], "hourly_units": payload.get("hourly_units", {}), "model": SATELLITE_MODEL}
    canonical_bytes(selected)
    return selected, datetime.now(timezone.utc)


def observed_day_records(
    actual_rows: list[dict[str, Any]], satellite: Mapping[str, Any], *,
    target_date: date, actual_available_at: datetime, certification_available_at: datetime,
    latitude: float, longitude: float, timezone_name: str, fingerprint: str,
    previous: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep all saved clear days; update complete observed dates without retroactive timestamps."""
    hourly = satellite.get("hourly")
    units = satellite.get("hourly_units")
    if not isinstance(hourly, dict) or not isinstance(units, dict):
        raise ValueError("observed hourly data/units missing")
    if units.get("shortwave_radiation") != "W/m²" or units.get("sunshine_duration") != "s":
        raise ValueError("unexpected satellite radiation/sunshine units")
    times, ghi, sun = (hourly.get(k) for k in ("time", "shortwave_radiation", "sunshine_duration"))
    if not isinstance(times, list) or not isinstance(ghi, list) or not isinstance(sun, list) or len(times) != len(ghi) or len(times) != len(sun):
        raise ValueError("incomplete observed hourly arrays")
    zone = ZoneInfo(timezone_name)
    observed: dict[datetime, tuple[float | None, float | None]] = {}
    for timestamp, radiation, sunshine in zip(times, ghi, sun, strict=True):
        end = datetime.fromisoformat(str(timestamp))
        end = end.replace(tzinfo=zone) if end.tzinfo is None else end.astimezone(zone)
        if end.minute or end.second or end.microsecond:
            raise ValueError("non-hourly satellite timestamp")
        interval = end - timedelta(hours=1)
        if interval in observed:
            raise ValueError("duplicate satellite interval")
        observed[interval] = (_number(radiation), _number(sunshine))
    samples: dict[date, dict[tuple[int, int], float | None]] = {}
    for row in actual_rows:
        timestamp = row.get("dt")
        if not isinstance(timestamp, datetime):
            raise ValueError("actual calibration timestamp missing")
        local = timestamp.replace(tzinfo=zone) if timestamp.tzinfo is None else timestamp.astimezone(zone)
        if local.date() >= target_date or local.hour not in range(5, 18):
            continue
        if local.minute not in (0, 30) or local.second or local.microsecond:
            raise ValueError("actual samples must start on half-hours")
        key = (local.hour, local.minute)
        day_samples = samples.setdefault(local.date(), {})
        value = _number(row.get("pv"))
        if key in day_samples and day_samples[key] != value:
            raise ValueError("conflicting actual halfhour samples")
        day_samples[key] = value
    saved = {str(r["date"]): r for r in previous}
    excluded: dict[str, list[str]] = {}
    retained_observation_gaps: list[str] = []
    checked_actual_sha256_by_date: dict[str, str] = {}
    for day, values in sorted(samples.items()):
        if any(values.get((hour, minute)) is None for hour in range(5, 18) for minute in (0, 30)):
            excluded.setdefault("actual_halfhour_missing", []).append(day.isoformat())
            # A correction to missing data invalidates a formerly certified day.
            saved.pop(day.isoformat(), None)
            continue
        checked_actual_source = {"date": day.isoformat(), "samples": [
            {"hour": hour, "actual_halfhour_kwh": [values[(hour, 0)], values[(hour, 30)]]}
            for hour in range(5, 18)
        ]}
        checked_actual_sha256_by_date[day.isoformat()] = hashlib.sha256(canonical_bytes(checked_actual_source)).hexdigest()
        hours: list[dict[str, Any]] = []
        certification_source: list[dict[str, Any]] = []
        core: list[bool] = []
        complete = True
        for hour in range(5, 18):
            altitude = solar_altitude(day, hour + 0.5, latitude=latitude, longitude=longitude, timezone=timezone_name)
            sine = max(0.0, math.sin(math.radians(altitude)))
            central = 1098 * sine * math.exp(-0.059 / sine) if sine > 0 else 0.0
            interval = datetime(day.year, day.month, day.day, hour, tzinfo=zone)
            radiation, sunshine = observed.get(interval, (None, None))
            if radiation is None or sunshine is None:
                complete = False
                break
            minutes = sunshine / 60
            if minutes > 60:
                raise ValueError("observed sunshine exceeds one hour")
            if altitude >= 10:
                core.append(minutes >= 55 and central > 0 and radiation / central >= 0.8)
            hours.append({"hour": hour, "actual_halfhour_kwh": [values[(hour, 0)], values[(hour, 30)]],
                          "solar_altitude_deg": altitude, "sunshine_minutes": minutes,
                          "satellite_ghi_w_m2": radiation, "central_haurwitz_w_m2": central})
            certification_source.append({"time": (interval + timedelta(hours=1)).isoformat(), "shortwave_radiation": radiation, "sunshine_duration": sunshine})
        if not complete:
            excluded.setdefault("satellite_hour_missing", []).append(day.isoformat())
            if day.isoformat() in saved:
                retained_observation_gaps.append(day.isoformat())
            # Provider gaps do not replace an existing verified observed original.
            continue
        if not core or not all(core):
            excluded.setdefault("not_certified_clear", []).append(day.isoformat())
            saved.pop(day.isoformat(), None)
            continue
        actual_source = {"date": day.isoformat(), "samples": [{"hour": h["hour"], "actual_halfhour_kwh": h["actual_halfhour_kwh"]} for h in hours]}
        cert_source = {"date": day.isoformat(), "model": SATELLITE_MODEL, "rule": CERTIFICATION_RULE_VERSION, "hourly": certification_source}
        ahash = hashlib.sha256(canonical_bytes(actual_source)).hexdigest()
        chash = hashlib.sha256(canonical_bytes(cert_source)).hexdigest()
        old = saved.get(day.isoformat(), {})
        saved[day.isoformat()] = {
            "date": day.isoformat(), "actual_sha256": ahash, "certification_sha256": chash,
            "actual_available_at": old.get("actual_available_at") if old.get("actual_sha256") == ahash else actual_available_at.isoformat(),
            "certification_available_at": old.get("certification_available_at") if old.get("certification_sha256") == chash else certification_available_at.isoformat(),
            "certification_rule_version": CERTIFICATION_RULE_VERSION, "certified_clear": True,
            "site_fingerprint": fingerprint, "basis_version": BASIS_VERSION,
            "hours": hours, "actual_source": actual_source, "certification_source": cert_source,
        }
    result = [saved[key] for key in sorted(saved) if date.fromisoformat(key) < target_date]
    print(json.dumps({"event": "pv_calibration_observations", "target_date": target_date.isoformat(),
                      "actual_days_checked": len(samples), "saved_clear_days": len(result),
                      "excluded_dates": excluded, "retained_observation_gaps": retained_observation_gaps,
                      "checked_actual_sha256_by_date": checked_actual_sha256_by_date}), flush=True)
    return result


def training_records(records: list[dict[str, Any]]) -> tuple[Mapping[str, object], ...]:
    result: list[Mapping[str, object]] = []
    for record in records:
        converted = dict(record)
        converted["date"] = date.fromisoformat(record["date"])
        for key in ("actual_available_at", "certification_available_at"):
            converted[key] = datetime.fromisoformat(record[key])
        converted["hours"] = tuple({**h, "actual_halfhour_kwh": tuple(h["actual_halfhour_kwh"])} for h in record["hours"])
        result.append(converted)
    return tuple(result)

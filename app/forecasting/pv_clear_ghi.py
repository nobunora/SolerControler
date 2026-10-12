"""Pure clear-sky annual-basis PV forecasts and completed calibration packs."""

from __future__ import annotations

import calendar
import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone as utc_timezone
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

MODEL_ID = "physical_clear_ghi_annual1"
BASIS_VERSION = "noaa_geometric_1min_midpoint_annual1_v1"
TERMS = ("1", "x", "cos_phi", "sin_phi")
# G03: the 30 candidate doubles accepted saved prefixes versus 10, with next-day
# clear-cap sensitivity 0.0631 vs 0.0610 kWh/hour. The 100 candidate adds annual
# extrapolation sensitivity without reducing next-day sensitivity. See G03 evidence.
CONDITION_NUMBER_LIMIT = 30.0
_PACK_KEYS = frozenset({
    "schema_version", "model_id", "basis_version", "site_fingerprint", "terms",
    "theta4", "training_dates", "training_samples", "rank", "condition_number",
    "training_as_of", "created_at", "usable_from", "actual_source_sha256",
    "certification_source_sha256", "certification_rule_version",
})


def _number(value: object, name: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}: numeric value required")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name}: finite value required") from exc
    if not math.isfinite(result) or (nonnegative and result < 0):
        raise ValueError(f"{name}: finite{' nonnegative' if nonnegative else ''} value required")
    return result


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name}: integer >= {minimum} required")
    return value


def _day(value: object, name: str) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError(f"{name}: date required")
    return value


def _aware(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name}: timezone-aware datetime required")
    return value


def _instant(value: datetime) -> datetime:
    return value.astimezone(utc_timezone.utc)


def _hash(value: object, name: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{name}: lowercase SHA256 required")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}: nonempty string required")
    return value


def canonical_pack_bytes(pack: object) -> bytes:
    """The same canonical encoding is used for packs and source-reference arrays."""
    return json.dumps(pack, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("calibration_pack: duplicate JSON key")
        result[key] = value
    return result


def _decode_pack(raw_bytes: bytes, sha256: str) -> dict[str, Any]:
    if not isinstance(raw_bytes, bytes):
        raise ValueError("raw_bytes: immutable bytes required")
    if hashlib.sha256(raw_bytes).hexdigest() != _hash(sha256, "sha256"):
        raise ValueError("calibration_pack: hash mismatch")
    pack = json.loads(raw_bytes.decode("utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(pack, dict) or set(pack) != _PACK_KEYS:
        raise ValueError("calibration_pack: schema keys mismatch")
    if canonical_pack_bytes(pack) != raw_bytes:
        raise ValueError("calibration_pack: noncanonical bytes")
    if _integer(pack["schema_version"], "schema_version") != 1:
        raise ValueError("calibration_pack: unsupported schema")
    if pack["model_id"] != MODEL_ID or pack["basis_version"] != BASIS_VERSION:
        raise ValueError("calibration_pack: model/basis mismatch")
    if pack["terms"] != list(TERMS):
        raise ValueError("calibration_pack: terms mismatch")
    _text(pack["site_fingerprint"], "site_fingerprint")
    _hash(pack["actual_source_sha256"], "actual_source_sha256")
    _hash(pack["certification_source_sha256"], "certification_source_sha256")
    _text(pack["certification_rule_version"], "certification_rule_version")
    if not isinstance(pack["theta4"], list) or len(pack["theta4"]) != 4:
        raise ValueError("calibration_pack: theta4 required")
    for value in pack["theta4"]:
        _number(value, "theta4")
    if not isinstance(pack["training_dates"], list) or not pack["training_dates"]:
        raise ValueError("calibration_pack: training_dates required")
    dates = tuple(date.fromisoformat(value) for value in pack["training_dates"])
    if tuple(sorted(set(dates))) != dates:
        raise ValueError("calibration_pack: sorted unique dates required")
    samples = _integer(pack["training_samples"], "training_samples", minimum=4)
    if samples != 13 * len(dates):
        raise ValueError("calibration_pack: sample count inconsistent with training dates")
    if _integer(pack["rank"], "rank") != 4:
        raise ValueError("calibration_pack: rank four required")
    condition = _number(pack["condition_number"], "condition_number")
    if not 1.0 <= condition <= CONDITION_NUMBER_LIMIT:
        raise ValueError("calibration_pack: unstable condition number")
    stamps = {key: _aware(datetime.fromisoformat(pack[key]), key)
              for key in ("training_as_of", "created_at", "usable_from")}
    if (_instant(stamps["training_as_of"]) > _instant(stamps["created_at"])
            or _instant(stamps["created_at"]) > _instant(stamps["usable_from"])):
        raise ValueError("calibration_pack: invalid availability order")
    return pack


@dataclass(frozen=True)
class ClearGhiCalibration:
    model_id: str
    basis_version: str
    site_fingerprint: str
    theta: tuple[float, float, float, float]
    training_dates: tuple[date, ...]
    training_samples: int
    rank: int
    condition_number: float
    training_as_of: datetime
    usable_from: datetime
    raw_bytes: bytes
    sha256: str

    def __post_init__(self) -> None:
        pack = _decode_pack(self.raw_bytes, self.sha256)
        if not isinstance(self.theta, tuple) or not isinstance(self.training_dates, tuple):
            raise ValueError("calibration: immutable tuple fields required")
        for day in self.training_dates:
            _day(day, "training_dates")
        _aware(self.training_as_of, "training_as_of")
        _aware(self.usable_from, "usable_from")
        expected = (
            pack["model_id"], pack["basis_version"], pack["site_fingerprint"],
            tuple(pack["theta4"]), tuple(date.fromisoformat(day) for day in pack["training_dates"]),
            pack["training_samples"], pack["rank"], pack["condition_number"],
            datetime.fromisoformat(pack["training_as_of"]), datetime.fromisoformat(pack["usable_from"]),
        )
        actual = (self.model_id, self.basis_version, self.site_fingerprint, self.theta,
                  self.training_dates, self.training_samples, self.rank, self.condition_number,
                  self.training_as_of, self.usable_from)
        if actual != expected:
            raise ValueError("calibration: fields differ from completed pack")
        for value in self.theta:
            _number(value, "theta")
        _integer(self.training_samples, "training_samples", minimum=4)
        _integer(self.rank, "rank")
        _number(self.condition_number, "condition_number")


def calibration_from_bytes(raw_bytes: bytes, *, sha256: str) -> ClearGhiCalibration:
    """Decode one completed original; never search for another calibration."""
    pack = _decode_pack(raw_bytes, sha256)
    return ClearGhiCalibration(
        model_id=pack["model_id"], basis_version=pack["basis_version"],
        site_fingerprint=pack["site_fingerprint"], theta=tuple(pack["theta4"]),
        training_dates=tuple(date.fromisoformat(value) for value in pack["training_dates"]),
        training_samples=pack["training_samples"], rank=pack["rank"],
        condition_number=pack["condition_number"],
        training_as_of=datetime.fromisoformat(pack["training_as_of"]),
        usable_from=datetime.fromisoformat(pack["usable_from"]), raw_bytes=raw_bytes, sha256=sha256,
    )


def validate_calibration_for_run(
    calibration: ClearGhiCalibration, *, expected_site: str, expected_basis: str,
    target_date: date, as_of: datetime,
) -> None:
    _day(target_date, "target_date")
    _aware(as_of, "as_of")
    if calibration.site_fingerprint != expected_site or calibration.basis_version != expected_basis:
        raise ValueError("calibration: site/basis mismatch")
    if any(day >= target_date for day in calibration.training_dates):
        raise ValueError("calibration: training date is not before target date")
    if (_instant(calibration.training_as_of) > _instant(as_of)
            or _instant(calibration.usable_from) > _instant(as_of)):
        raise ValueError("calibration: unavailable at run as_of")


@dataclass(frozen=True)
class ClearGhiForecast:
    target_date: date
    as_of: datetime
    calibration: ClearGhiCalibration
    forecast_hash: str
    retrieved_at: datetime
    provider_issued_at: datetime | None
    source_times: tuple[datetime | None, ...]
    q_kwh_m2: tuple[float, ...]
    f_kwh_m2: tuple[float | None, ...]
    c_raw: tuple[float, ...]
    pv_kwh: tuple[float, ...]
    repaired_hours: tuple[int, ...]

    def __post_init__(self) -> None:
        _day(self.target_date, "target_date")
        _aware(self.as_of, "as_of")
        _aware(self.retrieved_at, "retrieved_at")
        _hash(self.forecast_hash, "forecast_hash")
        if _instant(self.retrieved_at) > _instant(self.as_of):
            raise ValueError("forecast: retrieved after as_of")
        if self.provider_issued_at is not None:
            _aware(self.provider_issued_at, "provider_issued_at")
            if _instant(self.provider_issued_at) > _instant(self.retrieved_at):
                raise ValueError("forecast: provider issued after retrieval")
        validate_calibration_for_run(
            self.calibration, expected_site=self.calibration.site_fingerprint,
            expected_basis=BASIS_VERSION, target_date=self.target_date, as_of=self.as_of,
        )
        for values in (self.source_times, self.q_kwh_m2, self.f_kwh_m2, self.c_raw, self.pv_kwh):
            if not isinstance(values, tuple) or len(values) != 24:
                raise ValueError("forecast: immutable 24-hour tuples required")
        if (not isinstance(self.repaired_hours, tuple)
                or tuple(sorted(set(self.repaired_hours))) != self.repaired_hours
                or len(self.repaired_hours) > 2):
            raise ValueError("forecast: invalid repaired hours")
        for hour in self.repaired_hours:
            if _integer(hour, "repaired_hours") > 23:
                raise ValueError("forecast: invalid repaired hour")
        for hour in range(24):
            q = _number(self.q_kwh_m2[hour], "Q", nonnegative=True)
            f = self.f_kwh_m2[hour]
            if f is not None:
                _number(f, "F", nonnegative=True)
            elif q > 0:
                raise ValueError("forecast: missing daytime F")
            c = _number(self.c_raw[hour], "C")
            expected_c = sum(t * value for t, value in zip(self.calibration.theta, _annual_features(self.target_date, hour)))
            if not math.isfinite(expected_c) or c != expected_c:
                raise ValueError("forecast: C differs from annual calibration")
            pv = _number(self.pv_kwh[hour], "PV", nonnegative=True)
            expected = 0.0 if q == 0 else max(0.0, c) * min(_number(f, "F", nonnegative=True), q)
            if not math.isfinite(expected) or pv != expected:
                raise ValueError("forecast: PV differs from single formula")
            if self.source_times[hour] is not None:
                _aware(self.source_times[hour], "source_time")
            if hour in self.repaired_hours and q == 0:
                raise ValueError("forecast: geometric night is not a repaired hour")


def _geometry_inputs(day: date, latitude: float, longitude: float, timezone: str) -> ZoneInfo:
    _day(day, "day")
    lat, lon = _number(latitude, "latitude"), _number(longitude, "longitude")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("geometry: invalid coordinates")
    return ZoneInfo(timezone)


def _solar_altitude(day: date, local_hour: float, latitude: float, longitude: float, zone: ZoneInfo) -> float:
    year_days = 366 if calendar.isleap(day.year) else 365
    gamma = 2 * math.pi / year_days * (day.timetuple().tm_yday - 1 + (local_hour - 12) / 24)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
                       - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    decl = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
            - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
            - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma))
    offset = datetime.combine(day, time(12), tzinfo=zone).utcoffset()
    if offset is None:
        raise ValueError("geometry: timezone offset unavailable")
    solar_minutes = (local_hour * 60 + eqtime + 4 * longitude - offset.total_seconds() / 60) % 1440
    hour_angle = math.radians(solar_minutes / 4 - 180)
    lat = math.radians(latitude)
    cos_z = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(hour_angle)
    return math.degrees(math.asin(min(1.0, max(-1.0, cos_z))))


def clear_ghi_hour(day: date, hour: int, *, latitude: float, longitude: float, timezone: str) -> float:
    zone = _geometry_inputs(day, latitude, longitude, timezone)
    if _integer(hour, "hour") > 23:
        raise ValueError("hour: expected 0..23")
    values = []
    for minute in range(60):
        altitude = _solar_altitude(day, hour + (minute + 0.5) / 60, latitude, longitude, zone)
        sine = max(0.0, math.sin(math.radians(altitude)))
        values.append(1098 * sine * math.exp(-0.059 / sine) if sine > 0 else 0.0)
    return _number(sum(values) / 60 / 1000, "Q", nonnegative=True)


def solar_altitude(day: date, local_hour: float, *, latitude: float, longitude: float, timezone: str) -> float:
    """Geometric NOAA altitude for observed clear-day certification (no refraction)."""
    zone = _geometry_inputs(day, latitude, longitude, timezone)
    local_hour = _number(local_hour, "local_hour")
    if not 0 <= local_hour < 24:
        raise ValueError("local_hour: expected [0,24)")
    return _solar_altitude(day, local_hour, latitude, longitude, zone)


def normalize_radiation_intervals(
    source_rows: tuple[Mapping[str, object], ...], *, target_date: date, timezone: str,
) -> tuple[tuple[float | None, ...], tuple[datetime | None, ...]]:
    _day(target_date, "target_date")
    zone = ZoneInfo(timezone)
    if not isinstance(source_rows, tuple):
        raise ValueError("source_rows: tuple required")
    energy: list[float | None] = [None] * 24
    times: list[datetime | None] = [None] * 24
    seen: set[datetime] = set()
    for row in source_rows:
        if not isinstance(row, Mapping) or "shortwave_radiation_w_m2" not in row:
            raise ValueError("source row: original radiation field required")
        raw_time = row.get("time")
        if not isinstance(raw_time, str):
            raise ValueError("source time: original ISO timestamp required")
        stamp = datetime.fromisoformat(raw_time)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=zone)
        if stamp.minute or stamp.second or stamp.microsecond:
            raise ValueError("source time: hourly boundary required")
        instant = _instant(stamp)
        if instant in seen:
            raise ValueError("source time: duplicate timestamp")
        seen.add(instant)
        value = row.get("shortwave_radiation_w_m2")
        f = None if value is None else _number(value, "shortwave", nonnegative=True) / 1000
        interval = stamp.astimezone(zone) - timedelta(hours=1)
        if interval.date() != target_date:
            continue
        if times[interval.hour] is not None:
            raise ValueError("radiation: duplicate generation interval")
        energy[interval.hour], times[interval.hour] = f, stamp
    return tuple(energy), tuple(times)


def _repair_short_gaps(q: tuple[float, ...], energy: tuple[float | None, ...]) -> tuple[tuple[float | None, ...], tuple[int, ...]]:
    missing = [h for h in range(24) if q[h] > 0 and energy[h] is None]
    if len(missing) > 2:
        raise ValueError("radiation: daytime missing total exceeds two")
    repaired = list(energy)
    groups: list[list[int]] = []
    for hour in missing:
        if not groups or hour != groups[-1][-1] + 1:
            groups.append([])
        groups[-1].append(hour)
    for group in groups:
        left, right = group[0] - 1, group[-1] + 1
        if (left < 0 or right > 23 or q[left] <= 0 or q[right] <= 0
                or energy[left] is None or energy[right] is None):
            raise ValueError("radiation: missing daylight endpoint")
        kl = min(_number(energy[left], "left F", nonnegative=True) / q[left], 1.0)
        kr = min(_number(energy[right], "right F", nonnegative=True) / q[right], 1.0)
        for hour in group:
            k = kl + (kr - kl) * (hour - left) / (right - left)
            repaired[hour] = _number(k * q[hour], "repaired F", nonnegative=True)
    return tuple(repaired), tuple(missing)


def _annual_features(day: date, hour: int) -> tuple[float, float, float, float]:
    year_days = 366 if calendar.isleap(day.year) else 365
    phi = 2 * math.pi * (day.timetuple().tm_yday - 1) / year_days
    return 1.0, (hour + 0.5 - 12) / 6, math.cos(phi), math.sin(phi)


def forecast_clear_ghi(
    *, target_date: date, as_of: datetime, latitude: float, longitude: float, timezone: str,
    calibration: ClearGhiCalibration, source_rows: tuple[Mapping[str, object], ...],
    forecast_hash: str, retrieved_at: datetime, provider_issued_at: datetime | None,
) -> ClearGhiForecast:
    _aware(retrieved_at, "retrieved_at")
    _hash(forecast_hash, "forecast_hash")
    if _instant(retrieved_at) > _instant(_aware(as_of, "as_of")):
        raise ValueError("forecast: retrieved after as_of")
    if provider_issued_at is not None and _instant(_aware(provider_issued_at, "provider_issued_at")) > _instant(retrieved_at):
        raise ValueError("forecast: provider issued after retrieval")
    validate_calibration_for_run(calibration, expected_site=calibration.site_fingerprint,
                                 expected_basis=BASIS_VERSION, target_date=target_date, as_of=as_of)
    q = tuple(clear_ghi_hour(target_date, h, latitude=latitude, longitude=longitude, timezone=timezone) for h in range(24))
    energy, source_times = normalize_radiation_intervals(source_rows, target_date=target_date, timezone=timezone)
    energy, repaired = _repair_short_gaps(q, energy)
    c = tuple(_number(sum(t * f for t, f in zip(calibration.theta, _annual_features(target_date, h))), "C") for h in range(24))
    pv = tuple(_number(0.0 if q[h] == 0 else max(0.0, c[h]) * min(_number(energy[h], "F", nonnegative=True), q[h]), "PV", nonnegative=True) for h in range(24))
    return ClearGhiForecast(target_date, as_of, calibration, forecast_hash, retrieved_at,
                            provider_issued_at, source_times, q, energy, c, pv, repaired)


def fit_clear_ghi_calibration(
    clear_days: tuple[Mapping[str, object], ...], *, target_date: date, as_of: datetime,
    site: Mapping[str, object], basis_version: str,
) -> ClearGhiCalibration:
    _day(target_date, "target_date")
    _aware(as_of, "as_of")
    if basis_version != BASIS_VERSION or not isinstance(clear_days, tuple):
        raise ValueError("fit: basis mismatch or non-tuple input")
    latitude = _number(site.get("latitude"), "latitude")
    longitude = _number(site.get("longitude"), "longitude")
    zone = _text(site.get("timezone"), "timezone")
    fingerprint = _text(site.get("site_fingerprint"), "site_fingerprint")
    _geometry_inputs(target_date, latitude, longitude, zone)
    selected: dict[date, Mapping[str, object]] = {}
    for item in clear_days:
        day = _day(item.get("date"), "training date")
        actual_at = _aware(item.get("actual_available_at"), "actual_available_at")
        certified_at = _aware(item.get("certification_available_at"), "certification_available_at")
        if day >= target_date or max(_instant(actual_at), _instant(certified_at)) > _instant(as_of):
            raise ValueError("fit: target/future/unavailable training original")
        if item.get("site_fingerprint") != fingerprint or item.get("basis_version") != basis_version:
            raise ValueError("fit: training site/basis mismatch")
        _hash(item.get("actual_sha256"), "actual_sha256")
        _hash(item.get("certification_sha256"), "certification_sha256")
        _text(item.get("certification_rule_version"), "certification_rule_version")
        if not isinstance(item.get("certified_clear"), bool):
            raise ValueError("fit: certified_clear bool required")
        previous = selected.get(day)
        if previous is not None:
            prior_at = max(_instant(_aware(previous["actual_available_at"], "actual_available_at")),
                           _instant(_aware(previous["certification_available_at"], "certification_available_at")))
            latest_at = max(_instant(actual_at), _instant(certified_at))
            if latest_at == prior_at and (item["actual_sha256"], item["certification_sha256"]) != (previous["actual_sha256"], previous["certification_sha256"]):
                raise ValueError("fit: conflicting versions at identical availability")
            if latest_at <= prior_at:
                continue
        selected[day] = item
    matrix: list[list[float]] = []
    targets: list[float] = []
    used: list[Mapping[str, object]] = []
    rules: set[str] = set()
    for day, item in sorted(selected.items()):
        if item["certified_clear"] is not True:
            continue
        rows = item.get("hours")
        if not isinstance(rows, tuple):
            raise ValueError("fit: immutable hours tuple required")
        hourly: dict[int, Mapping[str, object]] = {}
        complete = True
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError("fit: hour mapping required")
            hour = _integer(row.get("hour"), "training hour")
            if hour not in range(5, 18) or hour in hourly:
                raise ValueError("fit: invalid/duplicate training interval")
            hourly[hour] = row
            pair = row.get("actual_halfhour_kwh")
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ValueError("fit: halfhour pair tuple required")
            if None in pair:
                complete = False
            for value in pair:
                if value is not None:
                    _number(value, "actual halfhour", nonnegative=True)
        if set(hourly) != set(range(5, 18)) or not complete:
            continue
        day_matrix, day_targets = [], []
        has_certification_core = False
        for hour, row in sorted(hourly.items()):
            altitude = _number(row.get("solar_altitude_deg"), "solar altitude")
            sunshine = _number(row.get("sunshine_minutes"), "sunshine minutes", nonnegative=True)
            if sunshine > 60:
                raise ValueError("fit: sunshine exceeds interval")
            central = _number(row.get("central_haurwitz_w_m2"), "central Haurwitz", nonnegative=True)
            observed = row.get("satellite_ghi_w_m2")
            observed_value = None if observed is None else _number(observed, "satellite GHI", nonnegative=True)
            if altitude >= 10:
                has_certification_core = True
                if sunshine < 55 or observed_value is None or central <= 0 or observed_value / central < 0.8:
                    raise ValueError("fit: certified-clear original contradicts certification rule")
            q = clear_ghi_hour(day, hour, latitude=latitude, longitude=longitude, timezone=zone)
            day_matrix.append([_number(q * f, "design row") for f in _annual_features(day, hour)])
            pair = row["actual_halfhour_kwh"]
            if not isinstance(pair, tuple):
                raise ValueError("fit: halfhour pair required")
            day_targets.append(_number(sum(pair), "actual hourly", nonnegative=True))
        if not has_certification_core:
            raise ValueError("fit: certified-clear original has no certification core")
        if day_matrix:
            matrix.extend(day_matrix)
            targets.extend(day_targets)
            used.append(item)
            rules.add(str(item["certification_rule_version"]))
    if not used or len(rules) != 1:
        raise ValueError("fit: no certified training days or mixed certification versions")
    theta, _, rank, singular = np.linalg.lstsq(np.array(matrix), np.array(targets), rcond=None)
    if int(rank) != 4 or not np.isfinite(theta).all() or not np.isfinite(singular).all():
        raise ValueError("fit: rank-deficient or nonfinite result")
    condition = _number(float(singular[0] / singular[-1]), "condition number")
    if condition > CONDITION_NUMBER_LIMIT:
        raise ValueError("fit: unstable condition number")
    def refs(key: str) -> list[dict[str, object]]:
        return [{"date": _day(item["date"], "training date").isoformat(), "sha256": item[key]} for item in used]
    pack = {
        "schema_version": 1, "model_id": MODEL_ID, "basis_version": BASIS_VERSION,
        "site_fingerprint": fingerprint, "terms": list(TERMS), "theta4": theta.tolist(),
        "training_dates": [_day(item["date"], "training date").isoformat() for item in used],
        "training_samples": len(matrix), "rank": int(rank), "condition_number": condition,
        "training_as_of": as_of.isoformat(), "created_at": as_of.isoformat(), "usable_from": as_of.isoformat(),
        "actual_source_sha256": hashlib.sha256(canonical_pack_bytes(refs("actual_sha256"))).hexdigest(),
        "certification_source_sha256": hashlib.sha256(canonical_pack_bytes(refs("certification_sha256"))).hexdigest(),
        "certification_rule_version": next(iter(rules)),
    }
    raw = canonical_pack_bytes(pack)
    return calibration_from_bytes(raw, sha256=hashlib.sha256(raw).hexdigest())

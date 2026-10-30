"""Forecast-free clear-sky capacity with one non-decaying equipment coefficient."""

from __future__ import annotations

import calendar
import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo

from app.forecasting.pv_clear_ghi import solar_altitude

MODEL_ID = "pv_ideal_max_global_v1"
BASIS_VERSION = "noaa_haurwitz_erbs_poa_stc25_1min_v1"
TOLERANCE_KWH = Decimal("0.01")


def _number(value: float, name: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}: a finite number is required")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ValueError(f"{name}: invalid value")
    return result


def _instant(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("an aware timestamp is required")
    return value.astimezone(timezone.utc)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@dataclass(frozen=True)
class IdealMaxArray:
    azimuth_deg: float
    tilt_deg: float
    capacity_kw: float
    performance_ratio: float
    shading_factor: float

    def __post_init__(self) -> None:
        for name in ("azimuth_deg", "tilt_deg", "capacity_kw", "performance_ratio", "shading_factor"):
            object.__setattr__(self, name, _number(getattr(self, name), name, minimum=-math.inf if name == "azimuth_deg" else 0.0))
        object.__setattr__(self, "azimuth_deg", self.azimuth_deg % 360)
        if self.tilt_deg > 90 or self.capacity_kw <= 0:
            raise ValueError("invalid array geometry or capacity")


@dataclass(frozen=True)
class IdealMaxGeometry:
    latitude: float
    longitude: float
    timezone_name: str
    arrays: tuple[IdealMaxArray, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "latitude", _number(self.latitude, "latitude", minimum=-90))
        object.__setattr__(self, "longitude", _number(self.longitude, "longitude", minimum=-180))
        if self.latitude > 90 or self.longitude > 180:
            raise ValueError("invalid coordinates")
        ZoneInfo(self.timezone_name)
        if not isinstance(self.arrays, tuple) or not self.arrays or any(not isinstance(a, IdealMaxArray) for a in self.arrays):
            raise ValueError("a nonempty immutable array configuration is required")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(_canonical({"model_id": MODEL_ID, "basis_version": BASIS_VERSION,
                                          **asdict(self)})).hexdigest()


def _position(day: date, local_hour: float, geometry: IdealMaxGeometry) -> tuple[float, float]:
    altitude = solar_altitude(day, local_hour, latitude=geometry.latitude,
                              longitude=geometry.longitude, timezone=geometry.timezone_name)
    year_days = 366 if calendar.isleap(day.year) else 365
    phase = 2 * math.pi / year_days * (day.timetuple().tm_yday - 1 + (local_hour - 12) / 24)
    equation = 229.18 * (0.000075 + 0.001868 * math.cos(phase) - 0.032077 * math.sin(phase)
                        - 0.014615 * math.cos(2 * phase) - 0.040849 * math.sin(2 * phase))
    declination = (0.006918 - 0.399912 * math.cos(phase) + 0.070257 * math.sin(phase)
                   - 0.006758 * math.cos(2 * phase) + 0.000907 * math.sin(2 * phase)
                   - 0.002697 * math.cos(3 * phase) + 0.00148 * math.sin(3 * phase))
    offset = datetime.combine(day, time(12), tzinfo=ZoneInfo(geometry.timezone_name)).utcoffset()
    if offset is None:
        raise ValueError("timezone offset unavailable")
    solar_minutes = (local_hour * 60 + equation + 4 * geometry.longitude - offset.total_seconds() / 60) % 1440
    angle = math.radians(solar_minutes / 4 - 180)
    latitude = math.radians(geometry.latitude)
    azimuth = (math.degrees(math.atan2(math.sin(angle), math.cos(angle) * math.sin(latitude)
                                      - math.tan(declination) * math.cos(latitude))) + 180) % 360
    return altitude, azimuth


def _diffuse_fraction(clearness: float) -> float:
    if clearness <= 0.22:
        return 1 - 0.09 * clearness
    if clearness <= 0.8:
        return 0.9511 - 0.1604 * clearness + 4.388 * clearness**2 - 16.638 * clearness**3 + 12.336 * clearness**4
    return 0.165


def ideal_max_base(day: date, hour: int, geometry: IdealMaxGeometry) -> float:
    """Return one hour's STC25 clear-POA energy, without temperature or weather."""
    if not isinstance(day, date) or isinstance(day, datetime) or type(hour) is not int or not 0 <= hour <= 23:
        raise ValueError("a calendar date and integer hour 0..23 are required")
    samples = []
    for minute in range(60):
        altitude, azimuth = _position(day, hour + (minute + 0.5) / 60, geometry)
        sine = max(0.0, math.sin(math.radians(altitude)))
        ghi = 1098 * sine * math.exp(-0.059 / sine) if sine > 0 else 0.0
        samples.append((altitude, azimuth, sine, ghi))
    clear_energy = sum(sample[3] for sample in samples) / 60000
    phase = 2 * math.pi * (day.timetuple().tm_yday - 1) / 365
    extra = 1366.1 * (1.00011 + 0.034221 * math.cos(phase) + 0.00128 * math.sin(phase)
                      + 0.000719 * math.cos(2 * phase) + 0.000077 * math.sin(2 * phase))
    extra_horizontal = sum(extra * sample[2] for sample in samples) / 60
    fraction = _diffuse_fraction(clear_energy * 1000 / extra_horizontal if extra_horizontal > 0 else 0.0)
    energy = 0.0
    for altitude, azimuth, sine, ghi in samples:
        # Frozen research reference: low-altitude POA decomposition, not an
        # observation eligibility threshold or a coefficient-update exception.
        diffuse = fraction * ghi if altitude >= 3 else ghi
        direct = (ghi - diffuse) / sine if altitude >= 3 and sine > 0 else 0.0
        for array in geometry.arrays:
            tilt = math.radians(array.tilt_deg)
            incidence = (sine * math.cos(tilt) + math.cos(math.radians(altitude))
                         * math.sin(tilt) * math.cos(math.radians(azimuth - array.azimuth_deg)))
            poa = (direct * max(0.0, incidence) + diffuse * (1 + math.cos(tilt)) / 2
                   + 0.2 * ghi * (1 - math.cos(tilt)) / 2)
            energy += array.capacity_kw * poa / 1000 / 60 * array.performance_ratio * array.shading_factor
    return _number(energy, "clear reference energy")


@dataclass(frozen=True)
class IdealMaxState:
    fingerprint: str
    coefficient: float
    revision: int
    updated_at: datetime

    def __post_init__(self) -> None:
        if len(self.fingerprint) != 64 or any(c not in "0123456789abcdef" for c in self.fingerprint):
            raise ValueError("invalid geometry fingerprint")
        object.__setattr__(self, "coefficient", _number(self.coefficient, "coefficient", minimum=1.0))
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("invalid state revision")
        _instant(self.updated_at)
        if self.revision == 0 and self.coefficient != 1.0:
            raise ValueError("initial state must have coefficient one")


@dataclass(frozen=True)
class HourlyPvObservation:
    interval_start: datetime
    first_half_kwh: float
    second_half_kwh: float
    available_at: datetime

    def __post_init__(self) -> None:
        start = _instant(self.interval_start)
        if self.interval_start.minute or self.interval_start.second or self.interval_start.microsecond:
            raise ValueError("observation must start on an hourly boundary")
        if _instant(self.available_at) < start + timedelta(hours=1):
            raise ValueError("observation is not a completed hour")
        object.__setattr__(self, "first_half_kwh", _number(self.first_half_kwh, "first half-hour"))
        object.__setattr__(self, "second_half_kwh", _number(self.second_half_kwh, "second half-hour"))
        _number(self.actual_kwh, "hourly actual")

    @property
    def actual_kwh(self) -> float:
        return self.first_half_kwh + self.second_half_kwh

    @property
    def source_sha256(self) -> str:
        return hashlib.sha256(_canonical({"interval_start": _instant(self.interval_start).isoformat(),
                                          "halfhour_kwh": [self.first_half_kwh, self.second_half_kwh]})).hexdigest()


@dataclass(frozen=True)
class IdealMaxUpdate:
    observation: HourlyPvObservation
    base_kwh: float
    maximum_before_kwh: float
    state_before: IdealMaxState
    state_after: IdealMaxState
    exceeded: bool


@dataclass(frozen=True)
class IdealMaxPrediction:
    fingerprint: str
    interval_start: datetime
    issued_at: datetime
    base_kwh: float
    coefficient: float
    revision: int

    @property
    def maximum_kwh(self) -> float:
        return _number(self.base_kwh * self.coefficient, "issued ideal maximum")


def initial_ideal_max_state(geometry: IdealMaxGeometry, *, as_of: datetime) -> IdealMaxState:
    return IdealMaxState(geometry.fingerprint, 1.0, 0, as_of)


def _validate_state(state: IdealMaxState, geometry: IdealMaxGeometry, as_of: datetime) -> None:
    if state.fingerprint != geometry.fingerprint:
        raise ValueError("ideal maximum state belongs to a different geometry or basis")
    if _instant(state.updated_at) > _instant(as_of):
        raise ValueError("ideal maximum state is unavailable at as_of")


def calculate_ideal_max(day: date, geometry: IdealMaxGeometry, state: IdealMaxState, *, as_of: datetime) -> tuple[float, ...]:
    """Calculate 24 hourly ceilings from one fixed, already available state."""
    _validate_state(state, geometry, as_of)
    return tuple(_number(ideal_max_base(day, hour, geometry) * state.coefficient, "ideal maximum") for hour in range(24))


def predict_ideal_max_hour(interval_start: datetime, geometry: IdealMaxGeometry, state: IdealMaxState,
                           *, as_of: datetime) -> IdealMaxPrediction:
    _validate_state(state, geometry, as_of)
    _instant(interval_start)
    start = interval_start.astimezone(ZoneInfo(geometry.timezone_name))
    if start.minute or start.second or start.microsecond:
        raise ValueError("prediction must use an hourly boundary")
    prediction = IdealMaxPrediction(geometry.fingerprint, start, as_of,
                                    ideal_max_base(start.date(), start.hour, geometry), state.coefficient, state.revision)
    _number(prediction.maximum_kwh, "issued maximum")
    return prediction


def exceeds_ideal_max(actual_kwh: float, maximum_kwh: float) -> bool:
    actual = _number(actual_kwh, "actual energy")
    maximum = _number(maximum_kwh, "maximum energy")
    # Canonical decimals preserve the inclusive boundary for finite binary64.
    with localcontext() as context:
        context.prec = 1000
        return Decimal(str(actual)) - Decimal(str(maximum)) > TOLERANCE_KWH


def update_ideal_max(state: IdealMaxState, geometry: IdealMaxGeometry, observation: HourlyPvObservation,
                     *, as_of: datetime) -> IdealMaxUpdate:
    """Score before commit. Only observed excess greater than 0.01 kWh raises C."""
    _validate_state(state, geometry, as_of)
    if not _instant(state.updated_at) <= _instant(observation.available_at) <= _instant(as_of):
        raise ValueError("observation availability is outside the state/as_of interval")
    start = observation.interval_start.astimezone(ZoneInfo(geometry.timezone_name))
    if start.minute or start.second or start.microsecond:
        raise ValueError("observation is not aligned to the geometry timezone")
    base = ideal_max_base(start.date(), start.hour, geometry)
    maximum = _number(base * state.coefficient, "ideal maximum before update")
    exceeded = exceeds_ideal_max(observation.actual_kwh, maximum)
    after = state
    if exceeded:
        if base == 0:
            raise ValueError("a multiplicative coefficient cannot represent a zero-base exceedance")
        coefficient = _number(observation.actual_kwh / base, "observed coefficient", minimum=state.coefficient)
        if base * coefficient < observation.actual_kwh:
            coefficient = _number(math.nextafter(coefficient, math.inf), "observed coefficient")
        after = replace(state, coefficient=coefficient, revision=state.revision + 1, updated_at=observation.available_at)
    return IdealMaxUpdate(observation, base, maximum, state, after, exceeded)

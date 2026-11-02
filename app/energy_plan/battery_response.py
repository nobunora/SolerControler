"""Empirical SOC response to AC-side battery energy, without symmetric losses."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Any


def _coefficient(value: float, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric, not boolean")
    result = float(value)
    if not isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return result


def _drift(value: float) -> float:
    if isinstance(value, bool):
        raise ValueError("drift must be numeric, not boolean")
    result = float(value)
    if not isfinite(result) or result < 0:
        raise ValueError("drift must be finite and nonnegative")
    return result


@dataclass(frozen=True)
class BatterySocResponse:
    """SOC percentage points per AC kWh; drift is SOC points lost per hour."""

    charge_soc_per_kwh: float
    discharge_soc_per_kwh: float
    drift_soc_per_hour: float = 0.0
    hourly_charge_soc_per_kwh: dict[int, float] = field(default_factory=dict)
    hourly_discharge_soc_per_kwh: dict[int, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        charge = _coefficient(self.charge_soc_per_kwh, "charge response")
        discharge = _coefficient(self.discharge_soc_per_kwh, "discharge response")
        if charge > discharge:
            raise ValueError("charge response must not exceed discharge response")
        drift = _drift(self.drift_soc_per_hour)
        object.__setattr__(self, "charge_soc_per_kwh", charge)
        object.__setattr__(self, "discharge_soc_per_kwh", discharge)
        object.__setattr__(self, "drift_soc_per_hour", drift)
        object.__setattr__(self, "hourly_charge_soc_per_kwh", dict(self.hourly_charge_soc_per_kwh))
        object.__setattr__(self, "hourly_discharge_soc_per_kwh", dict(self.hourly_discharge_soc_per_kwh))
        for mapping in (self.hourly_charge_soc_per_kwh, self.hourly_discharge_soc_per_kwh):
            for hour, value in mapping.items():
                if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour < 24:
                    raise ValueError("response hour must be an integer from 0 to 23")
                _coefficient(value, "hourly response")
        for hour in range(24):
            if self.charge_coefficient(hour) > self.discharge_coefficient(hour):
                raise ValueError("hourly charge response must not exceed discharge response")

    def charge_coefficient(self, hour: int) -> float:
        if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour < 24:
            raise ValueError("hour must be from 0 to 23")
        return self.hourly_charge_soc_per_kwh.get(hour, self.charge_soc_per_kwh)

    def discharge_coefficient(self, hour: int) -> float:
        if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour < 24:
            raise ValueError("hour must be from 0 to 23")
        return self.hourly_discharge_soc_per_kwh.get(hour, self.discharge_soc_per_kwh)

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "coefficient_unit": "soc_percentage_points_per_ac_kwh",
            "charge_soc_per_kwh": self.charge_soc_per_kwh,
            "discharge_soc_per_kwh": self.discharge_soc_per_kwh,
            "drift_soc_per_hour": self.drift_soc_per_hour,
            "hourly_charge_soc_per_kwh": {str(k): v for k, v in self.hourly_charge_soc_per_kwh.items()},
            "hourly_discharge_soc_per_kwh": {str(k): v for k, v in self.hourly_discharge_soc_per_kwh.items()},
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> BatterySocResponse:
        if isinstance(payload.get("schema_version", 1), bool) or payload.get("schema_version", 1) != 1:
            raise ValueError("unsupported battery response schema")
        if any(isinstance(key, bool) for name in ("hourly_charge_soc_per_kwh", "hourly_discharge_soc_per_kwh") for key in payload.get(name, {})):
            raise ValueError("response hour must not be boolean")
        return cls(
            charge_soc_per_kwh=_coefficient(payload["charge_soc_per_kwh"], "charge response"),
            discharge_soc_per_kwh=_coefficient(payload["discharge_soc_per_kwh"], "discharge response"),
            drift_soc_per_hour=_drift(payload.get("drift_soc_per_hour", 0.0)),
            hourly_charge_soc_per_kwh={int(k): _coefficient(v, "hourly charge response") for k, v in payload.get("hourly_charge_soc_per_kwh", {}).items()},
            hourly_discharge_soc_per_kwh={int(k): _coefficient(v, "hourly discharge response") for k, v in payload.get("hourly_discharge_soc_per_kwh", {}).items()},
        )

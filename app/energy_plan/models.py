from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PlanDocumentV1:
    csv_paths: list[str]
    plan_quality: dict[str, Any]
    forecast: dict[str, Any]
    pv_array_forecast: dict[str, Any] | None
    historical_profile: dict[str, Any]
    consumption_forecast: dict[str, Any]
    base_consumption_forecast: dict[str, Any]
    weather_history: dict[str, Any]
    occupancy_adjustment: dict[str, Any] | None
    coefficients: dict[str, Any]
    inputs: dict[str, Any]
    result: dict[str, Any]
    daytime_soc_optimization: dict[str, Any] | None
    decision_rationale: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        """Serialize with the stable V1 field names consumed by jobs and dashboards."""
        return {
            "csv_paths": self.csv_paths,
            "plan_quality": self.plan_quality,
            "forecast": self.forecast,
            "pv_array_forecast": self.pv_array_forecast,
            "historical_profile": self.historical_profile,
            "consumption_forecast": self.consumption_forecast,
            "base_consumption_forecast": self.base_consumption_forecast,
            "weather_history": self.weather_history,
            "occupancy_adjustment": self.occupancy_adjustment,
            "coefficients": self.coefficients,
            "inputs": self.inputs,
            "result": self.result,
            "daytime_soc_optimization": self.daytime_soc_optimization,
            "decision_rationale": self.decision_rationale,
        }

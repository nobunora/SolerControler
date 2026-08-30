from __future__ import annotations

import pytest

from app.domain.weather import open_meteo_weather_class
from app.energy_plan.weather_history import weather_class as energy_plan_weather_class
from app.forecasting.pv_array_calibration import _weather_class_from_code
from app.operations.shadow_gate import weather_class as shadow_weather_class


OPERATIONAL_WEATHER_CASES = [
    (None, "unknown"),
    (0, "clear"),
    (1, "cloudy"),
    (3, "cloudy"),
    (45, "fog"),
    (48, "fog"),
    (51, "rain"),
    (67, "rain"),
    (80, "rain"),
    (82, "rain"),
    (71, "snow"),
    (77, "snow"),
    (85, "snow"),
    (86, "snow"),
    (95, "storm"),
    (99, "storm"),
    (4, "other"),
    (44, "other"),
    (50, "other"),
    (100, "other"),
]


@pytest.mark.parametrize(("weather_code", "expected"), OPERATIONAL_WEATHER_CASES)
def test_open_meteo_weather_class_has_operational_mapping(
    weather_code: int | None, expected: str
) -> None:
    assert open_meteo_weather_class(weather_code) == expected


@pytest.mark.parametrize(("weather_code", "expected"), OPERATIONAL_WEATHER_CASES)
def test_operational_weather_class_compatibility_wrappers(
    weather_code: int | None, expected: str
) -> None:
    assert energy_plan_weather_class(weather_code) == expected
    assert _weather_class_from_code(weather_code) == expected


@pytest.mark.parametrize(
    ("weather_code", "operational", "frozen"),
    [(1, "cloudy", "clear"), (80, "rain", "shower")],
)
def test_shadow_gate_weather_class_is_intentionally_not_operational(
    weather_code: int, operational: str, frozen: str
) -> None:
    assert open_meteo_weather_class(weather_code) == operational
    assert shadow_weather_class(weather_code) == frozen

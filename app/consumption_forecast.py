from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import fmean
from typing import Any, Iterable, Mapping

import numpy as np

from app.utils import parse_csv_float

try:
    from sklearn.ensemble import HistGradientBoostingRegressor
except Exception:  # pragma: no cover - sklearn unavailable in some environments
    HistGradientBoostingRegressor = None  # type: ignore[assignment]


BASE_FEATURE_NAMES = [
    "month",
    "weekday",
    "weather_code",
    "is_weekend",
    "temp",
    "heating_degree",
    "cooling_degree",
    "sunshine_hours",
    "precipitation",
]
HISTORY_FEATURE_NAMES = [
    "lag1",
    "lag7",
    "rolling_7",
    "rolling_14",
    "same_weekday_avg",
]
MODEL_FEATURE_NAMES = [*BASE_FEATURE_NAMES, *HISTORY_FEATURE_NAMES]
CATEGORICAL_FEATURE_INDICES = [0, 1, 2]


@dataclass(frozen=True)
class LoadObservation:
    dt: datetime
    load_kwh: float


@dataclass(frozen=True)
class DailyWeatherFeatures:
    date: date
    temp: float
    weather_code: int | str
    sunshine_hours: float = 0.0
    precipitation: float = 0.0
    extras: Mapping[str, float] | None = None


@dataclass(frozen=True)
class ConsumptionForecast:
    target_date: date
    morning_load_kwh: float
    daytime_load_kwh: float
    source: str
    sample_count: int
    features: list[str]


@dataclass(frozen=True)
class _NormalizedWeather:
    date: date
    temp: float
    weather_code: int | str
    sunshine_hours: float
    precipitation: float
    extras: dict[str, float]


@dataclass(frozen=True)
class _FallbackPrediction:
    value: float
    source: str


class ConsumptionForecaster:
    def __init__(
        self,
        *,
        min_training_days: int = 45,
        fallback_window: int = 14,
        random_state: int = 42,
    ) -> None:
        self.min_training_days = min_training_days
        self.fallback_window = max(1, fallback_window)
        self.random_state = random_state
        self._daily_loads: dict[date, dict[str, float]] = {}
        self._weather_by_date: dict[date, _NormalizedWeather] = {}
        self._weather_code_to_int: dict[str, int] = {}
        self._sample_count = 0
        self._morning_model: HistGradientBoostingRegressor | None = None
        self._daytime_model: HistGradientBoostingRegressor | None = None

    def fit(
        self,
        load_rows: Iterable[LoadObservation | Mapping[str, Any]],
        weather_rows: Iterable[DailyWeatherFeatures | Mapping[str, Any]],
    ) -> ConsumptionForecaster:
        self._daily_loads = self._aggregate_daily_loads(load_rows)
        self._weather_by_date = self._normalize_weather_rows(weather_rows)
        self._weather_code_to_int = self._build_weather_code_mapping()
        self._sample_count = 0
        self._morning_model = None
        self._daytime_model = None

        training_dates = [
            current_date
            for current_date in sorted(self._daily_loads)
            if current_date in self._weather_by_date
        ]
        if not training_dates:
            return self

        morning_x, morning_y = self._build_training_matrix(training_dates, "morning_load_kwh")
        daytime_x, daytime_y = self._build_training_matrix(training_dates, "daytime_load_kwh")
        self._sample_count = min(len(morning_y), len(daytime_y))

        if (
            HistGradientBoostingRegressor is None
            or self._sample_count < self.min_training_days
            or self._sample_count < 2
        ):
            return self

        self._morning_model = self._fit_model(morning_x, morning_y)
        self._daytime_model = self._fit_model(daytime_x, daytime_y)
        if self._morning_model is None or self._daytime_model is None:
            self._morning_model = None
            self._daytime_model = None
        return self

    def predict(
        self,
        target_date: date | datetime | str,
        weather_row: DailyWeatherFeatures | Mapping[str, Any] | None = None,
    ) -> ConsumptionForecast:
        normalized_target_date = _coerce_date(target_date)
        weather = self._resolve_weather(normalized_target_date, weather_row)
        can_use_model = (
            self._morning_model is not None
            and self._daytime_model is not None
            and weather is not None
        )

        if can_use_model:
            morning_feature_map = self._build_feature_map(
                normalized_target_date,
                weather,
                "morning_load_kwh",
            )
            daytime_feature_map = self._build_feature_map(
                normalized_target_date,
                weather,
                "daytime_load_kwh",
            )
            morning_prediction = self._predict_value(self._morning_model, morning_feature_map)
            daytime_prediction = self._predict_value(self._daytime_model, daytime_feature_map)
            source = "hist_gradient_boosting"
        else:
            morning_fallback = self._fallback_prediction(
                normalized_target_date,
                "morning_load_kwh",
            )
            daytime_fallback = self._fallback_prediction(
                normalized_target_date,
                "daytime_load_kwh",
            )
            morning_prediction = morning_fallback.value
            daytime_prediction = daytime_fallback.value
            fallback_sources = {morning_fallback.source, daytime_fallback.source}
            if "fallback_previous_actual" in fallback_sources:
                source = "fallback_previous_actual"
            elif "fallback_no_history" in fallback_sources:
                source = "fallback_no_history"
            else:
                source = "fallback_rolling_average"

        return ConsumptionForecast(
            target_date=normalized_target_date,
            morning_load_kwh=max(0.0, morning_prediction),
            daytime_load_kwh=max(0.0, daytime_prediction),
            source=source,
            sample_count=self._sample_count,
            features=list(MODEL_FEATURE_NAMES),
        )

    def _aggregate_daily_loads(
        self,
        load_rows: Iterable[LoadObservation | Mapping[str, Any]],
    ) -> dict[date, dict[str, float]]:
        daily_loads: dict[date, dict[str, float]] = {}
        for row in load_rows:
            observation = _normalize_load_row(row)
            bucket = daily_loads.setdefault(
                observation.dt.date(),
                {"morning_load_kwh": 0.0, "daytime_load_kwh": 0.0},
            )
            if 7 <= observation.dt.hour < 10:
                bucket["morning_load_kwh"] += observation.load_kwh
            if 7 <= observation.dt.hour < 23:
                bucket["daytime_load_kwh"] += observation.load_kwh
        return daily_loads

    def _normalize_weather_rows(
        self,
        weather_rows: Iterable[DailyWeatherFeatures | Mapping[str, Any]],
    ) -> dict[date, _NormalizedWeather]:
        normalized: dict[date, _NormalizedWeather] = {}
        for row in weather_rows:
            weather = _normalize_weather_row(row)
            normalized[weather.date] = weather
        return normalized

    def _build_weather_code_mapping(self) -> dict[str, int]:
        codes = sorted({str(weather.weather_code) for weather in self._weather_by_date.values()})
        return {code: index for index, code in enumerate(codes)}

    def _build_training_matrix(
        self,
        training_dates: list[date],
        target_field: str,
    ) -> tuple[np.ndarray, np.ndarray]:
        feature_rows: list[list[float]] = []
        targets: list[float] = []
        for current_date in training_dates:
            weather = self._weather_by_date[current_date]
            feature_map = self._build_feature_map(current_date, weather, target_field)
            feature_rows.append(self._feature_vector(feature_map))
            targets.append(self._daily_loads[current_date][target_field])
        return np.asarray(feature_rows, dtype=float), np.asarray(targets, dtype=float)

    def _build_feature_map(
        self,
        current_date: date,
        weather: _NormalizedWeather,
        target_field: str,
    ) -> dict[str, float]:
        history = self._history_before(current_date, target_field)
        recent_values = [value for _, value in history]
        same_weekday_values = [
            value
            for history_date, value in history
            if history_date.weekday() == current_date.weekday()
        ]

        lag1 = recent_values[-1] if recent_values else 0.0
        lag7 = recent_values[-7] if len(recent_values) >= 7 else lag1
        rolling_7 = _mean_or_default(recent_values[-7:], lag1)
        rolling_14 = _mean_or_default(recent_values[-14:], rolling_7)
        same_weekday_avg = _mean_or_default(same_weekday_values, rolling_7)

        return {
            "month": float(current_date.month),
            "weekday": float(current_date.weekday()),
            "weather_code": float(self._weather_code_to_int.get(str(weather.weather_code), -1)),
            "is_weekend": 1.0 if current_date.weekday() >= 5 else 0.0,
            "temp": weather.temp,
            "heating_degree": max(0.0, 18.0 - weather.temp),
            "cooling_degree": max(0.0, weather.temp - 24.0),
            "sunshine_hours": weather.sunshine_hours,
            "precipitation": weather.precipitation,
            "lag1": lag1,
            "lag7": lag7,
            "rolling_7": rolling_7,
            "rolling_14": rolling_14,
            "same_weekday_avg": same_weekday_avg,
        }

    def _history_before(self, current_date: date, target_field: str) -> list[tuple[date, float]]:
        history = [
            (history_date, values[target_field])
            for history_date, values in self._daily_loads.items()
            if history_date < current_date
        ]
        history.sort(key=lambda item: item[0])
        return history

    def _fit_model(
        self,
        feature_rows: np.ndarray,
        targets: np.ndarray,
    ) -> HistGradientBoostingRegressor | None:
        if HistGradientBoostingRegressor is None:
            return None
        try:
            model = HistGradientBoostingRegressor(
                categorical_features=CATEGORICAL_FEATURE_INDICES,
                learning_rate=0.05,
                loss="quantile",
                max_depth=6,
                max_iter=300,
                min_samples_leaf=5,
                quantile=0.75,
                random_state=self.random_state,
            )
            model.fit(feature_rows, targets)
            return model
        except Exception:
            return None

    def _predict_value(
        self,
        model: HistGradientBoostingRegressor | None,
        feature_map: dict[str, float],
    ) -> float:
        if model is None:
            return 0.0
        vector = np.asarray([self._feature_vector(feature_map)], dtype=float)
        prediction = float(model.predict(vector)[0])
        return max(0.0, prediction)

    def _feature_vector(self, feature_map: Mapping[str, float]) -> list[float]:
        return [float(feature_map[name]) for name in MODEL_FEATURE_NAMES]

    def _resolve_weather(
        self,
        target_date: date,
        weather_row: DailyWeatherFeatures | Mapping[str, Any] | None,
    ) -> _NormalizedWeather | None:
        if weather_row is not None:
            return _normalize_weather_row(weather_row)
        return self._weather_by_date.get(target_date)

    def _previous_actual(self, current_date: date, target_field: str) -> float | None:
        previous_day = current_date - timedelta(days=1)
        previous_values = self._daily_loads.get(previous_day)
        if previous_values is not None:
            return previous_values[target_field]

        history = self._history_before(current_date, target_field)
        if not history:
            return None
        return history[-1][1]

    def _fallback_prediction(self, current_date: date, target_field: str) -> _FallbackPrediction:
        history = self._history_before(current_date, target_field)
        if not history:
            return _FallbackPrediction(0.0, "fallback_no_history")

        dates = [history_date for history_date, _ in history]
        values = [value for _, value in history]
        window = min(self.fallback_window, len(values))
        candidates = [
            values[-1],
            fmean(values[-window:]),
            fmean(values[-min(7, len(values)) :]),
        ]
        if len(values) >= 7:
            candidates.append(values[-7])

        same_weekday_values = [
            value
            for history_date, value in zip(dates, values)
            if history_date.weekday() == current_date.weekday()
        ]
        if same_weekday_values:
            candidates.append(fmean(same_weekday_values))

        prediction = max(0.0, fmean(candidates))
        if prediction <= 0.0:
            previous_actual = self._previous_actual(current_date, target_field)
            if previous_actual is not None:
                return _FallbackPrediction(max(0.0, previous_actual), "fallback_previous_actual")
        return _FallbackPrediction(prediction, "fallback_rolling_average")


def forecast_daily_consumption(
    load_rows: Iterable[LoadObservation | Mapping[str, Any]],
    weather_rows: Iterable[DailyWeatherFeatures | Mapping[str, Any]],
    target_date: date | datetime | str,
    *,
    weather_row: DailyWeatherFeatures | Mapping[str, Any] | None = None,
    min_training_days: int = 45,
    fallback_window: int = 14,
    random_state: int = 42,
) -> ConsumptionForecast:
    forecaster = ConsumptionForecaster(
        min_training_days=min_training_days,
        fallback_window=fallback_window,
        random_state=random_state,
    )
    return forecaster.fit(load_rows, weather_rows).predict(target_date, weather_row=weather_row)


def _normalize_load_row(row: LoadObservation | Mapping[str, Any]) -> LoadObservation:
    if isinstance(row, LoadObservation):
        return row
    dt_value = row.get("datetime", row.get("dt"))
    if dt_value is None:
        raise ValueError("load row requires datetime or dt")
    return LoadObservation(
        dt=_coerce_datetime(dt_value),
        load_kwh=parse_csv_float(row.get("load_kwh", row.get("load"))),
    )


def _normalize_weather_row(row: DailyWeatherFeatures | Mapping[str, Any]) -> _NormalizedWeather:
    if isinstance(row, DailyWeatherFeatures):
        extras = {key: float(value) for key, value in (row.extras or {}).items()}
        return _NormalizedWeather(
            date=row.date,
            temp=float(row.temp),
            weather_code=row.weather_code,
            sunshine_hours=float(row.sunshine_hours),
            precipitation=float(row.precipitation),
            extras=extras,
        )

    date_value = row.get("date", row.get("target_date"))
    if date_value is None:
        raise ValueError("weather row requires date or target_date")

    reserved_keys = {
        "date",
        "target_date",
        "temp",
        "weather_code",
        "sunshine_hours",
        "precipitation",
        "extras",
    }
    extras = {
        key: parse_csv_float(value)
        for key, value in row.items()
        if key not in reserved_keys and value is not None
    }
    extras.update({key: parse_csv_float(value) for key, value in dict(row.get("extras") or {}).items()})

    return _NormalizedWeather(
        date=_coerce_date(date_value),
        temp=parse_csv_float(row.get("temp")),
        weather_code=row.get("weather_code", "unknown"),
        sunshine_hours=parse_csv_float(row.get("sunshine_hours")),
        precipitation=parse_csv_float(row.get("precipitation")),
        extras=extras,
    )


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(f"unsupported datetime value: {value!r}")


def _coerce_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError(f"unsupported date value: {value!r}")


def _mean_or_default(values: list[float], default: float) -> float:
    return float(fmean(values)) if values else default


__all__ = [
    "ConsumptionForecast",
    "ConsumptionForecaster",
    "DailyWeatherFeatures",
    "LoadObservation",
    "forecast_daily_consumption",
]

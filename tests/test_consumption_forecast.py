from __future__ import annotations

from datetime import date, datetime, time, timedelta
from statistics import fmean

import pytest

from app.consumption_forecast import ConsumptionForecaster, forecast_daily_consumption


def _build_synthetic_dataset(
    total_days: int = 80,
) -> tuple[list[dict[str, object]], list[dict[str, object]], date, float, float]:
    start_date = date(2026, 1, 1)
    load_rows: list[dict[str, object]] = []
    weather_rows: list[dict[str, object]] = []
    morning_history: list[float] = []
    daytime_history: list[float] = []

    for offset in range(total_days):
        current_date = start_date + timedelta(days=offset)
        weekday = current_date.weekday()
        temp = 8.0 + 0.18 * offset + (weekday % 3)
        sunshine_hours = 3.0 + float(offset % 6)
        precipitation = 1.2 if offset % 5 == 0 else 0.1 * float(offset % 4)
        weather_code = ["sunny", "cloudy", "rain"][offset % 3]

        prev_morning = morning_history[-1] if morning_history else 5.0
        prev_daytime = daytime_history[-1] if daytime_history else 14.0
        lag7_morning = morning_history[-7] if len(morning_history) >= 7 else prev_morning
        lag7_daytime = daytime_history[-7] if len(daytime_history) >= 7 else prev_daytime

        morning_value = (
            3.8
            + 0.09 * temp
            + 0.22 * weekday
            + (0.9 if weather_code == "rain" else 0.0)
            - 0.14 * sunshine_hours
            + 0.24 * prev_morning
            + 0.12 * lag7_morning
        )
        daytime_value = (
            10.5
            + 0.18 * temp
            + 0.28 * weekday
            + (1.5 if weather_code == "rain" else 0.0)
            - 0.24 * sunshine_hours
            + 0.22 * prev_daytime
            + 0.16 * lag7_daytime
            + 0.12 * morning_value
        )

        morning_value = round(morning_value, 4)
        daytime_value = round(daytime_value, 4)
        morning_history.append(morning_value)
        daytime_history.append(daytime_value)

        weather_rows.append(
            {
                "date": current_date,
                "temp": temp,
                "weather_code": weather_code,
                "sunshine_hours": sunshine_hours,
                "precipitation": precipitation,
            }
        )

        morning_slots = [7, 8, 9]
        remaining_daytime = max(daytime_value - morning_value, 0.0)
        daytime_slots = [12, 16, 20, 22]
        for hour in morning_slots:
            load_rows.append(
                {
                    "datetime": datetime.combine(current_date, time(hour=hour)),
                    "load_kwh": morning_value / len(morning_slots),
                }
            )
        for hour in daytime_slots:
            load_rows.append(
                {
                    "datetime": datetime.combine(current_date, time(hour=hour)),
                    "load_kwh": remaining_daytime / len(daytime_slots),
                }
            )

    target_date = start_date + timedelta(days=total_days - 1)
    return load_rows, weather_rows, target_date, morning_history[-1], daytime_history[-1]


def test_consumption_forecaster_predicts_both_targets() -> None:
    load_rows, weather_rows, target_date, expected_morning, expected_daytime = _build_synthetic_dataset()
    historical_rows = [
        row
        for row in load_rows
        if isinstance(row["datetime"], datetime) and row["datetime"].date() < target_date
    ]

    forecaster = ConsumptionForecaster(min_training_days=30, random_state=0)
    forecast = forecaster.fit(historical_rows, weather_rows).predict(target_date)

    assert forecast.source == "hist_gradient_boosting"
    assert forecast.sample_count >= 30
    assert forecast.target_date == target_date
    assert {"temp", "month", "weekday", "weather_code", "lag1", "lag7", "rolling_7", "rolling_14", "same_weekday_avg"} <= set(forecast.features)
    assert forecast.morning_load_kwh == pytest.approx(expected_morning, abs=1.8)
    assert forecast.daytime_load_kwh == pytest.approx(expected_daytime, abs=2.4)


def test_consumption_forecaster_falls_back_when_training_days_are_insufficient() -> None:
    load_rows, weather_rows, target_date, _, _ = _build_synthetic_dataset(total_days=10)
    historical_rows = [
        row
        for row in load_rows
        if isinstance(row["datetime"], datetime) and row["datetime"].date() < target_date
    ]
    history_dates = sorted({row["datetime"].date() for row in historical_rows if isinstance(row["datetime"], datetime)})

    forecast = forecast_daily_consumption(
        historical_rows,
        weather_rows,
        target_date,
        min_training_days=45,
        fallback_window=14,
    )

    morning_history = [_daily_total_for_date(historical_rows, current_date, morning_only=True) for current_date in history_dates]
    daytime_history = [_daily_total_for_date(historical_rows, current_date, morning_only=False) for current_date in history_dates]

    assert forecast.source == "fallback_rolling_average"
    assert forecast.sample_count < 45
    assert forecast.morning_load_kwh == pytest.approx(
        _expected_fallback(morning_history, history_dates, target_date),
    )
    assert forecast.daytime_load_kwh == pytest.approx(
        _expected_fallback(daytime_history, history_dates, target_date),
    )


def test_consumption_forecaster_uses_previous_actual_when_sparse_fallback_is_zero() -> None:
    target_date = date(2026, 1, 10)
    load_rows: list[dict[str, object]] = []

    for days_before in range(1, 10):
        current_date = target_date - timedelta(days=days_before)
        if days_before == 1:
            morning_load = 4.0
            afternoon_load = 6.0
        else:
            # Defensive fixture: if sparse/bad history would collapse the fallback to zero,
            # the forecaster should still prefer the most recent actual consumption.
            morning_load = -20.0
            afternoon_load = -20.0
        load_rows.append(
            {
                "datetime": datetime.combine(current_date, time(hour=7)),
                "load_kwh": morning_load,
            }
        )
        load_rows.append(
            {
                "datetime": datetime.combine(current_date, time(hour=12)),
                "load_kwh": afternoon_load,
            }
        )

    forecast = forecast_daily_consumption(
        load_rows,
        [],
        target_date,
        min_training_days=45,
    )

    assert forecast.source == "fallback_previous_actual"
    assert forecast.morning_load_kwh == pytest.approx(4.0)
    assert forecast.daytime_load_kwh == pytest.approx(10.0)


def _daily_total_for_date(
    load_rows: list[dict[str, object]],
    current_date: date,
    *,
    morning_only: bool,
) -> float:
    total = 0.0
    for row in load_rows:
        dt = row["datetime"]
        if not isinstance(dt, datetime) or dt.date() != current_date:
            continue
        if morning_only and 7 <= dt.hour < 10:
            total += float(row["load_kwh"])
        if not morning_only and 7 <= dt.hour < 23:
            total += float(row["load_kwh"])
    return total


def _expected_fallback(values: list[float], history_dates: list[date], target_date: date) -> float:
    candidates = [
        values[-1],
        fmean(values),
        fmean(values[-min(7, len(values)) :]),
    ]
    if len(values) >= 7:
        candidates.append(values[-7])
    same_weekday_values = [
        value
        for history_date, value in zip(history_dates, values)
        if history_date.weekday() == target_date.weekday()
    ]
    if same_weekday_values:
        candidates.append(fmean(same_weekday_values))
    return fmean(candidates)

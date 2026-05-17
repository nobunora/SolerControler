from __future__ import annotations

from datetime import date, datetime

import pytest

from app.consumption_forecast import ConsumptionForecast
from app.occupancy_schedule import (
    apply_occupancy_schedule,
    events_from_values,
    filter_training_load_rows,
    find_event_for_date,
)


def _forecast(target_date: date) -> ConsumptionForecast:
    return ConsumptionForecast(
        target_date=target_date,
        morning_load_kwh=4.0,
        daytime_load_kwh=16.0,
        source="fallback_previous_actual",
        sample_count=10,
        features=["month", "weekday"],
    )


def test_events_from_values_parses_sheet_rows() -> None:
    values = [
        [
            "enabled",
            "start_date",
            "end_date",
            "status",
            "occupancy_factor",
            "morning_load_override_kwh",
            "daytime_load_override_kwh",
            "standby_floor_morning_kwh",
            "standby_floor_daytime_kwh",
            "include_in_training",
            "reason",
            "note",
        ],
        ["true", "2026-08-12", "2026-08-15", "away", "0.25", "", "", "0.5", "2.5", "false", "travel", "trip"],
    ]

    events = events_from_values(values, source="test")

    assert len(events) == 1
    assert events[0].start_date == date(2026, 8, 12)
    assert events[0].end_date == date(2026, 8, 15)
    assert events[0].status == "away"
    assert events[0].occupancy_factor == pytest.approx(0.25)
    assert events[0].standby_floor_daytime_kwh == pytest.approx(2.5)
    assert events[0].include_in_training is False


def test_apply_occupancy_schedule_uses_factor_and_floor() -> None:
    events = events_from_values(
        [
            [
                "enabled",
                "start_date",
                "end_date",
                "status",
                "occupancy_factor",
                "morning_load_override_kwh",
                "daytime_load_override_kwh",
                "standby_floor_morning_kwh",
                "standby_floor_daytime_kwh",
                "include_in_training",
            ],
            ["true", "2026-08-12", "2026-08-15", "away", "0.25", "", "", "1.5", "5.0", "false"],
        ]
    )

    adjusted, detail = apply_occupancy_schedule(_forecast(date(2026, 8, 13)), events)

    assert detail is not None
    assert detail.method == "factor"
    assert adjusted.morning_load_kwh == pytest.approx(1.5)
    assert adjusted.daytime_load_kwh == pytest.approx(5.0)
    assert adjusted.source == "fallback_previous_actual+occupancy_away"
    assert "occupancy_factor" in adjusted.features


def test_apply_occupancy_schedule_prefers_direct_overrides() -> None:
    events = events_from_values(
        [
            [
                "enabled",
                "start_date",
                "end_date",
                "status",
                "occupancy_factor",
                "morning_load_override_kwh",
                "daytime_load_override_kwh",
            ],
            ["true", "2026-09-03", "2026-09-03", "away", "0.25", "0.8", "3.0"],
        ]
    )

    adjusted, detail = apply_occupancy_schedule(_forecast(date(2026, 9, 3)), events)

    assert detail is not None
    assert detail.method == "override"
    assert adjusted.morning_load_kwh == pytest.approx(0.8)
    assert adjusted.daytime_load_kwh == pytest.approx(3.0)


def test_filter_training_load_rows_excludes_non_training_away_days() -> None:
    events = events_from_values(
        [
            ["enabled", "start_date", "end_date", "status", "include_in_training"],
            ["true", "2026-08-12", "2026-08-15", "away", "false"],
        ]
    )
    rows = [
        {"datetime": datetime(2026, 8, 11, 7), "load": 4.0},
        {"datetime": datetime(2026, 8, 12, 7), "load": 1.0},
        {"datetime": datetime(2026, 8, 15, 7), "load": 1.0},
        {"datetime": datetime(2026, 8, 16, 7), "load": 4.0},
    ]

    filtered = filter_training_load_rows(rows, events)

    assert [row["datetime"].date() for row in filtered] == [date(2026, 8, 11), date(2026, 8, 16)]


def test_find_event_for_date_ignores_normal_status() -> None:
    events = events_from_values(
        [
            ["enabled", "start_date", "end_date", "status"],
            ["true", "2026-08-12", "2026-08-15", "normal"],
        ]
    )

    assert find_event_for_date(events, date(2026, 8, 13)) is None

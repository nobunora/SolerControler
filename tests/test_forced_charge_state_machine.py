from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.forced_charge import (
    ChargeEffect,
    ChargeObservation,
    ChargePolicy,
    ChargeState,
    decide_transition,
)


NOW = datetime(2026, 7, 16, 3, 0, tzinfo=timezone.utc)
POLICY = ChargePolicy(80.0, NOW + timedelta(hours=3), 10_800, 3, 0.5)


@pytest.mark.parametrize(
    ("soc", "expected", "effect"),
    [
        (None, ChargeState.HOLDING_STANDBY, ChargeEffect.SET_STANDBY),
        (80.0, ChargeState.COMPLETED_NO_CHARGE, ChargeEffect.SET_STANDBY),
        (79.9, ChargeState.STARTING_FORCED, ChargeEffect.SET_FORCED_CHARGE),
    ],
)
def test_initial_transition_table(soc: float | None, expected: ChargeState, effect: ChargeEffect) -> None:
    transition = decide_transition(ChargeState.INITIALIZING, ChargeObservation(NOW, soc), POLICY)
    assert transition.next_state is expected
    assert effect in transition.effects


def test_missing_plan_fails_without_device_command() -> None:
    transition = decide_transition(
        ChargeState.INITIALIZING, ChargeObservation(NOW, 50.0), POLICY, plan_available=False
    )
    assert transition.next_state is ChargeState.FAILED_COMMAND
    assert ChargeEffect.SET_STANDBY not in transition.effects
    assert ChargeEffect.SET_FORCED_CHARGE not in transition.effects


@pytest.mark.parametrize(
    ("observation", "terminal"),
    [
        (ChargeObservation(NOW, 79.5), ChargeState.COMPLETED_TARGET),
        (ChargeObservation(NOW + timedelta(hours=3), 60.0), ChargeState.COMPLETED_CUTOFF),
        (ChargeObservation(NOW, None, consecutive_sensor_failures=3), ChargeState.FAILED_SENSOR),
        (ChargeObservation(NOW, 60.0, elapsed_seconds=10_800), ChargeState.FAILED_TIMEOUT),
    ],
)
def test_monitoring_stop_table(observation: ChargeObservation, terminal: ChargeState) -> None:
    transition = decide_transition(ChargeState.MONITORING, observation, POLICY)
    assert transition.next_state is ChargeState.STOPPING
    assert transition.terminal_after_stop is terminal
    assert transition.effects == (ChargeEffect.SET_STANDBY,)


def test_monitoring_continue_effect_order_is_explicit() -> None:
    transition = decide_transition(ChargeState.MONITORING, ChargeObservation(NOW, 60.0), POLICY)
    assert transition.effects == (
        ChargeEffect.PERSIST_OBSERVATION,
        ChargeEffect.SLEEP_UNTIL_NEXT_POLL,
        ChargeEffect.FETCH_MONITORING_CSV,
    )


def test_stopping_never_reports_success_when_standby_confirmation_fails() -> None:
    transition = decide_transition(
        ChargeState.STOPPING,
        ChargeObservation(NOW, 80.0, standby_confirmed=False),
        POLICY,
        terminal_after_stop=ChargeState.COMPLETED_TARGET,
    )
    assert transition.next_state is ChargeState.FAILED_COMMAND


def test_policy_rejects_naive_cutoff() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ChargePolicy(80.0, datetime(2026, 7, 16, 7, 0), 60, 1)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_policy_rejects_non_finite_runtime(value: float) -> None:
    with pytest.raises(ValueError, match="runtime"):
        ChargePolicy(80.0, NOW + timedelta(hours=1), value, 1)


@pytest.mark.parametrize("field", ["elapsed_seconds", "soc_percent"])
def test_observation_rejects_non_finite_measurements(field: str) -> None:
    values = {"now": NOW, "soc_percent": 50.0, "elapsed_seconds": 0.0}
    values[field] = float("nan")
    with pytest.raises(ValueError, match=field):
        ChargeObservation(**values)


def test_policy_rejects_hysteresis_larger_than_target() -> None:
    with pytest.raises(ValueError, match="hysteresis"):
        ChargePolicy(10.0, NOW + timedelta(hours=1), 60, 1, 10.1)


def test_stopping_rejects_non_terminal_destination() -> None:
    with pytest.raises(ValueError, match="terminal_after_stop"):
        decide_transition(
            ChargeState.STOPPING,
            ChargeObservation(NOW, 80.0, standby_confirmed=True),
            POLICY,
            terminal_after_stop=ChargeState.MONITORING,
        )

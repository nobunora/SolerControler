from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ChargeState(str, Enum):
    INITIALIZING = "initializing"
    HOLDING_STANDBY = "holding_standby"
    STARTING_FORCED = "starting_forced"
    MONITORING = "monitoring"
    STOPPING = "stopping"
    COMPLETED_TARGET = "completed_target"
    COMPLETED_CUTOFF = "completed_cutoff"
    COMPLETED_NO_CHARGE = "completed_no_charge"
    FAILED_SENSOR = "failed_sensor"
    FAILED_COMMAND = "failed_command"
    FAILED_TIMEOUT = "failed_timeout"

    @property
    def terminal(self) -> bool:
        return self in {
            ChargeState.COMPLETED_TARGET,
            ChargeState.COMPLETED_CUTOFF,
            ChargeState.COMPLETED_NO_CHARGE,
            ChargeState.FAILED_SENSOR,
            ChargeState.FAILED_COMMAND,
            ChargeState.FAILED_TIMEOUT,
        }


class ChargeEffect(str, Enum):
    SET_STANDBY = "set_standby"
    SET_FORCED_CHARGE = "set_forced_charge"
    FETCH_MONITORING_CSV = "fetch_monitoring_csv"
    PERSIST_OBSERVATION = "persist_observation"
    SLEEP_UNTIL_NEXT_POLL = "sleep_until_next_poll"
    RECORD_TERMINAL_RESULT = "record_terminal_result"


@dataclass(frozen=True)
class ChargeObservation:
    now: datetime
    soc_percent: float | None
    standby_confirmed: bool | None = None
    consecutive_sensor_failures: int = 0
    elapsed_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.now.tzinfo is None or self.now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if self.soc_percent is not None and (
            not math.isfinite(self.soc_percent) or not 0.0 <= self.soc_percent <= 100.0
        ):
            raise ValueError("soc_percent must be finite and within 0..100")
        if not isinstance(self.consecutive_sensor_failures, int) or isinstance(self.consecutive_sensor_failures, bool):
            raise ValueError("consecutive_sensor_failures must be an integer")
        if self.consecutive_sensor_failures < 0:
            raise ValueError("consecutive_sensor_failures must be non-negative")
        if not math.isfinite(self.elapsed_seconds) or self.elapsed_seconds < 0:
            raise ValueError("elapsed_seconds must be finite and non-negative")


@dataclass(frozen=True)
class ChargePolicy:
    target_soc_percent: float
    cutoff: datetime
    max_runtime_seconds: float
    max_sensor_failures: int
    hysteresis_percent: float = 0.0

    def __post_init__(self) -> None:
        if self.cutoff.tzinfo is None or self.cutoff.utcoffset() is None:
            raise ValueError("cutoff must be timezone-aware")
        if not math.isfinite(self.target_soc_percent) or not 0.0 <= self.target_soc_percent <= 100.0:
            raise ValueError("target_soc_percent must be finite and within 0..100")
        if not math.isfinite(self.max_runtime_seconds) or self.max_runtime_seconds <= 0:
            raise ValueError("runtime must be finite and positive")
        if not isinstance(self.max_sensor_failures, int) or isinstance(self.max_sensor_failures, bool):
            raise ValueError("max_sensor_failures must be an integer")
        if self.max_sensor_failures <= 0:
            raise ValueError("max_sensor_failures must be positive")
        if not math.isfinite(self.hysteresis_percent) or self.hysteresis_percent < 0:
            raise ValueError("hysteresis_percent must be finite and non-negative")
        if self.hysteresis_percent > self.target_soc_percent:
            raise ValueError("hysteresis_percent must not exceed target_soc_percent")


@dataclass(frozen=True)
class ChargeTransition:
    next_state: ChargeState
    effects: tuple[ChargeEffect, ...]
    reason: str
    terminal_after_stop: ChargeState | None = None


def _stop(terminal: ChargeState, reason: str) -> ChargeTransition:
    return ChargeTransition(
        next_state=ChargeState.STOPPING,
        effects=(ChargeEffect.SET_STANDBY,),
        reason=reason,
        terminal_after_stop=terminal,
    )


def decide_transition(
    state: ChargeState,
    observation: ChargeObservation,
    policy: ChargePolicy,
    *,
    plan_available: bool = True,
    forced_command_confirmed: bool | None = None,
    terminal_after_stop: ChargeState | None = None,
) -> ChargeTransition:
    """Return the next state and required effects without performing I/O."""
    if state.terminal:
        return ChargeTransition(state, (), "already_terminal")
    if state is ChargeState.INITIALIZING:
        if not plan_available:
            return ChargeTransition(
                ChargeState.FAILED_COMMAND,
                (ChargeEffect.RECORD_TERMINAL_RESULT,),
                "plan_unavailable",
            )
        if observation.soc_percent is None:
            return ChargeTransition(
                ChargeState.HOLDING_STANDBY,
                (ChargeEffect.SET_STANDBY,),
                "initial_soc_unavailable",
            )
        if observation.soc_percent >= policy.target_soc_percent:
            return ChargeTransition(
                ChargeState.COMPLETED_NO_CHARGE,
                (ChargeEffect.SET_STANDBY, ChargeEffect.RECORD_TERMINAL_RESULT),
                "target_already_reached",
            )
        return ChargeTransition(
            ChargeState.STARTING_FORCED,
            (ChargeEffect.SET_FORCED_CHARGE,),
            "charge_required",
        )
    if state is ChargeState.HOLDING_STANDBY:
        return ChargeTransition(
            ChargeState.COMPLETED_NO_CHARGE,
            (ChargeEffect.RECORD_TERMINAL_RESULT,),
            "standby_held_without_soc",
        )
    if state is ChargeState.STARTING_FORCED:
        if forced_command_confirmed is True:
            return ChargeTransition(
                ChargeState.MONITORING,
                (ChargeEffect.FETCH_MONITORING_CSV,),
                "forced_charge_confirmed",
            )
        if forced_command_confirmed is False:
            return _stop(ChargeState.FAILED_COMMAND, "forced_charge_confirm_failed")
        return ChargeTransition(state, (), "awaiting_forced_charge_confirmation")
    if state is ChargeState.MONITORING:
        threshold = max(0.0, policy.target_soc_percent - policy.hysteresis_percent)
        if observation.soc_percent is not None and observation.soc_percent >= threshold:
            return _stop(ChargeState.COMPLETED_TARGET, "target_reached")
        if observation.now >= policy.cutoff:
            return _stop(ChargeState.COMPLETED_CUTOFF, "cutoff_reached")
        if observation.consecutive_sensor_failures >= policy.max_sensor_failures:
            return _stop(ChargeState.FAILED_SENSOR, "sensor_failure_limit")
        if observation.elapsed_seconds >= policy.max_runtime_seconds:
            return _stop(ChargeState.FAILED_TIMEOUT, "runtime_limit")
        return ChargeTransition(
            ChargeState.MONITORING,
            (
                ChargeEffect.PERSIST_OBSERVATION,
                ChargeEffect.SLEEP_UNTIL_NEXT_POLL,
                ChargeEffect.FETCH_MONITORING_CSV,
            ),
            "monitoring_continues",
        )
    if state is ChargeState.STOPPING:
        if terminal_after_stop is not None and not terminal_after_stop.terminal:
            raise ValueError("terminal_after_stop must be a terminal state")
        terminal = terminal_after_stop or ChargeState.FAILED_COMMAND
        if observation.standby_confirmed is True:
            return ChargeTransition(
                terminal,
                (ChargeEffect.RECORD_TERMINAL_RESULT,),
                "standby_confirmed",
            )
        if observation.standby_confirmed is False:
            return ChargeTransition(
                ChargeState.FAILED_COMMAND,
                (ChargeEffect.RECORD_TERMINAL_RESULT,),
                "standby_confirm_failed",
            )
        return ChargeTransition(state, (ChargeEffect.SET_STANDBY,), "awaiting_standby_confirmation", terminal)
    raise AssertionError(f"unhandled state: {state}")

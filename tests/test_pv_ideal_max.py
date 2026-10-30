"""Clear reference, update boundaries and strictly causal shared state."""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.forecasting import pv_ideal_max as model
from app.forecasting.pv_clear_ghi import clear_ghi_hour

ZONE = ZoneInfo("Asia/Tokyo")
START = datetime(2026, 1, 1, 12, tzinfo=ZONE)


@pytest.fixture
def geometry():
    return model.IdealMaxGeometry(35, 135, "Asia/Tokyo", (model.IdealMaxArray(180, 0, 2, 0.8, 0.9),))


def observation(actual, start=START, *, available=None):
    return model.HourlyPvObservation(start, actual / 2, actual / 2, available or start + timedelta(hours=1))


def test_horizontal_reference_matches_existing_clear_geometry(geometry):
    for day, hour in [(date(2026, 1, 1), 12), (date(2024, 2, 29), 8), (date(2026, 6, 21), 17)]:
        expected = clear_ghi_hour(day, hour, latitude=35, longitude=135, timezone="Asia/Tokyo") * 2 * 0.8 * 0.9
        assert model.ideal_max_base(day, hour, geometry) == pytest.approx(expected, abs=1e-12)


def test_date_hour_array_orientation_and_immutable_inputs(geometry):
    summer, winter = date(2026, 6, 21), date(2026, 12, 21)
    assert model.ideal_max_base(summer, 12, geometry) > model.ideal_max_base(winter, 12, geometry)
    assert model.ideal_max_base(summer, 0, geometry) == 0
    east = replace(geometry, arrays=(model.IdealMaxArray(90, 30, 2, 0.8, 0.9),))
    west = replace(geometry, arrays=(model.IdealMaxArray(270, 30, 2, 0.8, 0.9),))
    assert model.ideal_max_base(summer, 8, east) > model.ideal_max_base(summer, 8, west)
    assert east.fingerprint != west.fingerprint
    with pytest.raises(FrozenInstanceError):
        geometry.latitude = 1


def test_negative_azimuth_preserves_equivalent_geometry(geometry):
    negative = replace(geometry, arrays=(model.IdealMaxArray(-90, 30, 2, 0.8, 1),))
    positive = replace(geometry, arrays=(model.IdealMaxArray(270, 30, 2, 0.8, 1),))
    assert negative.fingerprint == positive.fingerprint
    assert model.ideal_max_base(date(2026, 10, 28), 14, negative) == model.ideal_max_base(date(2026, 10, 28), 14, positive)


def test_shared_coefficient_raises_all_hours_without_age_decay(geometry, monkeypatch):
    monkeypatch.setattr(model, "ideal_max_base", lambda *_: 1.0)
    state = model.initial_ideal_max_state(geometry, as_of=START)
    update = model.update_ideal_max(state, geometry, observation(1.1), as_of=START + timedelta(hours=1))
    assert update.maximum_before_kwh == 1
    assert update.exceeded and update.state_after.coefficient == 1.1
    assert state.coefficient == 1
    for days in [1, 30, 90, 365, 730]:
        when = START + timedelta(days=days)
        assert model.calculate_ideal_max(when.date(), geometry, update.state_after, as_of=when) == (1.1,) * 24
    monkeypatch.setattr(model, "ideal_max_base", lambda *_: 0.8)
    assert model.calculate_ideal_max(START.date(), geometry, update.state_after, as_of=START + timedelta(days=1))[5] == pytest.approx(0.88)


@pytest.mark.parametrize("actual,exceeded", [(0, False), (1, False), (1.009, False), (1.01, False), (1.010001, True), (1.1, True)])
def test_decimal_threshold_is_inclusive_without_display_rounding(geometry, monkeypatch, actual, exceeded):
    monkeypatch.setattr(model, "ideal_max_base", lambda *_: 1.0)
    state = model.initial_ideal_max_state(geometry, as_of=START)
    update = model.update_ideal_max(state, geometry, observation(actual), as_of=START + timedelta(hours=1))
    assert update.exceeded is exceeded
    assert (update.state_after is state) is not exceeded
    if exceeded:
        assert update.base_kwh * update.state_after.coefficient >= actual


def test_zero_and_tiny_reference_have_no_fallback(geometry, monkeypatch):
    state = model.initial_ideal_max_state(geometry, as_of=START)
    monkeypatch.setattr(model, "ideal_max_base", lambda *_: 0.0)
    for actual in [0, 0.01]:
        assert model.update_ideal_max(state, geometry, observation(actual), as_of=START + timedelta(hours=1)).state_after is state
    with pytest.raises(ValueError, match="zero-base"):
        model.update_ideal_max(state, geometry, observation(0.02), as_of=START + timedelta(hours=1))
    monkeypatch.setattr(model, "ideal_max_base", lambda *_: 1e-12)
    assert model.update_ideal_max(state, geometry, observation(0.02), as_of=START + timedelta(hours=1)).state_after.coefficient >= 2e10


def test_observation_and_state_availability_are_enforced(geometry, monkeypatch):
    monkeypatch.setattr(model, "ideal_max_base", lambda *_: 1.0)
    state = model.initial_ideal_max_state(geometry, as_of=START)
    delayed = observation(1.1, available=START + timedelta(hours=2))
    with pytest.raises(ValueError, match="availability"):
        model.update_ideal_max(state, geometry, delayed, as_of=START + timedelta(hours=1))
    updated = model.update_ideal_max(state, geometry, delayed, as_of=delayed.available_at).state_after
    with pytest.raises(ValueError, match="unavailable"):
        model.calculate_ideal_max(START.date(), geometry, updated, as_of=START)
    assert state.revision == 0
    with pytest.raises(ValueError, match="completed"):
        observation(1, available=START)
    with pytest.raises(ValueError, match="aware"):
        model.initial_ideal_max_state(geometry, as_of=datetime(2026, 1, 1))


def test_geometry_identity_and_original_prediction_are_preserved(geometry, monkeypatch):
    monkeypatch.setattr(model, "ideal_max_base", lambda *_: 1.0)
    state = model.initial_ideal_max_state(geometry, as_of=START)
    issued = model.predict_ideal_max_hour(START, geometry, state, as_of=START)
    updated = model.update_ideal_max(state, geometry, observation(1.2), as_of=START + timedelta(hours=1)).state_after
    assert issued.maximum_kwh == 1 and updated.coefficient == 1.2
    with pytest.raises(ValueError, match="different geometry"):
        model.calculate_ideal_max(START.date(), replace(geometry, latitude=36), updated, as_of=START + timedelta(days=1))


@pytest.mark.parametrize("bad", [True, None, "1", float("nan"), float("inf"), -1])
def test_invalid_actual_is_never_silent_zero(bad):
    with pytest.raises(ValueError):
        model.HourlyPvObservation(START, bad, 0, START + timedelta(hours=1))


@pytest.mark.parametrize("hour", [-1, 24, True, 0.5])
def test_invalid_hour(geometry, hour):
    with pytest.raises(ValueError):
        model.ideal_max_base(START.date(), hour, geometry)


def test_no_weather_or_temperature_input_and_polar_day(geometry, monkeypatch):
    import socket
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: pytest.fail("external I/O"))
    state = model.initial_ideal_max_state(geometry, as_of=START)
    assert len(model.calculate_ideal_max(START.date(), geometry, state, as_of=START)) == 24
    polar = replace(geometry, latitude=89, longitude=0, timezone_name="UTC")
    assert model.ideal_max_base(date(2026, 6, 21), 0, polar) > 0

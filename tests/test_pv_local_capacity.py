from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.forecasting.pv_ideal_max import IdealMaxArray, IdealMaxGeometry, ideal_max_base
from app.forecasting.pv_local_capacity import calculate_local_capacity, capacity_cell, fit_local_capacity

ZONE = ZoneInfo("Asia/Tokyo")
DAY = date(2026, 11, 11)
GEOMETRY = IdealMaxGeometry(35, 135, "Asia/Tokyo", (IdealMaxArray(180, 19, 3.24, .8, 1),))
AS_OF = datetime(2026, 11, 12, tzinfo=ZONE)


def record(hour, coefficient, *, available=AS_OF):
    start = datetime(2026, 11, 11, hour, tzinfo=ZONE)
    base = ideal_max_base(DAY, hour, GEOMETRY)
    actual = base * coefficient
    return {"interval_start": start.isoformat(), "available_at": available.isoformat(),
            "base_kwh": base, "halfhour_kwh": [actual / 2, actual / 2], "actual_kwh": actual}


def test_sunrise_excess_does_not_raise_midday_and_preserves_midday_evidence():
    noon = record(11, 1.4)
    sunrise = record(6, 2.1)
    previous, _ = fit_local_capacity([noon], GEOMETRY, as_of=AS_OF)
    updated, trace = fit_local_capacity([noon, sunrise], GEOMETRY, as_of=AS_OF)
    old, cells = calculate_local_capacity(DAY, GEOMETRY, previous)
    new, _ = calculate_local_capacity(DAY, GEOMETRY, updated)
    assert cells[6] != cells[11]
    assert new[6] == pytest.approx(sunrise["actual_kwh"])
    assert new[11] == old[11] == pytest.approx(noon["actual_kwh"])
    assert trace["cell_evidence"][cells[11]]["interval_start"] == noon["interval_start"]


def test_available_completed_observations_only_and_unseen_cells_use_base():
    future = record(6, 2.1, available=AS_OF + timedelta(seconds=1))
    coefficients, trace = fit_local_capacity([future], GEOMETRY, as_of=AS_OF)
    pv, _ = calculate_local_capacity(DAY, GEOMETRY, coefficients)
    assert trace["observation_count"] == 0
    assert pv[11] == ideal_max_base(DAY, 11, GEOMETRY)
    assert pv[0] == 0
    with pytest.raises(ValueError, match="aware"):
        fit_local_capacity([], GEOMETRY, as_of=AS_OF.replace(tzinfo=None))


def test_threshold_zero_base_invalid_measurements_and_duplicates():
    row = record(11, 1)
    row.update(base_kwh=1., actual_kwh=1.01, halfhour_kwh=[.505, .505])
    assert fit_local_capacity([row], GEOMETRY, as_of=AS_OF)[0] == {}
    row.update(base_kwh=0., actual_kwh=.02, halfhour_kwh=[.01, .01])
    with pytest.raises(ValueError, match="zero-base"):
        fit_local_capacity([row], GEOMETRY, as_of=AS_OF)
    row = record(11, 1.4)
    with pytest.raises(ValueError, match="duplicate"):
        fit_local_capacity([row, row], GEOMETRY, as_of=AS_OF)
    row["halfhour_kwh"][0] = float("nan")
    with pytest.raises(ValueError):
        fit_local_capacity([row], GEOMETRY, as_of=AS_OF)


def test_profile_does_not_decay_or_use_an_unrelated_cell():
    coefficients, _ = fit_local_capacity([record(11, 1.4)], GEOMETRY, as_of=AS_OF)
    key = capacity_cell(DAY, 11, GEOMETRY)
    for day in (DAY, date(2026, 11, 12), date(2027, 11, 11)):
        values, cells = calculate_local_capacity(day, GEOMETRY, coefficients)
        for hour in range(24):
            expected = ideal_max_base(day, hour, GEOMETRY) * (coefficients[key] if cells[hour] == key else 1.)
            assert values[hour] == expected


def test_projection_rejects_overflow_instead_of_publishing_infinity(monkeypatch):
    from app.forecasting import pv_local_capacity as local

    monkeypatch.setattr(local, "ideal_max_base", lambda *_: 2.)
    key = capacity_cell(DAY, 11, GEOMETRY)
    with pytest.raises(ValueError, match="not finite"):
        calculate_local_capacity(DAY, GEOMETRY, {key: 1e308})

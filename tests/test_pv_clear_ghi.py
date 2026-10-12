"""Boundary fixtures are synthetic; no provider, coordinates from a plan, or I/O."""

from __future__ import annotations

import calendar
import hashlib
import math
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone

import pytest
import numpy as np

from app.forecasting.pv_clear_ghi import (
    BASIS_VERSION,
    CONDITION_NUMBER_LIMIT,
    MODEL_ID,
    ClearGhiForecast,
    calibration_from_bytes,
    canonical_pack_bytes,
    clear_ghi_hour,
    fit_clear_ghi_calibration,
    forecast_clear_ghi,
    normalize_radiation_intervals,
    solar_altitude,
    validate_calibration_for_run,
)

AS_OF = datetime(2026, 10, 14, tzinfo=timezone.utc)
TARGET = date(2026, 10, 15)
SITE = {"latitude": 35.0, "longitude": 135.0, "timezone": "Asia/Tokyo", "site_fingerprint": "synthetic-site"}


def calibration(theta=(2.0, 0.1, 0.2, 0.1), **updates):
    pack = {
        "schema_version": 1, "model_id": MODEL_ID, "basis_version": BASIS_VERSION,
        "site_fingerprint": "synthetic-site", "terms": ["1", "x", "cos_phi", "sin_phi"],
        "theta4": list(theta), "training_dates": ["2026-01-01", "2026-04-01", "2026-07-01"],
        "training_samples": 39, "rank": 4, "condition_number": 3.0,
        "training_as_of": AS_OF.isoformat(), "created_at": AS_OF.isoformat(), "usable_from": AS_OF.isoformat(),
        "actual_source_sha256": "a" * 64, "certification_source_sha256": "b" * 64,
        "certification_rule_version": "synthetic-certified-v1",
    }
    pack.update(updates)
    raw = canonical_pack_bytes(pack)
    return calibration_from_bytes(raw, sha256=hashlib.sha256(raw).hexdigest())


def source_rows(day=TARGET, *, absent=(), zero=()):
    return tuple({"time": (datetime.combine(day, datetime.min.time()) + timedelta(hours=h + 1)).isoformat(),
                  "shortwave_radiation_w_m2": 0.0 if h in zero else 400.0}
                 for h in range(24) if h not in absent)


def forecast(*, rows=None, coefficient=None, **updates) -> ClearGhiForecast:
    args = {"target_date": TARGET, "as_of": AS_OF, "latitude": SITE["latitude"],
            "longitude": SITE["longitude"], "timezone": SITE["timezone"],
            "calibration": coefficient or calibration(), "source_rows": source_rows() if rows is None else rows,
            "forecast_hash": "f" * 64, "retrieved_at": AS_OF, "provider_issued_at": None}
    args.update(updates)
    return forecast_clear_ghi(**args)


def certified_day(day: date, *, version=0, available=AS_OF, complete=True, certified=True):
    year_length = 366 if calendar.isleap(day.year) else 365
    phi = 2 * math.pi * (day.timetuple().tm_yday - 1) / year_length
    theta = (2.0, 0.1, 0.2, 0.1)
    rows = []
    for hour in range(5, 18):
        q = clear_ghi_hour(day, hour, latitude=35.0, longitude=135.0, timezone="Asia/Tokyo")
        c = theta[0] + theta[1] * (hour + 0.5 - 12) / 6 + theta[2] * math.cos(phi) + theta[3] * math.sin(phi)
        altitude = solar_altitude(day, hour + 0.5, latitude=35.0, longitude=135.0, timezone="Asia/Tokyo")
        rows.append({"hour": hour, "actual_halfhour_kwh": (q * c / 2, q * c / 2),
                     "solar_altitude_deg": altitude, "sunshine_minutes": 60.0,
                     "satellite_ghi_w_m2": 1000.0, "central_haurwitz_w_m2": 1000.0})
    if not complete:
        rows[0]["actual_halfhour_kwh"] = (None, 0.0)
    return {"date": day, "actual_available_at": available, "certification_available_at": available,
            "actual_sha256": hashlib.sha256(f"actual:{day}:{version}".encode()).hexdigest(),
            "certification_sha256": hashlib.sha256(f"certificate:{day}:{version}".encode()).hexdigest(),
            "certification_rule_version": "synthetic-certified-v1", "certified_clear": certified,
            "site_fingerprint": "synthetic-site", "basis_version": BASIS_VERSION, "hours": tuple(rows)}


def training_days():
    return tuple(certified_day(day) for day in (date(2025, 12, 1), date(2026, 2, 1),
                 date(2026, 4, 1), date(2026, 6, 1), date(2026, 8, 1), date(2026, 10, 1)))


def fit(days):
    return fit_clear_ghi_calibration(tuple(days), target_date=TARGET, as_of=AS_OF,
                                     site=SITE, basis_version=BASIS_VERSION)


def test_geometry_matches_research_midpoints():
    # NOAA/Haurwitz research midpoint quadrature, evaluated at synthetic sites.
    # Source version: artifacts/analysis/pv_geometry_20261010.py, 2026-10-10.
    # Source SHA256: 150d56b5cc2d6d74ab084de7dc574c654e60537727434641ac077418f5dc02e8
    fixtures = [
        (date(2026, 1, 1), 12, 35.0, 135.0, "Asia/Tokyo", 0.5115554149075773),
        (date(2024, 2, 29), 12, 0.0, 0.0, "UTC", 1.0185032377169752),
        (date(2026, 6, 21), 0, 89.0, 0.0, "UTC", 0.35942479847686254),
    ]
    for day, hour, lat, lon, zone, expected in fixtures:
        assert clear_ghi_hour(day, hour, latitude=lat, longitude=lon, timezone=zone) == expected
    assert clear_ghi_hour(date(2026, 1, 1), 0, latitude=35, longitude=135, timezone="Asia/Tokyo") == 0


def test_annual_basis_and_leap_year():
    leap = clear_ghi_hour(date(2024, 2, 29), 12, latitude=0, longitude=0, timezone="UTC")
    regular = clear_ghi_hour(date(2023, 3, 1), 12, latitude=0, longitude=0, timezone="UTC")
    assert math.isfinite(leap) and leap != regular
    with pytest.raises(FrozenInstanceError):
        calibration().theta = (1, 1, 1, 1)


def test_radiation_interval_tail_and_single_shift():
    rows = source_rows()
    energy, times = normalize_radiation_intervals(rows, target_date=TARGET, timezone="Asia/Tokyo")
    assert len(energy) == len(times) == 24
    assert energy[8] == 0.4 and times[8].hour == 9
    assert times[23].date() == TARGET + timedelta(days=1) and times[23].hour == 0
    assert rows[8]["time"].endswith("09:00:00")
    with pytest.raises(ValueError, match="original radiation field"):
        normalize_radiation_intervals(({"time": "2026-10-15T09:00:00", "f_kwh_m2": 0.4},),
                                     target_date=TARGET, timezone="Asia/Tokyo")
    offset = ({"time": "2026-10-15T00:00:00+00:00", "shortwave_radiation_w_m2": 100.0},)
    values, source = normalize_radiation_intervals(offset, target_date=TARGET, timezone="Asia/Tokyo")
    assert values[8] == 0.1 and source[8].utcoffset() == timedelta(0)


def test_nonconstant_provider_pulse_is_shifted_once_into_interval_eight():
    rows = list(source_rows())
    rows[7] = {**rows[7], "shortwave_radiation_w_m2": 100.0}  # original08:00
    rows[8] = {**rows[8], "shortwave_radiation_w_m2": 200.0}  # original09:00
    result = forecast(rows=tuple(rows))
    assert result.f_kwh_m2[7] == .1 and result.f_kwh_m2[8] == .2
    assert result.source_times[8].hour == 9
    assert result.pv_kwh[8] == pytest.approx(max(0., result.c_raw[8]) * min(.2, result.q_kwh_m2[8]))
    assert result.pv_kwh[8] != pytest.approx(max(0., result.c_raw[8]) * min(.1, result.q_kwh_m2[8]))


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), -1.0, "400"])
def test_invalid_values_fail_before_success(bad):
    rows = list(source_rows())
    rows[12] = {**rows[12], "shortwave_radiation_w_m2": bad}
    with pytest.raises(ValueError):
        forecast(rows=tuple(rows))


def test_zero_missing_duplicate_and_overflow_are_distinct():
    good = forecast(rows=source_rows(absent=(0, 1, 23), zero=(12,)))
    assert good.f_kwh_m2[0] is None and good.source_times[0] is None and good.pv_kwh[0] == 0
    assert good.f_kwh_m2[12] == 0 and good.pv_kwh[12] == 0 and good.repaired_hours == ()
    with pytest.raises(ValueError, match="duplicate"):
        forecast(rows=source_rows() + source_rows()[:1])
    with pytest.raises(ValueError, match="finite"):
        forecast(coefficient=calibration(theta=(1e308, 1e308, 0.0, 0.0)))
    with pytest.raises(ValueError, match="retrieved"):
        forecast(retrieved_at=AS_OF + timedelta(seconds=1))


@pytest.mark.parametrize("missing", [(10,), (10, 11), (9, 13)])
def test_fixed_short_gap_policy(missing):
    result = forecast(rows=source_rows(absent=missing))
    assert result.repaired_hours == missing
    for hour in missing:
        assert result.source_times[hour] is None and result.f_kwh_m2[hour] is not None
        assert result.pv_kwh[hour] >= 0
    assert all(result.pv_kwh[h] == max(0, result.c_raw[h]) * min(result.f_kwh_m2[h], result.q_kwh_m2[h])
               for h in range(24) if result.q_kwh_m2[h] > 0)


def test_gap_interpolates_clear_fraction_and_preserves_valid_zero():
    rows = list(source_rows(absent=(10, 11), zero=(9,)))
    result = forecast(rows=tuple(rows))
    right_fraction = min(0.4 / result.q_kwh_m2[12], 1.0)
    assert result.f_kwh_m2[9] == 0 and result.pv_kwh[9] == 0
    assert result.f_kwh_m2[10] == pytest.approx(result.q_kwh_m2[10] * right_fraction / 3)
    assert result.f_kwh_m2[11] == pytest.approx(result.q_kwh_m2[11] * right_fraction * 2 / 3)


def test_pure_forecast_does_not_read_files_or_apply_weather_again(monkeypatch):
    coeff = calibration()
    rows = tuple({**row, "temp_c": 60.0, "cloud_cover": 100.0, "precipitation_mm": 100.0} for row in source_rows())
    def forbidden(*args, **kwargs):
        raise AssertionError("pure calculation attempted file I/O")
    monkeypatch.setattr("builtins.open", forbidden)
    plain = forecast(coefficient=coeff)
    changed_weather = forecast(rows=rows, coefficient=coeff)
    assert plain.pv_kwh == changed_weather.pv_kwh


@pytest.mark.parametrize("missing", [(9, 10, 11), (6,), tuple(range(24))])
def test_long_endpoint_and_all_day_gaps_fail(missing):
    with pytest.raises(ValueError, match="radiation"):
        forecast(rows=source_rows(absent=missing))


def test_fit_uses_only_available_certified_originals():
    result = fit(training_days())
    assert result.theta == pytest.approx((2.0, 0.1, 0.2, 0.1), abs=1e-12)
    assert result.condition_number <= CONDITION_NUMBER_LIMIT
    assert result.training_dates == tuple(item["date"] for item in training_days())
    assert result.training_samples == 13 * len(training_days())
    actual_refs = [{"date": item["date"].isoformat(), "sha256": item["actual_sha256"]} for item in training_days()]
    import json
    assert json.loads(result.raw_bytes)["actual_source_sha256"] == hashlib.sha256(canonical_pack_bytes(actual_refs)).hexdigest()
    excluded = certified_day(date(2026, 9, 1), certified=False)
    incomplete = certified_day(date(2026, 9, 2), complete=False)
    assert fit(training_days() + (excluded, incomplete)).raw_bytes == result.raw_bytes
    for extra in (certified_day(TARGET), certified_day(TARGET + timedelta(days=1)),
                  certified_day(date(2026, 9, 1), available=AS_OF + timedelta(seconds=1))):
        with pytest.raises(ValueError, match="target/future/unavailable"):
            fit(training_days() + (extra,))


def test_fit_corrections_replace_and_same_time_conflicts_fail():
    days = training_days()
    original = days[0]
    changed = certified_day(original["date"], version=1, available=AS_OF - timedelta(hours=1))
    newer = certified_day(original["date"], version=2, available=AS_OF)
    result = fit(days[1:] + (changed, newer))
    expected = fit(days[1:] + (newer,))
    assert result.raw_bytes == expected.raw_bytes
    assert len(result.training_dates) == len(days)
    with pytest.raises(ValueError, match="conflicting versions"):
        fit(days + (newer,))


def test_rank_insufficient_and_condition_boundary_fail_closed():
    with pytest.raises(ValueError, match="rank-deficient"):
        fit(training_days()[:1])
    with pytest.raises(ValueError, match="unstable"):
        calibration(condition_number=CONDITION_NUMBER_LIMIT + 0.01)
    accepted = calibration(condition_number=CONDITION_NUMBER_LIMIT)
    validate_calibration_for_run(accepted, expected_site="synthetic-site", expected_basis=BASIS_VERSION,
                                 target_date=TARGET, as_of=AS_OF)
    with pytest.raises(ValueError, match="unavailable"):
        validate_calibration_for_run(accepted, expected_site="synthetic-site", expected_basis=BASIS_VERSION,
                                     target_date=TARGET, as_of=AS_OF - timedelta(seconds=1))
    with pytest.raises(ValueError, match="fields differ"):
        replace(accepted, site_fingerprint="different")
    with pytest.raises(ValueError, match="sample"):
        calibration(training_samples=38)


def test_narrow_season_full_rank_fit_is_rejected_without_clipping():
    narrow = tuple(certified_day(date(2026, 1, 1) + timedelta(days=i)) for i in range(3))
    with pytest.raises(ValueError, match="unstable"):
        fit(narrow)


def test_measured_actual_zero_is_kept_and_invalid_actual_is_rejected():
    days = list(training_days())
    rows = list(days[0]["hours"])
    rows[7] = {**rows[7], "actual_halfhour_kwh": (0.0, 0.0)}
    days[0] = {**days[0], "hours": tuple(rows)}
    assert fit(days).training_dates == tuple(item["date"] for item in training_days())
    rows[7] = {**rows[7], "actual_halfhour_kwh": (False, 0.0)}
    days[0] = {**days[0], "hours": tuple(rows)}
    with pytest.raises(ValueError, match="numeric"):
        fit(days)


def test_certification_core_does_not_filter_low_altitude_or_zero_q_training_rows():
    days = list(training_days())
    rows = list(days[0]["hours"])
    low = max((i for i,r in enumerate(rows) if r["solar_altitude_deg"] < 10
               and clear_ghi_hour(days[0]["date"],r["hour"],latitude=35.,longitude=135.,timezone="Asia/Tokyo") > 0),
              key=lambda i:clear_ghi_hour(days[0]["date"],rows[i]["hour"],latitude=35.,longitude=135.,timezone="Asia/Tokyo"))
    rows[low] = {**rows[low], "sunshine_minutes":0., "satellite_ghi_w_m2":None,
                 "actual_halfhour_kwh":(.1,.1)}
    days[0] = {**days[0], "hours":tuple(rows)}
    design,targets=[],[]
    for item in days:
        day=item["date"]
        phi=2*math.pi*(day.timetuple().tm_yday-1)/(366 if calendar.isleap(day.year) else 365)
        for row in item["hours"]:
            h=row["hour"]
            q=clear_ghi_hour(day,h,latitude=35.,longitude=135.,timezone="Asia/Tokyo")
            design.append(q*np.array([1.,(h+.5-12)/6,math.cos(phi),math.sin(phi)]))
            targets.append(sum(row["actual_halfhour_kwh"]))
    expected=np.linalg.lstsq(np.array(design),np.array(targets),rcond=None)[0]
    result=fit(days)
    assert result.training_samples == len(design) == 13*len(days)
    assert any(np.all(row==0) for row in design)
    assert result.theta == pytest.approx(expected,abs=1e-12,rel=0)
    assert result.theta != pytest.approx((2.,.1,.2,.1),abs=1e-9,rel=0)

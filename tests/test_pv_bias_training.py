from dataclasses import replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.energy_plan.hourly_pv_bias import PvBiasDay, ReplayErrors
from app.energy_plan.pv_bias_training import (
    PvTrainingDay, coefficient_at, collect_training_days, fit_training_bias, select_training_bias,
)
from app.forecasting.pv_ideal_max import IdealMaxArray, IdealMaxGeometry

ZONE = ZoneInfo("Asia/Tokyo")
AS_OF = datetime(2026, 11, 1, 2, tzinfo=ZONE)


def history(count=20):
    days = []
    for i in range(count):
        day = date(2026, 10, 1) + timedelta(days=i)
        issue = datetime.combine(day, time(2), ZONE)
        days.append(PvTrainingDay(day, issue, dict.fromkeys(range(24), 4.),
                    dict.fromkeys(range(24), .5), dict.fromkeys(range(24), "clear"),
                    dict.fromkeys(range(24), 1.), dict.fromkeys(range(24), issue + timedelta(days=1, hours=-1)), str(i)))
    return tuple(days)


def economics(day):
    values = dict.fromkeys(range(24), 1.)
    return PvBiasDay(day.day, day.issued_at, day.available[0], "run", dict.fromkeys(range(24), 2.),
                     day.basis, values, day.weather, day.actual, values, values, values, values, (), day.source_sha256)


def select(days, callback=None):
    return select_training_bias(days=days, coefficients=[(datetime(2020, 1, 1, tzinfo=ZONE), 1.)],
        as_of=AS_OF, target_date="2026-11-01", forecast_pv=dict.fromkeys(range(24), 2.),
        maximum=dict.fromkeys(range(24), 4.), weather=dict.fromkeys(range(24), "clear"),
        economic_days=tuple(economics(d) for d in days), evaluate_day=callback)


def test_minimum_seven_is_separate_from_twenty_day_regularization():
    six, _ = fit_training_bias(history(6), coefficient=1, as_of=AS_OF, before=date(2026, 11, 1))
    seven, counts = fit_training_bias(history(7), coefficient=1, as_of=AS_OF, before=date(2026, 11, 1))
    assert six == {}
    assert counts["12:clear"] == 7
    assert seven[(12, "clear")] == pytest.approx(-7 / 27)


def test_soc_tie_with_fee_improvement_can_be_adopted():
    def callback(day, pv):
        return ReplayErrors(0., 0., 1. if pv[12] == 2 else .5)
    pv, diagnostic, model = select(history(), callback)
    assert model is not None and pv[12] < 2
    assert diagnostic["status"] == "accepted"
    assert len(diagnostic["validation_days"]) >= 5
    assert diagnostic["minimum_bin_days"] == 7 and diagnostic["lookback_days"] == 365


@pytest.mark.parametrize("regression", ["soc", "buy", "fee"])
def test_all_available_complete_replays_still_cannot_regress(regression):
    def callback(day, pv):
        candidate = pv[12] != 2
        return ReplayErrors(float(candidate and regression == "soc"),
                            float(candidate and regression == "buy"), float(candidate and regression == "fee"))
    pv, diagnostic, model = select(history(), callback)
    assert model is None and pv[12] == 2
    assert diagnostic["reason"] == "economic_replay_unavailable_or_regressed"


def test_local_shadow_does_not_fake_economic_acceptance():
    _, diagnostic, model = select(history())
    assert model is None and diagnostic["economic_validation_days"] == 0
    assert diagnostic["pv_validation"]["candidate_mae"] < diagnostic["pv_validation"]["baseline_mae"]


def test_late_receipts_cannot_leak_into_earlier_folds():
    days = tuple(replace(d, available=dict.fromkeys(range(24), AS_OF)) for d in history())
    _, diagnostic, model = select(days, lambda d, p: ReplayErrors(0, 0, 0))
    assert model is None and diagnostic["validation_days"] == []


def test_same_timestamp_uses_last_native_capability_and_duplicate_dates_fail():
    t = AS_OF - timedelta(days=1)
    assert coefficient_at([(t, 1.1), (t, 1.5), (AS_OF + timedelta(days=1), 2.)], AS_OF) == 1.5
    with pytest.raises(ValueError, match="duplicate"):
        select(history() + history(1))


def test_partial_actuals_and_missing_soc_do_not_discard_other_pv_hours():
    geo = IdealMaxGeometry(35., 139., "Asia/Tokyo", (IdealMaxArray(0, 19, 1.08, .82, 1.),))
    plan = {"forecast": {"date": "2026-10-01", "hourly_weather": [
        {"hour": h, "weather_code": 0, "shortwave_radiation_w_m2": 100.} for h in range(24)]}}
    origin = {"date": "2026-10-01", "issued_at": "2026-10-01T02:00:00+09:00",
              "available_at": AS_OF - timedelta(days=1), "sha256": "f" * 64, "plan": plan}
    actual = [{"dt": datetime(2026, 10, 1, h, minute), "pv": 0., "available_at": AS_OF - timedelta(days=1)}
              for h in (10, 11) for minute in (0, 30)]
    actual.pop()
    days, _ = collect_training_days([origin], actual, geometry=geo, target_date="2026-11-01", as_of=AS_OF)
    assert len(days) == 1 and days[0].actual == {10: 0.}
    bad = [dict(r, pv=float("nan")) for r in actual]
    days, _ = collect_training_days([origin], bad, geometry=geo, target_date="2026-11-01", as_of=AS_OF)
    assert days == ()


def test_365_day_window_includes_old_weather_without_pooling_classes():
    day = history(1)[0]
    old = replace(day, day=date(2026, 1, 1))
    _, counts = fit_training_bias((old,), coefficient=1, as_of=AS_OF, before=date(2026, 11, 1))
    assert counts["12:clear"] == 1 and "12:rain" not in counts
    very_old = replace(day, day=date(2025, 1, 1))
    assert fit_training_bias((very_old,), coefficient=1, as_of=AS_OF, before=date(2026, 11, 1))[1] == {}


def test_csv_reader_to_pv_sample_preserves_energy_and_missing_soc(tmp_path):
    from app.energy_plan.workflow import _read_rows
    path = tmp_path / "actual.csv"
    path.write_text(
        "年月日,時刻,発電電力量[kWh],消費電力量[kWh],売電電力量[kWh],買電電力量[kWh],充電電力量[kWh],放電電力量[kWh],蓄電残量(SOC)[%]\n"
        "2026/10/01,10:00,0,1,0,1,0,0,\n"
        "2026/10/01,10:30,0.2,1,0,0.8,0,0,\n", encoding="utf-8-sig")
    rows = _read_rows([path])
    for row in rows:
        row["available_at"] = "2026-10-02T00:00:00+09:00"
    geo = IdealMaxGeometry(35., 139., "Asia/Tokyo", (IdealMaxArray(0, 19, 1.08, .82, 1.),))
    plan = {"forecast": {"date": "2026-10-01", "hourly_weather": [
        {"hour": h, "weather_code": 0, "shortwave_radiation_w_m2": 100.} for h in range(24)]}}
    origin = {"date": "2026-10-01", "issued_at": "2026-10-01T02:00:00+09:00",
              "available_at": "2026-10-02T00:00:00+09:00", "sha256": "a" * 64, "plan": plan}
    days, _ = collect_training_days([origin], rows, geometry=geo, target_date="2026-11-01", as_of=AS_OF)
    assert days[0].actual[10] == pytest.approx(.2)

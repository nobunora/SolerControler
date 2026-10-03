from datetime import datetime, timedelta, timezone

from app.runtime.forced_charge_monitor import ForcedChargeCompletionEstimator
from app.runtime.soc_reading import SocReading, read_soc_with_fallback


def test_repeated_measurement_does_not_reset_eta_and_new_measurement_does():
    estimator = ForcedChargeCompletionEstimator(rate_percent_per_hour=30, confirm_before_minutes=0)
    measured = datetime(2026, 10, 2, 20, tzinfo=timezone.utc)
    args = dict(target_soc=97, latest_soc=94, fallback_poll_seconds=180, cutoff_seconds=3000)
    assert estimator.next_check_seconds(**args, measured_at=measured, now=measured, measurement_id="a") == 180
    eta = estimator.eta
    assert estimator.next_check_seconds(**args, measured_at=measured, now=measured + timedelta(minutes=5), measurement_id="a") == 60
    assert estimator.eta == eta
    assert estimator.next_check_seconds(**args, measured_at=measured + timedelta(minutes=5), now=measured + timedelta(minutes=5), measurement_id="b") == 180
    assert estimator.eta == eta + timedelta(minutes=5)


def test_unknown_future_naive_and_cutoff_keep_numeric_schedule():
    estimator = ForcedChargeCompletionEstimator(rate_percent_per_hour=30)
    now = datetime.now(timezone.utc)
    args = dict(target_soc=97, latest_soc=80, fallback_poll_seconds=180, cutoff_seconds=20)
    for measured in (None, now + timedelta(hours=1), now.replace(tzinfo=None)):
        assert estimator.next_check_seconds(**args, measured_at=measured, now=now) == 20
        assert estimator.eta is None
    assert SocReading(94, "realtime", None, now).measurement_time_source == "unknown"


def test_structured_callback_is_only_called_once():
    now = datetime.now(timezone.utc)
    reading = SocReading(94, "realtime", None, now, measured_at=now)
    calls = []

    def structured():
        calls.append(1)
        return reading

    def forbidden(*args):
        raise AssertionError("unnecessary extra read")

    result = read_soc_with_fallback([], latest_realtime=forbidden, latest_csv=forbidden,
                                    env_int=lambda name, default: default, env_float=lambda name, default: default,
                                    latest_structured=structured, allow_csv_fallback=False)
    assert result is reading
    assert calls == [1]


def test_nonfinite_rate_keeps_monitor_alive():
    estimator = ForcedChargeCompletionEstimator(rate_percent_per_hour=float("inf"))
    assert estimator.next_check_seconds(target_soc=97, latest_soc=94, fallback_poll_seconds=180, cutoff_seconds=300) == 60

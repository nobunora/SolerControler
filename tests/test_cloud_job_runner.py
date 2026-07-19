from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cloud_job_runner import (
    ForcedChargeCompletionEstimator,
    _execute_monitor_terminal_transition,
    SocReading,
    _adjust03_target_date,
    _estimate_forced_charge_minutes,
    _estimate_forced_charge_rate_percent_per_hour,
    _estimate_required_charge_kwh,
    _mask_env_updates,
    _monitor_partial_forced_and_stop,
    _persist_03_monitor_schedule_to_firestore,
    _persist_03_no_charge_decision_to_firestore,
    _read_plan_meta,
    _read_soc_with_fallback,
    _latest_realtime_soc_percent,
    _latest_csv_soc_reading,
    _required_charge_percent_from_plan,
    _run_adjust_03,
    _run_night_23,
)
from app.forced_charge import ChargeEffect, ChargeState, ChargeTransition


def test_mask_env_updates_hides_secrets() -> None:
    masked = _mask_env_updates(
        {
            "KP_MONITOR_PASSWORD": "plain-password",
            "API_TOKEN": "plain-token",
            "KP_WORKFLOW_MODE": "settings",
        }
    )
    assert masked["KP_MONITOR_PASSWORD"] == "***"
    assert masked["API_TOKEN"] == "***"
    assert masked["KP_WORKFLOW_MODE"] == "settings"


def test_mask_env_updates_none() -> None:
    assert _mask_env_updates(None) == {}


def test_required_charge_percent_from_plan_uses_soc_delta() -> None:
    pct = _required_charge_percent_from_plan(
        {
            "target_soc_7_percent": 80.0,
            "soc_now_percent": 25.0,
            "effective_capacity_kwh": 9.0,
            "required_night_charge_kwh": 2.0,
        }
    )
    assert pct == 55.0


def test_read_plan_meta_rejects_missing_target(tmp_path: Path) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text(
        """
        {
          "forecast": {"date": "2026-05-27"},
          "result": {"required_night_charge_kwh": 0.0}
        }
        """.strip(),
        encoding="utf-8",
    )

    try:
        _read_plan_meta(plan_path)
    except RuntimeError as exc:
        assert "target_soc_7_percent" in str(exc)
    else:
        raise AssertionError("missing target_soc_7_percent must be rejected")


def test_read_plan_meta_rejects_plan_quality_should_apply_false(tmp_path: Path) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text(
        """
        {
          "forecast": {"date": "2026-05-27"},
          "plan_quality": {"should_apply": false},
          "result": {
            "target_soc_7_percent": 40.0,
            "required_night_charge_kwh": 0.0
          }
        }
        """.strip(),
        encoding="utf-8",
    )

    try:
        _read_plan_meta(plan_path)
    except RuntimeError as exc:
        assert "not safe to apply" in str(exc)
    else:
        raise AssertionError("plan_quality.should_apply=false must be rejected")


def test_estimate_required_charge_kwh_uses_latest_soc(monkeypatch) -> None:
    monkeypatch.setenv("KP_NIGHT_CHARGE_EFFICIENCY", "0.9")
    required = _estimate_required_charge_kwh(
        plan_meta={
            "target_soc_7_percent": 100.0,
            "soc_now_percent": 0.0,
            "effective_capacity_kwh": 9.0,
            "required_night_charge_kwh": 9.0,
        },
        latest_soc_percent=60.0,
    )
    assert required == 4.0


def test_estimate_forced_charge_minutes_uses_empirical_soc_rate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ADJUST03_FORCE_CHARGE_SAMPLE_MIN_KWH", "1.2")
    monkeypatch.setenv("ADJUST03_FORCE_CHARGE_RATE_MIN_PERCENT_PER_HOUR", "25")
    monkeypatch.setenv("ADJUST03_FORCE_CHARGE_RATE_MAX_PERCENT_PER_HOUR", "50")
    csv_path = tmp_path / "kp.csv"
    csv_path.write_text(
        "\n".join(
            [
                "年月日,時刻,蓄電残量(SOC)[%],充電電力量[kWh]",
                "2026/06/03,02:30,0,0",
                "2026/06/03,03:00,21,2.01",
                "2026/06/03,03:30,42,2.00",
            ]
        ),
        encoding="utf-8-sig",
    )

    minutes, info = _estimate_forced_charge_minutes(
        plan_meta={"target_soc_7_percent": 80.0, "soc_now_percent": 0.0},
        latest_soc_percent=0.0,
        csv_paths=[csv_path],
    )

    assert minutes == 115
    assert info["source"] == "csv-14d-degradation-trend-ewma-soc-rate"
    assert info["sample_count"] == 1
    assert info["interval_sample_count"] == 2
    assert info["percent_per_hour"] == 42.0


def test_forced_charge_rate_tracks_recent_14_day_degradation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ADJUST03_FORCE_CHARGE_SAMPLE_MIN_KWH", "1.2")
    csv_path = tmp_path / "kp.csv"
    lines = ["年月日,時刻,蓄電残量(SOC)[%],充電電力量[kWh]"]
    for offset, daily_gain in enumerate([20, 19, 18, 17, 16, 15]):
        day = 10 + offset
        lines.extend(
            [
                f"2026/07/{day:02d},02:30,0,0",
                f"2026/07/{day:02d},03:00,{daily_gain},2.0",
                f"2026/07/{day:02d},03:30,{daily_gain * 2},2.0",
            ]
        )
    csv_path.write_text("\n".join(lines), encoding="utf-8-sig")

    info = _estimate_forced_charge_rate_percent_per_hour([csv_path])

    assert info["source"] == "csv-14d-degradation-trend-ewma-soc-rate"
    assert info["sample_count"] == 6
    assert float(info["raw_percent_per_hour"]) < 35.0


def test_forced_charge_completion_estimator_checks_before_predicted_completion() -> None:
    estimator = ForcedChargeCompletionEstimator(rate_percent_per_hour=40.0, confirm_before_minutes=5)

    assert estimator.remaining_minutes(target_soc=80.0, latest_soc=60.0) == 30
    assert estimator.next_check_seconds(
        target_soc=80.0,
        latest_soc=60.0,
        fallback_poll_seconds=3600,
        cutoff_seconds=7200,
    ) == 25 * 60


def test_forced_charge_completion_estimator_caps_to_poll_and_cutoff() -> None:
    estimator = ForcedChargeCompletionEstimator(rate_percent_per_hour=20.0, confirm_before_minutes=5)

    assert estimator.next_check_seconds(
        target_soc=90.0,
        latest_soc=10.0,
        fallback_poll_seconds=180,
        cutoff_seconds=120,
    ) == 120


def test_adjust03_target_date_uses_current_day(monkeypatch) -> None:
    monkeypatch.delenv("FORECAST_DATE_OVERRIDE", raising=False)
    monkeypatch.setenv("TIMEZONE", "Asia/Tokyo")
    now = datetime(2026, 5, 27, 3, 10, tzinfo=ZoneInfo("Asia/Tokyo"))
    assert _adjust03_target_date(now=now) == "2026-05-27"


def test_monitor_forced_charge_applies_minimum_when_plan_target_is_zero(
    monkeypatch,
    tmp_path,
) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    calls: list[tuple[str, bool]] = []

    monkeypatch.setattr(
        "cloud_job_runner._read_plan_meta",
        lambda _: {"required_night_charge_kwh": 0.0, "target_soc_7_percent": 0.0, "effective_capacity_kwh": 10.0},
    )
    monkeypatch.setattr("cloud_job_runner._latest_kpnet_csv_paths", lambda _: [])
    soc_values = iter([0.0, 30.0])
    monkeypatch.setattr("cloud_job_runner._latest_realtime_soc_percent", lambda: next(soc_values))
    monkeypatch.setattr("cloud_job_runner._seconds_until_cutoff", lambda **kwargs: 3600)
    monkeypatch.setattr(
        "cloud_job_runner._run_settings_profile",
        lambda *, profile, dynamic_forced_profile: calls.append((profile, dynamic_forced_profile)),
    )
    db_calls: list[tuple[str, bool, bool, dict[str, str] | None]] = []
    monkeypatch.setattr(
        "cloud_job_runner._run_db_pipeline_slot",
        lambda slot, *, include_csv=True, include_settings=True, extra_env=None: db_calls.append(
            (slot, include_csv, include_settings, extra_env)
        ),
    )

    _monitor_partial_forced_and_stop(plan_path)

    assert calls == [("forced", True), ("standby", False)]
    assert db_calls == [
        (
            "03",
            False,
            True,
            {
                "DATA_DB_WRITE_ONLY_23": "false",
                "DATA_PREFER_NIGHT_PLAN_METRICS": "true",
            },
        ),
        (
            "03",
            False,
            True,
            {
                "DATA_DB_WRITE_ONLY_23": "false",
                "DATA_PREFER_NIGHT_PLAN_METRICS": "true",
            },
        ),
    ]


def test_monitor_partial_forced_keeps_standby_when_charge_not_needed(
    monkeypatch,
    tmp_path,
) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("ADJUST03_MIN_TARGET_SOC_PERCENT", "0")

    monkeypatch.setattr(
        "cloud_job_runner._read_plan_meta",
        lambda _: {
            "required_night_charge_kwh": 0.2,
            "target_soc_7_percent": 2.0,
            "effective_capacity_kwh": 10.0,
        },
    )
    monkeypatch.setattr("cloud_job_runner._latest_kpnet_csv_paths", lambda _: [])
    monkeypatch.setattr("cloud_job_runner._latest_realtime_soc_percent", lambda: 10.0)
    persisted: list[dict] = []
    monkeypatch.setattr(
        "cloud_job_runner._persist_03_no_charge_decision_to_firestore",
        lambda **kwargs: persisted.append(kwargs) or True,
    )
    monkeypatch.setattr(
        "cloud_job_runner._run_settings_profile",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no KP-NET setting change expected")),
    )
    monkeypatch.setattr(
        "cloud_job_runner._run_db_pipeline_slot",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no settings ingestion expected")),
    )

    _monitor_partial_forced_and_stop(plan_path)

    assert persisted == [
        {
            "plan_meta": {
                "required_night_charge_kwh": 0.2,
                "target_soc_7_percent": 2.0,
                "effective_capacity_kwh": 10.0,
            },
            "target_soc": 2.0,
            "latest_soc": 10.0,
            "soc_source": "realtime",
            "required_kwh": 0.0,
        }
    ]


def test_read_soc_with_fallback_uses_realtime(monkeypatch) -> None:
    monkeypatch.setattr("cloud_job_runner._latest_realtime_soc_percent", lambda: 42.0)

    reading = _read_soc_with_fallback([])

    assert reading.value_percent == 42.0
    assert reading.source == "realtime"


@pytest.mark.parametrize("value", ["0", "38", "100"])
def test_latest_csv_soc_reading_accepts_valid_values(tmp_path, value: str) -> None:
    csv_path = tmp_path / "soc.csv"
    csv_path.write_text(
        f"年月日,時刻,蓄電残量(SOC)[%]\n2026/07/14,03:00,{value}\n",
        encoding="utf-8-sig",
    )

    reading, observed_at = _latest_csv_soc_reading([csv_path])

    assert reading == float(value)
    assert observed_at == datetime(2026, 7, 14, 3, 0)


@pytest.mark.parametrize("value", ["-1", "101", "780", "NaN", "Infinity", "-Infinity"])
def test_latest_csv_soc_reading_rejects_invalid_values(tmp_path, value: str) -> None:
    csv_path = tmp_path / "soc.csv"
    csv_path.write_text(
        f"年月日,時刻,蓄電残量(SOC)[%]\n2026/07/14,03:00,{value}\n",
        encoding="utf-8-sig",
    )

    assert _latest_csv_soc_reading([csv_path]) == (None, None)


def test_latest_csv_soc_reading_skips_newer_invalid_row(tmp_path) -> None:
    csv_path = tmp_path / "soc.csv"
    csv_path.write_text(
        "年月日,時刻,蓄電残量(SOC)[%]\n"
        "2026/07/14,02:55,38\n"
        "2026/07/14,03:00,780\n",
        encoding="utf-8-sig",
    )

    assert _latest_csv_soc_reading([csv_path]) == (38.0, datetime(2026, 7, 14, 2, 55))


def test_realtime_soc_returns_value_when_logout_fails(monkeypatch) -> None:
    monkeypatch.setenv("KP_MONITOR_USERNAME", "test-user")
    monkeypatch.setenv("KP_MONITOR_PASSWORD", "test-password")
    monkeypatch.setattr("app.kpnet_workflow.KpNetClient.login", lambda self: None)
    monkeypatch.setattr("app.kpnet_workflow.KpNetClient.read_realtime_soc_percent", lambda self: 47.0)
    monkeypatch.setattr(
        "app.kpnet_workflow.KpNetClient.logout",
        lambda self: (_ for _ in ()).throw(RuntimeError("logout failed")),
    )

    assert _latest_realtime_soc_percent() == 47.0


def test_realtime_soc_preserves_read_failure_when_logout_also_fails(monkeypatch) -> None:
    monkeypatch.setenv("KP_MONITOR_USERNAME", "test-user")
    monkeypatch.setenv("KP_MONITOR_PASSWORD", "test-password")
    monkeypatch.setattr("app.kpnet_workflow.KpNetClient.login", lambda self: None)
    monkeypatch.setattr(
        "app.kpnet_workflow.KpNetClient.read_realtime_soc_percent",
        lambda self: (_ for _ in ()).throw(ValueError("read failed")),
    )
    monkeypatch.setattr(
        "app.kpnet_workflow.KpNetClient.logout",
        lambda self: (_ for _ in ()).throw(RuntimeError("logout failed")),
    )

    with pytest.raises(ValueError, match="read failed"):
        _latest_realtime_soc_percent()


def test_realtime_soc_does_not_logout_after_login_failure(monkeypatch) -> None:
    logout_calls: list[bool] = []
    monkeypatch.setenv("KP_MONITOR_USERNAME", "test-user")
    monkeypatch.setenv("KP_MONITOR_PASSWORD", "test-password")
    monkeypatch.setattr(
        "app.kpnet_workflow.KpNetClient.login",
        lambda self: (_ for _ in ()).throw(RuntimeError("login failed")),
    )
    monkeypatch.setattr("app.kpnet_workflow.KpNetClient.logout", lambda self: logout_calls.append(True))

    with pytest.raises(RuntimeError, match="login failed"):
        _latest_realtime_soc_percent()

    assert logout_calls == []


def test_read_soc_with_fallback_uses_fresh_csv(monkeypatch) -> None:
    now = datetime.now()
    monkeypatch.setenv("ADJUST03_REALTIME_SOC_RETRY_ATTEMPTS", "1")
    monkeypatch.setattr(
        "cloud_job_runner._latest_realtime_soc_percent",
        lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    monkeypatch.setattr("cloud_job_runner._latest_csv_soc_reading", lambda _paths: (38.0, now))

    reading = _read_soc_with_fallback([])

    assert reading.value_percent == 38.0
    assert reading.source == "csv"
    assert "offline" in str(reading.error)


def test_read_soc_with_fallback_rejects_stale_csv(monkeypatch) -> None:
    monkeypatch.setenv("ADJUST03_REALTIME_SOC_RETRY_ATTEMPTS", "1")
    monkeypatch.setenv("ADJUST03_CSV_SOC_MAX_AGE_MINUTES", "60")
    monkeypatch.setattr(
        "cloud_job_runner._latest_realtime_soc_percent",
        lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    monkeypatch.setattr(
        "cloud_job_runner._latest_csv_soc_reading",
        lambda _paths: (38.0, datetime.now() - timedelta(hours=2)),
    )

    reading = _read_soc_with_fallback([])

    assert reading.value_percent is None
    assert reading.source == "unavailable"
    assert "stale" in str(reading.error)


def test_monitor_partial_forced_starts_immediately_then_switches_standby_at_cutoff(
    monkeypatch,
    tmp_path,
) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    calls: list[tuple[str, bool]] = []
    sleeps: list[int] = []
    cutoff_values = iter([3600, 3600])
    time_values = iter([0.0, 0.0, 0.0, 4000.0])

    monkeypatch.setattr(
        "cloud_job_runner._read_plan_meta",
        lambda _: {"required_night_charge_kwh": 1.0, "target_soc_7_percent": 25.0, "effective_capacity_kwh": 10.0},
    )
    monkeypatch.setattr(
        "cloud_job_runner._latest_kpnet_csv_paths",
        lambda _: [],
    )
    monkeypatch.setattr(
        "cloud_job_runner._latest_realtime_soc_percent",
        lambda: 20.0,
    )
    monkeypatch.setattr(
        "cloud_job_runner._seconds_until_cutoff",
        lambda **kwargs: next(cutoff_values),
    )
    monkeypatch.setattr(
        "cloud_job_runner._run_csv_with_retry",
        lambda *, label="kpnet-csv": (_ for _ in ()).throw(AssertionError("03 monitor must not fetch CSV")),
    )
    monkeypatch.setattr(
        "cloud_job_runner.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )
    monkeypatch.setattr(
        "cloud_job_runner.time.time",
        lambda: next(time_values),
    )
    monkeypatch.setattr(
        "cloud_job_runner._run_settings_profile",
        lambda *, profile, dynamic_forced_profile: calls.append((profile, dynamic_forced_profile)),
    )

    _monitor_partial_forced_and_stop(plan_path)

    assert sleeps == [180]
    assert calls == [("forced", True), ("standby", False)]


@pytest.mark.parametrize(
    "terminal",
    [ChargeState.COMPLETED_CUTOFF, ChargeState.FAILED_TIMEOUT],
)
def test_monitor_terminal_executor_preserves_timeout_persistence_reason(
    monkeypatch, terminal: ChargeState
) -> None:
    calls: list[str] = []
    reasons: list[str] = []
    monkeypatch.setattr(
        "cloud_job_runner._run_03_settings_profile_with_db",
        lambda *, profile, dynamic_forced_profile, label: calls.append(label),
    )
    monkeypatch.setattr(
        "cloud_job_runner._persist_03_monitor_stop_reason",
        lambda _meta, reason: reasons.append(reason) or True,
    )

    handled = _execute_monitor_terminal_transition(
        {},
        ChargeTransition(
            ChargeState.STOPPING,
            (ChargeEffect.SET_STANDBY,),
            "cutoff_reached" if terminal is ChargeState.COMPLETED_CUTOFF else "runtime_limit",
            terminal,
        ),
    )

    assert handled is True
    assert calls == ["03-timer-standby"]
    assert reasons == ["monitor_timeout"]


def test_monitor_terminal_executor_persists_sensor_reason_when_standby_fails(monkeypatch) -> None:
    reasons: list[str] = []
    monkeypatch.setattr(
        "cloud_job_runner._run_03_settings_profile_with_db",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("standby failed")),
    )
    monkeypatch.setattr(
        "cloud_job_runner._persist_03_monitor_stop_reason",
        lambda _meta, reason: reasons.append(reason) or True,
    )

    with pytest.raises(RuntimeError, match="standby failed"):
        _execute_monitor_terminal_transition(
            {},
            ChargeTransition(
                ChargeState.STOPPING,
                (ChargeEffect.SET_STANDBY,),
                "sensor_failure_limit",
                ChargeState.FAILED_SENSOR,
            ),
        )

    assert reasons == ["soc_unavailable_fail_safe"]


def test_monitor_stops_safely_after_consecutive_soc_failures(monkeypatch, tmp_path) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    calls: list[tuple[str, bool]] = []
    reasons: list[str] = []
    readings = iter(
        [
            SocReading(None, "unavailable", "offline", None),
            SocReading(None, "unavailable", "offline", None),
            SocReading(None, "unavailable", "offline", None),
        ]
    )
    monkeypatch.setenv("ADJUST03_MAX_CONSECUTIVE_SOC_FAILURES", "2")
    monkeypatch.setenv("ADJUST03_ALLOW_FORCED_START_WITHOUT_SOC", "true")
    monkeypatch.setattr(
        "cloud_job_runner._read_plan_meta",
        lambda _: {
            "date": "2026-07-14",
            "required_night_charge_kwh": 1.0,
            "target_soc_7_percent": 80.0,
            "effective_capacity_kwh": 10.0,
        },
    )
    monkeypatch.setattr("cloud_job_runner._latest_kpnet_csv_paths", lambda _: [])
    monkeypatch.setattr("cloud_job_runner._read_soc_with_fallback", lambda _: next(readings))
    monkeypatch.setattr("cloud_job_runner._seconds_until_cutoff", lambda **kwargs: 3600)
    monkeypatch.setattr("cloud_job_runner.time.time", lambda: 0.0)
    monkeypatch.setattr("cloud_job_runner.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "cloud_job_runner._run_settings_profile",
        lambda *, profile, dynamic_forced_profile: calls.append((profile, dynamic_forced_profile)),
    )
    monkeypatch.setattr("cloud_job_runner._run_db_pipeline_slot", lambda *args, **kwargs: None)
    monkeypatch.setattr("cloud_job_runner._persist_03_monitor_schedule_to_firestore", lambda **kwargs: True)
    monkeypatch.setattr(
        "cloud_job_runner._persist_03_monitor_stop_reason",
        lambda _plan, reason: reasons.append(reason) or True,
    )

    _monitor_partial_forced_and_stop(plan_path)

    assert calls == [("forced", True), ("standby", False)]
    assert reasons == ["soc_unavailable_fail_safe"]


def test_monitor_exception_after_forced_start_attempts_fail_safe_standby(monkeypatch, tmp_path) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    profiles: list[str] = []
    stop_reasons: list[str] = []
    readings = iter([SocReading(20.0, "realtime", None, None), RuntimeError("sensor transport failed")])

    monkeypatch.setattr(
        "cloud_job_runner._read_plan_meta",
        lambda _: {
            "required_night_charge_kwh": 1.0,
            "target_soc_7_percent": 80.0,
            "effective_capacity_kwh": 10.0,
        },
    )
    monkeypatch.setattr("cloud_job_runner._latest_kpnet_csv_paths", lambda _: [])

    def read_soc(_paths):
        value = next(readings)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr("cloud_job_runner._read_soc_with_fallback", read_soc)
    monkeypatch.setattr("cloud_job_runner._seconds_until_cutoff", lambda **kwargs: 3600)
    monkeypatch.setattr(
        "cloud_job_runner._run_03_settings_profile_with_db",
        lambda *, profile, dynamic_forced_profile, label: profiles.append(profile),
    )
    monkeypatch.setattr("cloud_job_runner._persist_03_monitor_schedule_to_firestore", lambda **kwargs: True)
    monkeypatch.setattr(
        "cloud_job_runner._persist_03_monitor_stop_reason",
        lambda _meta, reason, **kwargs: stop_reasons.append(reason) or True,
    )

    with pytest.raises(RuntimeError, match="sensor transport failed"):
        _monitor_partial_forced_and_stop(plan_path)

    assert profiles == ["forced", "standby"]
    assert stop_reasons[-1] == "monitor_exception_fail_safe"


def test_forced_start_failure_attempts_fail_safe_standby(monkeypatch, tmp_path) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    profiles: list[str] = []

    monkeypatch.setattr(
        "cloud_job_runner._read_plan_meta",
        lambda _: {
            "required_night_charge_kwh": 1.0,
            "target_soc_7_percent": 80.0,
            "effective_capacity_kwh": 10.0,
        },
    )
    monkeypatch.setattr("cloud_job_runner._latest_kpnet_csv_paths", lambda _: [])
    monkeypatch.setattr(
        "cloud_job_runner._read_soc_with_fallback",
        lambda _paths: SocReading(20.0, "realtime", None, None),
    )
    monkeypatch.setattr("cloud_job_runner._seconds_until_cutoff", lambda **kwargs: 3600)
    monkeypatch.setattr("cloud_job_runner._persist_03_monitor_schedule_to_firestore", lambda **kwargs: True)

    def apply_profile(*, profile, dynamic_forced_profile, label):
        profiles.append(profile)
        if profile == "forced":
            raise RuntimeError("write confirmation failed")

    monkeypatch.setattr("cloud_job_runner._run_03_settings_profile_with_db", apply_profile)
    monkeypatch.setattr("cloud_job_runner._persist_03_monitor_stop_reason", lambda *args, **kwargs: True)

    with pytest.raises(RuntimeError, match="write confirmation failed"):
        _monitor_partial_forced_and_stop(plan_path)

    assert profiles == ["forced", "standby"]


def test_forced_reapply_failure_attempts_fail_safe_standby(monkeypatch, tmp_path) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    profiles: list[str] = []
    stop_reasons: list[str] = []

    monkeypatch.setenv("ADJUST03_FORCE_REAPPLY_AFTER_POLLS", "1")
    monkeypatch.setattr(
        "cloud_job_runner._read_plan_meta",
        lambda _: {
            "required_night_charge_kwh": 1.0,
            "target_soc_7_percent": 80.0,
            "effective_capacity_kwh": 10.0,
        },
    )
    monkeypatch.setattr("cloud_job_runner._latest_kpnet_csv_paths", lambda _: [])
    monkeypatch.setattr(
        "cloud_job_runner._read_soc_with_fallback",
        lambda _paths: SocReading(20.0, "realtime", None, None),
    )
    monkeypatch.setattr("cloud_job_runner._seconds_until_cutoff", lambda **kwargs: 3600)
    monkeypatch.setattr("cloud_job_runner._persist_03_monitor_schedule_to_firestore", lambda **kwargs: True)
    monkeypatch.setattr(
        "cloud_job_runner._persist_03_monitor_stop_reason",
        lambda _meta, reason, **kwargs: stop_reasons.append(reason) or True,
    )

    forced_calls = 0

    def apply_profile(*, profile, dynamic_forced_profile, label):
        nonlocal forced_calls
        profiles.append(profile)
        if profile == "forced":
            forced_calls += 1
            if forced_calls == 2:
                raise RuntimeError("reapply confirmation failed")

    monkeypatch.setattr("cloud_job_runner._run_03_settings_profile_with_db", apply_profile)

    with pytest.raises(RuntimeError, match="reapply confirmation failed"):
        _monitor_partial_forced_and_stop(plan_path)

    assert profiles == ["forced", "forced", "standby"]
    assert stop_reasons[-1] == "forced_reapply_failed_fail_safe"


def test_monitor_keeps_standby_when_initial_soc_is_unavailable(monkeypatch, tmp_path) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    calls: list[tuple[str, bool]] = []
    persisted: list[tuple[str, SocReading | None]] = []
    reading = SocReading(None, "unavailable", "realtime offline; CSV SOC unavailable", None)
    monkeypatch.delenv("ADJUST03_ALLOW_FORCED_START_WITHOUT_SOC", raising=False)
    monkeypatch.setattr(
        "cloud_job_runner._read_plan_meta",
        lambda _: {
            "date": "2026-07-14",
            "required_night_charge_kwh": 1.0,
            "target_soc_7_percent": 80.0,
            "effective_capacity_kwh": 10.0,
        },
    )
    monkeypatch.setattr("cloud_job_runner._latest_kpnet_csv_paths", lambda _: [])
    monkeypatch.setattr("cloud_job_runner._read_soc_with_fallback", lambda _: reading)
    monkeypatch.setattr(
        "cloud_job_runner._run_settings_profile",
        lambda *, profile, dynamic_forced_profile: calls.append((profile, dynamic_forced_profile)),
    )
    monkeypatch.setattr("cloud_job_runner._run_db_pipeline_slot", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "cloud_job_runner._persist_03_monitor_stop_reason",
        lambda _plan, reason, *, soc_reading=None: persisted.append((reason, soc_reading)) or True,
    )

    _monitor_partial_forced_and_stop(plan_path)

    assert calls == [("standby", False)]
    assert persisted == [("initial_soc_unavailable", reading)]


def test_run_night_23_only_applies_standby_mode(monkeypatch) -> None:
    calls: list[tuple[str, bool]] = []

    monkeypatch.delenv("NIGHT23_SETTINGS_PROFILE", raising=False)
    monkeypatch.setattr(
        "cloud_job_runner._run_settings_profile_with_retry",
        lambda *, profile, dynamic_forced_profile, label: calls.append((profile, dynamic_forced_profile)),
    )
    monkeypatch.setattr(
        "cloud_job_runner._run_csv_with_retry",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("23:00 must not fetch CSV")),
    )
    monkeypatch.setattr(
        "cloud_job_runner._run_with_retry",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("23:00 must not run forecasts")),
    )
    monkeypatch.setattr(
        "cloud_job_runner._run_db_pipeline_slot",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("23:00 must not run data pipeline")),
    )

    _run_night_23()

    assert calls == [("standby", False)]


def test_run_adjust_03_regenerates_missing_plan(monkeypatch, tmp_path) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    calls: list[tuple[str, dict[str, str]]] = []
    persisted: list[str] = []
    feedback_targets: list[str] = []
    monitored: list[Path] = []

    def fake_run(command, env_updates=None):
        script = list(command)[-1]
        calls.append((script, dict(env_updates or {})))
        if script == "energy_model_main.py":
            plan_path.write_text(
                '{"forecast":{"date":"2026-05-27"},"result":{"target_soc_7_percent":80}}',
                encoding="utf-8",
            )

    monkeypatch.setenv("KP_NIGHT_PLAN_PATH", str(plan_path))
    monkeypatch.setattr("cloud_job_runner._run", fake_run)
    monkeypatch.setattr("cloud_job_runner._adjust03_target_date", lambda: "2026-05-27")
    monkeypatch.setattr("cloud_job_runner._restore_night_plan_from_firestore", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        "cloud_job_runner._persist_night_plan_to_firestore",
        lambda _path, *, source: persisted.append(source) or True,
    )
    monkeypatch.setattr(
        "cloud_job_runner._persist_previous_day_soc_feedback",
        lambda *, target_date, csv_paths: feedback_targets.append(target_date) or True,
    )
    monkeypatch.setattr("cloud_job_runner._monitor_partial_forced_and_stop", lambda path: monitored.append(path))

    _run_adjust_03()

    assert ("kpnet_main.py", {"KP_WORKFLOW_MODE": "csv"}) in calls
    assert ("energy_model_main.py", {"FORECAST_DATE_OVERRIDE": "2026-05-27"}) in calls
    assert feedback_targets == ["2026-05-27"]
    assert persisted == ["adjust03-regenerated"]
    assert monitored == [plan_path]


def test_persist_03_monitor_schedule_records_dashboard_event(monkeypatch) -> None:
    writes: dict[tuple[str, str], dict] = {}

    class FakeDocument:
        def __init__(self, collection_name: str, document_id: str) -> None:
            self.collection_name = collection_name
            self.document_id = document_id

        def set(self, payload: dict, merge: bool = False) -> None:
            writes[(self.collection_name, self.document_id)] = payload

    class FakeCollection:
        def __init__(self, collection_name: str) -> None:
            self.collection_name = collection_name

        def document(self, document_id: str) -> FakeDocument:
            return FakeDocument(self.collection_name, document_id)

    class FakeClient:
        def collection(self, collection_name: str) -> FakeCollection:
            return FakeCollection(collection_name)

    monkeypatch.setattr("cloud_job_runner._open_firestore_for_plan", lambda: FakeClient())

    persisted = _persist_03_monitor_schedule_to_firestore(
        plan_meta={"date": "2026-06-03"},
        charge_start_time="02:43",
        charge_end_time="07:00",
        target_soc=79.0,
        latest_soc=0.0,
        required_kwh=7.68,
        estimated_charge_minutes=257,
        default_power_kw=1.8,
    )

    assert persisted is True
    event = writes[("settings_events", "2026-06-03-03-monitor-schedule")]
    assert event["slot"] == "03"
    assert event["status"] == "forced-started"
    assert event["detail_json"]["charge_start_time"] == "02:43"
    assert event["detail_json"]["charge_end_time"] == "07:00"
    assert writes[("night_charge_plans", "2026-06-03")]["monitor_schedule"]["schedule_source"] == "03-monitor"


def test_persist_03_no_charge_decision_records_completed_event(monkeypatch) -> None:
    writes: dict[tuple[str, str], dict] = {}

    class FakeDocument:
        def __init__(self, collection_name: str, document_id: str) -> None:
            self.collection_name = collection_name
            self.document_id = document_id

        def set(self, payload: dict, merge: bool = False) -> None:
            writes[(self.collection_name, self.document_id)] = payload

    class FakeCollection:
        def __init__(self, collection_name: str) -> None:
            self.collection_name = collection_name

        def document(self, document_id: str) -> FakeDocument:
            return FakeDocument(self.collection_name, document_id)

    class FakeClient:
        def collection(self, collection_name: str) -> FakeCollection:
            return FakeCollection(collection_name)

    monkeypatch.setattr("cloud_job_runner._open_firestore_for_plan", lambda: FakeClient())

    persisted = _persist_03_no_charge_decision_to_firestore(
        plan_meta={"date": "2026-07-09"},
        target_soc=0.0,
        latest_soc=0.0,
        required_kwh=0.0,
    )

    assert persisted is True
    event = writes[("settings_events", "2026-07-09-03-no-charge")]
    assert event["status"] == "skipped-no-charge"
    assert event["detail_json"]["plan_date"] == "2026-07-09"
    assert event["detail_json"]["schedule_source"] == "03-no-charge"

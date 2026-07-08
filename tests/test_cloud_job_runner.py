from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cloud_job_runner import (
    ForcedChargeCompletionEstimator,
    _adjust03_target_date,
    _estimate_forced_charge_minutes,
    _estimate_required_charge_kwh,
    _forecast_changed,
    _mask_env_updates,
    _monitor_partial_forced_and_stop,
    _persist_03_monitor_schedule_to_firestore,
    _read_plan_meta,
    _refresh_plan_for_same_date_if_changed,
    _required_charge_percent_from_plan,
    _run_adjust_03,
    _run_night_23,
    _should_stage_partial_forced,
)


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


def test_forecast_changed_threshold() -> None:
    base = ("2026-05-04", 5.00, 18.0)
    same = ("2026-05-04", 5.03, 18.1)
    changed = ("2026-05-04", 5.12, 18.1)
    changed_date = ("2026-05-05", 5.00, 18.0)

    assert not _forecast_changed(base, same, sun_epsilon_h=0.05, temp_epsilon_c=0.2)
    assert _forecast_changed(base, changed, sun_epsilon_h=0.05, temp_epsilon_c=0.2)
    assert _forecast_changed(base, changed_date, sun_epsilon_h=0.05, temp_epsilon_c=0.2)


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


def test_stage_partial_forced_enabled_for_51_to_99(monkeypatch) -> None:
    monkeypatch.setenv("KP_FORCE_PARTIAL_SOC_MIN_PERCENT", "51")
    monkeypatch.setenv("KP_FORCE_PARTIAL_SOC_MAX_PERCENT", "99")
    staged, required_pct, target_soc = _should_stage_partial_forced(
        plan_meta={
            "target_soc_7_percent": 80.0,
            "soc_now_percent": 20.0,
            "effective_capacity_kwh": 9.0,
            "required_night_charge_kwh": 5.0,
        },
        green_mode_max_charge_percent=50.0,
    )
    assert staged is True
    assert required_pct == 60.0
    assert target_soc == 80.0


def test_stage_partial_forced_uses_target_soc_above_green_ceiling(monkeypatch) -> None:
    monkeypatch.setenv("KP_FORCE_PARTIAL_SOC_MIN_PERCENT", "51")
    monkeypatch.setenv("KP_FORCE_PARTIAL_SOC_MAX_PERCENT", "99")
    staged, required_pct, target_soc = _should_stage_partial_forced(
        plan_meta={
            "target_soc_7_percent": 80.0,
            "soc_now_percent": 39.0,
            "effective_capacity_kwh": 9.0,
            "required_night_charge_kwh": 3.7,
        },
        green_mode_max_charge_percent=50.0,
    )

    assert staged is True
    assert required_pct == 41.0
    assert target_soc == 80.0


def test_stage_partial_forced_includes_100_percent(monkeypatch) -> None:
    monkeypatch.setenv("KP_FORCE_PARTIAL_SOC_MIN_PERCENT", "51")
    monkeypatch.setenv("KP_FORCE_PARTIAL_SOC_MAX_PERCENT", "100")
    staged, required_pct, target_soc = _should_stage_partial_forced(
        plan_meta={
            "target_soc_7_percent": 100.0,
            "soc_now_percent": 30.0,
            "effective_capacity_kwh": 9.0,
            "required_night_charge_kwh": 6.0,
        },
        green_mode_max_charge_percent=50.0,
    )
    assert staged is True
    assert required_pct == 70.0
    assert target_soc == 100.0


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
    assert info["source"] == "csv-forced-charge-soc-rate"
    assert info["sample_count"] == 2
    assert info["percent_per_hour"] == 42.0


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


def test_monitor_partial_forced_applies_forced_immediately_when_not_staged(
    monkeypatch,
    tmp_path,
) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    calls: list[tuple[str, bool]] = []

    monkeypatch.setattr(
        "cloud_job_runner._should_stage_partial_forced",
        lambda **kwargs: (False, 10.0, 40.0),
    )
    monkeypatch.setattr(
        "cloud_job_runner._read_plan_meta",
        lambda _: {"required_night_charge_kwh": 0.0, "target_soc_7_percent": 40.0},
    )
    monkeypatch.setattr("cloud_job_runner._latest_kpnet_csv_paths", lambda _: [])
    monkeypatch.setattr("cloud_job_runner._latest_soc_percent", lambda _: None)
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

    assert calls == [("forced", True)]
    assert db_calls == [
        (
            "03",
            False,
            True,
            {
                "DATA_DB_WRITE_ONLY_23": "false",
                "DATA_PREFER_NIGHT_PLAN_METRICS": "true",
            },
        )
    ]


def test_monitor_partial_forced_keeps_standby_when_charge_not_needed(
    monkeypatch,
    tmp_path,
) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "cloud_job_runner._should_stage_partial_forced",
        lambda **kwargs: (False, 0.0, 2.0),
    )
    monkeypatch.setattr(
        "cloud_job_runner._read_plan_meta",
        lambda _: {
            "required_night_charge_kwh": 0.2,
            "target_soc_7_percent": 2.0,
            "effective_capacity_kwh": 10.0,
        },
    )
    monkeypatch.setattr("cloud_job_runner._latest_kpnet_csv_paths", lambda _: [])
    monkeypatch.setattr("cloud_job_runner._latest_soc_percent", lambda _: 10.0)
    monkeypatch.setattr(
        "cloud_job_runner._run_settings_profile",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no KP-NET setting change expected")),
    )
    monkeypatch.setattr(
        "cloud_job_runner._run_db_pipeline_slot",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no settings ingestion expected")),
    )

    _monitor_partial_forced_and_stop(plan_path)


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
        "cloud_job_runner._should_stage_partial_forced",
        lambda **kwargs: (True, 60.0, 80.0),
    )
    monkeypatch.setattr(
        "cloud_job_runner._read_plan_meta",
        lambda _: {"required_night_charge_kwh": 1.0, "target_soc_7_percent": 80.0, "effective_capacity_kwh": 10.0},
    )
    monkeypatch.setattr(
        "cloud_job_runner._latest_kpnet_csv_paths",
        lambda _: [],
    )
    monkeypatch.setattr(
        "cloud_job_runner._latest_soc_percent",
        lambda _: 20.0,
    )
    monkeypatch.setattr(
        "cloud_job_runner._seconds_until_cutoff",
        lambda **kwargs: next(cutoff_values),
    )
    monkeypatch.setattr(
        "cloud_job_runner._run_csv_with_retry",
        lambda *, label="kpnet-csv": None,
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
    monkeypatch.setattr("cloud_job_runner._monitor_partial_forced_and_stop", lambda path: monitored.append(path))

    _run_adjust_03()

    assert ("kpnet_main.py", {"KP_WORKFLOW_MODE": "csv"}) in calls
    assert ("energy_model_main.py", {"FORECAST_DATE_OVERRIDE": "2026-05-27"}) in calls
    assert persisted == ["adjust03-regenerated"]
    assert monitored == [plan_path]


def test_refresh_plan_changed_disables_settings_ingestion(monkeypatch, tmp_path) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text(
        """
        {
          "forecast": {"date": "2026-05-27", "sun_hours": 5.0, "temp_c": 20.0},
          "result": {
            "target_soc_7_percent": 40.0,
            "required_night_charge_kwh": 0.0,
            "predicted_midday_surplus_kwh": 1.0
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    db_calls: list[dict[str, str]] = []

    def fake_run_with_retry(*args, **kwargs) -> None:
        plan_path.write_text(
            """
            {
              "forecast": {"date": "2026-05-27", "sun_hours": 6.0, "temp_c": 20.0},
              "result": {
                "target_soc_7_percent": 40.0,
                "required_night_charge_kwh": 0.0,
                "predicted_midday_surplus_kwh": 1.0
              }
            }
            """.strip(),
            encoding="utf-8",
        )

    monkeypatch.setattr("cloud_job_runner._run_with_retry", fake_run_with_retry)
    monkeypatch.setattr("cloud_job_runner._persist_night_plan_to_firestore", lambda _path, *, source: True)
    monkeypatch.setattr("cloud_job_runner._run", lambda _command, env_updates=None: db_calls.append(dict(env_updates or {})))

    assert _refresh_plan_for_same_date_if_changed(plan_path) is True

    assert db_calls == [
        {
            "CLOUD_JOB_SLOT": "03",
            "DATA_DB_WRITE_ONLY_23": "false",
            "DATA_PIPELINE_INCLUDE_SETTINGS": "false",
            "DATA_PREFER_NIGHT_PLAN_METRICS": "true",
        }
    ]


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

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cloud_job_runner import (
    _adjust03_target_date,
    _compute_force_activation_delay_seconds,
    _estimate_forced_charge_minutes,
    _estimate_required_charge_kwh,
    _forecast_changed,
    _mask_env_updates,
    _monitor_partial_forced_and_stop,
    _night23_target_date,
    _persist_03_monitor_schedule_to_firestore,
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


def test_compute_force_activation_delay_seconds() -> None:
    delay = _compute_force_activation_delay_seconds(
        cutoff_seconds=3 * 60 * 60,
        estimated_charge_minutes=90,
        start_advance_minutes=0,
    )
    # 3h先のcutoffに対して、90分前に強制開始
    assert delay == 90 * 60


def test_compute_force_activation_delay_seconds_immediate_when_late() -> None:
    delay = _compute_force_activation_delay_seconds(
        cutoff_seconds=30 * 60,
        estimated_charge_minutes=90,
        start_advance_minutes=0,
    )
    assert delay == 0


def test_night23_target_date_uses_next_day_before_midnight(monkeypatch) -> None:
    monkeypatch.delenv("FORECAST_DATE_OVERRIDE", raising=False)
    monkeypatch.setenv("TIMEZONE", "Asia/Tokyo")
    now = datetime(2026, 5, 26, 23, 10, tzinfo=ZoneInfo("Asia/Tokyo"))
    assert _night23_target_date(now=now) == "2026-05-27"


def test_night23_target_date_recovers_today_after_midnight(monkeypatch) -> None:
    monkeypatch.delenv("FORECAST_DATE_OVERRIDE", raising=False)
    monkeypatch.setenv("TIMEZONE", "Asia/Tokyo")
    now = datetime(2026, 5, 27, 0, 55, tzinfo=ZoneInfo("Asia/Tokyo"))
    assert _night23_target_date(now=now) == "2026-05-27"


def test_night23_target_date_uses_tomorrow_after_recovery_cutoff(monkeypatch) -> None:
    monkeypatch.delenv("FORECAST_DATE_OVERRIDE", raising=False)
    monkeypatch.setenv("TIMEZONE", "Asia/Tokyo")
    now = datetime(2026, 5, 27, 7, 1, tzinfo=ZoneInfo("Asia/Tokyo"))
    assert _night23_target_date(now=now) == "2026-05-28"


def test_night23_target_date_respects_explicit_override(monkeypatch) -> None:
    monkeypatch.setenv("FORECAST_DATE_OVERRIDE", "2026-06-01")
    now = datetime(2026, 5, 27, 0, 55, tzinfo=ZoneInfo("Asia/Tokyo"))
    assert _night23_target_date(now=now) == "2026-06-01"


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
    monkeypatch.setattr(
        "cloud_job_runner._run_settings_profile",
        lambda *, profile, dynamic_forced_profile: calls.append((profile, dynamic_forced_profile)),
    )

    _monitor_partial_forced_and_stop(plan_path)

    assert calls == []


def test_monitor_partial_forced_delays_forced_start_then_switches_green(
    monkeypatch,
    tmp_path,
) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    calls: list[tuple[str, bool]] = []
    sleeps: list[int] = []
    cutoff_values = iter([3600, 0, 0])

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
        lambda _: None,
    )
    monkeypatch.setattr(
        "cloud_job_runner._seconds_until_cutoff",
        lambda **kwargs: next(cutoff_values),
    )
    monkeypatch.setattr(
        "cloud_job_runner._sleep_with_progress",
        lambda total_seconds, *, label, chunk_seconds=300: sleeps.append(total_seconds),
    )
    monkeypatch.setattr(
        "cloud_job_runner._run_settings_profile",
        lambda *, profile, dynamic_forced_profile: calls.append((profile, dynamic_forced_profile)),
    )

    _monitor_partial_forced_and_stop(plan_path)

    assert sleeps and sleeps[0] > 0
    assert calls == [("forced", True), ("green", False)]


def test_run_night_23_ingests_csv_before_forecast(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_run(command, env_updates=None):
        script = list(command)[-1]
        calls.append((script, dict(env_updates or {})))
        if script == "energy_model_main.py":
            raise RuntimeError("forecast unavailable")

    monkeypatch.setenv("KP_NIGHT_PLAN_PATH", str(tmp_path / "night_charge_plan.json"))
    monkeypatch.setattr("cloud_job_runner._run", fake_run)
    monkeypatch.setattr("cloud_job_runner._night23_target_date", lambda: "2026-05-27")

    try:
        _run_night_23()
    except RuntimeError as exc:
        assert "forecast unavailable" in str(exc)
    else:
        raise AssertionError("_run_night_23 should fail when forecast fails")

    assert calls[:3] == [
        ("kpnet_main.py", {"KP_WORKFLOW_MODE": "csv"}),
        (
            "db_pipeline_main.py",
            {
                "CLOUD_JOB_SLOT": "23",
                "DATA_PIPELINE_INCLUDE_CSV": "true",
                "DATA_PIPELINE_INCLUDE_SETTINGS": "false",
            },
        ),
        ("energy_model_main.py", {"FORECAST_DATE_OVERRIDE": "2026-05-27"}),
    ]


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
        delay_seconds=9382,
    )

    assert persisted is True
    event = writes[("settings_events", "2026-06-03-03-monitor-schedule")]
    assert event["slot"] == "03"
    assert event["status"] == "planned-force-start"
    assert event["detail_json"]["charge_start_time"] == "02:43"
    assert event["detail_json"]["charge_end_time"] == "07:00"
    assert writes[("night_charge_plans", "2026-06-03")]["monitor_schedule"]["schedule_source"] == "03-monitor"

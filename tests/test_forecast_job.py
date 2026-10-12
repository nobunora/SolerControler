from __future__ import annotations

from pathlib import Path

from app.runtime import forecast_job


def test_forecast_job_ingests_csv_actuals_before_plan_and_forecast_persistence(monkeypatch, tmp_path) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []
    monkeypatch.setattr(forecast_job, "_target_date", lambda: "2026-09-03")
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setenv("KP_NIGHT_PLAN_PATH", str(tmp_path / "legacy-plan.json"))
    def run(command, env, **_kwargs):
        calls.append((command, env))
        if command[1] == "energy_model_main.py":
            Path(env["KP_NIGHT_PLAN_PATH"]).write_bytes(b"{}")
    monkeypatch.setattr(forecast_job, "_run", run)
    monkeypatch.setattr(forecast_job, "open_firestore", lambda: "client")
    recovered: list[dict] = []
    monkeypatch.setattr(forecast_job, "recover_missing_forecast_snapshots", lambda client, **kwargs: recovered.append({"client": client, **kwargs}) or {})
    # A unit test must never rebuild/upload the real dashboard snapshot when
    # the production gate has loaded its environment.
    monkeypatch.setattr(forecast_job, "dashboard_snapshot_prefix", lambda: "gs://unit-test/snapshots")
    snapshot_calls: list[Path] = []
    monkeypatch.setattr(forecast_job, "write_dashboard_snapshots", lambda path: snapshot_calls.append(path) or {})
    persisted: list[dict] = []
    monkeypatch.setattr(forecast_job, "persist_forecast_only_plan", lambda client, **kwargs: persisted.append({"client": client, **kwargs}) or 24)

    assert forecast_job.main() == 0
    private_path = Path(calls[2][1]["KP_NIGHT_PLAN_PATH"])
    assert private_path.parent.name.startswith("forecast-plan-")
    assert private_path != tmp_path / "legacy-plan.json"
    assert (tmp_path / "legacy-plan.json").read_bytes() == private_path.read_bytes()
    assert calls == [
        ([forecast_job.sys.executable, "kpnet_main.py"], {"KP_WORKFLOW_MODE": "csv"}),
        (
            [forecast_job.sys.executable, "db_pipeline_main.py"],
            {
                "DATA_PIPELINE_INCLUDE_SETTINGS": "false",
                "DATA_PIPELINE_INCLUDE_NIGHT_PLAN": "false",
                "DASHBOARD_SNAPSHOT_GCS_PREFIX": "",
                "NIGHT_PLAN_ARCHIVE_GCS_PREFIX": "",
            },
        ),
        ([forecast_job.sys.executable, "energy_model_main.py"], {"FORECAST_DATE_OVERRIDE": "2026-09-03", "KP_NIGHT_PLAN_PATH": str(private_path)}),
    ]
    assert persisted[0]["target_date"] == "2026-09-03"
    assert recovered[0]["target_date"] == "2026-09-03"
    assert len(snapshot_calls) == 1


def test_forecast_job_source_has_no_control_or_settings_write_path() -> None:
    source = Path("app/runtime/forecast_job.py").read_text(encoding="utf-8")
    for forbidden in ("slot_orchestration", "run_kpnet_mode_only_profile", "run_settings_roundtrip", "apply_profile", "settings_events", "CLOUD_JOB_SLOT"):
        assert forbidden not in source

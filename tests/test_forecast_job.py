from __future__ import annotations

from pathlib import Path

from app.runtime import forecast_job


def test_forecast_job_runs_only_csv_plan_and_forecast_persistence(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []
    monkeypatch.setattr(forecast_job, "_target_date", lambda: "2026-09-03")
    monkeypatch.setattr(forecast_job, "_run", lambda command, env, **_kwargs: calls.append((command, env)))
    monkeypatch.setattr(forecast_job, "open_firestore", lambda: "client")
    persisted: list[dict] = []
    monkeypatch.setattr(forecast_job, "persist_forecast_only_plan", lambda client, **kwargs: persisted.append({"client": client, **kwargs}) or 24)

    assert forecast_job.main() == 0
    assert calls == [([forecast_job.sys.executable, "kpnet_main.py"], {"KP_WORKFLOW_MODE": "csv"}), ([forecast_job.sys.executable, "energy_model_main.py"], {"FORECAST_DATE_OVERRIDE": "2026-09-03"})]
    assert persisted[0]["target_date"] == "2026-09-03"


def test_forecast_job_source_has_no_control_or_settings_write_path() -> None:
    source = Path("app/runtime/forecast_job.py").read_text(encoding="utf-8")
    for forbidden in ("slot_orchestration", "run_kpnet_mode_only_profile", "run_settings_roundtrip", "apply_profile", "settings_events", "CLOUD_JOB_SLOT"):
        assert forbidden not in source

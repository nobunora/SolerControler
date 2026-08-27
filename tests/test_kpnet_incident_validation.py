from __future__ import annotations

from pathlib import Path

from scripts.kpnet_incident_validation import run_manual_handoff_validation


ROOT = Path(__file__).resolve().parents[1]


def test_manual_handoff_validation_is_in_memory_and_requires_explicit_owner() -> None:
    result = run_manual_handoff_validation()

    assert result["status"] == "passed"
    assert result["storage"] == "in-memory-only"
    assert result["production_records_written"] is False
    assert result["gate_allows_explicit_manual"] is True
    assert result["gate_rejects_implicit_manual"] is True
    assert result["green_path"]["profile"] == "green-mode"
    assert result["green_writer_called"] is True


def test_live_validation_script_is_explicit_and_does_not_create_scheduler() -> None:
    script = (ROOT / "scripts" / "run_kpnet_incident_validation_from_env.ps1").read_text(
        encoding="utf-8"
    )

    assert "Live KP-NET mutation requires -TestExecution." in script
    assert "--test-charge-start" in script
    assert "--test-charge-end" in script
    assert "scheduler" not in script.lower()


def test_deployed_roundtrip_job_has_no_retry_and_no_scheduler() -> None:
    deploy = (ROOT / "scripts" / "deploy_gcp_jobs.ps1").read_text(encoding="utf-8")
    marker = "CLOUD_JOB_SLOT=settings-roundtrip"
    start = deploy.index(marker)
    segment = deploy[max(0, start - 220) : start + len(marker) + 80]

    assert "--max-retries 0" in segment
    assert "Upsert-SchedulerRunJob" not in segment


def test_live_failure_result_retains_restore_outcome() -> None:
    script = (ROOT / "scripts" / "kpnet_incident_validation.py").read_text(encoding="utf-8")

    assert 'summary = getattr(exc, "summary", None)' in script
    assert 'live_failure.update(summary)' in script

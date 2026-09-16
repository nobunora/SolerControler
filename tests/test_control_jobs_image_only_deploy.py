from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_control_image_only_rollout_is_commit_pinned_image_only_and_live_gated() -> None:
    script = (ROOT / "scripts" / "deploy_control_jobs_image_only.ps1").read_text(
        encoding="utf-8"
    )

    assert "[string]$ExpectedCommit" in script
    assert "[switch]$SkipBuild" in script
    assert "git rev-parse HEAD" in script
    assert "HEAD $actualCommit does not match expected commit $ExpectedCommit" in script
    assert ":git-$actualCommit" in script
    assert "value(image_summary.digest)" in script
    assert "Skip build (reusing exact commit image)" in script
    assert "^sha256:[0-9a-f]{64}$" in script

    for job_name in ("solar-battery-23", "solar-battery-03", "solar-battery-07"):
        assert job_name in script

    update_lines = [line.strip() for line in script.splitlines() if "run jobs update" in line]
    assert update_lines == [
        "& $gcloud run jobs update $jobName --region $region --project $projectId --image $immutableImage | Out-Null",
        "& $gcloud run jobs update $jobName --region $region --project $projectId --image $previousImage | Out-Null",
    ]

    assert "07:15 through 22:29 JST" in script
    assert "run_control_postdeploy_live_probe.ps1" in script
    assert "$previousImages[$jobName] = Get-ControlJobImage -JobName $jobName" in script
    assert "23/03/07 images were rolled back to their pre-release values" in script
    assert "Release accepted only after the live CSV/plan/settings round-trip probe passed." in script
    assert "Updated only the image field of the existing 23/03/07 control Jobs." in script


def test_live_probe_is_guarded_non_retrying_and_uses_exact_deployed_image() -> None:
    script = (ROOT / "scripts" / "run_control_postdeploy_live_probe.ps1").read_text(
        encoding="utf-8"
    )

    assert "@sha256:[0-9a-f]{64}$" in script
    assert "07:15 through 22:29 JST" in script
    assert "Assert-NoRunningExecution" in script
    assert "solar-battery-settings-roundtrip" in script
    assert "--image $ImmutableImage" in script
    assert "--command python" in script
    assert "--args postdeploy_probe_main.py" in script
    assert "--max-retries 0" in script
    assert "--task-timeout 900" in script
    assert "run jobs execute $ProbeJobName" in script
    assert "LIVE POST-DEPLOY PROBE PASSED" in script


def test_postdeploy_probe_proves_readonly_prep_before_real_settings_roundtrip() -> None:
    source = (ROOT / "postdeploy_probe_main.py").read_text(encoding="utf-8")

    csv_pos = source.index("run_csv_workflow()")
    plan_pos = source.index("_run_plan_generation()")
    settings_pos = source.index("run_settings_roundtrip(target_soc_percent=target_soc)")
    assert csv_pos < plan_pos < settings_pos
    assert "SETTINGS_ROUNDTRIP_TARGET_SOC" in source
    assert "roundtrip_restore_verified" in source

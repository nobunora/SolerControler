from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_control_image_only_rollout_is_commit_pinned_image_only_and_live_gated() -> None:
    script = (ROOT / "scripts" / "deploy_control_jobs_image_only.ps1").read_text(
        encoding="utf-8"
    )

    assert "[string]$ExpectedCommit" in script
    assert "[switch]$SkipBuild" in script
    assert "[switch]$AllowOutOfWindowLiveProbe" in script
    assert "git rev-parse HEAD" in script
    assert "HEAD $actualCommit does not match expected commit $ExpectedCommit" in script
    assert "git status --porcelain --untracked-files=all" in script
    assert "Refusing control rollout from a dirty working tree." in script
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
    assert "production 23/03/07 Job images were NOT changed" in script
    assert "real 03 forced + real 07 economy + exact restore proof" in script
    assert "Updated only the image field of the existing 23/03/07 control Jobs." in script

    probe_pos = script.index("run_control_postdeploy_live_probe.ps1")
    release_pos = script.index(
        "run jobs update $jobName --region $region --project $projectId --image $immutableImage"
    )
    assert probe_pos < release_pos


def test_control_live_proof_release_wrapper_requires_the_full_successful_path() -> None:
    script = (ROOT / "scripts" / "release_control_jobs_with_live_proof.ps1").read_text(
        encoding="utf-8"
    )

    assert "[string]$ExpectedCommit" in script
    assert "ExpectedCommit must be a full lowercase Git SHA." in script
    assert "StatePath must remain under artifacts/deployment_state." in script
    assert "production_deployment_gate.ps1') -RunPreRelease" in script
    assert "HEAD does not match ExpectedCommit" in script
    assert "deploy_control_jobs_image_only.ps1') @releaseArgs" in script
    assert "run_cloud_job_from_env.ps1') -Slot 07 -DryRun" in script
    assert "if ($AllowOutOfWindowLiveProbe) { $releaseArgs.AllowOutOfWindowLiveProbe = $true }" in script
    assert "kind = 'control_live_proof_release'" in script


def test_live_probe_is_guarded_non_retrying_and_uses_exact_deployed_image() -> None:
    script = (ROOT / "scripts" / "run_control_postdeploy_live_probe.ps1").read_text(
        encoding="utf-8"
    )

    assert "@sha256:[0-9a-f]{64}$" in script
    assert "07:15 through 22:29 JST" in script
    assert "[switch]$AllowOutOfWindowLiveProbe" in script
    assert "[switch]$RunSlot23StandbyRecovery" in script
    assert "LIVE_PROBE_OUT_OF_WINDOW_AUTHORIZED" in script
    assert "Assert-NoRunningExecution" in script
    assert "solar-battery-settings-roundtrip" in script
    assert "--image $ImmutableImage" in script
    assert "--command python" in script
    assert "'postdeploy_probe_main.py'" in script
    assert "--args $probeEntrypoint" in script
    assert "--max-retries 0" in script
    assert "--task-timeout 900" in script
    assert "run jobs execute $ProbeJobName" in script
    assert "LIVE POST-DEPLOY PROBE PASSED" in script


def test_live_probe_can_run_only_the_actual_slot23_standby_owner() -> None:
    script = (ROOT / "scripts" / "run_control_postdeploy_live_probe.ps1").read_text(
        encoding="utf-8"
    )

    assert "$RunSlot23StandbyRecovery" in script
    assert "cloud_job_runner.py" in script
    assert "CLOUD_JOB_SLOT=23,DRY_RUN=false,KP_NET_UNKNOWN_EXIT_ZERO=false" in script
    assert "Slot-23 standby recovery requires the explicit one-shot out-of-window authorization." in script
    assert "LIVE SLOT-23 STANDBY RECOVERY PASSED" in script


def test_postdeploy_probe_proves_readonly_prep_before_real_settings_roundtrip() -> None:
    source = (ROOT / "postdeploy_probe_main.py").read_text(encoding="utf-8")

    csv_pos = source.index("run_csv_workflow()")
    plan_pos = source.index("_run_plan_generation()", csv_pos)
    settings_pos = source.index("run_settings_roundtrip(target_soc_percent=target_soc)")
    assert csv_pos < plan_pos < settings_pos
    assert "SETTINGS_ROUNDTRIP_TARGET_SOC" in source
    assert "roundtrip_forced_proof" in source
    assert "roundtrip_economy_proof" in source
    assert "roundtrip_restore_verified" in source
    assert 'roundtrip.get("forced_proof") != "passed"' in source
    assert 'roundtrip.get("economy_proof") != "passed"' in source
    assert 'roundtrip.get("restore_verified") is not True' in source


def test_runner_image_includes_postdeploy_probe_entrypoint() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "postdeploy_probe_main.py" in dockerfile

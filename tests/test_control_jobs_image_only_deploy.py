from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_control_image_only_rollout_is_commit_pinned_and_image_only() -> None:
    script = (ROOT / "scripts" / "deploy_control_jobs_image_only.ps1").read_text(
        encoding="utf-8"
    )

    assert "[string]$ExpectedCommit" in script
    assert "git rev-parse HEAD" in script
    assert "HEAD $actualCommit does not match expected commit $ExpectedCommit" in script
    assert ":git-$actualCommit" in script
    assert "value(image_summary.digest)" in script
    assert "^sha256:[0-9a-f]{64}$" in script

    for job_name in ("solar-battery-23", "solar-battery-03", "solar-battery-07"):
        assert job_name in script

    update_lines = [
        line.strip()
        for line in script.splitlines()
        if "run jobs update" in line
    ]
    assert update_lines == [
        "& $gcloud run jobs update $jobName --region $region --project $projectId --image $immutableImage | Out-Null"
    ]

    assert "Updated only the image field of the existing 23/03/07 control Jobs." in script

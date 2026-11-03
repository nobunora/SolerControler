"""Exercise the provisioning wrapper offline at its native CLI boundary."""
import json
from pathlib import Path
import shutil
import subprocess


def test_import_provisioning_uses_explicit_project_without_namespace_resolution(tmp_path):
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "scripts/configure_dashboard_actual_import.ps1"
    shutil.copy2(source, scripts / source.name)
    (repo / ".gitignore").write_text("artifacts/\n", encoding="utf-8")
    (scripts / "production_env.ps1").write_text("""
function Import-ProductionEnv {}
function Get-RequiredProductionEnv([string]$Name) {
    switch ($Name) {
        'GCP_PROJECT_ID' { return 'example-project' }
        'GCP_REGION' { return 'asia-northeast1' }
        'GCP_DASHBOARD_SERVICE' { return 'dashboard' }
        default { throw 'Unexpected configuration dependency' }
    }
}
""", encoding="utf-8")
    (scripts / "gcloud.ps1").write_text("""
$global:LASTEXITCODE = 0
$arguments = @($args)
$projectIndex = [Array]::IndexOf($arguments, '--project')
if ($projectIndex -lt 0 -or $arguments[$projectIndex + 1] -ne 'example-project') { throw 'Project binding lost' }
$operation = $arguments[0..2] -join ' '
Add-Content artifacts/commands.log $operation
switch ($operation) {
    'run jobs describe' {
        if ($arguments[3] -eq 'solar-forecast-daily') {
            $planner = Get-Content -Raw fixtures/planner.json | ConvertFrom-Json -AsHashtable
            $planner.spec.template.spec.template.spec.containers[0].env[0].value = (git rev-parse HEAD).Trim()
            $planner | ConvertTo-Json -Depth 30
        } else { Get-Content -Raw artifacts/job.json }
    }
    'run services describe' { Get-Content -Raw fixtures/web.json }
    'run jobs list' { '[]' }
    'run jobs replace' {
        $resource = Get-Content -Raw -LiteralPath $arguments[3] | ConvertFrom-Json -AsHashtable
        # This reproduces the installed CLI's Resource Manager lookup for any
        # explicit namespace, including a number, before the Run write occurs.
        if ($resource.metadata.ContainsKey('namespace')) { throw 'Cloud Resource Manager API is disabled' }
        $resource.metadata.namespace = '123456789012'
        $resource | ConvertTo-Json -Depth 30 | Set-Content artifacts/job.json -Encoding utf8
        '{}'
    }
    'run jobs add-iam-policy-binding' { '{}' }
    'run jobs get-iam-policy' {
        '{"bindings":[{"role":"roles/run.jobsExecutorWithOverrides","members":["serviceAccount:web@example.test"]}]}'
    }
    default { throw 'Unexpected cloud operation; no API enablement or build is allowed' }
}
""", encoding="utf-8")
    digest = "sha256:" + "a" * 64
    task = {"containers": [{"image": "example.invalid/planner@" + digest,
                            "command": ["python"], "args": ["planner_job_main.py"],
                            "env": [{"name": "PLAN_SOURCE_REVISION", "value": "replaced-at-read"},
                                    {"name": "PLAN_IMAGE_DIGEST", "value": digest}]}],
            "maxRetries": 0, "timeoutSeconds": "900", "serviceAccountName": "planner@example.test"}
    planner = {"metadata": {"name": "solar-forecast-daily", "namespace": "123456789012"},
               "spec": {"template": {"spec": {"taskCount": 1, "parallelism": 1, "template": {"spec": task}}}}}
    fixtures = repo / "fixtures"
    fixtures.mkdir()
    (fixtures / "planner.json").write_text(json.dumps(planner), encoding="utf-8")
    (fixtures / "web.json").write_text(json.dumps({"spec": {"template": {"spec": {
        "serviceAccountName": "web@example.test"}}}}), encoding="utf-8")
    for arguments in (["init", "-q"], ["add", "."],
                      ["-c", "user.name=test", "-c", "user.email=test@example.test", "commit", "-qm", "fixture"]):
        subprocess.run(["git", *arguments], cwd=repo, check=True, capture_output=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    powershell = shutil.which("pwsh")
    assert powershell, "PowerShell 7 is required for the deployment contract"
    command = [powershell, "-NoProfile", "-File", str(scripts / source.name),
               "-StateDirectory", "artifacts/deployment_state/import.d", "-RepositoryCommit", commit]
    # A second successful call may verify the saved identity; it must not repeat
    # the successful build/job write or try enabling a project-level API.
    for _ in range(2):
        result = subprocess.run(command, cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert result.returncode == 0, result.stderr
    operations = (repo / "artifacts/commands.log").read_text(encoding="utf-8-sig").splitlines()
    assert operations.count("run jobs replace") == 1
    state = json.loads((repo / "artifacts/deployment_state/import.d/actual-import.json").read_text(encoding="utf-8-sig"))
    assert state["status"] == "success" and state["namespace"] == "123456789012"
    saved = json.loads((repo / "artifacts/job.json").read_text(encoding="utf-8-sig"))
    assert saved["metadata"]["name"] == "solar-actual-import"
    assert saved["spec"]["template"]["spec"]["template"]["spec"]["containers"][0]["image"] == "example.invalid/planner@" + digest
    assert (repo / "artifacts/deployment_state/import.d/last-cloud-response.private.txt").exists()

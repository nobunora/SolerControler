from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from app.separated_runtime import planner

ROOT = Path(__file__).resolve().parents[1]


def test_unknown_exact_execution_cannot_select_current_or_relaunch(tmp_path, monkeypatch):
    execution = tmp_path / "execution.json"
    intent = tmp_path / "intent.json"
    execution.write_text(json.dumps({"execution_name": "one-execution", "logs": [
        {"labels": {"run.googleapis.com/execution_name": "another-execution"},
         "jsonPayload": {"event": "plan_publication_succeeded", "target_date": "2026-10-12",
                         "control_version_id": "other", "raw_plan_sha256": "other"}},
    ]}))
    intent.write_text(json.dumps({"target_date": "2026-10-12"}))
    monkeypatch.setattr(planner, "open_firestore", lambda: pytest.fail("unknown execution must not select current"))
    with pytest.raises(ValueError, match="unknown or ambiguous"):
        planner.capture_adopted_forecast_receipt(execution, intent, tmp_path / "adopted.json")
    assert not (tmp_path / "adopted.json").exists()


def test_production_fixture_rejects_global_parameter_and_hash_drift():
    spec = importlib.util.spec_from_file_location("adapter_proof_guard", ROOT / "tests/g07/adapter_proof.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    operation = "owned-proof-operation"
    value = {"schema_version": 1, "source_commit": "1" * 40, "operation_id": operation,
             "kind": "production_server", "project_id": "existing-project", "database_id": "(default)",
             "bucket": "existing-bucket", "prefix": f"adapter-proof/{operation}/", "target_date": "2099-01-01",
             "approval_receipt_sha256": "2" * 64,
             "owned_document_paths": [f"forecast_hourly_snapshots/{operation}-a", f"forecast_hourly_snapshots/{operation}-b"],
             "owned_object_names": [f"adapter-proof/{operation}/a", f"adapter-proof/{operation}/b"]}
    value["fixture_sha256"] = module.canonical_sha(value)
    module.validate_fixture(value)
    value["owned_document_paths"][0] = "model_parameters/the-real-model"
    value["fixture_sha256"] = module.canonical_sha(value)
    with pytest.raises(ValueError, match="synthetic scope"):
        module.validate_fixture(value)
    value["bucket"] = "changed"
    with pytest.raises(ValueError, match="checksum"):
        module.validate_fixture(value)


@pytest.mark.parametrize("proof_status", ["PASS", "FAIL"])
def test_server_proof_restores_dedicated_job_without_environment_or_control_writes(tmp_path, proof_status):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell integration requires the project interpreter")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(ROOT / "scripts/run_server_adapter_proof_from_env.ps1", scripts)
    (scripts / "production_env.ps1").write_text("function Import-ProductionEnv {}\nfunction Get-RequiredProductionEnv($Name) { return 'test-value' }\n")
    for name in ("adapter_proof.py", "firestore_server_cases.py", "gcs_server_cases.py"):
        (tmp_path / "tests/g07").mkdir(parents=True, exist_ok=True)
        (tmp_path / "tests/g07" / name).write_text("# test-only bundle fixture\n")
    fixture = {"kind": "production_server", "bucket": "bucket", "prefix": "adapter-proof/owned-proof/", "fixture_sha256": "b" * 64}
    (tmp_path / "fixture.json").write_text(json.dumps(fixture))
    container = {"image": "original-image@sha256:" + "c" * 64, "command": ["python"], "args": ["control_job_main.py"],
                 "env": [{"name": "UNCHANGED", "value": "settings-profile"}], "resources": {"limits": {"memory": "512Mi"}}}
    job = {"spec": {"template": {"spec": {"template": {"spec": {"containers": [container]}}}}}}
    (tmp_path / "job.json").write_text(json.dumps(job))
    (scripts / "gcloud.ps1").write_text(r'''
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)
$root=Split-Path $PSScriptRoot -Parent
Add-Content -LiteralPath (Join-Path $root 'calls.txt') -Value ($Args[0..2] -join ' ')
if (($Args[0..2] -join ' ') -eq 'run jobs executions') {
 if($Args[3] -eq 'list'){'[]';exit 0}
 if($Args[3] -eq 'describe'){
  @{metadata=@{name='exact-proof'};spec=@{taskCount=1};status=@{completionTime='2026-10-13T03:20:00+09:00';succeededCount=1;conditions=@('Completed','ResourcesAvailable','Started','ContainerReady')|ForEach-Object {@{type=$_;status='True'}}}}|ConvertTo-Json -Depth 10;exit 0
 }
 throw 'unexpected execution read'
}
if (($Args[0..2] -join ' ') -eq 'run jobs describe') { Get-Content -Raw (Join-Path $root 'job.json'); exit 0 }
if (($Args[0..2] -join ' ') -eq 'run jobs update') {
 $flags=Get-Content -Raw $Args[([array]::IndexOf($Args,'--flags-file')+1)]|ConvertFrom-Json -AsHashtable
 if($flags.Count -ne 3 -or -not $flags.ContainsKey('--image') -or -not $flags.ContainsKey('--command') -or -not $flags.ContainsKey('--args')){throw 'Unrecognized flags-file arguments: option keys require -- prefix'}
 $job=Get-Content -Raw (Join-Path $root 'job.json')|ConvertFrom-Json -AsHashtable
 $c=$job.spec.template.spec.template.spec.containers[0]
 foreach($key in @('image','command','args')){if($key -eq 'image'){$c[$key]=$flags['--'+$key]}else{$c[$key]=@($flags['--'+$key])}}
 $job|ConvertTo-Json -Depth 40|Set-Content (Join-Path $root 'job.json');'updated';exit 0
}
if (($Args[0..2] -join ' ') -eq 'run jobs execute') {
 @{metadata=@{name='exact-proof'};status=@{failedCount=0;conditions=@('Completed','ResourcesAvailable','Started','ContainerReady')|ForEach-Object {@{type=$_;status='True'}}}}|ConvertTo-Json -Depth 10;exit 0
}
if (($Args[0..1] -join ' ') -eq 'logging read') {
 @(@{labels=@{'run.googleapis.com/execution_name'='exact-proof'};jsonPayload=@{event='server_adapter_proof';receipt=@{status='PROOF_STATUS';image_digest=('sha256:'+('a'*64));fixture_sha256=('b'*64)}}})|ConvertTo-Json -Depth 10;exit 0
}
throw 'unexpected cloud call'
'''.replace("PROOF_STATUS", proof_status), encoding="utf-8")
    driver = tmp_path / "driver.ps1"
    driver.write_text(r'''
function global:python {
 param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)
 $global:LASTEXITCODE=0
 if($Args[0] -like '*adapter_proof.py'){return}
 if($Args[1] -like '*zipfile*'){Set-Content -LiteralPath $Args[-1] -Value 'fixed-bundle';return}
 if($Args[1] -like '*upload_from_string*'){'{"generation":7,"created":true}';return}
 if($Args[1] -like '*.delete*'){return}
 throw 'unexpected Python call'
}
& (Join-Path $PSScriptRoot 'scripts/run_server_adapter_proof_from_env.ps1') -ImmutablePlannerImage ('repo@sha256:'+('a'*64)) -FixturePath (Join-Path $PSScriptRoot 'fixture.json') -ReceiptPath (Join-Path $PSScriptRoot 'receipt.json')
''', encoding="utf-8")
    result = subprocess.run([pwsh, "-NoProfile", "-File", str(driver)], capture_output=True, text=True, timeout=30)
    assert (result.returncode == 0) is (proof_status == "PASS"), result.stderr
    assert json.loads((tmp_path / "job.json").read_text()) == job
    calls = (tmp_path / "calls.txt").read_text().splitlines()
    assert calls.count("run jobs update") == 2
    assert calls.count("run jobs execute") == 1
    assert all(not line.startswith(("run services", "run jobs create")) for line in calls)

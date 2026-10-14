from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from app.separated_runtime import planner

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='The canonical gcloud boundary uses Windows gcloud.cmd')
@pytest.mark.parametrize('escaped', [False, True])
def test_actual_cmd_boundary_preserves_exact_filter_quotes_once(tmp_path, escaped):
    pwsh = shutil.which('pwsh')
    if not pwsh:
        pytest.skip('PowerShell is required for the canonical native-command boundary')
    recorder = tmp_path / 'record_argv.py'
    recorder.write_text('import json,sys\nprint(json.dumps(sys.argv[1:]))\n', encoding='utf-8')
    command = tmp_path / 'record.cmd'
    command.write_text(f'@echo off\r\n"{sys.executable}" "{recorder}" %*\r\n', encoding='utf-8')
    driver = tmp_path / 'filter_boundary.ps1'
    driver.write_text('''param([string]$Source,[string]$Command,[string]$Style)
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)
$assignments=@($ast.FindAll({param($node)
 $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and
 $node.Left -is [System.Management.Automation.Language.VariableExpressionAst] -and
 $node.Left.VariablePath.UserPath -eq 'filter'
},$true))
if($errors.Count -or $assignments.Count -ne 1){throw 'Expected one parsed filter assignment'}
$ProbeJobName='owned-test-job';$executionName='owned-test-execution'
. ([scriptblock]::Create($assignments[0].Extent.Text))
if($Style -eq 'plain'){$filter=$filter.Replace(([string][char]92+[char]34),[string][char]34)}
& $Command logging read $filter --limit 200
exit $LASTEXITCODE
''', encoding='utf-8')
    result = subprocess.run([pwsh, '-NoProfile', '-File', str(driver),
                             str(ROOT / 'scripts/run_server_adapter_proof_from_env.ps1'),
                             str(command), 'escaped' if escaped else 'plain'],
                            capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr
    arguments = json.loads(result.stdout)
    expected = ('resource.type=cloud_run_job AND resource.labels.job_name="owned-test-job" '
                'AND labels."run.googleapis.com/execution_name"="owned-test-execution" '
                'AND jsonPayload.event="server_adapter_proof"')
    assert arguments[:2] == ['logging', 'read']
    assert arguments[-2:] == ['--limit', '200']
    assert (arguments[2] == expected) is escaped
    if escaped:
        assert '\\"' not in arguments[2]


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
@pytest.mark.parametrize("log_shape", ["normal", "mixed", "empty", "missing_exact", "duplicate_exact"])
def test_server_proof_restores_dedicated_job_without_environment_or_control_writes(tmp_path, proof_status, log_shape):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell integration requires the project interpreter")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(ROOT / "scripts/run_server_adapter_proof_from_env.ps1", scripts)
    if log_shape in {'empty', 'missing_exact'}:
        # These fixtures never produce a receipt. Accelerate only their copied
        # timeout; production retains its full log-delivery budget.
        copied = scripts / "run_server_adapter_proof_from_env.ps1"
        source = copied.read_text(encoding='utf-8')
        assert '[DateTime]::UtcNow.AddSeconds(60)' in source
        copied.write_text(source.replace('[DateTime]::UtcNow.AddSeconds(60)',
                                         '[DateTime]::UtcNow.AddSeconds(2)'), encoding='utf-8')
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
 $decodedFilter=$Args[2].Replace('\"','"')
 if($decodedFilter -notmatch 'labels\."run.googleapis.com/execution_name"="exact-proof"' -or $decodedFilter -notmatch 'jsonPayload.event="server_adapter_proof"' -or $Args[([array]::IndexOf($Args,'--limit')+1)] -ne '200'){throw 'Proof log query must be bounded to the exact execution and event'}
 $proof=@{labels=@{'run.googleapis.com/execution_name'='exact-proof'};jsonPayload=@{event='server_adapter_proof';receipt=@{status='PROOF_STATUS';image_digest=('sha256:'+('a'*64));fixture_sha256=('b'*64)}}}
 $noise=@(@{textPayload='system'},@{labels=$null;jsonPayload=@{event='server_adapter_proof'}},@{labels=@{'run.googleapis.com/execution_name'='exact-proof'}},@{labels=@{'run.googleapis.com/execution_name'='exact-proof'};jsonPayload=@{message='different event'}},@{labels=@{'run.googleapis.com/execution_name'='other-proof'};jsonPayload=$proof.jsonPayload})
 switch('LOG_SHAPE'){
  'normal' { $rows=@($proof) }
  'mixed' { $rows=@($noise)+@($proof) }
  'empty' { $rows=@() }
  'missing_exact' { $rows=@($noise) }
  'duplicate_exact' { $rows=@($proof,$proof) }
 }
 ConvertTo-Json -InputObject $rows -Depth 10;exit 0
}
throw 'unexpected cloud call'
'''.replace("PROOF_STATUS", proof_status).replace("LOG_SHAPE", log_shape), encoding="utf-8")
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
    result = subprocess.run([pwsh, "-NoProfile", "-File", str(driver)], capture_output=True, text=True, encoding='utf-8', timeout=30)
    accepted = proof_status == 'PASS' and log_shape in {'normal', 'mixed'}
    assert (result.returncode == 0) is accepted, result.stderr
    assert (tmp_path / 'receipt.json').exists() is (log_shape in {'normal', 'mixed'})
    saved_records = json.loads((tmp_path / 'receipt.json.d/proof-records.private.json').read_text(encoding='utf-8-sig'))
    assert isinstance(saved_records, list)
    assert len(saved_records) == {'normal': 1, 'mixed': 6, 'empty': 0, 'missing_exact': 5, 'duplicate_exact': 2}[log_shape]
    assert json.loads((tmp_path / "job.json").read_text()) == job
    calls = (tmp_path / "calls.txt").read_text().splitlines()
    assert calls.count("run jobs update") == 2
    assert calls.count("run jobs execute") == 1
    assert all(not line.startswith(("run services", "run jobs create")) for line in calls)

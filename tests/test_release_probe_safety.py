from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def invoke_guard(tmp_path, script, function, parameters):
    pwsh = shutil.which('pwsh')
    if not pwsh:
        pytest.skip('PowerShell is required for the production wrapper guards')
    fixture = tmp_path / 'parameters.json'
    fixture.write_text(json.dumps(parameters), encoding='utf-8')
    driver = tmp_path / 'guard.ps1'
    driver.write_text('''param([string]$Source,[string]$FunctionName,[string]$Fixture)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Source parse failed'}
$definition=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $FunctionName},$true)
if(-not $definition){throw 'Guard definition missing'}
. ([scriptblock]::Create($definition.Extent.Text))
$parameters=Get-Content -Raw -LiteralPath $Fixture | ConvertFrom-Json -AsHashtable
if($parameters.ContainsKey('Scenarios')){
 $outcomes=@(foreach($scenario in $parameters.Scenarios){
  try { & $FunctionName -Execution $scenario; $true } catch { $false }
 })
 ConvertTo-Json -InputObject $outcomes -Compress
 exit 0
}
if($parameters.ContainsKey('Rows')){
 $script:rowsJson=ConvertTo-Json -InputObject @($parameters.Rows) -Depth 10 -Compress
 $parameters.Remove('Rows')
 $project='test-only';$region='test-only'
 function Invoke-Cloud([string[]]$Arguments){return $script:rowsJson}
 function Invoke-SocCloud([string[]]$Arguments){return $script:rowsJson}
}
& $FunctionName @parameters
''', encoding='utf-8')
    return subprocess.run([pwsh, '-NoProfile', '-File', str(driver), str(ROOT / script), function, str(fixture)],
                          capture_output=True, text=True, encoding='utf-8', timeout=20)


@pytest.mark.parametrize('script,options', [
    ('scripts/run_server_adapter_proof_from_env.ps1', {'--image', '--command', '--args'}),
    ('scripts/run_kpnet_soc_setting_probe_from_env.ps1', {'--command', '--args'}),
])
def test_actual_update_and_restore_flags_emit_cli_option_names(tmp_path, script, options):
    pwsh = shutil.which('pwsh')
    if not pwsh:
        pytest.skip('PowerShell is required to evaluate the actual flags-file emitters')
    driver = tmp_path / 'emit-flags.ps1'
    driver.write_text('''param([string]$Source,[string]$OutputDirectory)
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Source parse failed'}
$pipelines=@($ast.FindAll({param($node)
 $node -is [System.Management.Automation.Language.PipelineAst] -and
 ($node.Extent.Text.Contains('Set-Content -LiteralPath $flagsPath') -or $node.Extent.Text.Contains('Set-Content -LiteralPath $restorePath'))
},$true))
if($pipelines.Count -ne 2){throw 'Expected one update and one restore flags emitter'}
$ImmutablePlannerImage='test-image';$loader='test-loader';$bundleUri='test-bundle'
$bundleSha='test-sha';$bundle=@{generation=1};$imageDigest='test-digest'
$argsForProbe=@('-c','test-entry','30')
$previous=@{image='test-original';command=@('python');args=@('original.py')}
$flagsPath=Join-Path $OutputDirectory 'update.json'
$restorePath=Join-Path $OutputDirectory 'restore.json'
foreach($pipeline in $pipelines){. ([scriptblock]::Create($pipeline.Extent.Text))}
''', encoding='utf-8')
    result = subprocess.run([pwsh, '-NoProfile', '-File', str(driver), str(ROOT / script), str(tmp_path)],
                            capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr
    for name in ('update.json', 'restore.json'):
        flags = json.loads((tmp_path / name).read_text(encoding='utf-8-sig'))
        assert set(flags) == options
        assert flags['--command'] == 'python'
        assert isinstance(flags['--args'], list) and flags['--args']


@pytest.mark.parametrize('clock,override,accepted', [
    ('03:05', False, False), ('03:05', True, False), ('03:15', True, True),
    ('03:20', False, False), ('06:44', True, True), ('06:45', True, False),
    ('07:14', True, False), ('07:15', False, True), ('22:29', False, True),
    ('22:30', True, False), ('23:14', True, False), ('23:15', True, True),
])
def test_soc_one_shot_never_bypasses_owner_transition(tmp_path, clock, override, accepted):
    result = invoke_guard(tmp_path, 'scripts/run_kpnet_soc_setting_probe_from_env.ps1', 'Assert-SocProbeWindow',
                          {'NowJst': f'2026-10-13T{clock}:00', 'AllowOutside': override})
    assert (result.returncode == 0) is accepted, result.stderr


@pytest.mark.parametrize('failure', [None, 'active', 'missing_succeeded', 'failed', 'unknown_completed'])
def test_soc_requires_strict_terminal_03_evidence(tmp_path, failure):
    status = {'completionTime': '2026-10-13T03:15:00+09:00', 'succeededCount': 1,
              'conditions': [{'type': key, 'status': 'True'} for key in
                             ('Completed', 'ResourcesAvailable', 'Started', 'ContainerReady')]}
    if failure == 'active':
        status.pop('completionTime')
    elif failure == 'missing_succeeded':
        status.pop('succeededCount')
    elif failure == 'failed':
        status['failedCount'] = 1
    elif failure == 'unknown_completed':
        status['conditions'][0]['status'] = 'Unknown'
    result = invoke_guard(tmp_path, 'scripts/run_kpnet_soc_setting_probe_from_env.ps1', 'Assert-SocNightTerminal',
                          {'Execution': {'spec': {'taskCount': 1}, 'status': status}})
    assert (result.returncode == 0) is (failure is None), result.stderr


@pytest.mark.parametrize('script,function', [
    ('scripts/run_kpnet_soc_setting_probe_from_env.ps1', 'Assert-SocNightTerminal'),
    ('scripts/run_server_adapter_proof_from_env.ps1', 'Assert-ProofTerminal'),
    ('scripts/run_cloud_job_from_env.ps1', 'Assert-ForecastReceiptTerminal'),
])
def test_native_omitted_zero_failed_requires_integer_all_task_success(tmp_path, script, function):
    def execution():
        return {'spec': {'taskCount': 1}, 'status': {
            'completionTime': '2026-10-13T03:20:00+09:00', 'succeededCount': 1,
            'conditions': [{'type': key, 'status': 'True'} for key in
                           ('Completed', 'ResourcesAvailable', 'Started', 'ContainerReady')]}}

    scenarios, expected = [execution()], [True]
    retry = execution()
    retry['status']['retriedCount'] = 1
    scenarios.append(retry)
    expected.append(True)
    for key in ('failedCount', 'runningCount', 'pendingCount', 'cancelledCount'):
        item = execution()
        item['status'][key] = 0
        scenarios.append(item)
        expected.append(True)
        for value in (1, True, '0', 0.0, None, -1):
            item = execution()
            item['status'][key] = value
            scenarios.append(item)
            expected.append(False)
    for key in ('taskCount', 'succeededCount'):
        for value in (True, '1', 1.0, None, 0, -1, 2):
            item = execution()
            item['spec' if key == 'taskCount' else 'status'][key] = value
            scenarios.append(item)
            expected.append(False)
    missing = execution()
    missing['spec'].pop('taskCount')
    scenarios.append(missing)
    expected.append(False)
    partial = execution()
    partial['spec']['taskCount'] = 2
    scenarios.append(partial)
    expected.append(False)
    result = invoke_guard(tmp_path, script, function, {'Scenarios': scenarios})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected


@pytest.mark.parametrize('script,function', [
    ('scripts/deploy_separated_runtime.ps1', 'Assert-Idle'),
    ('scripts/run_kpnet_soc_setting_probe_from_env.ps1', 'Assert-SocIdle'),
])
@pytest.mark.parametrize('status,accepted', [({}, False), ({'conditions': []}, False),
                                         ({'completionTime': '2026-10-13T03:20:00+09:00'}, True)])
def test_active_or_unknown_control_owner_blocks_both_wrappers(tmp_path, script, function, status, accepted):
    result = invoke_guard(tmp_path, script, function,
                          {'Job': 'solar-battery-03', 'Rows': [{'status': status}]})
    assert (result.returncode == 0) is accepted, result.stderr


@pytest.mark.parametrize('failure', [None, 'no_new', 'capacity', 'soc', 'original', 'identity', 'required', 'parity'])
def test_post_adoption_requires_same_new_raw_capacity24_soc17(tmp_path, failure):
    adopted = {'target_date': '2026-10-13', 'raw_plan_sha256': 'a' * 64,
               'control_version_id': 'fixed-version', 'forecast_run_id': 'fixed-run'}
    day = {'evidence_kind': 'NEW_ADOPTED', 'status': 'PASS', 'evidence_required': True,
           'original_verified': True, 'metadata_verified': True, 'capacity_verified': True,
           'required_soc_points': 17, 'soc_points_verified': 17, 'original_sha256': 'a' * 64,
           'forecast_identity': dict(adopted)}
    evidence = {'status': 'INCONCLUSIVE', 'required_evidence_status': 'PASS', 'common_parity_status': 'PASS',
                'coverage': {'new_required_days': 1, 'new_verified_days': 1},
                'days': {adopted['target_date']: day, '2026-10-12': {'evidence_kind': 'LEGACY_UNAVAILABLE', 'status': 'INCONCLUSIVE'}}}
    if failure == 'no_new':
        evidence['coverage'] = {'new_required_days': 0, 'new_verified_days': 0}
    elif failure == 'capacity':
        day['capacity_verified'] = False
    elif failure == 'soc':
        day['soc_points_verified'] = 16
    elif failure == 'original':
        day['original_sha256'] = 'b' * 64
    elif failure == 'identity':
        day['forecast_identity']['forecast_run_id'] = 'other-run'
    elif failure == 'required':
        evidence['required_evidence_status'] = 'INCONCLUSIVE'
    elif failure == 'parity':
        evidence['common_parity_status'] = 'FAIL'
    result = invoke_guard(tmp_path, 'scripts/deploy_separated_runtime.ps1', 'Assert-AdoptedForecastEvidence',
                          {'Evidence': evidence, 'Adopted': adopted})
    assert (result.returncode == 0) is (failure is None), result.stderr

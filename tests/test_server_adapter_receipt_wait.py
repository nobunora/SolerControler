import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which('pwsh')
pytestmark = pytest.mark.skipif(PWSH is None, reason='PowerShell 7 is unavailable')


@pytest.mark.parametrize(('scenario', 'success', 'reads'), [
    ('delayed', True, 2), ('foreign', True, 2), ('null_labels', True, 2),
    ('ambiguous', False, 1), ('expired', False, 0),
])
def test_exact_receipt_wait_retries_only_reads_and_fails_closed(tmp_path, scenario, success, reads):
    runner = tmp_path / 'receipt.ps1'
    runner.write_text(r'''
$ErrorActionPreference = 'Stop'
$tokens=$null; $errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($env:TEST_SCRIPT,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'SDK wrapper syntax failed' }
$definition=$ast.FindAll({ param($n)
    $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Read-ExactProofReceipt'
},$true)[0]
Invoke-Expression $definition.Extent.Text
$project='test-project'; $privateDirectory=$PSScriptRoot; $script:reads=0
function Start-Sleep { param($Milliseconds) }
function Invoke-ProofCloud([string[]]$Arguments) {
    if ($Arguments[0] -ne 'logging' -or $Arguments[1] -ne 'read') { throw 'A mutating command was attempted' }
    $script:reads++
    $row=@{labels=@{'run.googleapis.com/execution_name'='owned-execution'};jsonPayload=@{event='server_adapter_proof';receipt=@{status='PASS'}}}
    if ($env:TEST_SCENARIO -eq 'ambiguous') { return ConvertTo-Json -InputObject @($row,$row) -Depth 8 }
    if ($script:reads -eq 1) {
        if ($env:TEST_SCENARIO -eq 'delayed') { return '[]' }
        if ($env:TEST_SCENARIO -eq 'foreign') { $row.labels['run.googleapis.com/execution_name']='other-execution' }
        if ($env:TEST_SCENARIO -eq 'null_labels') { $row.labels=$null }
    }
    return ConvertTo-Json -InputObject @($row) -Depth 8
}
$deadline=if ($env:TEST_SCENARIO -eq 'expired') { [DateTime]::UtcNow.AddSeconds(-1) } else { [DateTime]::UtcNow.AddSeconds(10) }
$success=$false
try { $receipt=Read-ExactProofReceipt 'owned-execution' 'fixed-execution-filter' $deadline; $success=$receipt.status -eq 'PASS' }
catch { $success=$false }
@{success=$success;reads=$script:reads} | ConvertTo-Json -Compress
''', encoding='utf-8')
    result = subprocess.run([PWSH or 'pwsh', '-NoProfile', '-File', str(runner)],
        capture_output=True, text=True, encoding='utf-8', check=False, timeout=15,
        env={**os.environ, 'TEST_SCRIPT': str(ROOT / 'scripts/run_server_adapter_proof_from_env.ps1'),
             'TEST_SCENARIO': scenario})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'success': success, 'reads': reads}

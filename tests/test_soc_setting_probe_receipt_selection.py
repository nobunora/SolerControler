from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("exact_count", [1, 0, 2])
def test_soc_probe_actual_powershell_selects_only_one_exact_receipt(tmp_path, exact_count):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell is required for the canonical SOC wrapper")
    labels = {"run.googleapis.com/execution_name": "owned-execution"}
    payload = {"message": "post-deploy-soc-setting-probe", "status": "passed", "restore_verified": True}
    rows = [None, 17, "system", {"textPayload": "system"}, {"labels": None},
            {"labels": "not-a-map"}, {"labels": labels}, {"labels": labels, "jsonPayload": None},
            {"labels": labels, "jsonPayload": "not-a-map"}, {"labels": labels, "jsonPayload": {}},
            {"labels": labels, "jsonPayload": {"message": "another-event"}},
            {"labels": {"run.googleapis.com/execution_name": "another-execution"}, "jsonPayload": payload}]
    rows.extend({"labels": labels, "jsonPayload": payload} for _ in range(exact_count))
    fixture = tmp_path / "records.json"
    fixture.write_text(json.dumps(rows), encoding="utf-8")
    driver = tmp_path / "select-receipt.ps1"
    driver.write_text(r'''param([string]$Source,[string]$Fixture)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Source parse failed'}
$ProbeJobName='owned-job';$executionName='owned-execution';$project='test-only'
$script:reads=0
function Invoke-SocCloud([string[]]$Arguments){
 $script:reads++
 if(($Arguments[0..1] -join ' ') -ne 'logging read'){throw 'Only one stubbed log read is allowed'}
 $decoded=$Arguments[2].Replace('\"','"')
 $expected='resource.type=cloud_run_job AND resource.labels.job_name="owned-job" AND labels."run.googleapis.com/execution_name"="owned-execution" AND jsonPayload.message="post-deploy-soc-setting-probe"'
 if($decoded -ne $expected -or $Arguments[([array]::IndexOf($Arguments,'--limit')+1)] -ne '200'){throw 'Exact bounded filter changed'}
 Get-Content -Raw -LiteralPath $Fixture
}
foreach($name in @('filter','records','proofs')){
 $nodes=@($ast.FindAll({param($node)
  $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and
  $node.Left -is [System.Management.Automation.Language.VariableExpressionAst] -and
  $node.Left.VariablePath.UserPath -eq $name
 },$true))
 if($nodes.Count -ne 1){throw 'Expected one actual selection assignment'}
 . ([scriptblock]::Create($nodes[0].Extent.Text))
}
$guards=@($ast.FindAll({param($node)
 $node -is [System.Management.Automation.Language.IfStatementAst] -and
 $node.Extent.Text.StartsWith('if ($proofs.Count -ne 1)')
},$true))
if($guards.Count -ne 1){throw 'Expected existing exact receipt count guard'}
. ([scriptblock]::Create($guards[0].Extent.Text))
@{reads=$script:reads;proof=$proofs[0].jsonPayload}|ConvertTo-Json -Depth 10 -Compress
''', encoding="utf-8")
    result = subprocess.run([pwsh, "-NoProfile", "-File", str(driver),
                             str(ROOT / "scripts/run_kpnet_soc_setting_probe_from_env.ps1"), str(fixture)],
                            capture_output=True, text=True, encoding="utf-8", timeout=20)
    if exact_count == 1:
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"reads": 1, "proof": payload}
    else:
        assert result.returncode != 0
        assert "Exact execution SOC proof is absent or ambiguous" in result.stderr

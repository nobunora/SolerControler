"""Execute the production audit reader against delayed and unrelated receipts."""
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("duplicate", [False, True])
def test_soc_cycle_audit_waits_for_exact_execution_without_reexecuting(tmp_path, duplicate):
    source = Path("scripts/run_cloud_job_from_env.ps1").read_text(encoding="utf-8-sig")
    reader = source[source.index("    $executionName ="):source.index("    $proof = $exact[0].jsonPayload")]
    fake = tmp_path / "cloud.ps1"
    fake.write_text('''
if ($args[0] -ne 'logging' -or $args[1] -ne 'read') { throw 'Only read-only audit lookup is allowed' }
$global:auditReads += 1
if ($global:auditReads -eq 1) {
    '[{"labels":null},{"labels":{"run.googleapis.com/execution_name":"old"},"jsonPayload":{"message":"soc-cycle-probe"}}]'
} else {
    $receipt = @{labels=@{'run.googleapis.com/execution_name'='exact'};jsonPayload=@{message='soc-cycle-probe'}}
    if ($global:duplicate) { ConvertTo-Json -InputObject @($receipt,$receipt) -Depth 6 -Compress }
    else { ConvertTo-Json -InputObject @($receipt) -Depth 6 -Compress }
}
$global:LASTEXITCODE = 0
''', encoding="utf-8")
    script = tmp_path / "reader.ps1"
    script.write_text('''
$ErrorActionPreference='Stop'
function Start-Sleep { param($Milliseconds) }
$global:auditReads=0
$execution=@{metadata=@{name='projects/private/locations/private/executions/exact'}}
$jobName='fake-job'; $projectId='fake-project'
$gcloud=Join-Path $PSScriptRoot 'cloud.ps1'
$SocCycleEvidenceDirectory=$PSScriptRoot
''' + f"$global:duplicate=${str(duplicate).lower()}\n" + reader + '''
if ($auditReads -ne 2 -or $exact.Count -ne 1) { throw 'Wrong audit or lookup count' }
''', encoding="utf-8")
    result = subprocess.run(["pwsh", "-NoProfile", "-File", str(script)], capture_output=True, text=True, check=False)
    if duplicate:
        assert result.returncode != 0
        assert "ambiguous exact-execution audits" in result.stderr
    else:
        assert result.returncode == 0, result.stderr

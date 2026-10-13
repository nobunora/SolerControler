"""Exercise the deployed receipt filter with mixed Cloud Logging records."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("include_match", [True, False])
def test_forecast_receipt_filter_handles_system_records_and_exact_execution(
    include_match: bool,
) -> None:
    records: list[object] = [
        {"textPayload": "system entry without labels"},
        {"labels": None},
        {"labels": []},
        {"labels": {}},
        {"labels": {"run.googleapis.com/execution_name": "other-execution"}},
    ]
    expected = {
        "labels": {"run.googleapis.com/execution_name": "test-execution"},
        "jsonPayload": {"event": "plan_publication_succeeded"},
    }
    if include_match:
        records.insert(1, expected)
    command = r"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$source = Get-Content -Raw -LiteralPath $env:FORECAST_RUNNER_SOURCE
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Runner syntax invalid' }
$assignments = @($ast.FindAll({ param($node)
    $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and
    $node.Left.Extent.Text -eq '$forecastReceipt.logs'
}, $true))
if ($assignments.Count -ne 1) { throw 'Receipt assignment missing or ambiguous' }
$forecastReceipt = @{}
$exactName = 'test-execution'
$logsText = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:FORECAST_LOG_RECORDS))
Invoke-Expression $assignments[0].Extent.Text
ConvertTo-Json -InputObject $forecastReceipt.logs -Depth 20 -Compress
"""
    pwsh = shutil.which("pwsh")
    assert pwsh is not None
    result = subprocess.run(
        [pwsh, "-NoProfile", "-Command", command],
        cwd=ROOT, check=False, capture_output=True, text=True,
        env={
            **os.environ,
            "FORECAST_RUNNER_SOURCE": str(ROOT / "scripts/run_cloud_job_from_env.ps1"),
            "FORECAST_LOG_RECORDS": base64.b64encode(json.dumps(records).encode()).decode(),
        },
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ([expected] if include_match else [])

param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

if (-not $SkipInstall) {
    python -m pip install -r .\requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
}

python -m compileall app main.py kpnet_main.py energy_model_main.py cloud_job_runner.py db_pipeline_main.py dashboard_server.py
if ($LASTEXITCODE -ne 0) { throw "compileall failed" }

python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "pytest failed" }

node .\tests\test_dashboard_calculations.js
if ($LASTEXITCODE -ne 0) { throw "dashboard JavaScript tests failed" }

python -m mypy app/time_windows.py app/tariff.py app/monitoring_csv.py app/night_plan.py app/settings
if ($LASTEXITCODE -ne 0) { throw "domain mypy failed" }

python .\scripts\security_check.py
if ($LASTEXITCODE -ne 0) { throw "security_check failed" }

Write-Host "Local pre-release checks passed."


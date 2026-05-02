param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

if (-not $SkipInstall) {
    python -m pip install -r .\requirements-dev.txt
    if ($LASTEXITCODE -ne 0) {
        throw "pip install failed"
    }
}

python -m compileall app main.py kpnet_main.py energy_model_main.py cloud_job_runner.py db_pipeline_main.py dashboard_server.py dashboard_mock_png.py
if ($LASTEXITCODE -ne 0) {
    throw "compileall failed"
}

python -m pytest -q
if ($LASTEXITCODE -ne 0) {
    throw "pytest failed"
}

python .\scripts\security_check.py
if ($LASTEXITCODE -ne 0) {
    throw "security_check failed"
}

Write-Host "Pre-release checks passed."

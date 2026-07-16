param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

Write-Host "Synchronize Firestore and local SQLite before validation..."
python .\scripts\sync_validation_state.py --direction firestore-to-sqlite
if ($LASTEXITCODE -ne 0) { throw "sync_validation_state failed" }

python .\scripts\validate_dashboard_backend_parity.py
if ($LASTEXITCODE -ne 0) { throw "dashboard backend parity failed" }

& .\scripts\pre_release_local.ps1 -SkipInstall:$SkipInstall
if ($LASTEXITCODE -ne 0) { throw "local pre-release checks failed" }

Write-Host "Integration pre-release checks passed."

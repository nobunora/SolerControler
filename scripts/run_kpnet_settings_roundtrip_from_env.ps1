param(
    [double]$TargetSoc,
    [switch]$TestExecution
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv

if ($TargetSoc -lt 0 -or $TargetSoc -gt 100) { throw 'TargetSoc must be within 0..100.' }
if (-not $TestExecution) { throw 'Live setting mutation requires -TestExecution.' }
$projectId = Get-RequiredProductionEnv 'GCP_PROJECT_ID'
$usernameSecret = Get-RequiredProductionEnv 'KP_MONITOR_USERNAME_SECRET'
$passwordSecret = Get-RequiredProductionEnv 'KP_MONITOR_PASSWORD_SECRET'
$env:KP_MONITOR_USERNAME = (& (Join-Path $PSScriptRoot 'gcloud.ps1') secrets versions access latest --secret $usernameSecret --project $projectId).Trim()
$env:KP_MONITOR_PASSWORD = (& (Join-Path $PSScriptRoot 'gcloud.ps1') secrets versions access latest --secret $passwordSecret --project $projectId).Trim()
$env:KP_USE_HAR_CREDENTIALS = 'false'
$env:DRY_RUN = 'false'
try {
    python .\scripts\kpnet_settings_roundtrip.py --target-soc $TargetSoc --test-execution
    if ($LASTEXITCODE -ne 0) { throw 'KP-NET settings round-trip failed.' }
} finally {
    Remove-Item Env:KP_MONITOR_USERNAME -ErrorAction SilentlyContinue
    Remove-Item Env:KP_MONITOR_PASSWORD -ErrorAction SilentlyContinue
}

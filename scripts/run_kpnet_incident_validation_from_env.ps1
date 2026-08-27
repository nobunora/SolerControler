param(
    [ValidateRange(50, 50)]
    [double]$TargetSoc = 50,
    [ValidatePattern('^(?:[01]\d|2[0-3]):[0-5]\d$')]
    [string]$TestChargeStart = '',
    [ValidatePattern('^(?:[01]\d|2[0-3]):[0-5]\d$')]
    [string]$TestChargeEnd = '',
    [string]$ResultPath = '',
    [switch]$TestExecution
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv

if (-not $TestExecution) {
    throw 'Live KP-NET mutation requires -TestExecution.'
}
$projectId = Get-RequiredProductionEnv 'GCP_PROJECT_ID'
$usernameSecret = Get-RequiredProductionEnv 'KP_MONITOR_USERNAME_SECRET'
$passwordSecret = Get-RequiredProductionEnv 'KP_MONITOR_PASSWORD_SECRET'
$env:KP_MONITOR_USERNAME = (& (Join-Path $PSScriptRoot 'gcloud.ps1') secrets versions access latest --secret $usernameSecret --project $projectId).Trim()
$env:KP_MONITOR_PASSWORD = (& (Join-Path $PSScriptRoot 'gcloud.ps1') secrets versions access latest --secret $passwordSecret --project $projectId).Trim()
$env:KP_USE_HAR_CREDENTIALS = 'false'
$env:DRY_RUN = 'false'
$pythonArguments = @(
    '.\scripts\kpnet_incident_validation.py',
    '--target-soc', $TargetSoc,
    '--test-execution'
)
if ($TestChargeStart) { $pythonArguments += @('--test-charge-start', $TestChargeStart) }
if ($TestChargeEnd) { $pythonArguments += @('--test-charge-end', $TestChargeEnd) }
if ($ResultPath) {
    $pythonArguments += @('--result-path', $ResultPath)
}
try {
    python @pythonArguments
    if ($LASTEXITCODE -ne 0) { throw 'KP-NET incident validation failed.' }
} finally {
    Remove-Item Env:KP_MONITOR_USERNAME -ErrorAction SilentlyContinue
    Remove-Item Env:KP_MONITOR_PASSWORD -ErrorAction SilentlyContinue
}

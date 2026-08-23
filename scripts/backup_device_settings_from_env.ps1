param(
    [string]$OutDir = "artifacts/backups/operational"
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv

$projectId = Get-RequiredProductionEnv 'GCP_PROJECT_ID'
$usernameSecret = Get-RequiredProductionEnv 'KP_MONITOR_USERNAME_SECRET'
$passwordSecret = Get-RequiredProductionEnv 'KP_MONITOR_PASSWORD_SECRET'
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$output = Join-Path $OutDir "device-settings-$stamp.json"
$gcloud = Join-Path $PSScriptRoot 'gcloud.ps1'

$env:KP_MONITOR_USERNAME = (& $gcloud secrets versions access latest --secret $usernameSecret --project $projectId).Trim()
$env:KP_MONITOR_PASSWORD = (& $gcloud secrets versions access latest --secret $passwordSecret --project $projectId).Trim()
$env:KP_USE_HAR_CREDENTIALS = 'false'
$env:DRY_RUN = 'false'
try {
    python .\scripts\backup_device_settings.py --output $output
    if ($LASTEXITCODE -ne 0) { throw 'KP-NET device settings read-back failed.' }
} finally {
    Remove-Item Env:KP_MONITOR_USERNAME -ErrorAction SilentlyContinue
    Remove-Item Env:KP_MONITOR_PASSWORD -ErrorAction SilentlyContinue
}
Write-Host "Device settings read-back backup completed: $output"

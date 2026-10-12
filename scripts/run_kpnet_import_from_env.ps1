param([switch]$SkipFirestoreIngest, [string[]]$TargetMonths = @())

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot
. (Join-Path $PSScriptRoot 'production_env.ps1')
$previousTargetMonths = [Environment]::GetEnvironmentVariable('KP_CSV_TARGET_MONTHS', 'Process')
Import-ProductionEnv
try {
if ($TargetMonths.Count) {
    foreach ($month in $TargetMonths) {
        if ($month -notmatch '^\d{4}-(0[1-9]|1[0-2])$') { throw 'Invalid target month.' }
    }
    $env:KP_CSV_TARGET_MONTHS = $TargetMonths -join ','
}

$projectId = Get-RequiredProductionEnv 'GCP_PROJECT_ID'
$usernameSecret = Get-RequiredProductionEnv 'KP_MONITOR_USERNAME_SECRET'
$passwordSecret = Get-RequiredProductionEnv 'KP_MONITOR_PASSWORD_SECRET'
$env:KP_MONITOR_USERNAME = (& (Join-Path $PSScriptRoot 'gcloud.ps1') secrets versions access latest --secret $usernameSecret --project $projectId).Trim()
$env:KP_MONITOR_PASSWORD = (& (Join-Path $PSScriptRoot 'gcloud.ps1') secrets versions access latest --secret $passwordSecret --project $projectId).Trim()
if (-not $env:KP_MONITOR_USERNAME -or -not $env:KP_MONITOR_PASSWORD) {
    throw 'KP-NET credentials could not be loaded from Secret Manager.'
}

$env:KP_USE_HAR_CREDENTIALS = 'false'
$env:KP_WORKFLOW_MODE = 'csv'
$env:KP_DOWNLOAD_LATEST_MONTH = 'true'
    python kpnet_main.py
    if ($LASTEXITCODE -ne 0) { throw 'KP-NET CSV import failed.' }

    if (-not $SkipFirestoreIngest) {
        $env:DATA_BACKEND = Get-RequiredProductionEnv 'DATA_BACKEND'
        $env:FIRESTORE_PROJECT_ID = Get-RequiredProductionEnv 'FIRESTORE_PROJECT_ID'
        $env:FIRESTORE_DATABASE_ID = Get-RequiredProductionEnv 'FIRESTORE_DATABASE_ID'
        $env:CLOUD_JOB_SLOT = 'manual-csv'
        $env:DATA_DB_WRITE_ONLY_23 = 'false'
        $env:DATA_PIPELINE_INCLUDE_CSV = 'true'
        $env:DATA_PIPELINE_INCLUDE_SETTINGS = 'false'
        $env:DATA_PIPELINE_INCLUDE_NIGHT_PLAN = 'false'
        python db_pipeline_main.py
        if ($LASTEXITCODE -ne 0) { throw 'Firestore ingest failed after KP-NET import.' }
    }
} finally {
    [Environment]::SetEnvironmentVariable('KP_CSV_TARGET_MONTHS', $previousTargetMonths, 'Process')
    Remove-Item Env:KP_MONITOR_USERNAME -ErrorAction SilentlyContinue
    Remove-Item Env:KP_MONITOR_PASSWORD -ErrorAction SilentlyContinue
}

Write-Host 'KP-NET import completed.'

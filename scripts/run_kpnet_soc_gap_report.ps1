param(
    [string]$Date = "",
    [string]$ProjectId = "",
    [string]$UsernameSecret = "",
    [string]$PasswordSecret = "",
    [string]$FirestoreProjectId = "",
    [switch]$SkipDownload
)

$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSEdition -ne "Core") {
    $pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($pwshCmd) {
        & $pwshCmd.Source -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath @PSBoundParameters
        exit $LASTEXITCODE
    }
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv

if ([string]::IsNullOrWhiteSpace($ProjectId)) {
    $ProjectId = Get-RequiredProductionEnv 'GCP_PROJECT_ID'
}
if ([string]::IsNullOrWhiteSpace($UsernameSecret)) {
    $UsernameSecret = Get-RequiredProductionEnv 'KP_MONITOR_USERNAME_SECRET'
}
if ([string]::IsNullOrWhiteSpace($PasswordSecret)) {
    $PasswordSecret = Get-RequiredProductionEnv 'KP_MONITOR_PASSWORD_SECRET'
}

if ([string]::IsNullOrWhiteSpace($Date)) {
    $Date = Get-Date -Format "yyyy-MM-dd"
}

if ([string]::IsNullOrWhiteSpace($FirestoreProjectId)) {
    $FirestoreProjectId = Get-RequiredProductionEnv 'FIRESTORE_PROJECT_ID'
}

if (-not $SkipDownload) {
    Write-Host "[kpnet-soc-report] loading KP-NET credentials from Secret Manager"
    $env:KP_MONITOR_USERNAME = (& "$PSScriptRoot\gcloud.ps1" secrets versions access latest --secret $UsernameSecret --project $ProjectId).Trim()
    $env:KP_MONITOR_PASSWORD = (& "$PSScriptRoot\gcloud.ps1" secrets versions access latest --secret $PasswordSecret --project $ProjectId).Trim()
    $env:KP_USE_HAR_CREDENTIALS = "false"
    $env:KP_WORKFLOW_MODE = "csv"
    $env:DRY_RUN = "false"

    Write-Host "[kpnet-soc-report] downloading KP-NET CSV"
    python .\kpnet_main.py
    if ($LASTEXITCODE -ne 0) {
        throw "kpnet_main.py failed with exit code $LASTEXITCODE"
    }
}

$env:FIRESTORE_PROJECT_ID = $FirestoreProjectId

$latestSummary = Get-ChildItem -Path ".\artifacts\*\kpnet_summary.json" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

if (-not $latestSummary) {
    throw "kpnet_summary.json was not found under artifacts"
}

$runDir = $latestSummary.Directory.FullName
Write-Host "[kpnet-soc-report] building report from $runDir"
python .\scripts\kpnet_soc_gap_report.py --run-dir $runDir --date $Date
if ($LASTEXITCODE -ne 0) {
    throw "kpnet_soc_gap_report.py failed with exit code $LASTEXITCODE"
}

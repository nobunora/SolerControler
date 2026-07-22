param(
    [switch]$ValidateOnly,
    [switch]$SkipPreRelease,
    [switch]$SkipJobBuild,
    [switch]$SkipJobDeploy,
    [switch]$SkipDashboardBuild,
    [switch]$SkipKpNetImport,
    [switch]$SkipDriveBackup
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv

Assert-ProductionEnv @(
    'GCP_PROJECT_ID',
    'GCP_REGION',
    'GCP_SCHEDULER_REGION',
    'GCP_RUNNER_REPOSITORY',
    'GCP_RUNNER_IMAGE_NAME',
    'GCP_DASHBOARD_REPOSITORY',
    'GCP_DASHBOARD_IMAGE_NAME',
    'GCP_DASHBOARD_SERVICE',
    'GCP_RUN_SERVICE_ACCOUNT',
    'GCP_RUN_SERVICE_ACCOUNT_NAME',
    'DATA_BACKEND',
    'FIRESTORE_PROJECT_ID',
    'FIRESTORE_DATABASE_ID',
    'DRIVE_BACKUP_FOLDER_ID',
    'SHEETS_SPREADSHEET_ID',
    'SHEETS_SHARE_EMAIL',
    'NIGHT_PLAN_ARCHIVE_GCS_PREFIX',
    'KP_MONITOR_USERNAME_SECRET',
    'KP_MONITOR_PASSWORD_SECRET',
    'DASHBOARD_BASIC_USER',
    'DASHBOARD_BASIC_PASSWORD',
    'DASHBOARD_SESSION_SECRET'
)

$projectId = Get-RequiredProductionEnv 'GCP_PROJECT_ID'
$region = Get-RequiredProductionEnv 'GCP_REGION'
$schedulerRegion = Get-RequiredProductionEnv 'GCP_SCHEDULER_REGION'
$runnerRepository = Get-RequiredProductionEnv 'GCP_RUNNER_REPOSITORY'
$runnerImage = Get-RequiredProductionEnv 'GCP_RUNNER_IMAGE_NAME'
$dashboardRepository = Get-RequiredProductionEnv 'GCP_DASHBOARD_REPOSITORY'
$dashboardImageName = Get-RequiredProductionEnv 'GCP_DASHBOARD_IMAGE_NAME'
$dashboardService = Get-RequiredProductionEnv 'GCP_DASHBOARD_SERVICE'
$driveFolder = Get-RequiredProductionEnv 'DRIVE_BACKUP_FOLDER_ID'
$sheetsId = Get-RequiredProductionEnv 'SHEETS_SPREADSHEET_ID'
$sheetsShare = Get-RequiredProductionEnv 'SHEETS_SHARE_EMAIL'
$archivePrefix = Get-RequiredProductionEnv 'NIGHT_PLAN_ARCHIVE_GCS_PREFIX'
$gcloud = Join-Path $PSScriptRoot 'gcloud.ps1'

if ($ValidateOnly) {
    & (Join-Path $PSScriptRoot 'check_production_env.ps1') -CheckCloud
    if ($LASTEXITCODE -ne 0) { throw 'Production environment validation failed.' }
    Write-Host 'Production deployment configuration is valid. No deployment was performed.'
    return
}

if (-not $SkipPreRelease) {
    & (Join-Path $PSScriptRoot 'pre_release_integration.ps1') -SkipInstall
    if ($LASTEXITCODE -ne 0) { throw 'Pre-release integration checks failed.' }
}

$jobDeployArgs = @{
    ProjectId = $projectId
    Region = $region
    SchedulerRegion = $schedulerRegion
    Repository = $runnerRepository
    ImageName = $runnerImage
    RunServiceAccountName = Get-RequiredProductionEnv 'GCP_RUN_SERVICE_ACCOUNT_NAME'
    UsernameSecretName = Get-RequiredProductionEnv 'KP_MONITOR_USERNAME_SECRET'
    PasswordSecretName = Get-RequiredProductionEnv 'KP_MONITOR_PASSWORD_SECRET'
    DataBackend = Get-RequiredProductionEnv 'DATA_BACKEND'
    SheetsSpreadsheetId = $sheetsId
    SheetsShareEmail = $sheetsShare
    DriveBackupFolderId = $driveFolder
    NightPlanArchiveGcsPrefix = $archivePrefix
    RunSmokeTest = $true
    # Cloud capacity is already checked by ValidateOnly/CheckCloud. Running the
    # legacy Windows PowerShell capacity helper here can terminate the parent
    # deployment process after gcloud.cmd exits.
    SkipCapacityCheck = $true
    SkipIamSetup = $true
    SkipSecretSetup = $true
}
if ($SkipJobBuild) { $jobDeployArgs.SkipBuild = $true }
if ($SkipJobDeploy) { $jobDeployArgs.SkipJobDeploy = $true }
& (Join-Path $PSScriptRoot 'deploy_gcp_jobs.ps1') @jobDeployArgs
if ($LASTEXITCODE -ne 0) { throw 'Cloud Run Jobs deployment failed.' }

$dashboardImage = "$region-docker.pkg.dev/$projectId/$dashboardRepository/${dashboardImageName}:latest"
if (-not $SkipDashboardBuild) {
    & $gcloud builds submit --config cloudbuild.dashboard.yaml --region $region --project $projectId --substitutions "_DASHBOARD_IMAGE=$dashboardImage" .
    if ($LASTEXITCODE -ne 0) { throw 'Dashboard image build failed.' }
}

$tempEnv = New-TemporaryFile
try {
    $dashboardEnv = [ordered]@{
        DATA_BACKEND = Get-RequiredProductionEnv 'DATA_BACKEND'
        FIRESTORE_PROJECT_ID = Get-RequiredProductionEnv 'FIRESTORE_PROJECT_ID'
        FIRESTORE_DATABASE_ID = Get-RequiredProductionEnv 'FIRESTORE_DATABASE_ID'
        DASHBOARD_HOST = '0.0.0.0'
        DASHBOARD_BASIC_USER = Get-RequiredProductionEnv 'DASHBOARD_BASIC_USER'
        DASHBOARD_BASIC_PASSWORD = Get-RequiredProductionEnv 'DASHBOARD_BASIC_PASSWORD'
        DASHBOARD_SESSION_SECRET = Get-RequiredProductionEnv 'DASHBOARD_SESSION_SECRET'
        DASHBOARD_COOKIE_SECURE = 'true'
        DASHBOARD_AGGREGATION_CLOSE_DAY = Get-ProductionEnv 'DASHBOARD_AGGREGATION_CLOSE_DAY' '14'
        DASHBOARD_SESSION_TTL_SECONDS = Get-ProductionEnv 'DASHBOARD_SESSION_TTL_SECONDS' '31536000'
    }
    $yamlLines = foreach ($entry in $dashboardEnv.GetEnumerator()) {
        $escaped = ([string]$entry.Value).Replace("'", "''")
        "$($entry.Key): '$escaped'"
    }
    [IO.File]::WriteAllLines($tempEnv.FullName, $yamlLines, [Text.UTF8Encoding]::new($false))
    & $gcloud run services update $dashboardService --region $region --project $projectId --image $dashboardImage --env-vars-file $tempEnv.FullName
    if ($LASTEXITCODE -ne 0) { throw 'Dashboard service deployment failed.' }
} finally {
    Remove-Item -LiteralPath $tempEnv.FullName -Force -ErrorAction SilentlyContinue
}

if (-not $SkipKpNetImport) {
    & (Join-Path $PSScriptRoot 'run_kpnet_import_from_env.ps1')
}
if (-not $SkipDriveBackup) {
    & (Join-Path $PSScriptRoot 'run_drive_backup_cloud_from_env.ps1')
}

Write-Host 'Production deployment, validation, KP-NET import, and Drive backup completed.'

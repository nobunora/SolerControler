param([switch]$CheckCloud)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv

$required = @(
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
Assert-ProductionEnv $required

git check-ignore --quiet .env
if ($LASTEXITCODE -ne 0) {
    throw '.env is not ignored by Git.'
}

if ($CheckCloud) {
    $projectId = Get-RequiredProductionEnv 'GCP_PROJECT_ID'
    $region = Get-RequiredProductionEnv 'GCP_REGION'
    $dashboardService = Get-RequiredProductionEnv 'GCP_DASHBOARD_SERVICE'
    $schedulerRegion = Get-RequiredProductionEnv 'GCP_SCHEDULER_REGION'
    $gcloud = Join-Path $PSScriptRoot 'gcloud.ps1'
    foreach ($jobName in @('solar-battery-23', 'solar-battery-03', 'solar-battery-07')) {
        $ready = (& $gcloud run jobs describe $jobName --project $projectId --region $region --format 'value(status.conditions[0].status)').Trim()
        if ($LASTEXITCODE -ne 0 -or $ready -ne 'True') {
            throw "Cloud Run Job is not ready: $jobName"
        }
    }
    $serviceReady = (& $gcloud run services describe $dashboardService --project $projectId --region $region --format 'value(status.conditions[0].status)').Trim()
    if ($LASTEXITCODE -ne 0 -or $serviceReady -ne 'True') {
        throw "Cloud Run service is not ready: $dashboardService"
    }
    foreach ($schedulerName in @('solar-battery-run-23', 'solar-battery-run-03', 'solar-battery-run-07')) {
        $state = (& $gcloud scheduler jobs describe $schedulerName --project $projectId --location $schedulerRegion --format 'value(state)').Trim()
        if ($LASTEXITCODE -ne 0 -or $state -ne 'ENABLED') {
            throw "Cloud Scheduler is not enabled: $schedulerName"
        }
    }
}

Write-Host "Production environment check passed ($($required.Count) required settings)."

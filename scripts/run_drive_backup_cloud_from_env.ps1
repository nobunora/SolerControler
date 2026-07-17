param([ValidateSet('data')][string]$Mode = 'data')

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv

Assert-ProductionEnv @(
    'GCP_PROJECT_ID',
    'GCP_REGION',
    'GCP_RUNNER_REPOSITORY',
    'GCP_RUNNER_IMAGE_NAME',
    'GCP_RUN_SERVICE_ACCOUNT',
    'FIRESTORE_PROJECT_ID',
    'FIRESTORE_DATABASE_ID',
    'DRIVE_BACKUP_FOLDER_ID'
)

$projectId = Get-RequiredProductionEnv 'GCP_PROJECT_ID'
$region = Get-RequiredProductionEnv 'GCP_REGION'
$repository = Get-RequiredProductionEnv 'GCP_RUNNER_REPOSITORY'
$imageName = Get-RequiredProductionEnv 'GCP_RUNNER_IMAGE_NAME'
$serviceAccount = Get-RequiredProductionEnv 'GCP_RUN_SERVICE_ACCOUNT'
$firestoreProject = Get-RequiredProductionEnv 'FIRESTORE_PROJECT_ID'
$firestoreDatabase = Get-RequiredProductionEnv 'FIRESTORE_DATABASE_ID'
$folderId = Get-RequiredProductionEnv 'DRIVE_BACKUP_FOLDER_ID'
$jobName = "solar-drive-backup-manual-$((Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss'))-$PID"
$image = "$region-docker.pkg.dev/$projectId/$repository/${imageName}:latest"
$gcloud = Join-Path $PSScriptRoot 'gcloud.ps1'
$created = $false

try {
    $deployArgs = @(
        'run', 'jobs', 'deploy', $jobName,
        '--project', $projectId,
        '--region', $region,
        '--image', $image,
        '--service-account', $serviceAccount,
        '--task-timeout', '1800',
        '--max-retries', '0',
        '--command', 'python',
        '--args', "scripts/backup_drive.py,--mode,$Mode,--folder-id,$folderId,--pretty",
        '--set-env-vars', "DATA_BACKEND=firestore,FIRESTORE_PROJECT_ID=$firestoreProject,FIRESTORE_DATABASE_ID=$firestoreDatabase,DRIVE_BACKUP_FOLDER_ID=$folderId,DRIVE_BACKUP_MODE=$Mode"
    )
    & $gcloud @deployArgs
    if ($LASTEXITCODE -ne 0) { throw 'Temporary Drive backup job deployment failed.' }
    $created = $true

    & $gcloud run jobs execute $jobName --project $projectId --region $region --wait
    if ($LASTEXITCODE -ne 0) { throw 'Drive backup execution failed.' }
} finally {
    if ($created) {
        & $gcloud run jobs delete $jobName --project $projectId --region $region --quiet
    }
}

Write-Host 'Google Drive data backup completed.'

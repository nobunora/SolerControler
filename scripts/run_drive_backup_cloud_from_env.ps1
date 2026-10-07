param(
    [ValidateSet('data')][string]$Mode = 'data',
    [switch]$UpdateScheduledJobMemory,
    [switch]$SeparatedRuntime,
    [string]$ScheduledJobName = 'solar-drive-backup'
)

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
    'DRIVE_BACKUP_FOLDER_ID',
    'KP_MONITOR_USERNAME_SECRET',
    'KP_MONITOR_PASSWORD_SECRET'
)

$projectId = Get-RequiredProductionEnv 'GCP_PROJECT_ID'
$region = Get-RequiredProductionEnv 'GCP_REGION'
$repository = Get-RequiredProductionEnv 'GCP_RUNNER_REPOSITORY'
$imageName = Get-RequiredProductionEnv 'GCP_RUNNER_IMAGE_NAME'
if ($SeparatedRuntime -or (Get-ProductionEnv 'SOLAR_RUNTIME_LAYOUT' 'legacy') -eq 'separated') { $imageName += '-planner' }
$serviceAccount = Get-RequiredProductionEnv 'GCP_RUN_SERVICE_ACCOUNT'
$firestoreProject = Get-RequiredProductionEnv 'FIRESTORE_PROJECT_ID'
$firestoreDatabase = Get-RequiredProductionEnv 'FIRESTORE_DATABASE_ID'
$folderId = Get-RequiredProductionEnv 'DRIVE_BACKUP_FOLDER_ID'
$usernameSecret = Get-RequiredProductionEnv 'KP_MONITOR_USERNAME_SECRET'
$passwordSecret = Get-RequiredProductionEnv 'KP_MONITOR_PASSWORD_SECRET'
$jobName = "solar-drive-backup-manual-$((Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss'))-$PID"
$image = "$region-docker.pkg.dev/$projectId/$repository/${imageName}:latest"
$gcloud = Join-Path $PSScriptRoot 'gcloud.ps1'
if ($SeparatedRuntime -or (Get-ProductionEnv 'SOLAR_RUNTIME_LAYOUT' 'legacy') -eq 'separated') {
    # Reuse the exact deployed planner, never an absent/stale mutable tag.
    $image = ((& $gcloud run jobs describe $ScheduledJobName --project $projectId --region $region `
        --format 'value(spec.template.spec.template.spec.containers[0].image)') -join '').Trim()
    $expectedPackage = "$region-docker.pkg.dev/$projectId/$repository/${imageName}@sha256:"
    if ($LASTEXITCODE -ne 0 -or -not $image.StartsWith($expectedPackage) -or $image -notmatch '@sha256:[0-9a-f]{64}$') {
        throw 'Scheduled backup does not reference a verified immutable planner image.'
    }
}
# Detailed plans and the 14-generation quota fallback exceed the default 512Mi.
# This is the same bounded allocation used by the scheduled backup job.
$backupMemory = '2Gi'
if ($UpdateScheduledJobMemory) {
    & $gcloud run jobs update $ScheduledJobName --project $projectId --region $region --memory $backupMemory
    if ($LASTEXITCODE -ne 0) { throw 'Scheduled Drive backup memory update failed.' }
}
$created = $false
$backupSucceeded = $false
$cleanupSucceeded = $true

try {
    $deployArgs = @(
        'run', 'jobs', 'deploy', $jobName,
        '--project', $projectId,
        '--region', $region,
        '--image', $image,
        '--service-account', $serviceAccount,
        '--task-timeout', '1800',
        '--memory', $backupMemory,
        '--max-retries', '0',
        '--command', 'python',
        '--args', "scripts/backup_drive.py,--mode,$Mode,--folder-id,$folderId,--pretty",
        '--set-env-vars', "DATA_BACKEND=firestore,FIRESTORE_PROJECT_ID=$firestoreProject,FIRESTORE_DATABASE_ID=$firestoreDatabase,DRIVE_BACKUP_FOLDER_ID=$folderId,DRIVE_BACKUP_MODE=$Mode,DRIVE_BACKUP_DEVICE_READBACK=true",
        '--set-secrets', "KP_MONITOR_USERNAME=$usernameSecret`:latest,KP_MONITOR_PASSWORD=$passwordSecret`:latest"
    )
    & $gcloud @deployArgs
    if ($LASTEXITCODE -ne 0) { throw 'Temporary Drive backup job deployment failed.' }
    $created = $true

    & $gcloud run jobs execute $jobName --project $projectId --region $region --wait
    if ($LASTEXITCODE -ne 0) { throw 'Drive backup execution failed.' }
    $backupSucceeded = $true
} finally {
    if ($created) {
        & $gcloud run jobs delete $jobName --project $projectId --region $region --quiet
        if ($LASTEXITCODE -ne 0) {
            $cleanupSucceeded = $false
            Write-Warning 'Drive backup completed, but temporary job cleanup failed.'
        }
    }
}

if (-not $backupSucceeded) { throw 'Drive backup did not complete successfully.' }
if (-not $cleanupSucceeded) { throw 'Drive backup succeeded, but temporary job cleanup failed.' }
Write-Host 'Google Drive data backup completed.'
exit 0

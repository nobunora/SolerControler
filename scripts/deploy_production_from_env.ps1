param(
    [switch]$ValidateOnly,
    [switch]$SkipPreRelease,
    [switch]$SkipJobBuild,
    [switch]$SkipJobDeploy,
    [switch]$SkipJob23Deploy,
    [switch]$SkipJob03Deploy,
    [switch]$SkipJob07Deploy,
    [switch]$SkipForecastJobDeploy,
    [switch]$SkipForecastSchedulerDeploy,
    [switch]$SkipSettingsRoundTripJobDeploy,
    [switch]$SkipDashboardBuild,
    [switch]$SkipInlineSmokeTest,
    [ValidateRange(50, 50)]
    [double]$SettingsRoundTripTargetSoc = 50,
    [switch]$SkipKpNetImport,
    [switch]$SkipDriveBackup,
    [ValidateSet('auto', 'full', 'runner', 'dashboard')]
    [string]$DeploymentScope = 'auto',
    [string]$StatePath = "",
    [switch]$Resume
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

function Get-LastCompletedDeploymentCommit {
    $stateDir = Join-Path $artifactsRoot 'deployment_state'
    if (-not (Test-Path -LiteralPath $stateDir)) { return $null }
    foreach ($candidate in Get-ChildItem -LiteralPath $stateDir -Filter 'production-*.json' | Sort-Object LastWriteTimeUtc -Descending) {
        try { $record = Get-Content -LiteralPath $candidate.FullName -Raw -Encoding utf8 | ConvertFrom-Json } catch { continue }
        if ($record.kind -eq 'production_deployment' -and $record.status -eq 'complete' -and $record.repository_commit) {
            return [string]$record.repository_commit
        }
    }
    return $null
}

function Resolve-DeploymentScope {
    param([string]$RequestedScope)
    if ($RequestedScope -ne 'auto') { return $RequestedScope }
    $baseCommit = Get-LastCompletedDeploymentCommit
    if (-not $baseCommit) { return 'full' }
    git merge-base --is-ancestor $baseCommit HEAD
    if ($LASTEXITCODE -ne 0) { return 'full' }
    $changed = @(git diff --name-only "$baseCommit..HEAD")
    if ($changed.Count -eq 0) { return 'none' }
    $runnerChanged = $false
    $dashboardChanged = $false
    foreach ($path in $changed) {
        if ($path -match '^(app/|config/|Dockerfile$|requirements-runner\.txt$|cloudbuild\.runner\.yaml$|main\.py$|kpnet_main\.py$|energy_model_main\.py$|cloud_job_runner\.py$|db_pipeline_main\.py$|sheets_export_main\.py$)') { $runnerChanged = $true }
        if ($path -match '^(app/|templates/|static/|Dockerfile\.dashboard$|requirements-dashboard\.txt$|cloudbuild\.dashboard\.yaml$|dashboard_server\.py$)') { $dashboardChanged = $true }
    }
    if ($runnerChanged -and $dashboardChanged) { return 'full' }
    if ($runnerChanged) { return 'runner' }
    if ($dashboardChanged) { return 'dashboard' }
    return 'none'
}

if ($ValidateOnly) {
    & (Join-Path $PSScriptRoot 'check_production_env.ps1') -CheckCloud
    if ($LASTEXITCODE -ne 0) { throw 'Production environment validation failed.' }
    Write-Host 'Production deployment configuration is valid. No deployment was performed.'
    return
}

$artifactsRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot 'artifacts'))
if (-not $StatePath) {
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    $StatePath = Join-Path $artifactsRoot "deployment_state/production-$stamp.json"
} elseif (-not [IO.Path]::IsPathRooted($StatePath)) {
    $StatePath = Join-Path $repoRoot $StatePath
}
$StatePath = [IO.Path]::GetFullPath($StatePath)
if (-not $StatePath.StartsWith($artifactsRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "StatePath must remain under artifacts/deployment_state."
}

$resolvedScope = Resolve-DeploymentScope -RequestedScope $DeploymentScope
Write-Host "Deployment scope: $resolvedScope"
if ($resolvedScope -eq 'none') {
    Write-Host 'No deployable runner or dashboard source changed since the last completed deployment. No production mutation was performed.'
    return
}
if ($resolvedScope -eq 'runner') { $SkipDashboardBuild = $true }
if ($resolvedScope -eq 'dashboard') {
    $SkipJobBuild = $true
    $SkipJobDeploy = $true
    $SkipKpNetImport = $true
    $SkipDriveBackup = $true
}

$state = $null
if ($Resume) {
    if (-not (Test-Path -LiteralPath $StatePath)) {
        throw "Resume state was not found: $StatePath"
    }
    $state = Get-Content -LiteralPath $StatePath -Raw -Encoding utf8 | ConvertFrom-Json
    $currentCommit = (git rev-parse HEAD).Trim()
    if ($state.repository_commit -ne $currentCommit) {
        throw 'Resume state belongs to a different repository commit.'
    }
    if ($state.kind -ne 'production_deployment') {
        throw 'Resume state kind is not production_deployment.'
    }
    if ($state.stages.pre_release.status -eq 'success') { $SkipPreRelease = $true }
    if ($state.stages.jobs.status -eq 'success') {
        $SkipJobBuild = $true
        $SkipJobDeploy = $true
    }
    if ($state.stages.dashboard.status -eq 'success') { $SkipDashboardBuild = $true }
    if ($state.stages.kpnet_import.status -eq 'success') { $SkipKpNetImport = $true }
    if ($state.stages.drive_backup.status -eq 'success') { $SkipDriveBackup = $true }
} else {
    $state = [ordered]@{
        schema_version = 1
        kind = 'production_deployment'
        repository_commit = ((git rev-parse HEAD).Trim())
        repository_branch = ((git branch --show-current).Trim())
        started_at = (Get-Date).ToUniversalTime().ToString('o')
        updated_at = $null
        status = 'running'
        stages = [ordered]@{
            pre_release = [ordered]@{ status = 'not_started'; started_at = $null; completed_at = $null; error_code = $null; error_detail = $null }
            jobs = [ordered]@{ status = 'not_started'; started_at = $null; completed_at = $null; error_code = $null; error_detail = $null }
            dashboard = [ordered]@{ status = 'not_started'; started_at = $null; completed_at = $null; error_code = $null; error_detail = $null }
            settings_roundtrip = [ordered]@{ status = 'not_started'; started_at = $null; completed_at = $null; error_code = $null; error_detail = $null }
            kpnet_import = [ordered]@{ status = 'not_started'; started_at = $null; completed_at = $null; error_code = $null; error_detail = $null }
            drive_backup = [ordered]@{ status = 'not_started'; started_at = $null; completed_at = $null; error_code = $null; error_detail = $null }
        }
    }
}

if (-not ($state.stages.PSObject.Properties.Name -contains 'settings_roundtrip')) {
    $state.stages | Add-Member -NotePropertyName settings_roundtrip -NotePropertyValue ([ordered]@{
        status = 'not_started'; started_at = $null; completed_at = $null; error_code = $null; error_detail = $null
    })
}

function Save-DeploymentState {
    $state.updated_at = (Get-Date).ToUniversalTime().ToString('o')
    $parent = Split-Path -Parent $StatePath
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $StatePath -Encoding utf8
}

function Get-SafeErrorDetail {
    param([System.Management.Automation.ErrorRecord]$ErrorRecord)

    $message = $ErrorRecord.Exception.Message
    if (-not $message) {
        return 'No error message was available.'
    }
    $safeLines = @(
        $message -split "`r?`n" |
            Where-Object { $_ -notmatch '(?i)password|secret|token|authorization|credential' } |
            Select-Object -First 6
    )
    if (-not $safeLines) {
        return 'Error detail was redacted.'
    }
    return (($safeLines -join ' ') -replace '\s+', ' ').Trim()
}

# HISTORICAL_FAILURE_LOCK (0bc046b, 92c32d0, 5e46ff8): deployment stage state is
# a durable resume contract. Resolve both dictionary shapes explicitly; dynamic
# member access previously allowed a completed action to remain not_started.
function Get-DeploymentStageRecord {
    param([string]$Name)

    # New state uses OrderedDictionary while -Resume reads PSCustomObject.
    # Do not rely on dynamic member resolution: it can leave a stage's durable
    # state unchanged even though its action has run.
    if ($state.stages -is [System.Collections.IDictionary]) {
        $stage = $state.stages[$Name]
    } else {
        $property = $state.stages.PSObject.Properties[$Name]
        $stage = if ($property) { $property.Value } else { $null }
    }
    if ($null -eq $stage) {
        throw "Deployment state is missing stage: $Name"
    }
    return ,$stage
}

function Invoke-DeploymentStage {
    param(
        [string]$Name,
        [bool]$Skip,
        [scriptblock]$Action
    )
    $stage = Get-DeploymentStageRecord -Name $Name
    if ($Skip) {
        if ($stage.status -ne 'success') {
            $stage.status = 'skipped_manual'
            $stage.completed_at = (Get-Date).ToUniversalTime().ToString('o')
            Save-DeploymentState
        }
        Write-Host "Skip stage: $Name"
        return
    }
    $stage.status = 'running'
    $stage.started_at = (Get-Date).ToUniversalTime().ToString('o')
    $stage.completed_at = $null
    $stage.error_code = $null
    $stage.error_detail = $null
    Save-DeploymentState
    try {
        & $Action
        if ($LASTEXITCODE -ne 0) {
            throw "Deployment stage failed: $Name"
        }
        $stage.status = 'success'
        $stage.completed_at = (Get-Date).ToUniversalTime().ToString('o')
        Save-DeploymentState
    } catch {
        $stage.status = 'failed'
        $stage.completed_at = (Get-Date).ToUniversalTime().ToString('o')
        $stage.error_code = 'command_failed'
        $stage.error_detail = Get-SafeErrorDetail -ErrorRecord $_
        $state.status = 'failed'
        Save-DeploymentState
        throw
    }
}

Save-DeploymentState

if (-not $SkipPreRelease) {
    Invoke-DeploymentStage -Name 'pre_release' -Skip:$false -Action {
        & (Join-Path $PSScriptRoot 'pre_release_integration.ps1') -SkipInstall
    }
} else {
    Invoke-DeploymentStage -Name 'pre_release' -Skip:$true -Action { }
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
    # Cloud capacity is already checked by ValidateOnly/CheckCloud. Running the
    # legacy Windows PowerShell capacity helper here can terminate the parent
    # deployment process after gcloud.cmd exits.
    SkipCapacityCheck = $true
    SkipIamSetup = $true
    SkipSecretSetup = $true
}
if (-not $SkipInlineSmokeTest) { $jobDeployArgs.RunSmokeTest = $true }
if ($SkipJobBuild) { $jobDeployArgs.SkipBuild = $true }
if ($SkipJobDeploy) { $jobDeployArgs.SkipJobDeploy = $true }
if ($SkipJob23Deploy) { $jobDeployArgs.SkipJob23Deploy = $true }
if ($SkipJob03Deploy) { $jobDeployArgs.SkipJob03Deploy = $true }
if ($SkipJob07Deploy) { $jobDeployArgs.SkipJob07Deploy = $true }
if ($SkipForecastJobDeploy) { $jobDeployArgs.SkipForecastJobDeploy = $true }
if ($SkipForecastSchedulerDeploy) { $jobDeployArgs.SkipForecastSchedulerDeploy = $true }
if ($SkipSettingsRoundTripJobDeploy) { $jobDeployArgs.SkipSettingsRoundTripJobDeploy = $true }
Invoke-DeploymentStage -Name 'jobs' -Skip:($SkipJobBuild -and $SkipJobDeploy) -Action {
    & (Join-Path $PSScriptRoot 'deploy_gcp_jobs.ps1') @jobDeployArgs
}

$dashboardImage = "$region-docker.pkg.dev/$projectId/$dashboardRepository/${dashboardImageName}:latest"
Invoke-DeploymentStage -Name 'dashboard' -Skip:$SkipDashboardBuild -Action {
    $dashboardIgnoreFile = Join-Path $repoRoot '.gcloudignore-dashboard'
    & $gcloud builds submit --config cloudbuild.dashboard.yaml --ignore-file $dashboardIgnoreFile --region $region --project $projectId --substitutions "_DASHBOARD_IMAGE=$dashboardImage" .
    if ($LASTEXITCODE -ne 0) {
        throw 'Dashboard image build failed.'
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
    } finally {
        Remove-Item -LiteralPath $tempEnv.FullName -Force -ErrorAction SilentlyContinue
    }
}

# HISTORICAL_FAILURE_LOCK (device contract, confirmed 2026-08-23): every
# production deployment must prove the live forced-charge command path using
# the inverter's supported 50% SocChargeMode, then restore the exact snapshot.
# Do not make this stage optional or change the target to the continuous plan
# target; the 03 monitor, not SocChargeMode, stops at the plan target.
Invoke-DeploymentStage -Name 'settings_roundtrip' -Skip:$false -Action {
    & (Join-Path $PSScriptRoot 'run_cloud_job_from_env.ps1') -Slot settings-roundtrip -SettingsRoundTripTargetSoc $SettingsRoundTripTargetSoc -TestExecution
}

Invoke-DeploymentStage -Name 'kpnet_import' -Skip:$SkipKpNetImport -Action {
    & (Join-Path $PSScriptRoot 'run_kpnet_import_from_env.ps1')
}
Invoke-DeploymentStage -Name 'drive_backup' -Skip:$SkipDriveBackup -Action {
    & (Join-Path $PSScriptRoot 'run_drive_backup_cloud_from_env.ps1')
}

$state.status = 'complete'
Save-DeploymentState
Write-Host "Production deployment, validation, KP-NET import, and Drive backup completed. State: $StatePath"

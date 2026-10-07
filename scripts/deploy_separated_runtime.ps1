param(
    [Parameter(Mandatory = $true)][string]$StatePath,
    [switch]$Resume
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv
$gcloud = Join-Path $PSScriptRoot 'gcloud.ps1'
$project = Get-RequiredProductionEnv 'GCP_PROJECT_ID'
$region = Get-RequiredProductionEnv 'GCP_REGION'
$runnerRepo = Get-RequiredProductionEnv 'GCP_RUNNER_REPOSITORY'
$runnerName = Get-RequiredProductionEnv 'GCP_RUNNER_IMAGE_NAME'
$webRepo = Get-RequiredProductionEnv 'GCP_DASHBOARD_REPOSITORY'
$webName = Get-RequiredProductionEnv 'GCP_DASHBOARD_IMAGE_NAME'
$webService = Get-RequiredProductionEnv 'GCP_DASHBOARD_SERVICE'
$commit = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $commit -notmatch '^[0-9a-f]{40}$' -or @(git status --porcelain --untracked-files=all).Count) {
    throw 'Separated release requires a fixed commit and a clean worktree.'
}
$stateRoot = Join-Path $repoRoot 'artifacts/deployment_state'
$StatePath = [IO.Path]::GetFullPath($StatePath, $repoRoot)
if (-not $StatePath.StartsWith($stateRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'StatePath must be inside artifacts/deployment_state.'
}
$roles = [ordered]@{
    planner = "$region-docker.pkg.dev/$project/$runnerRepo/${runnerName}-planner"
    control = "$region-docker.pkg.dev/$project/$runnerRepo/${runnerName}-control"
    web = "$region-docker.pkg.dev/$project/$webRepo/$webName"
}
$stageNames = @('preflight', 'parity', 'inventory', 'build', 'planner', 'calculation', 'control', 'web', 'smoke', 'drive_backup', 'verification')
if ($Resume) {
    $state = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json -AsHashtable
    if ($state.repository_commit -ne $commit -or $state.kind -ne 'separated_runtime_deployment') {
        throw 'Resume state does not belong to this commit and release layout.'
    }
    # Ambiguous device writes must be inspected before resuming. Never repeat a
    # live round-trip because the outer shell lost its response.
    if ($state.stages.control.status -eq 'running') {
        throw 'Control stage is inconclusive; inspect exact restoration and terminal probe evidence before retrying.'
    }
} else {
    if (Test-Path -LiteralPath $StatePath) { throw 'State already exists; use Resume.' }
    $state = [ordered]@{ kind = 'separated_runtime_deployment'; schema_version = 1; repository_commit = $commit
        status = 'running'; stages = [ordered]@{}; image_digests = [ordered]@{} }
    foreach ($name in $stageNames) { $state.stages[$name] = [ordered]@{ status = 'not_started' } }
}

function Save-State {
    New-Item -ItemType Directory -Force -Path $stateRoot | Out-Null
    $state.updated_at = [DateTime]::UtcNow.ToString('o')
    $temp = "$StatePath.part"
    $state | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $temp -Encoding utf8
    Move-Item -LiteralPath $temp -Destination $StatePath -Force
}

function Invoke-Stage([string]$Name, [scriptblock]$Action) {
    if ($state.stages[$Name].status -eq 'success') { Write-Host "Verified previous stage: $Name"; return }
    $state.stages[$Name].status = 'running'
    $state.stages[$Name].started_at = [DateTime]::UtcNow.ToString('o')
    Save-State
    try {
        & $Action
        if ($LASTEXITCODE -ne 0) { throw 'Stage returned a non-zero exit code.' }
        $state.stages[$Name].status = 'success'
        $state.stages[$Name].completed_at = [DateTime]::UtcNow.ToString('o')
        Save-State
    } catch {
        $state.stages[$Name].status = 'failed'
        $state.status = 'failed'
        Save-State
        throw
    }
}

function Invoke-Cloud([string[]]$Arguments) {
    $output = (& $gcloud @Arguments 2>&1) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw 'Cloud operation failed; consult the private release log.' }
    return $output
}

function Get-Image([string]$Role) {
    $digest = [string]$state.image_digests[$Role]
    if ($digest -notmatch '^sha256:[0-9a-f]{64}$') { throw 'Pinned image digest is missing.' }
    return "$($roles[$Role])@$digest"
}

function Assert-Idle([string]$Job) {
    $rows = @(Invoke-Cloud @('run', 'jobs', 'executions', 'list', '--job', $Job, '--region', $region, '--project', $project,
                              '--limit', '5', '--sort-by', '~createTime', '--format', 'json') | ConvertFrom-Json -AsHashtable)
    foreach ($row in $rows) {
        if ($row.status -and -not $row.status.completionTime) { throw 'A target job has an active or inconclusive execution.' }
    }
}

Invoke-Stage 'preflight' {
    $valid = $false
    foreach ($file in Get-ChildItem -LiteralPath $stateRoot -Filter 'preflight-*.json' | Sort-Object LastWriteTimeUtc -Descending) {
        $record = Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json -AsHashtable
        if ($record.repository_commit -eq $commit -and $record.status -eq 'passed') { $valid = $true; break }
    }
    if (-not $valid) { & (Join-Path $PSScriptRoot 'production_deployment_gate.ps1') -RunPreRelease }
}
Invoke-Stage 'parity' {
    $backup = Get-RequiredProductionEnv 'SOLAR_RECOVERY_BACKUP_PATH'
    & python (Join-Path $PSScriptRoot 'verify_separated_runtime_parity.py') --backup $backup `
        --baseline fd022e7a94abc40d8190b639722d9f5471042d07 --output (Join-Path $stateRoot "parity-$commit.json")
}
Invoke-Stage 'inventory' {
    & (Join-Path $PSScriptRoot 'backup_operational_state_from_env.ps1')
}
Invoke-Stage 'build' {
    $substitutions = "_PLANNER_IMAGE=$($roles.planner):git-$commit,_CONTROL_IMAGE=$($roles.control):git-$commit,_WEB_IMAGE=$($roles.web):git-$commit"
    $cacheCommit = Get-ProductionEnv 'SOLAR_RUNTIME_BUILD_CACHE_COMMIT' $commit
    if ($cacheCommit -notmatch '^[0-9a-f]{40}$') { throw 'Runtime build cache must name a fixed source commit.' }
    $substitutions += ",_PLANNER_CACHE_IMAGE=$($roles.planner):git-$cacheCommit,_CONTROL_CACHE_IMAGE=$($roles.control):git-$cacheCommit,_WEB_CACHE_IMAGE=$($roles.web):git-$cacheCommit"
    # One context upload and one build for each role. No uncommitted sources,
    # credentials, backups, logs or model artifacts may enter this context.
    Invoke-Cloud @('builds', 'submit', '--config', 'cloudbuild.separated.yaml', '--ignore-file', '.gcloudignore-separated',
        '--region', $region, '--project', $project, '--substitutions', $substitutions, '.') | Out-Null
    foreach ($role in $roles.Keys) {
        $digest = (Invoke-Cloud @('artifacts', 'docker', 'images', 'describe', "$($roles[$role]):git-$commit", '--project', $project,
                                  '--format', 'value(image_summary.digest)')).Trim()
        if ($digest -notmatch '^sha256:[0-9a-f]{64}$') { throw 'Built image digest is invalid.' }
        $state.image_digests[$role] = $digest
    }
}
Invoke-Stage 'planner' {
    foreach ($job in @('solar-forecast-daily', 'solar-drive-backup')) { Assert-Idle $job }
    Invoke-Cloud @('run', 'jobs', 'update', 'solar-forecast-daily', '--region', $region, '--project', $project,
        '--image', (Get-Image 'planner'), '--command', 'python', '--args', 'planner_job_main.py',
        '--update-env-vars', "PLAN_SOURCE_REVISION=$commit,PLAN_IMAGE_DIGEST=$($state.image_digests.planner),KP_CSV_PLOT_ENABLED=false") | Out-Null
    # Retain the existing backup command, arguments, memory, SA and secret refs.
    Invoke-Cloud @('run', 'jobs', 'update', 'solar-drive-backup', '--region', $region, '--project', $project,
        '--image', (Get-Image 'planner')) | Out-Null
}
Invoke-Stage 'calculation' {
    & (Join-Path $PSScriptRoot 'run_cloud_job_from_env.ps1') -Slot forecast
}
Invoke-Stage 'control' {
    & (Join-Path $PSScriptRoot 'deploy_control_jobs_image_only.ps1') -ExpectedCommit $commit -SkipBuild -SeparatedRuntime
}
Invoke-Stage 'web' {
    Invoke-Cloud @('run', 'services', 'update', $webService, '--region', $region, '--project', $project,
        '--image', (Get-Image 'web')) | Out-Null
}
Invoke-Stage 'smoke' {
    & (Join-Path $PSScriptRoot 'run_cloud_job_from_env.ps1') -Slot 07 -DryRun
}
Invoke-Stage 'drive_backup' {
    & (Join-Path $PSScriptRoot 'run_drive_backup_cloud_from_env.ps1') -SeparatedRuntime
}
Invoke-Stage 'verification' {
    foreach ($pair in @(@('solar-forecast-daily', 'planner'), @('solar-drive-backup', 'planner'),
                       @('solar-battery-23', 'control'), @('solar-battery-03', 'control'),
                       @('solar-battery-07', 'control'), @('solar-battery-settings-roundtrip', 'control'))) {
        $job = Invoke-Cloud @('run', 'jobs', 'describe', $pair[0], '--region', $region, '--project', $project, '--format', 'json') | ConvertFrom-Json -AsHashtable
        if ($job.spec.template.spec.template.spec.containers[0].image -ne (Get-Image $pair[1])) {
            throw 'Deployed job image differs from the validated role digest.'
        }
    }
    $service = Invoke-Cloud @('run', 'services', 'describe', $webService, '--region', $region, '--project', $project, '--format', 'json') | ConvertFrom-Json -AsHashtable
    if ($service.spec.template.spec.containers[0].image -ne (Get-Image 'web') -or
        $service.status.latestReadyRevisionName -ne $service.status.latestCreatedRevisionName) {
        throw 'Web image or ready revision differs from the validated release.'
    }
}
$state.status = 'complete'
Save-State
Write-Host 'Separated runtime deployed and verified. Image pruning is a separate backup-protected operation.'

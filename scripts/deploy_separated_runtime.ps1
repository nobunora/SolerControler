param(
    [Parameter(Mandatory = $true)][string]$StatePath,
    [switch]$Resume,
    [string]$ApprovedCalculationPatchSha256 = '',
    [switch]$AllowOutOfWindowLiveProbe,
    [string]$MonitoringRepairStatePath = '',
    [string]$ReviewedRepairStateSha256 = '',
    [string]$AdapterProofFixturePath = ''
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
if ([bool]$MonitoringRepairStatePath -ne [bool]$ReviewedRepairStateSha256) { throw 'Repair path and reviewed SHA must be supplied together.' }
if ($ReviewedRepairStateSha256 -and $ReviewedRepairStateSha256 -notmatch '^[a-f0-9]{64}$') { throw 'Reviewed repair identity must be a SHA-256.' }
if (-not $AdapterProofFixturePath) { throw 'Explicit adapter proof fixture is required before a separated release.' }
$AdapterProofFixturePath = [IO.Path]::GetFullPath($AdapterProofFixturePath, $repoRoot)
if (-not (Test-Path -LiteralPath $AdapterProofFixturePath)) { throw 'Adapter proof fixture is missing.' }
$fixtureSha = (& python -c 'import json,hashlib,sys;v=json.load(open(sys.argv[1],encoding="utf-8-sig"));h=hashlib.sha256(json.dumps({k:x for k,x in v.items() if k!="fixture_sha256"},sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest();assert h==v["fixture_sha256"];print(h)' $AdapterProofFixturePath).Trim()
if ($LASTEXITCODE -ne 0 -or $fixtureSha -notmatch '^[a-f0-9]{64}$') { throw 'Adapter fixture identity is invalid.' }
if ($MonitoringRepairStatePath) { $MonitoringRepairStatePath = [IO.Path]::GetFullPath($MonitoringRepairStatePath, $repoRoot) }
if ($ApprovedCalculationPatchSha256 -and $ApprovedCalculationPatchSha256 -notmatch '^[0-9a-f]{64}$') {
    throw 'Approved calculation patch must be a SHA-256.'
}
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
$stageNames = @('preflight', 'parity', 'inventory', 'build', 'planner', 'calculation', 'soc_api_credentials', 'control', 'web', 'smoke', 'drive_backup', 'verification')
if ($Resume) {
    $state = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json -AsHashtable
    if ($state.repository_commit -ne $commit -or $state.kind -ne 'separated_runtime_deployment') {
        throw 'Resume state does not belong to this commit and release layout.'
    }
    if ([string]$state['approved_calculation_patch_sha256'] -ne $ApprovedCalculationPatchSha256 -or
        [bool]$state['allow_out_of_window_live_probe'] -ne [bool]$AllowOutOfWindowLiveProbe) {
        throw 'Resume requires the same explicit calculation approval and probe-window option.'
    }
    if ([string]$state['adapter_fixture_sha256'] -ne $fixtureSha -or
        [string]$state['reviewed_repair_sha256'] -ne $ReviewedRepairStateSha256 -or
        [string]$state['monitoring_repair_state_path'] -ne $MonitoringRepairStatePath) {
        throw 'Resume requires the same adapter fixture and prepared repair identity.'
    }
    # Ambiguous device writes must be inspected before resuming. Never repeat a
    # live round-trip because the outer shell lost its response.
    if ($state.stages.control.status -eq 'running') {
        throw 'Control stage is inconclusive; inspect exact restoration and terminal probe evidence before retrying.'
    }
} else {
    if (Test-Path -LiteralPath $StatePath) { throw 'State already exists; use Resume.' }
    $state = [ordered]@{ kind = 'separated_runtime_deployment'; schema_version = 1; repository_commit = $commit
        status = 'running'; stages = [ordered]@{}; image_digests = [ordered]@{}
        approved_calculation_patch_sha256 = $ApprovedCalculationPatchSha256
        allow_out_of_window_live_probe = [bool]$AllowOutOfWindowLiveProbe
        adapter_fixture_sha256 = $fixtureSha; reviewed_repair_sha256 = $ReviewedRepairStateSha256
        monitoring_repair_state_path = $MonitoringRepairStatePath }
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
    if ($state.stages[$Name].status -eq 'success') {
        if ($Name -eq 'build') {
            if (-not $state.stages.build.Contains('proof_path') -or -not (Test-Path -LiteralPath $state.stages.build.proof_path)) { throw 'Build success lacks its native proof receipt.' }
            $proof = Get-Content -Raw -LiteralPath $state.stages.build.proof_path | ConvertFrom-Json -AsHashtable
            if ($proof.status -ne 'PASS' -or $proof.fixture_sha256 -ne $fixtureSha -or $proof.image_digest -ne $state.image_digests.planner -or
                (Get-FileHash -LiteralPath $state.stages.build.proof_path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $state.stages.build.proof_sha256) { throw 'Build native proof changed.' }
        }
        if ($Name -eq 'calculation' -and $MonitoringRepairStatePath) {
            $adoptedPath = "$MonitoringRepairStatePath.d/adopted-forecast.json"
            if (-not (Test-Path -LiteralPath $adoptedPath) -or -not $state.stages.calculation.Contains('adopted_sha256') -or
                (Get-FileHash -LiteralPath $adoptedPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $state.stages.calculation.adopted_sha256) { throw 'Calculation success lacks its adopted receipt.' }
            if (-not $state.stages.calculation.Contains('forecast_evidence_path') -or
                -not (Test-Path -LiteralPath $state.stages.calculation.forecast_evidence_path) -or
                (Get-FileHash -LiteralPath $state.stages.calculation.forecast_evidence_path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $state.stages.calculation.forecast_evidence_sha256) {
                throw 'Calculation success lacks its unchanged NEW evidence receipt.'
            }
            $previousEvidence = Get-Content -Raw -LiteralPath $state.stages.calculation.forecast_evidence_path | ConvertFrom-Json -AsHashtable
            $previousAdopted = Get-Content -Raw -LiteralPath $adoptedPath | ConvertFrom-Json -AsHashtable
            Assert-AdoptedForecastEvidence $previousEvidence $previousAdopted
            Invoke-PreparedMonitoringRepair -VerifyOnly
        }
        Write-Host "Verified previous stage: $Name"; return
    }
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
        if (-not $row.status -or -not $row.status.completionTime) { throw 'A target job has an active or inconclusive execution.' }
    }
}

function Assert-AdoptedForecastEvidence([hashtable]$Evidence, [hashtable]$Adopted) {
    if ($Evidence.required_evidence_status -ne 'PASS' -or $Evidence.common_parity_status -ne 'PASS' -or
        $Evidence.coverage.new_required_days -lt 1 -or $Evidence.coverage.new_verified_days -ne $Evidence.coverage.new_required_days) {
        throw 'Post-adoption evidence requires at least one fully verified NEW plan and common parity.'
    }
    foreach ($day in $Evidence.days.Values) {
        if ($day.evidence_kind -eq 'NEW_ADOPTED' -and ($day.status -ne 'PASS' -or $day.evidence_required -ne $true -or
            $day.original_verified -ne $true -or $day.metadata_verified -ne $true -or $day.capacity_verified -ne $true -or
            $day.required_soc_points -ne 17 -or $day.soc_points_verified -ne 17)) { throw 'NEW raw/capacity24/SOC17 evidence is incomplete.' }
    }
    $selected = $Evidence.days[$Adopted.target_date]
    if (-not $selected -or $selected.evidence_kind -ne 'NEW_ADOPTED' -or $selected.status -ne 'PASS' -or
        $selected.original_sha256 -ne $Adopted.raw_plan_sha256) { throw 'Adopted raw original is not verified.' }
    foreach ($key in @('target_date', 'raw_plan_sha256', 'control_version_id', 'forecast_run_id')) {
        if (-not $Adopted[$key] -or $selected.forecast_identity[$key] -ne $Adopted[$key]) { throw 'Verified NEW plan differs from the adopted receipt.' }
    }
}

function Invoke-PreparedMonitoringRepair([switch]$VerifyOnly) {
    $receiptDirectory = "$MonitoringRepairStatePath.d"
    $intentPath = Join-Path $receiptDirectory 'forecast-intent.json'
    $executionPath = Join-Path $receiptDirectory 'forecast-execution.json'
    $adoptedPath = Join-Path $receiptDirectory 'adopted-forecast.json'
    $repairWrapper = Join-Path $PSScriptRoot 'run_monitoring_source_repair_from_env.ps1'
    if (-not (Test-Path -LiteralPath $repairWrapper)) { throw 'Prepared repair wrapper is unavailable.' }
    if (-not $VerifyOnly) {
        & python -c 'from pathlib import Path;import sys;from app.separated_runtime.planner import write_forecast_intent;write_forecast_intent(Path(sys.argv[1]),prepared_sha256=sys.argv[2],source_commit=sys.argv[3],planner_digest=sys.argv[4])' $intentPath $ReviewedRepairStateSha256 $commit $state.image_digests.planner
        if ($LASTEXITCODE -ne 0) { throw 'Forecast intent could not be fixed.' }
        foreach ($unit in @('source', 'daily', 'cost')) {
            & $repairWrapper -StatePath $MonitoringRepairStatePath -Stage $unit -Write -Resume -ReviewedStateSha256 $ReviewedRepairStateSha256
            if ($LASTEXITCODE -ne 0) { throw 'Source repair is incomplete.' }
        }
        $forecastSpec = Invoke-Cloud @('run', 'jobs', 'describe', 'solar-forecast-daily', '--region', $region, '--project', $project, '--format', 'json') | ConvertFrom-Json -AsHashtable
        $container = $forecastSpec.spec.template.spec.template.spec.containers[0]
        if ($container.image -ne (Get-Image 'planner') -or @($container.command).Count -ne 1 -or $container.command[0] -ne 'python' -or
            @($container.args).Count -ne 1 -or $container.args[0] -ne 'planner_job_main.py') { throw 'Forecast job differs from the canonical planner entrypoint.' }
        if (-not (Test-Path -LiteralPath $executionPath)) {
            & (Join-Path $PSScriptRoot 'run_cloud_job_from_env.ps1') -Slot forecast -ExecutionReceiptPath $executionPath
            # Even a non-zero job can have adopted the plan. Only exact native
            # identity readback below can resolve it; never launch a second job.
        }
        & python -c 'from pathlib import Path;import sys;from app.separated_runtime.planner import capture_adopted_forecast_receipt;capture_adopted_forecast_receipt(*(Path(x) for x in sys.argv[1:]))' $executionPath $intentPath $adoptedPath
        if ($LASTEXITCODE -ne 0) { throw 'Forecast adoption is unknown; planner rerun is prohibited.' }
        & python -c 'from pathlib import Path;import sys;from app.separated_runtime.planner import recover_adopted_forecast_projection;recover_adopted_forecast_projection(Path(sys.argv[1]))' $adoptedPath
        if ($LASTEXITCODE -ne 0) { throw 'Adopted forecast projections remain incomplete.' }
        & $repairWrapper -StatePath $MonitoringRepairStatePath -Stage history -Write -Resume -ReviewedStateSha256 $ReviewedRepairStateSha256
        if ($LASTEXITCODE -ne 0) { throw 'Historical asset remains incomplete.' }
    }
    $adopted = Get-Content -Raw -LiteralPath $adoptedPath | ConvertFrom-Json -AsHashtable
    & python (Join-Path $PSScriptRoot 'sync_validation_state.py') --direction firestore-to-sqlite
    if ($LASTEXITCODE -ne 0) { throw 'Post-adoption validation sync failed.' }
    $parityOutput = @(& python (Join-Path $PSScriptRoot 'validate_dashboard_backend_parity.py') --end-date $adopted.target_date --window-days 31)
    if ($LASTEXITCODE -ne 0) { throw 'Post-adoption dashboard parity failed.' }
    $evidenceLines = @($parityOutput | Where-Object { ([string]$_).StartsWith('[forecast-evidence] ') })
    if ($evidenceLines.Count -ne 1) { throw 'Post-adoption parity evidence is missing or ambiguous.' }
    $forecastEvidence = ([string]$evidenceLines[0]).Substring('[forecast-evidence] '.Length) | ConvertFrom-Json -AsHashtable
    Assert-AdoptedForecastEvidence $forecastEvidence $adopted
    $evidencePath = Join-Path $receiptDirectory 'post-adoption-evidence.json'
    $forecastEvidence | ConvertTo-Json -Depth 40 | Set-Content -LiteralPath "$evidencePath.part" -Encoding utf8
    Move-Item -LiteralPath "$evidencePath.part" -Destination $evidencePath -Force
    $state.stages.calculation.forecast_evidence_path = $evidencePath
    $state.stages.calculation.forecast_evidence_sha256 = (Get-FileHash -LiteralPath $evidencePath -Algorithm SHA256).Hash.ToLowerInvariant()
    $parityOutput | ForEach-Object { Write-Host $_ }
    & $repairWrapper -StatePath $MonitoringRepairStatePath -Stage verify -Resume -ReviewedStateSha256 $ReviewedRepairStateSha256
    if ($LASTEXITCODE -ne 0) { throw 'Final source and adopted forecast verification failed.' }
    $state.stages.calculation.adopted_sha256 = (Get-FileHash -LiteralPath $adoptedPath -Algorithm SHA256).Hash.ToLowerInvariant()
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
    $parityArgs = @('--backup', $backup, '--baseline', 'fd022e7a94abc40d8190b639722d9f5471042d07',
                    '--output', (Join-Path $stateRoot "parity-$commit.json"))
    if ($ApprovedCalculationPatchSha256) { $parityArgs += @('--approved-calculation-patch-sha256', $ApprovedCalculationPatchSha256) }
    & python (Join-Path $PSScriptRoot 'verify_separated_runtime_parity.py') @parityArgs
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
    Save-State
    $proofDirectory = "$StatePath.d"
    New-Item -ItemType Directory -Force -Path $proofDirectory | Out-Null
    $proofPath = Join-Path $proofDirectory 'adapter-proof.json'
    & (Join-Path $PSScriptRoot 'run_server_adapter_proof_from_env.ps1') -ImmutablePlannerImage (Get-Image 'planner') `
        -FixturePath $AdapterProofFixturePath -ReceiptPath $proofPath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $proofPath)) { throw 'Native adapter proof is incomplete; production jobs remain unchanged.' }
    $proof = Get-Content -Raw -LiteralPath $proofPath | ConvertFrom-Json -AsHashtable
    if ($proof.status -ne 'PASS' -or $proof.fixture_sha256 -ne $fixtureSha -or $proof.image_digest -ne $state.image_digests.planner) { throw 'Native adapter proof identity mismatch.' }
    $state.stages.build.proof_path = $proofPath
    $state.stages.build.proof_sha256 = (Get-FileHash -LiteralPath $proofPath -Algorithm SHA256).Hash.ToLowerInvariant()
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
    if ($MonitoringRepairStatePath) { Invoke-PreparedMonitoringRepair }
    else { & (Join-Path $PSScriptRoot 'run_cloud_job_from_env.ps1') -Slot forecast }
}
Invoke-Stage 'soc_api_credentials' {
    & (Join-Path $PSScriptRoot 'configure_soc_api_from_env.ps1')
}
Invoke-Stage 'control' {
    $tokyo = [TimeZoneInfo]::FindSystemTimeZoneById('Tokyo Standard Time')
    $ownerNow = [TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $tokyo)
    $ownerMinutes = $ownerNow.Hour * 60 + $ownerNow.Minute
    if (($ownerMinutes -ge 165 -and $ownerMinutes -lt 195) -or ($ownerMinutes -ge 405 -and $ownerMinutes -lt 435) -or
        ($ownerMinutes -ge 1350 -and $ownerMinutes -lt 1395)) { throw 'Control stage is forbidden during a scheduled owner transition.' }
    foreach ($job in @('solar-battery-23', 'solar-battery-03', 'solar-battery-07', 'solar-battery-settings-roundtrip')) { Assert-Idle $job }
    & (Join-Path $PSScriptRoot 'deploy_control_jobs_image_only.ps1') -ExpectedCommit $commit -SkipBuild -SeparatedRuntime `
        -AllowOutOfWindowLiveProbe:$AllowOutOfWindowLiveProbe
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

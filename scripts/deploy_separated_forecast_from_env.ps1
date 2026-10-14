param(
    [Parameter(Mandatory = $true)][string]$StatePath,
    [Parameter(Mandatory = $true)][string]$AdapterProofFixturePath,
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
$commit = (git rev-parse HEAD).Trim()
$targetJson = @($project, $region, (Get-RequiredProductionEnv 'GCP_RUNNER_REPOSITORY'),
    (Get-RequiredProductionEnv 'GCP_RUNNER_IMAGE_NAME'), (Get-RequiredProductionEnv 'GCP_DASHBOARD_SERVICE'),
    (Get-RequiredProductionEnv 'GCP_SCHEDULER_REGION')) | ConvertTo-Json -Compress
$hash = [Security.Cryptography.SHA256]::Create()
try { $environmentSha = [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($targetJson))).Replace('-', '').ToLowerInvariant() }
finally { $hash.Dispose() }
if (git status --porcelain) { throw 'Forecast release requires a clean committed tree.' }
$StatePath = [IO.Path]::GetFullPath($StatePath, $repoRoot)
$stateRoot = Join-Path $repoRoot 'artifacts/deployment_state'
if (-not $StatePath.StartsWith($stateRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Forecast state must remain under artifacts/deployment_state.'
}
$AdapterProofFixturePath = [IO.Path]::GetFullPath($AdapterProofFixturePath, $repoRoot)
$fixture = Get-Content -LiteralPath $AdapterProofFixturePath -Raw | ConvertFrom-Json -AsHashtable
if ($fixture.source_commit -ne $commit -or $fixture.backend -ne 'firestore' -or
    $fixture.project_id -ne (Get-RequiredProductionEnv 'FIRESTORE_PROJECT_ID') -or
    $fixture.database_id -ne (Get-RequiredProductionEnv 'FIRESTORE_DATABASE_ID') -or
    $fixture.bucket -ne ([Uri](Get-RequiredProductionEnv 'NIGHT_PLAN_ARCHIVE_GCS_PREFIX')).Host) {
    throw 'Forecast adapter fixture identity mismatch.'
}
$fixtureSha = (& python -c 'import json,hashlib,sys;v=json.load(open(sys.argv[1],encoding="utf-8-sig"));h=hashlib.sha256(json.dumps({k:x for k,x in v.items() if k!="fixture_sha256"},sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest();assert h==v["fixture_sha256"];print(h)' $AdapterProofFixturePath).Trim()
if ($LASTEXITCODE -ne 0 -or $fixtureSha -notmatch '^[a-f0-9]{64}$') { throw 'Forecast fixture checksum mismatch.' }
$preflightPaths = @(Get-ChildItem -LiteralPath $stateRoot -Filter 'preflight-*.json' | Sort-Object LastWriteTimeUtc -Descending)
$preflight = $null
$mandatoryChecks = @('working_tree_clean', 'git_diff_check', 'env_is_ignored_and_unstaged', 'local_backup',
                     'security_check', 'production_validate_only', 'pre_release_integration')
foreach ($path in $preflightPaths) {
    $candidate = Get-Content -LiteralPath $path.FullName -Raw | ConvertFrom-Json -AsHashtable
    if ($candidate.repository_commit -eq $commit -and $candidate.status -eq 'passed' -and
        @($candidate.checks | Where-Object { $_.status -ne 'success' }).Count -eq 0 -and
        @($mandatoryChecks | Where-Object { $checkName = $_; @($candidate.checks | Where-Object { $_.name -eq $checkName }).Count -ne 1 }).Count -eq 0) {
        $preflight = $path.FullName
        break
    }
}
if (-not $preflight) { throw 'Forecast release requires the successful same-commit mandatory preflight.' }
$privateDirectory = "$StatePath.d"
New-Item -ItemType Directory -Force -Path $privateDirectory | Out-Null
if (Test-Path -LiteralPath $StatePath) {
    if (-not $Resume) { throw 'Existing forecast release requires Resume.' }
    $state = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json -AsHashtable
    if ($state.kind -ne 'separated_forecast_deployment' -or $state.repository_commit -ne $commit -or
        $state.fixture_sha256 -ne $fixtureSha -or $state.environment_sha256 -ne $environmentSha) { throw 'Forecast resume identity mismatch.' }
    if (@($state.stages.Values | Where-Object { $_.status -eq 'running' }).Count) {
        throw 'Forecast stage is inconclusive; reconcile its exact cloud execution before resume.'
    }
} else {
    if ($Resume) { throw 'Forecast resume state is missing.' }
    $state = [ordered]@{ kind = 'separated_forecast_deployment'; repository_commit = $commit;
        fixture_sha256 = $fixtureSha; environment_sha256 = $environmentSha; preflight_path = $preflight; status = 'running';
        stages = [ordered]@{
            build = [ordered]@{ status = 'not_started' }; image_pin = [ordered]@{ status = 'not_started' };
            adapter_proof = [ordered]@{ status = 'not_started' };
            jobs = [ordered]@{ status = 'not_started' };
            verification = [ordered]@{ status = 'not_started' };
            settings_roundtrip = [ordered]@{ status = 'skipped_not_applicable' }
        }
    }
}

function Save-State {
    $temporary = "$StatePath.tmp"
    $state | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $StatePath -Force
}
function Invoke-Cloud([string[]]$Arguments) {
    $response = (& $gcloud @Arguments 2>&1) -join "`n"
    $response | Set-Content -LiteralPath (Join-Path $privateDirectory 'last-cloud-response.private.txt') -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw 'Forecast cloud operation failed; see the private state evidence.' }
    return $response
}
function Invoke-Stage([string]$Name, [scriptblock]$Action) {
    if ($state.stages[$Name].status -eq 'success') { return }
    $state.stages[$Name].status = 'running'; Save-State
    try {
        & $Action
        $state.stages[$Name].status = 'success'; Save-State
    } catch {
        $state.stages[$Name].status = 'failed'; $state.status = 'failed'; Save-State
        throw
    }
}
function Read-Inventory {
    $inventory = [ordered]@{}
    foreach ($job in @('solar-forecast-daily', 'solar-battery-23', 'solar-battery-03', 'solar-battery-07',
                       'solar-battery-settings-roundtrip', 'solar-drive-backup')) {
        $inventory[$job] = Invoke-Cloud @('run', 'jobs', 'describe', $job, '--region', $region, '--project', $project, '--format', 'json') | ConvertFrom-Json -AsHashtable
    }
    $inventory.schedulers = Invoke-Cloud @('scheduler', 'jobs', 'list', '--location', (Get-RequiredProductionEnv 'GCP_SCHEDULER_REGION'), '--project', $project, '--format', 'json') | ConvertFrom-Json -AsHashtable
    $inventory.web = Invoke-Cloud @('run', 'services', 'describe', (Get-RequiredProductionEnv 'GCP_DASHBOARD_SERVICE'), '--region', $region, '--project', $project, '--format', 'json') | ConvertFrom-Json -AsHashtable
    return $inventory
}
function Assert-Idle {
    $executions = @(Invoke-Cloud @('run', 'jobs', 'executions', 'list', '--job', 'solar-forecast-daily', '--region', $region,
        '--project', $project, '--limit', '5', '--sort-by', '~createTime', '--format', 'json') | ConvertFrom-Json -AsHashtable)
    foreach ($execution in $executions) {
        if (-not $execution.status -or -not $execution.status.completionTime) { throw 'Forecast job is active or inconclusive.' }
    }
}
function Canonical-Json($Value) {
    $json = ($Value | ConvertTo-Json -Depth 100) | python -c 'import json,sys;print(json.dumps(json.load(sys.stdin),sort_keys=True,separators=(",",":"),ensure_ascii=True))'
    if ($LASTEXITCODE -ne 0) { throw 'Forecast inventory normalization failed.' }
    return $json
}
function Comparable-Spec($Spec) {
    $copy = $Spec | ConvertTo-Json -Depth 100 | ConvertFrom-Json -AsHashtable
    # gcloud regenerates this revision nonce even when restoring an identical
    # template. Preserve every other label and all runtime configuration.
    if ($copy.template.Contains('metadata') -and $copy.template.metadata.Contains('labels')) {
        $copy.template.metadata.labels.Remove('client.knative.dev/nonce')
    }
    foreach ($item in $copy.template.spec.template.spec.containers) {
        if ($item.Contains('env')) { $item.env = @($item.env | Sort-Object { $_.name }) }
    }
    return Canonical-Json $copy
}
function Comparable-Schedulers($Schedulers) {
    $copy = @($Schedulers | ConvertTo-Json -Depth 100 | ConvertFrom-Json -AsHashtable)
    foreach ($item in $copy) {
        foreach ($key in @('lastAttemptTime', 'status', 'scheduleTime')) { $item.Remove($key) }
    }
    return Canonical-Json @($copy | Sort-Object { $_.name })
}
Save-State
$beforePath = Join-Path $privateDirectory 'before.private.json'
if (-not (Test-Path -LiteralPath $beforePath)) {
    Read-Inventory | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $beforePath -Encoding utf8
}
$before = Get-Content -LiteralPath $beforePath -Raw | ConvertFrom-Json -AsHashtable
$container = $before['solar-forecast-daily'].spec.template.spec.template.spec.containers[0]
if (@($container.command).Count -ne 1 -or $container.command[0] -ne 'python' -or
    @($container.args).Count -ne 1 -or $container.args[0] -ne 'planner_job_main.py') {
    throw 'Forecast job must retain the canonical separated planner entrypoint.'
}
$imageBase = "$region-docker.pkg.dev/$project/$(Get-RequiredProductionEnv 'GCP_RUNNER_REPOSITORY')/$(Get-RequiredProductionEnv 'GCP_RUNNER_IMAGE_NAME')-planner"
Invoke-Stage 'build' {
    Invoke-Cloud @('builds', 'submit', '--config', 'cloudbuild.planner.yaml', '--ignore-file', '.gcloudignore-separated',
        '--region', $region, '--project', $project, '--substitutions', "_PLANNER_IMAGE=${imageBase}:git-$commit,_PLANNER_CACHE_IMAGE=$($container.image)", '.') | Out-Null
}
Invoke-Stage 'image_pin' {
    $digest = (Invoke-Cloud @('artifacts', 'docker', 'images', 'describe', "${imageBase}:git-$commit", '--project', $project, '--format', 'value(image_summary.digest)')).Trim()
    if ($digest -notmatch '^sha256:[a-f0-9]{64}$') { throw 'Forecast immutable digest is invalid.' }
    $state.image_digest = $digest; Save-State
}
Invoke-Stage 'adapter_proof' {
    $probe = Invoke-Cloud @('run', 'jobs', 'describe', 'solar-battery-settings-roundtrip', '--region', $region,
        '--project', $project, '--format', 'json') | ConvertFrom-Json -AsHashtable
    if ((Comparable-Spec $probe.spec) -ne (Comparable-Spec $before['solar-battery-settings-roundtrip'].spec)) {
        throw 'SDK probe configuration is not restored; do not repeat the proof.'
    }
    $proofPath = Join-Path $privateDirectory 'adapter-proof.json'
    & (Join-Path $PSScriptRoot 'run_server_adapter_proof_from_env.ps1') -ImmutablePlannerImage "$imageBase@$($state.image_digest)" -FixturePath $AdapterProofFixturePath -ReceiptPath $proofPath
    if ($LASTEXITCODE -ne 0) { throw 'Forecast native adapter proof failed.' }
    $proof = Get-Content -LiteralPath $proofPath -Raw | ConvertFrom-Json -AsHashtable
    if ($proof.status -ne 'PASS' -or $proof.image_digest -ne $state.image_digest -or $proof.fixture_sha256 -ne $fixtureSha) {
        throw 'Forecast native proof identity mismatch.'
    }
    $state.proof_path = $proofPath
    $state.proof_sha256 = (Get-FileHash -LiteralPath $proofPath -Algorithm SHA256).Hash.ToLowerInvariant()
}
$proof = Get-Content -LiteralPath $state.proof_path -Raw | ConvertFrom-Json -AsHashtable
if ((Get-FileHash -LiteralPath $state.proof_path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $state.proof_sha256 -or
    $proof.status -ne 'PASS' -or $proof.image_digest -ne $state.image_digest -or $proof.fixture_sha256 -ne $fixtureSha) {
    throw 'Forecast successful build lacks its matching native proof.'
}
Invoke-Stage 'jobs' {
    Assert-Idle
    Invoke-Cloud @('run', 'jobs', 'update', 'solar-forecast-daily', '--region', $region, '--project', $project,
        '--image', "$imageBase@$($state.image_digest)", '--update-env-vars', "PLAN_SOURCE_REVISION=$commit,PLAN_IMAGE_DIGEST=$($state.image_digest)") | Out-Null
}
Invoke-Stage 'verification' {
    $after = Read-Inventory
    $after | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath (Join-Path $privateDirectory 'after.private.json') -Encoding utf8
    foreach ($job in @('solar-battery-23', 'solar-battery-03', 'solar-battery-07', 'solar-battery-settings-roundtrip', 'solar-drive-backup')) {
        if ((Comparable-Spec $before[$job].spec) -ne (Comparable-Spec $after[$job].spec)) {
            throw 'A non-target job configuration changed or the SDK probe was not restored.'
        }
        if ($job -ne 'solar-battery-settings-roundtrip' -and $before[$job].metadata.generation -ne $after[$job].metadata.generation) {
            throw 'A non-target production job revision changed.'
        }
    }
    if ((Comparable-Schedulers $before.schedulers) -ne (Comparable-Schedulers $after.schedulers) -or
        (Canonical-Json $before.web.spec) -ne (Canonical-Json $after.web.spec)) {
        throw 'Forecast release changed Scheduler or web configuration.'
    }
    $actual = $after['solar-forecast-daily'].spec.template.spec.template.spec.containers[0]
    if ($actual.image -ne "$imageBase@$($state.image_digest)") { throw 'Forecast image readback mismatch.' }
    $ready = @($after['solar-forecast-daily'].status.conditions | Where-Object { $_.type -eq 'Ready' })
    if ($ready.Count -ne 1 -or $ready[0].status -ne 'True') { throw 'Forecast job is not ready.' }
    $expected = $before['solar-forecast-daily'].spec | ConvertTo-Json -Depth 100 | ConvertFrom-Json -AsHashtable
    $expectedContainer = $expected.template.spec.template.spec.containers[0]
    $expectedContainer.image = $actual.image
    foreach ($key in @('PLAN_SOURCE_REVISION', 'PLAN_IMAGE_DIGEST')) {
        $entries = @($actual.env | Where-Object { $_.name -eq $key })
        $value = if ($key -eq 'PLAN_SOURCE_REVISION') { $commit } else { $state.image_digest }
        if ($entries.Count -ne 1 -or $entries[0].value -ne $value) { throw 'Forecast source/image identity readback mismatch.' }
        $expectedEntries = @($expectedContainer.env | Where-Object { $_.name -eq $key })
        if ($expectedEntries.Count -ne 1) { throw 'Forecast runtime identity keys must already exist.' }
        $expectedEntries[0].value = $value
    }
    if ((Comparable-Spec $expected) -ne (Comparable-Spec $after['solar-forecast-daily'].spec)) {
        throw 'Forecast release changed an unexpected job field.'
    }
}
$state.status = 'complete'; Save-State
Write-Host 'Separated forecast release complete; control and device settings were not changed.'

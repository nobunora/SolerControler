param(
    [Parameter(Mandatory = $true)][string]$StateDirectory,
    [Parameter(Mandatory = $true)][string]$RepositoryCommit
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv
$gcloud = Join-Path $PSScriptRoot 'gcloud.ps1'
$project = Get-RequiredProductionEnv 'GCP_PROJECT_ID'
$region = Get-RequiredProductionEnv 'GCP_REGION'
$service = Get-RequiredProductionEnv 'GCP_DASHBOARD_SERVICE'
$job = 'solar-actual-import'
$directory = [IO.Path]::GetFullPath($StateDirectory, $repoRoot)
$allowed = [IO.Path]::GetFullPath((Join-Path $repoRoot 'artifacts/deployment_state'))
if (-not $directory.StartsWith($allowed + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Import deployment state must remain under artifacts/deployment_state.'
}
if ($RepositoryCommit -ne (git rev-parse HEAD).Trim() -or @(git status --porcelain).Count) {
    throw 'Import deployment requires the clean fixed release commit.'
}
New-Item -ItemType Directory -Force -Path $directory | Out-Null
$path = Join-Path $directory 'actual-import.json'

function Invoke-Cloud([string[]]$Arguments) {
    $output = (& $gcloud @Arguments 2>&1) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw 'Import provisioning failed; retain the private deployment state.' }
    return $output
}
function Save-State {
    $state | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath "$path.part" -Encoding utf8
    Move-Item -LiteralPath "$path.part" -Destination $path -Force
}
function Assert-ImportJob($Current, $Expected) {
    $task = $Current.spec.template.spec.template.spec
    $container = $task.containers[0]
    $revision = @($container.env | Where-Object { $_.name -eq 'ACTUAL_IMPORT_SOURCE_REVISION' })
    if ($container.image -ne $Expected.image -or ($container.command -join ' ') -ne 'python' -or
        ($container.args -join ' ') -ne '-m app.operations.actual_import' -or
        [int]$task.maxRetries -ne 0 -or [int]$task.timeoutSeconds -ne 600 -or
        [int]$Current.spec.template.spec.taskCount -ne 1 -or [int]$Current.spec.template.spec.parallelism -ne 1 -or
        $task.serviceAccountName -ne $Expected.runtime_account -or $revision.Count -ne 1 -or
        $revision[0].value -ne $RepositoryCommit) { throw 'Import job identity or execution limits changed.' }
    $policy = Invoke-Cloud @('run', 'jobs', 'get-iam-policy', $job, '--project', $project, '--region', $region, '--format', 'json') | ConvertFrom-Json -AsHashtable
    $grant = @($policy.bindings | Where-Object { $_.role -eq 'roles/run.jobsExecutorWithOverrides' -and
        -not $_.ContainsKey('condition') -and $_.members -contains "serviceAccount:$($Expected.web_account)" })
    if ($grant.Count -ne 1) { throw 'Import job dashboard invocation grant is unproven.' }
}

if (Test-Path -LiteralPath $path) {
    $state = Get-Content -Raw -LiteralPath $path | ConvertFrom-Json -AsHashtable
    if ($state.repository_commit -ne $RepositoryCommit -or $state.project -ne $project -or $state.region -ne $region) {
        throw 'Import deployment identity mismatch.'
    }
    if ($state.status -ne 'success') {
        throw 'Import provisioning is unresolved; reconcile exact build/job before resuming. No automatic repeat.'
    }
    $current = Invoke-Cloud @('run', 'jobs', 'describe', $job, '--project', $project, '--region', $region, '--format', 'json') | ConvertFrom-Json -AsHashtable
    Assert-ImportJob $current $state
    return
}
$state = [ordered]@{ repository_commit = $RepositoryCommit; project = $project; region = $region; status = 'running'; stage = 'inventory' }
Save-State
$planner = Invoke-Cloud @('run', 'jobs', 'describe', 'solar-forecast-daily', '--project', $project, '--region', $region, '--format', 'json') | ConvertFrom-Json -AsHashtable
$web = Invoke-Cloud @('run', 'services', 'describe', $service, '--project', $project, '--region', $region, '--format', 'json') | ConvertFrom-Json -AsHashtable
$webAccount = [string]$web.spec.template.spec.serviceAccountName
if (-not $webAccount) { throw 'Explicit dashboard runtime identity is required.' }
$state.web_account = $webAccount
$state.runtime_account = [string]$planner.spec.template.spec.template.spec.serviceAccountName
if (-not $state.runtime_account) { throw 'Explicit import runtime identity is required.' }
Save-State
$existing = Invoke-Cloud @('run', 'jobs', 'list', '--project', $project, '--region', $region, '--format', 'json') | ConvertFrom-Json -AsHashtable
if (@($existing | Where-Object { $_.metadata.name -eq $job }).Count) {
    $executions = Invoke-Cloud @('run', 'jobs', 'executions', 'list', '--job', $job, '--project', $project, '--region', $region, '--format', 'json') | ConvertFrom-Json -AsHashtable
    if (@($executions | Where-Object { -not $_.status.ContainsKey('completionTime') }).Count) { throw 'An import execution is unresolved.' }
}
$plannerContainer = $planner.spec.template.spec.template.spec.containers[0]
$plannerRevision = @($plannerContainer.env | Where-Object { $_.name -eq 'PLAN_SOURCE_REVISION' })
$plannerDigest = @($plannerContainer.env | Where-Object { $_.name -eq 'PLAN_IMAGE_DIGEST' })
if ($plannerRevision.Count -ne 1 -or $plannerRevision[0].value -ne $RepositoryCommit -or
    $plannerDigest.Count -ne 1 -or $plannerDigest[0].value -notmatch '^sha256:[a-f0-9]{64}$' -or
    -not ([string]$plannerContainer.image).EndsWith("@$($plannerDigest[0].value)")) {
    throw 'Deploy the same-commit forecast through its canonical SDK-verified wrapper first.'
}
# Reuse the already SDK-verified immutable planner image; never build it twice.
$state.image = [string]$plannerContainer.image; $state.stage = 'job'; Save-State
$spec = $planner.spec.template.spec
$spec.taskCount = 1
$spec.parallelism = 1
$task = $spec.template.spec
$task.maxRetries = 0
$task.timeoutSeconds = '600'
$task.containers[0].image = $state.image
$task.containers[0].command = @('python')
$task.containers[0].args = @('-m', 'app.operations.actual_import')
$task.containers[0].env = @($task.containers[0].env | Where-Object { $_.name -ne 'ACTUAL_IMPORT_SOURCE_REVISION' }) +
    @(@{name = 'ACTUAL_IMPORT_SOURCE_REVISION'; value = $RepositoryCommit})
$resource = @{ apiVersion = 'run.googleapis.com/v1'; kind = 'Job'; metadata = @{name = $job; namespace = $project};
    spec = @{template = @{spec = $spec}} }
$resourcePath = Join-Path $directory 'actual-import-job.private.json'
$resource | ConvertTo-Json -Depth 40 | Set-Content -LiteralPath $resourcePath -Encoding utf8
Invoke-Cloud @('run', 'jobs', 'replace', $resourcePath, '--project', $project, '--region', $region) | Out-Null
Invoke-Cloud @('run', 'jobs', 'add-iam-policy-binding', $job, '--project', $project, '--region', $region,
    '--member', "serviceAccount:$webAccount", '--role', 'roles/run.jobsExecutorWithOverrides') | Out-Null
$current = Invoke-Cloud @('run', 'jobs', 'describe', $job, '--project', $project, '--region', $region, '--format', 'json') | ConvertFrom-Json -AsHashtable
Assert-ImportJob $current $state
$state.stage = 'verified'; $state.status = 'success'; Save-State

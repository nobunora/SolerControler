param(
    [Parameter(Mandatory = $true)][string]$DeploymentStatePath,
    [Parameter(Mandatory = $true)][string]$ReceiptPath,
    [ValidateSet(30, 50)][int]$TargetSoc = 30,
    [switch]$TestExecution,
    [switch]$AllowOutOfWindowLiveProbe,
    [string]$ProbeJobName = 'solar-battery-settings-roundtrip'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv
if (-not $TestExecution) { throw 'Explicit SOC mutation requires -TestExecution.' }
if (Test-Path -LiteralPath $ReceiptPath) { throw 'SOC receipt already exists; inspect the previous operation instead of repeating it.' }
$state = Get-Content -Raw -LiteralPath $DeploymentStatePath | ConvertFrom-Json -AsHashtable
$commit = ([string](git rev-parse HEAD)).Trim()
if ($LASTEXITCODE -ne 0 -or $state.kind -ne 'separated_runtime_deployment' -or $state.status -ne 'complete' -or
    $state.repository_commit -ne $commit -or $state.stages.verification.status -ne 'success') {
    throw 'SOC mutation requires a verified complete deployment of the current commit.'
}
$digest = [string]$state.image_digests.control
if ($digest -notmatch '^sha256:[a-f0-9]{64}$') { throw 'Deployed control digest is missing.' }
if ($AllowOutOfWindowLiveProbe -and $state.allow_out_of_window_live_probe -ne $true) {
    throw 'The one-shot window override must match the completed deployment approval.'
}
function Assert-SocProbeWindow([DateTime]$NowJst, [bool]$AllowOutside) {
    $minutes = $NowJst.Hour * 60 + $NowJst.Minute
    if (($minutes -ge 165 -and $minutes -lt 195) -or ($minutes -ge 405 -and $minutes -lt 435) -or
        ($minutes -ge 1350 -and $minutes -lt 1395)) { throw 'SOC probe is forbidden during a scheduled owner transition.' }
    if (($minutes -lt 435 -or $minutes -ge 1350) -and -not $AllowOutside) {
        throw 'SOC mutation outside 07:15–22:29 JST requires the explicit one-shot override.'
    }
}
$tokyo = [TimeZoneInfo]::FindSystemTimeZoneById('Tokyo Standard Time')
$now = [TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $tokyo)
Assert-SocProbeWindow $now ([bool]$AllowOutOfWindowLiveProbe)
$project = Get-RequiredProductionEnv 'GCP_PROJECT_ID'
$region = Get-RequiredProductionEnv 'GCP_REGION'
$gcloud = Join-Path $PSScriptRoot 'gcloud.ps1'
$ReceiptPath = [IO.Path]::GetFullPath($ReceiptPath, $repoRoot)
$privateDirectory = "$ReceiptPath.d"
New-Item -ItemType Directory -Force -Path $privateDirectory | Out-Null

function Invoke-SocCloud([string[]]$Arguments) {
    $output = (& $gcloud @Arguments 2>&1) -join "`n"
    $output | Set-Content -LiteralPath (Join-Path $privateDirectory 'last-response.private.txt') -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw 'SOC probe cloud operation failed; inspect private evidence.' }
    return $output
}
function Assert-SocIdle([string]$Job) {
    $rows = @(Invoke-SocCloud @('run', 'jobs', 'executions', 'list', '--job', $Job, '--project', $project,
        '--region', $region, '--limit', '10', '--sort-by', '~createTime', '--format', 'json') | ConvertFrom-Json -AsHashtable)
    foreach ($row in $rows) {
        if (-not $row.status -or -not $row.status.completionTime) { throw 'A control execution is active or unknown.' }
    }
    return $rows
}
function Assert-SocNightTerminal([hashtable]$Execution) {
    if (-not $Execution.status -or -not $Execution.status.completionTime) { throw 'The latest 03 owner is not terminal.' }
    foreach ($type in @('Completed', 'ResourcesAvailable', 'Started', 'ContainerReady')) {
        $conditions = @($Execution.status.conditions | Where-Object { $_.type -eq $type })
        if ($conditions.Count -ne 1 -or $conditions[0].status -ne 'True') { throw 'The latest 03 owner lacks terminal success evidence.' }
    }
    $tasks = $Execution.spec.taskCount
    $succeeded = $Execution.status.succeededCount
    if (($tasks -isnot [int] -and $tasks -isnot [long]) -or $tasks -lt 1 -or
        ($succeeded -isnot [int] -and $succeeded -isnot [long]) -or $succeeded -ne $tasks) {
        throw 'The latest 03 owner lacks integer total-task success evidence.'
    }
    foreach ($key in @('failedCount', 'cancelledCount', 'pendingCount', 'runningCount')) {
        if ($Execution.status.ContainsKey($key)) {
            $count = $Execution.status[$key]
            if (($count -isnot [int] -and $count -isnot [long]) -or $count -ne 0) {
                throw 'The latest 03 owner has invalid or unfinished task evidence.'
            }
        }
    }
}
foreach ($job in @($ProbeJobName, 'solar-battery-23', 'solar-battery-07')) { Assert-SocIdle $job | Out-Null }
$nightRows = @(Assert-SocIdle 'solar-battery-03')
if ($nightRows.Count -eq 0 -or -not $nightRows[0].metadata.name) { throw 'The latest 03 execution identity is unavailable.' }
$nightName = ([string]$nightRows[0].metadata.name -split '/')[-1]
$nightExecution = Invoke-SocCloud @('run', 'jobs', 'executions', 'describe', $nightName, '--project', $project,
    '--region', $region, '--format', 'json') | ConvertFrom-Json -AsHashtable
Assert-SocNightTerminal $nightExecution
$nightJob = Invoke-SocCloud @('run', 'jobs', 'describe', 'solar-battery-03', '--project', $project,
    '--region', $region, '--format', 'json') | ConvertFrom-Json -AsHashtable
if (([string]$nightJob.spec.template.spec.template.spec.containers[0].image -split '@')[-1] -ne $digest) {
    throw 'The current 03 Job does not use the deployed control digest.'
}
$before = Invoke-SocCloud @('run', 'jobs', 'describe', $ProbeJobName, '--project', $project, '--region', $region, '--format', 'json') | ConvertFrom-Json -AsHashtable
$containers = @($before.spec.template.spec.template.spec.containers)
if ($containers.Count -ne 1) { throw 'Dedicated SOC probe requires exactly one container.' }
$previous = $containers[0]
if ($before.spec.template.spec.template.spec.maxRetries -ne 0) { throw 'Physical SOC probe requires an explicit zero retry policy.' }
if (-not $previous.image -or ([string]$previous.image -split '@')[-1] -ne $digest) { throw 'Dedicated Job does not use the deployed control digest.' }
$before | ConvertTo-Json -Depth 40 | Set-Content -LiteralPath (Join-Path $privateDirectory 'job-before.private.json') -Encoding utf8
$entry = 'import sys;from app.kpnet.soc_setting_probe import run_soc_setting_probe;run_soc_setting_probe(target_soc_percent=float(sys.argv[1]))'
$argsForProbe = @('-c', $entry, [string]$TargetSoc)
$flagsPath = Join-Path $privateDirectory 'probe-update.private.json'
@{ '--command' = 'python'; '--args' = $argsForProbe } | ConvertTo-Json -Depth 8 |
    Set-Content -LiteralPath $flagsPath -Encoding utf8
$updated = $false
$restored = $false
$failure = $null
$receipt = @{ status = 'FAIL'; repository_commit = $commit; control_digest = $digest; requested_target_soc = $TargetSoc; allow_out_of_window_live_probe = [bool]$AllowOutOfWindowLiveProbe; device_restore_verified = $false; job_restore_verified = $false }
try {
    $updated = $true
    Invoke-SocCloud @('run', 'jobs', 'update', $ProbeJobName, '--project', $project, '--region', $region, '--flags-file', $flagsPath) | Out-Null
    $configured = Invoke-SocCloud @('run', 'jobs', 'describe', $ProbeJobName, '--project', $project, '--region', $region, '--format', 'json') | ConvertFrom-Json -AsHashtable
    $actual = $configured.spec.template.spec.template.spec.containers[0]
    if ($actual.image -ne $previous.image -or ($actual.command -join ',') -ne 'python' -or
        ($actual.args | ConvertTo-Json -Compress) -ne ($argsForProbe | ConvertTo-Json -Compress)) { throw 'SOC probe entrypoint readback mismatch.' }
    Assert-SocProbeWindow ([TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $tokyo)) ([bool]$AllowOutOfWindowLiveProbe)
    foreach ($job in @($ProbeJobName, 'solar-battery-23', 'solar-battery-03', 'solar-battery-07')) { Assert-SocIdle $job | Out-Null }
    $text = (& $gcloud run jobs execute $ProbeJobName --project $project --region $region --wait --format json) -join "`n"
    $executionExit = $LASTEXITCODE
    $text | Set-Content -LiteralPath (Join-Path $privateDirectory 'execution.private.json') -Encoding utf8
    $execution = $text | ConvertFrom-Json -AsHashtable
    $executionName = ([string]$execution.metadata.name -split '/')[-1]
    if (-not $executionName) { throw 'SOC probe execution identity is unknown.' }
    $filter = ('resource.type=cloud_run_job AND resource.labels.job_name="' + $ProbeJobName +
        '" AND labels."run.googleapis.com/execution_name"="' + $executionName + '" AND jsonPayload.message="post-deploy-soc-setting-probe"').Replace('"', '\"')
    $records = @(Invoke-SocCloud @('logging', 'read', $filter,
        '--project', $project, '--freshness', '1d', '--limit', '200', '--format', 'json') | ConvertFrom-Json -AsHashtable)
    $proofs = @($records | Where-Object {
        $_ -is [System.Collections.IDictionary] -and $_.Contains('labels') -and
        $_.labels -is [System.Collections.IDictionary] -and $_.labels.Contains('run.googleapis.com/execution_name') -and
        $_.labels['run.googleapis.com/execution_name'] -eq $executionName -and
        $_.Contains('jsonPayload') -and $_.jsonPayload -is [System.Collections.IDictionary] -and
        $_.jsonPayload.Contains('message') -and $_.jsonPayload.message -eq 'post-deploy-soc-setting-probe'
    })
    if ($proofs.Count -ne 1) { throw 'Exact execution SOC proof is absent or ambiguous.' }
    $proof = $proofs[0].jsonPayload
    $receipt.device_proof = $proof
    $receipt.device_restore_verified = $proof.restore_verified -eq $true
    if ($executionExit -ne 0 -or $proof.status -ne 'passed' -or -not $receipt.device_restore_verified) { throw 'SOC setting or restoration proof failed.' }
    $receipt.status = 'PASS'
} catch { $failure = $_ }
finally {
    if ($updated) {
        $restorePath = Join-Path $privateDirectory 'restore.private.json'
        @{ '--command' = ($previous.command -join ','); '--args' = @($previous.args) } | ConvertTo-Json -Depth 8 |
            Set-Content -LiteralPath $restorePath -Encoding utf8
        try {
            Assert-SocIdle $ProbeJobName | Out-Null
            Invoke-SocCloud @('run', 'jobs', 'update', $ProbeJobName, '--project', $project, '--region', $region, '--flags-file', $restorePath) | Out-Null
            $after = Invoke-SocCloud @('run', 'jobs', 'describe', $ProbeJobName, '--project', $project, '--region', $region, '--format', 'json') | ConvertFrom-Json -AsHashtable
            $container = $after.spec.template.spec.template.spec.containers[0]
            $restored = $container.image -eq $previous.image -and ($container.command | ConvertTo-Json -Compress) -eq ($previous.command | ConvertTo-Json -Compress) -and
                ($container.args | ConvertTo-Json -Compress) -eq ($previous.args | ConvertTo-Json -Compress)
        } catch { $restored = $false }
    }
    $receipt.job_restore_verified = $restored
    if (-not $restored) { $receipt.status = 'FAIL' }
    $receipt | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $ReceiptPath -Encoding utf8
}
if (-not $restored) { throw 'Dedicated Job restoration is unverified; inspect private evidence before another execution.' }
if ($failure) { throw $failure }
Write-Host 'Actual-server SOC setting, readback, 60-second hold, device restoration and Job restoration passed.'

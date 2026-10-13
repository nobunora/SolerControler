param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('23', '03', '07', 'settings-roundtrip', 'forecast')]
    [string]$Slot,
    [switch]$DryRun,
    [switch]$PlanRefreshOnly,
    [ValidateRange(50, 50)]
    [double]$SettingsRoundTripTargetSoc = 50,
    [switch]$TestExecution,
    [switch]$SocCycleProbe,
    [switch]$SocCycleTemporaryWindow,
    [string]$PreflightStatePath = '',
    [string]$SocCycleEvidenceDirectory = 'artifacts/deployment_state/soc-cycle',
    [string]$ExecutionReceiptPath = ''
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot
if ($ExecutionReceiptPath -and $Slot -ne 'forecast') { throw 'ExecutionReceiptPath is forecast-only.' }
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv

$projectId = Get-RequiredProductionEnv 'GCP_PROJECT_ID'
$region = Get-RequiredProductionEnv 'GCP_REGION'
$jobName = "solar-battery-$Slot"
if ($Slot -eq 'forecast') { $jobName = 'solar-forecast-daily' }
$gcloud = Join-Path $PSScriptRoot 'gcloud.ps1'

if ($SocCycleTemporaryWindow -and -not $SocCycleProbe) {
    throw 'A temporary device charge window is only permitted for the explicit SOC cycle probe.'
}

if ($SocCycleProbe) {
    if ($Slot -ne 'settings-roundtrip' -or -not $TestExecution -or $DryRun -or $PlanRefreshOnly) {
        throw 'SOC cycle probe requires -Slot settings-roundtrip -TestExecution, without dry-run or plan refresh.'
    }
    if (-not $PreflightStatePath) { throw 'SOC cycle probe requires the successful current-commit preflight state.' }
    $preflight = Get-Content -LiteralPath $PreflightStatePath -Raw | ConvertFrom-Json
    $head = (git rev-parse HEAD).Trim()
    if ($preflight.status -ne 'passed' -or $preflight.repository_commit -ne $head) {
        throw 'SOC cycle probe preflight must pass for the current commit.'
    }
    if ((git status --porcelain)) { throw 'SOC cycle probe requires a clean committed source tree.' }
    $tokyo = [System.TimeZoneInfo]::FindSystemTimeZoneById('Tokyo Standard Time')
    $nowJst = [System.TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $tokyo)
    if (($nowJst.Hour * 60 + $nowJst.Minute) -lt 435 -or ($nowJst.Hour * 60 + $nowJst.Minute) -ge 1335) {
        throw 'SOC cycle probe and restoration must fit between 07:15 and 22:30 JST.'
    }
    $described = @{}
    foreach ($name in @('solar-battery-23', 'solar-battery-03', 'solar-battery-07', $jobName)) {
        $jobText = (& $gcloud run jobs describe $name --region $region --project $projectId --format json) -join "`n"
        if ($LASTEXITCODE -ne 0) { throw 'SOC probe resource inspection failed.' }
        $described[$name] = $jobText | ConvertFrom-Json
        $executionText = (& $gcloud run jobs executions list --job $name --region $region --project $projectId --limit 10 --format json) -join "`n"
        if ($LASTEXITCODE -ne 0) { throw 'SOC probe concurrency inspection failed.' }
        foreach ($execution in @($executionText | ConvertFrom-Json)) {
            if (-not $execution.status.completionTime) { throw 'SOC probe refused while a control or probe execution is active.' }
        }
    }
    $probeSpec = $described[$jobName].spec.template.spec.template.spec
    $controlImage = [string]$described['solar-battery-03'].spec.template.spec.template.spec.containers[0].image
    $probeImage = [string]$probeSpec.containers[0].image
    if ($probeImage -ne $controlImage -or $probeImage -notmatch '@sha256:[0-9a-f]{64}$') {
        throw 'SOC cycle probe must use the identical immutable deployed control image.'
    }
    if ([int]$probeSpec.maxRetries -ne 0 -or [int]$probeSpec.timeoutSeconds -lt 900) {
        throw 'SOC cycle probe requires zero retries and at least 900 seconds task timeout.'
    }
    if ([string]$probeSpec.containers[0].command[0] -ne 'python') {
        throw 'SOC cycle probe requires the canonical Python probe entrypoint.'
    }
    New-Item -ItemType Directory -Path $SocCycleEvidenceDirectory -Force | Out-Null
    $jobText | Set-Content -LiteralPath (Join-Path $SocCycleEvidenceDirectory 'job-before.private.json') -Encoding utf8
    # Execute a reviewed diagnostic inside the existing immutable control image.
    # Canonical wrappers previously only exposed the fixed 60-second round-trip.
    # This execution override changes no Job, Scheduler, image, or stored plan.
    $sourcePath = Join-Path $PSScriptRoot 'kpnet_soc_cycle_probe.py'
    $encoder = 'import base64,pathlib,sys,zlib; print(base64.b64encode(zlib.compress(pathlib.Path(sys.argv[1]).read_bytes(),9)).decode())'
    $encoded = (& python -c $encoder $sourcePath).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $encoded) { throw 'SOC probe source encoding failed.' }
    $code = "exec(__import__('zlib').decompress(__import__('base64').b64decode('$encoded')))"
    # gcloud.cmd consumes caret delimiters on Windows. Use the documented
    # JSON flags-file path so both Python arguments survive without shell escaping.
    $flagsPath = Join-Path $SocCycleEvidenceDirectory 'execution-flags.private.json'
    $probeEnv = @{ DRY_RUN = 'false'; SOC_CYCLE_PROBE_AUTHORIZED = 'true'; SOC_CYCLE_PROBE_TEMPORARY_WINDOW = 'false' }
    if ($SocCycleTemporaryWindow) { $probeEnv['SOC_CYCLE_PROBE_TEMPORARY_WINDOW'] = 'true' }
    @{ '--args' = @('-c', $code); '--update-env-vars' = $probeEnv } |
        ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $flagsPath -Encoding utf8
    $executeArgs = @('run', 'jobs', 'execute', $jobName, '--project', $projectId, '--region', $region,
                    '--wait', '--format=json', '--flags-file', $flagsPath)
    $executionText = (& $gcloud @executeArgs) -join "`n"
    $executeExit = $LASTEXITCODE
    $executionText | Set-Content -LiteralPath (Join-Path $SocCycleEvidenceDirectory 'execution.private.json') -Encoding utf8
    if ($executeExit -ne 0) { throw 'SOC cycle execution did not pass; inspect its exact terminal audit and restoration before retrying.' }
    $execution = $executionText | ConvertFrom-Json
    foreach ($type in @('Completed', 'ResourcesAvailable', 'Started', 'ContainerReady')) {
        $condition = $execution.status.conditions | Where-Object { $_.type -eq $type } | Select-Object -First 1
        if (-not $condition -or [string]$condition.status -ne 'True') { throw "SOC cycle condition failed: $type" }
    }
    $failedCount = if ($execution.status.PSObject.Properties.Name -contains 'failedCount') { [int]$execution.status.failedCount } else { 0 }
    if ($failedCount -gt 0) { throw 'SOC cycle execution has failed tasks.' }
    $filter = "resource.type=cloud_run_job AND resource.labels.job_name=$jobName AND jsonPayload.message=soc-cycle-probe"
    $logText = (& $gcloud logging read $filter --project $projectId --limit 20 --freshness 1h --format json) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw 'SOC cycle audit retrieval failed.' }
    $logText | Set-Content -LiteralPath (Join-Path $SocCycleEvidenceDirectory 'logs.private.json') -Encoding utf8
    $exact = @($logText | ConvertFrom-Json) | Where-Object {
        $_.labels.'run.googleapis.com/execution_name' -eq $execution.metadata.name
    }
    if (@($exact).Count -ne 1) { throw 'SOC cycle requires exactly one audit from the executed task.' }
    $proof = $exact[0].jsonPayload
    if ($proof.status -ne 'passed' -or -not $proof.forced_readback_verified -or
        -not $proof.standby_readback_verified -or -not $proof.restore_verified -or
        $proof.stop_reason -ne 'target_reached' -or [double]$proof.soc_increase_percent -lt 1) {
        throw 'SOC cycle physical target/stop/restoration proof did not pass.'
    }
    $proof | Add-Member -NotePropertyName probe_source_sha256 -NotePropertyValue (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash
    $proof | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $SocCycleEvidenceDirectory 'summary.safe.json') -Encoding utf8
    Write-Host 'SOC +1 point cycle passed: forced SET/read-back, realtime target reached, standby SET/read-back, exact settings restored.'
    exit 0
}

function Assert-LatestDryRunExecution {
    param(
        [string]$JobName,
        [string]$ProjectId,
        [string]$Region
    )

    $jsonText = & $gcloud run jobs executions list --job $JobName --region $Region --project $ProjectId --limit 1 --sort-by "~createTime" --format json
    if ($LASTEXITCODE -ne 0) {
        throw "Cloud Run execution status query failed: $JobName"
    }
    $executions = @($jsonText | ConvertFrom-Json)
    if ($executions.Count -eq 0) {
        throw 'Cloud Run dry-run execution was not found.'
    }
    $status = $executions[0].status
    if (-not $status) {
        throw 'Cloud Run dry-run execution status was missing from gcloud output.'
    }
    $conditions = @($status.conditions)
    if ($conditions.Count -eq 0) {
        throw 'Cloud Run dry-run execution conditions were missing from gcloud output.'
    }
    foreach ($type in @('Completed', 'ResourcesAvailable', 'Started', 'ContainerReady')) {
        $condition = $conditions | Where-Object { $_.type -eq $type } | Select-Object -First 1
        if (-not $condition -or [string]$condition.status -ne 'True') {
            throw "Cloud Run dry-run execution condition is not ready: $type"
        }
    }
    $failedCount = if ($status.PSObject.Properties.Name -contains 'failedCount') {
        [int]$status.failedCount
    } else {
        0
    }
    if ($failedCount -gt 0) {
        throw 'Cloud Run dry-run execution reported failed tasks.'
    }
    Write-Host 'Cloud Run dry-run execution passed explicit completion and readiness checks.'
}

$arguments = @('run', 'jobs', 'execute', $jobName, '--project', $projectId, '--region', $region, '--wait')
if ($PlanRefreshOnly -and $Slot -ne '03') {
    throw '-PlanRefreshOnly requires -Slot 03.'
}
if ($Slot -eq 'settings-roundtrip' -and -not $TestExecution) {
    throw 'Live settings round-trip requires -TestExecution.'
}
if ($DryRun) {
    $arguments += @('--update-env-vars', 'DRY_RUN=true')
}
if ($PlanRefreshOnly) {
    $arguments += '--args=--plan-refresh-only'
}
if ($Slot -eq 'settings-roundtrip') {
    $arguments += @('--update-env-vars', "SETTINGS_ROUNDTRIP_TARGET_SOC=$SettingsRoundTripTargetSoc,DRY_RUN=false")
}
if ($Slot -eq 'forecast') {
    $forecastReceipt = [ordered]@{ schema_version = 1; slot = 'forecast'; status = 'running'
        requested_at = [DateTime]::UtcNow.ToString('o'); execution_name = $null; execution = $null; logs = @() }
    if ($ExecutionReceiptPath) {
        $ExecutionReceiptPath = [IO.Path]::GetFullPath($ExecutionReceiptPath, $repoRoot)
        if (Test-Path -LiteralPath $ExecutionReceiptPath) { throw 'Forecast intent already has an execution receipt; inspect it before any retry.' }
    }
    function Save-ForecastExecutionReceipt {
        if (-not $ExecutionReceiptPath) { return }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ExecutionReceiptPath) | Out-Null
        $temporaryReceipt = "$ExecutionReceiptPath.part"
        $forecastReceipt | ConvertTo-Json -Depth 40 | Set-Content -LiteralPath $temporaryReceipt -Encoding utf8
        Move-Item -LiteralPath $temporaryReceipt -Destination $ExecutionReceiptPath -Force
    }
    function Assert-ForecastReceiptTerminal([hashtable]$Execution) {
        if (-not $Execution.status -or -not $Execution.status.completionTime) { throw 'Exact forecast execution is not terminal.' }
        foreach ($type in @('Completed', 'ResourcesAvailable', 'Started', 'ContainerReady')) {
            $condition = @($Execution.status.conditions | Where-Object { $_.type -eq $type })
            if ($condition.Count -ne 1 -or [string]$condition[0].status -ne 'True') { throw 'Exact forecast execution is not complete and ready.' }
        }
        $tasks = $Execution.spec.taskCount
        $succeeded = $Execution.status.succeededCount
        if (($tasks -isnot [int] -and $tasks -isnot [long]) -or $tasks -lt 1 -or
            ($succeeded -isnot [int] -and $succeeded -isnot [long]) -or $succeeded -ne $tasks) {
            throw 'Exact forecast total-task success evidence is incomplete.'
        }
        foreach ($key in @('failedCount', 'cancelledCount', 'pendingCount', 'runningCount')) {
            if ($Execution.status.ContainsKey($key)) {
                $count = $Execution.status[$key]
                if (($count -isnot [int] -and $count -isnot [long]) -or $count -ne 0) { throw 'Exact forecast task outcome is invalid or unfinished.' }
            }
        }
    }
    Save-ForecastExecutionReceipt
    $executionText = (& $gcloud @arguments '--format=json') -join "`n"
    $executeExit = $LASTEXITCODE
    try {
        $execution = $executionText | ConvertFrom-Json -AsHashtable
        if (-not $execution.metadata.name) { throw 'Execution identity is absent.' }
        $forecastReceipt.execution_name = [string]$execution.metadata.name
        $forecastReceipt.execution = $execution
        Save-ForecastExecutionReceipt
        $exactName = ([string]$execution.metadata.name -split '/')[-1]
        $exactText = (& $gcloud run jobs executions describe $exactName --project $projectId --region $region --format json) -join "`n"
        if ($LASTEXITCODE -ne 0) { throw 'Exact forecast execution readback failed.' }
        $exact = $exactText | ConvertFrom-Json -AsHashtable
        if (([string]$exact.metadata.name -split '/')[-1] -ne $exactName) { throw 'Forecast execution identity changed.' }
        $forecastReceipt.execution = $exact
        $filter = "resource.type=cloud_run_job AND resource.labels.job_name=$jobName"
        $logsText = (& $gcloud logging read $filter --project $projectId --freshness 1d --limit 200 --format json) -join "`n"
        if ($LASTEXITCODE -ne 0) { throw 'Forecast execution logs readback failed.' }
        $forecastReceipt.logs = @($logsText | ConvertFrom-Json -AsHashtable | Where-Object {
            $_ -is [System.Collections.IDictionary] -and $_.ContainsKey('labels') -and
                $_['labels'] -is [System.Collections.IDictionary] -and
                $_['labels']['run.googleapis.com/execution_name'] -eq $exactName
        })
        Assert-ForecastReceiptTerminal $exact
        if ($executeExit -ne 0) { throw 'Exact forecast execution command did not complete successfully.' }
        $forecastReceipt.status = 'success'
        Save-ForecastExecutionReceipt
    } catch {
        $forecastReceipt.status = 'unknown_commit'
        Save-ForecastExecutionReceipt
        throw 'Forecast outcome is unresolved; inspect the private exact-execution receipt before retrying.'
    }
} else {
    & $gcloud @arguments
    if ($LASTEXITCODE -ne 0) { throw "Cloud Run Job failed: $jobName" }
}
if ($DryRun -and $Slot -ne 'forecast') {
    Assert-LatestDryRunExecution -JobName $jobName -ProjectId $projectId -Region $region
}

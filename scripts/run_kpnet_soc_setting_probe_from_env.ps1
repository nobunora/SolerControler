param(
    [Parameter(Mandatory = $true)][string]$DeploymentStatePath,
    [Parameter(Mandatory = $true)][string]$ReceiptPath,
    [ValidateSet(30, 50)][int]$TargetSoc = 30,
    [switch]$TestExecution,
    [string]$ProbeJobName = 'solar-battery-settings-roundtrip'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv
if (-not $TestExecution) { throw 'Explicit SOC mutation requires -TestExecution.' }
$state = Get-Content -Raw -LiteralPath $DeploymentStatePath | ConvertFrom-Json -AsHashtable
$commit = ([string](git rev-parse HEAD)).Trim()
if ($LASTEXITCODE -ne 0 -or $state.kind -ne 'separated_runtime_deployment' -or $state.status -ne 'complete' -or
    $state.repository_commit -ne $commit -or $state.stages.verification.status -ne 'success') {
    throw 'SOC mutation requires a verified complete deployment of the current commit.'
}
$digest = [string]$state.image_digests.control
if ($digest -notmatch '^sha256:[a-f0-9]{64}$') { throw 'Deployed control digest is missing.' }
$tokyo = [TimeZoneInfo]::FindSystemTimeZoneById('Tokyo Standard Time')
$now = [TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $tokyo)
$minutes = $now.Hour * 60 + $now.Minute
if ($minutes -lt 435 -or $minutes -ge 1350) { throw 'SOC mutation is allowed only during 07:15–22:29 JST.' }
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
}
foreach ($job in @($ProbeJobName, 'solar-battery-23', 'solar-battery-03', 'solar-battery-07')) { Assert-SocIdle $job }
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
@{ command = 'python'; args = $argsForProbe } | ConvertTo-Json -Depth 8 |
    Set-Content -LiteralPath $flagsPath -Encoding utf8
$updated = $false
$restored = $false
$failure = $null
$receipt = @{ status = 'FAIL'; repository_commit = $commit; control_digest = $digest; requested_target_soc = $TargetSoc; device_restore_verified = $false; job_restore_verified = $false }
try {
    $updated = $true
    Invoke-SocCloud @('run', 'jobs', 'update', $ProbeJobName, '--project', $project, '--region', $region, '--flags-file', $flagsPath) | Out-Null
    $configured = Invoke-SocCloud @('run', 'jobs', 'describe', $ProbeJobName, '--project', $project, '--region', $region, '--format', 'json') | ConvertFrom-Json -AsHashtable
    $actual = $configured.spec.template.spec.template.spec.containers[0]
    if ($actual.image -ne $previous.image -or ($actual.command -join ',') -ne 'python' -or
        ($actual.args | ConvertTo-Json -Compress) -ne ($argsForProbe | ConvertTo-Json -Compress)) { throw 'SOC probe entrypoint readback mismatch.' }
    $text = (& $gcloud run jobs execute $ProbeJobName --project $project --region $region --wait --format json) -join "`n"
    $executionExit = $LASTEXITCODE
    $text | Set-Content -LiteralPath (Join-Path $privateDirectory 'execution.private.json') -Encoding utf8
    $execution = $text | ConvertFrom-Json -AsHashtable
    $executionName = ([string]$execution.metadata.name -split '/')[-1]
    if (-not $executionName) { throw 'SOC probe execution identity is unknown.' }
    $records = @(Invoke-SocCloud @('logging', 'read', "resource.type=cloud_run_job AND resource.labels.job_name=$ProbeJobName",
        '--project', $project, '--freshness', '1d', '--limit', '200', '--format', 'json') | ConvertFrom-Json -AsHashtable)
    $proofs = @($records | Where-Object { $_.labels['run.googleapis.com/execution_name'] -eq $executionName -and
        $_.jsonPayload.message -eq 'post-deploy-soc-setting-probe' })
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
        @{ command = ($previous.command -join ','); args = @($previous.args) } | ConvertTo-Json -Depth 8 |
            Set-Content -LiteralPath $restorePath -Encoding utf8
        try {
            Assert-SocIdle $ProbeJobName
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

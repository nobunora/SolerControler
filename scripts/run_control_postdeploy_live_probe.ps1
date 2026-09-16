param(
    [Parameter(Mandatory = $true)]
    [string]$ExpectedCommit,
    [Parameter(Mandatory = $true)]
    [string]$ImmutableImage,
    [string]$ProbeJobName = 'solar-battery-settings-roundtrip',
    [string]$Job23Name = 'solar-battery-23',
    [string]$Job03Name = 'solar-battery-03',
    [string]$Job07Name = 'solar-battery-07'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv
Assert-ProductionEnv @('GCP_PROJECT_ID', 'GCP_REGION')

$projectId = Get-RequiredProductionEnv 'GCP_PROJECT_ID'
$region = Get-RequiredProductionEnv 'GCP_REGION'
$gcloud = Join-Path $PSScriptRoot 'gcloud.ps1'

$actualCommit = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $actualCommit -ne $ExpectedCommit) {
    throw "Post-deploy probe requires exact HEAD $ExpectedCommit; current HEAD is $actualCommit"
}
if ($ImmutableImage -notmatch '@sha256:[0-9a-f]{64}$') {
    throw "Post-deploy probe requires an immutable sha256 image: $ImmutableImage"
}

# A live settings probe intentionally changes the real battery configuration for
# exactly 60 seconds. Run only in the daytime gap between the unconditional 07
# owner and the 23 owner; never race the protected 03 control window.
$tokyo = [System.TimeZoneInfo]::FindSystemTimeZoneById('Tokyo Standard Time')
$nowJst = [System.TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $tokyo)
$minutes = ($nowJst.Hour * 60) + $nowJst.Minute
if ($minutes -lt (7 * 60 + 15) -or $minutes -ge (22 * 60 + 30)) {
    throw "Live post-deploy probe is allowed only from 07:15 through 22:29 JST; current JST=$($nowJst.ToString('yyyy-MM-dd HH:mm:ss'))"
}

function Assert-NoRunningExecution {
    param([string]$JobName)
    $json = (& $gcloud run jobs executions list --job $JobName --region $region --project $projectId --limit 5 --sort-by '~createTime' --format json) -join "`n"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect executions for $JobName"
    }
    $rows = @($json | ConvertFrom-Json)
    foreach ($row in $rows) {
        if (-not $row.status) { continue }
        # Failed executions are still completed executions. completionTime is the
        # authoritative discriminator; do not confuse an old Completed=False result
        # with a task that is still running.
        if ($row.status.completionTime) { continue }
        $conditions = @($row.status.conditions)
        $completed = $conditions | Where-Object { $_.type -eq 'Completed' } | Select-Object -First 1
        if (-not $completed -or [string]$completed.status -ne 'True') {
            throw "Refusing live probe while control execution is active: $JobName"
        }
    }
}

foreach ($jobName in @($Job23Name, $Job03Name, $Job07Name)) {
    & $gcloud run jobs describe $jobName --region $region --project $projectId | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Production control Job does not exist: $jobName"
    }
    Assert-NoRunningExecution -JobName $jobName
}

# The dedicated probe Job must already exist with the production service account,
# secrets and common environment. We update only its image/entrypoint and the two
# explicit probe env keys; no Scheduler/IAM/Secret resource is created or changed.
& $gcloud run jobs describe $ProbeJobName --region $region --project $projectId | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Dedicated post-deploy probe Job does not already exist: $ProbeJobName"
}

& $gcloud run jobs update $ProbeJobName `
    --region $region `
    --project $projectId `
    --image $ImmutableImage `
    --command python `
    --args postdeploy_probe_main.py `
    --max-retries 0 `
    --task-timeout 900 `
    --update-env-vars 'DRY_RUN=false,SETTINGS_ROUNDTRIP_TARGET_SOC=50' | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to update the dedicated post-deploy probe Job.'
}

& $gcloud run jobs execute $ProbeJobName --region $region --project $projectId --wait | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'LIVE POST-DEPLOY PROBE FAILED: CSV/plan/settings round-trip did not complete successfully.'
}

Write-Host "LIVE POST-DEPLOY PROBE PASSED for source $ExpectedCommit"
Write-Host 'Verified: KP-NET CSV download -> plan generation -> real settings SET/readback -> exact snapshot restore/readback.'

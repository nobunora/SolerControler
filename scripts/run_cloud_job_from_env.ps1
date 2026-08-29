param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('23', '03', '07', 'settings-roundtrip')]
    [string]$Slot,
    [switch]$DryRun,
    [switch]$PlanRefreshOnly,
    [ValidateRange(50, 50)]
    [double]$SettingsRoundTripTargetSoc = 50,
    [switch]$TestExecution
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv

$projectId = Get-RequiredProductionEnv 'GCP_PROJECT_ID'
$region = Get-RequiredProductionEnv 'GCP_REGION'
$jobName = "solar-battery-$Slot"
$gcloud = Join-Path $PSScriptRoot 'gcloud.ps1'

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
& $gcloud @arguments
if ($LASTEXITCODE -ne 0) { throw "Cloud Run Job failed: $jobName" }
if ($DryRun) {
    Assert-LatestDryRunExecution -JobName $jobName -ProjectId $projectId -Region $region
}

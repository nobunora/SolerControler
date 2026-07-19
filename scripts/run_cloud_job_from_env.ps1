param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('23', '03', '07')]
    [string]$Slot,
    [switch]$DryRun,
    [switch]$PlanRefreshOnly
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
$arguments = @('run', 'jobs', 'execute', $jobName, '--project', $projectId, '--region', $region, '--wait')
if ($PlanRefreshOnly -and $Slot -ne '03') {
    throw '-PlanRefreshOnly requires -Slot 03.'
}
if ($DryRun) {
    $arguments += @('--update-env-vars', 'DRY_RUN=true')
}
if ($PlanRefreshOnly) {
    $arguments += '--args=--plan-refresh-only'
}
& $gcloud @arguments
if ($LASTEXITCODE -ne 0) { throw "Cloud Run Job failed: $jobName" }

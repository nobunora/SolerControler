param(
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot

. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv

$projectId = $env:GCP_PROJECT_ID
$region = $env:GCP_REGION
$schedulerRegion = if ($env:GCP_SCHEDULER_REGION) { $env:GCP_SCHEDULER_REGION } else { $region }
$forecastJob = 'solar-forecast-daily'
$forecastScheduler = 'solar-forecast-daily-0230'
$gcloud = Join-Path $PSScriptRoot 'gcloud.ps1'

$scheduler = (& $gcloud scheduler jobs describe $forecastScheduler `
    --project $projectId --location $schedulerRegion --format json) | ConvertFrom-Json
$target = $scheduler.httpTarget
$auth = if ($target.PSObject.Properties.Name -contains 'oauthToken') {
    $target.oauthToken
} elseif ($target.PSObject.Properties.Name -contains 'oidcToken') {
    $target.oidcToken
} else {
    throw 'Forecast Scheduler has no authenticated service-account token.'
}
$serviceAccount = [string]$auth.serviceAccountEmail
if (-not $serviceAccount) {
    throw 'Forecast Scheduler service account is missing.'
}

$policy = (& $gcloud run jobs get-iam-policy $forecastJob `
    --project $projectId --region $region --format json) | ConvertFrom-Json
$bindings = if ($policy.PSObject.Properties.Name -contains 'bindings') { @($policy.bindings) } else { @() }
$members = @(
    $bindings |
        Where-Object { $_.role -eq 'roles/run.invoker' } |
        ForEach-Object { @($_.members) }
)
$member = "serviceAccount:$serviceAccount"
$present = $members -contains $member

if ($ValidateOnly) {
    if (-not $present) {
        throw 'Forecast Scheduler lacks roles/run.invoker on the forecast job.'
    }
    Write-Host 'Forecast Scheduler access validation passed. No mutation was performed.'
    return
}

if (-not $present) {
    & $gcloud run jobs add-iam-policy-binding $forecastJob `
        --project $projectId --region $region --member $member --role roles/run.invoker | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to grant forecast Scheduler run.invoker access.'
    }
}

$verified = (& $gcloud run jobs get-iam-policy $forecastJob `
    --project $projectId --region $region --format json) | ConvertFrom-Json
$verifiedBindings = if ($verified.PSObject.Properties.Name -contains 'bindings') { @($verified.bindings) } else { @() }
$verifiedMembers = @(
    $verifiedBindings |
        Where-Object { $_.role -eq 'roles/run.invoker' } |
        ForEach-Object { @($_.members) }
)
if ($verifiedMembers -notcontains $member) {
    throw 'Forecast Scheduler run.invoker access could not be verified after repair.'
}

Write-Host 'Forecast Scheduler access repair passed.'

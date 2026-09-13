param(
    [Parameter(Mandatory = $true)]
    [string]$ExpectedCommit,
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

Assert-ProductionEnv @(
    'GCP_PROJECT_ID',
    'GCP_REGION',
    'GCP_RUNNER_REPOSITORY',
    'GCP_RUNNER_IMAGE_NAME'
)

$projectId = Get-RequiredProductionEnv 'GCP_PROJECT_ID'
$region = Get-RequiredProductionEnv 'GCP_REGION'
$repository = Get-RequiredProductionEnv 'GCP_RUNNER_REPOSITORY'
$imageName = Get-RequiredProductionEnv 'GCP_RUNNER_IMAGE_NAME'
$gcloud = Join-Path $PSScriptRoot 'gcloud.ps1'

$actualCommit = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or -not $actualCommit) {
    throw 'Could not resolve the current repository commit.'
}
if ($actualCommit -ne $ExpectedCommit) {
    throw "Refusing control rollout: HEAD $actualCommit does not match expected commit $ExpectedCommit."
}
if ($actualCommit -notmatch '^[0-9a-f]{40}$') {
    throw "Refusing control rollout: invalid Git commit SHA: $actualCommit"
}

# This rollout intentionally assumes all production infrastructure already exists.
# A missing repository is a hard failure; this script never creates APIs, IAM,
# secrets, buckets, repositories, schedulers, or Jobs.
& $gcloud artifacts repositories describe $repository --location $region --project $projectId | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Artifact Registry repository does not already exist: $repository"
}

$imageTag = "$region-docker.pkg.dev/$projectId/$repository/${imageName}:git-$actualCommit"
$ignoreFile = Join-Path $repoRoot '.gcloudignore-runner'
& $gcloud builds submit `
    --config (Join-Path $repoRoot 'cloudbuild.runner.yaml') `
    --ignore-file $ignoreFile `
    --region $region `
    --project $projectId `
    --substitutions "_RUNNER_IMAGE=$imageTag" `
    $repoRoot
if ($LASTEXITCODE -ne 0) {
    throw 'Runner image build failed.'
}

$digest = ((& $gcloud artifacts docker images describe $imageTag --project $projectId --format 'value(image_summary.digest)') -join '').Trim()
if ($LASTEXITCODE -ne 0 -or $digest -notmatch '^sha256:[0-9a-f]{64}$') {
    throw "Runner image digest could not be established for $imageTag."
}
$immutableImage = "$($imageTag -replace ':git-[0-9a-f]{40}$','')@$digest"

$jobs = @($Job23Name, $Job03Name, $Job07Name)
foreach ($jobName in $jobs) {
    # Require the production Job to exist. Do not create or reconfigure it.
    & $gcloud run jobs describe $jobName --region $region --project $projectId | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Production control Job does not already exist: $jobName"
    }

    # Image is the only mutable Job field in this rollout. Existing command, args,
    # environment, secrets, service account, retry count, timeout and task settings
    # remain untouched by gcloud because they are not specified here.
    & $gcloud run jobs update $jobName --region $region --project $projectId --image $immutableImage | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to update production control Job image: $jobName"
    }
}

Write-Host "Control rollout source SHA: $actualCommit"
Write-Host "Control rollout immutable image: $immutableImage"
Write-Host 'Updated only the image field of the existing 23/03/07 control Jobs.'

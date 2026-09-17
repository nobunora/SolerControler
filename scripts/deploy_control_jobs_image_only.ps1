param(
    [Parameter(Mandatory = $true)]
    [string]$ExpectedCommit,
    [switch]$SkipBuild,
    [string]$Job23Name = 'solar-battery-23',
    [string]$Job03Name = 'solar-battery-03',
    [string]$Job07Name = 'solar-battery-07',
    [string]$ProbeJobName = 'solar-battery-settings-roundtrip'
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
$workingTree = @(git status --porcelain --untracked-files=all)
if ($LASTEXITCODE -ne 0 -or $workingTree.Count -ne 0) {
    throw 'Refusing control rollout from a dirty working tree.'
}

# A release is not considered valid until a real reversible device-setting probe
# succeeds. Restrict rollout to the daytime gap so the probe cannot race the 23,
# protected 03, or unconditional 07 owners.
$tokyo = [System.TimeZoneInfo]::FindSystemTimeZoneById('Tokyo Standard Time')
$nowJst = [System.TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $tokyo)
$minutes = ($nowJst.Hour * 60) + $nowJst.Minute
if ($minutes -lt (7 * 60 + 15) -or $minutes -ge (22 * 60 + 30)) {
    throw "Control rollout with mandatory live probe is allowed only from 07:15 through 22:29 JST; current JST=$($nowJst.ToString('yyyy-MM-dd HH:mm:ss'))"
}

# This rollout intentionally assumes all production infrastructure already exists.
# A missing repository is a hard failure; this script never creates APIs, IAM,
# secrets, buckets, repositories, schedulers, or Jobs.
& $gcloud artifacts repositories describe $repository --location $region --project $projectId | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Artifact Registry repository does not already exist: $repository"
}

$imageTag = "$region-docker.pkg.dev/$projectId/$repository/${imageName}:git-$actualCommit"
if (-not $SkipBuild) {
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
} else {
    Write-Host "Skip build (reusing exact commit image): $imageTag"
}

$digest = ((& $gcloud artifacts docker images describe $imageTag --project $projectId --format 'value(image_summary.digest)') -join '').Trim()
if ($LASTEXITCODE -ne 0 -or $digest -notmatch '^sha256:[0-9a-f]{64}$') {
    throw "Runner image digest could not be established for $imageTag."
}
$immutableImage = "$($imageTag -replace ':git-[0-9a-f]{40}$','')@$digest"

function Get-ControlJobImage {
    param([string]$JobName)
    $formats = @(
        'value(spec.template.spec.template.spec.containers[0].image)',
        'value(template.template.containers[0].image)'
    )
    foreach ($format in $formats) {
        $value = ((& $gcloud run jobs describe $JobName --region $region --project $projectId --format $format) -join '').Trim()
        if ($LASTEXITCODE -eq 0 -and $value) {
            return $value
        }
    }
    throw "Could not resolve current image for production control Job: $JobName"
}

$jobs = @($Job23Name, $Job03Name, $Job07Name)
$previousImages = @{}
foreach ($jobName in $jobs) {
    & $gcloud run jobs describe $jobName --region $region --project $projectId | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Production control Job does not already exist: $jobName"
    }
    $previousImages[$jobName] = Get-ControlJobImage -JobName $jobName
}

foreach ($jobName in $jobs) {
    # Image is the only mutable control-Job field in this rollout. Existing command,
    # args, environment, secrets, service account, retry count, timeout and task
    # settings remain untouched because they are not specified here.
    & $gcloud run jobs update $jobName --region $region --project $projectId --image $immutableImage | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to update production control Job image: $jobName"
    }
}

# Mandatory end-to-end release gate. This proves the same live dependencies that
# can otherwise fail only after release: CSV navigation/download, plan generation,
# then an actual KP-NET settings mutation/readback followed by exact snapshot restore.
$probeError = $null
try {
    & (Join-Path $PSScriptRoot 'run_control_postdeploy_live_probe.ps1') `
        -ExpectedCommit $actualCommit `
        -ImmutableImage $immutableImage `
        -ProbeJobName $ProbeJobName `
        -Job23Name $Job23Name `
        -Job03Name $Job03Name `
        -Job07Name $Job07Name
    if ($LASTEXITCODE -ne 0) {
        throw 'mandatory live probe returned a non-zero exit code'
    }
} catch {
    $probeError = $_
}

if ($null -ne $probeError) {
    $rollbackFailures = @()
    foreach ($jobName in $jobs) {
        $previousImage = [string]$previousImages[$jobName]
        & $gcloud run jobs update $jobName --region $region --project $projectId --image $previousImage | Out-Null
        if ($LASTEXITCODE -ne 0) {
            $rollbackFailures += $jobName
        }
    }
    if ($rollbackFailures.Count -gt 0) {
        throw "LIVE PROBE FAILED and rollback also failed for: $($rollbackFailures -join ', '). Original probe error: $probeError"
    }
    throw "LIVE PROBE FAILED; 23/03/07 images were rolled back to their pre-release values. Probe error: $probeError"
}

Write-Host "Control rollout source SHA: $actualCommit"
Write-Host "Control rollout immutable image: $immutableImage"
Write-Host 'Updated only the image field of the existing 23/03/07 control Jobs.'
Write-Host 'Release accepted only after the live CSV/plan/settings round-trip probe passed.'

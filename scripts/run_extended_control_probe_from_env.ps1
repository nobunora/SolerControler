param(
    [Parameter(Mandatory = $true)][string]$StatePath,
    [switch]$ChargeWindowFixture,
    [switch]$Resume
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv
Assert-ProductionEnv @('GCP_PROJECT_ID', 'GCP_REGION', 'GCP_RUNNER_REPOSITORY', 'GCP_RUNNER_IMAGE_NAME')
$commit = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or @(git status --porcelain --untracked-files=all).Count -ne 0) {
    throw 'Extended probe requires a clean committed worktree.'
}
$passedGates = @(Get-ChildItem artifacts/deployment_state/preflight-*.json | ForEach-Object {
    Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json -AsHashtable
} | Where-Object { $_['repository_commit'] -eq $commit -and $_['status'] -eq 'passed' })
if ($passedGates.Count -eq 0) { throw 'Run production_deployment_gate.ps1 -RunPreRelease for this commit first.' }
$state = @{kind='extended_control_probe'; repository_commit=$commit; status='running'; build='not_started'; probe='not_started'; charge_window_fixture=[bool]$ChargeWindowFixture}
if (Test-Path -LiteralPath $StatePath) {
    if (-not $Resume) { throw 'Existing probe state requires -Resume.' }
    $state = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json -AsHashtable
    if ($state['repository_commit'] -ne $commit) { throw 'Probe resume commit mismatch.' }
    if ([bool]$state['charge_window_fixture'] -ne [bool]$ChargeWindowFixture) { throw 'Probe resume fixture mismatch.' }
    if ($state['status'] -eq 'complete') { Write-Host 'Extended probe already complete.'; exit 0 }
    if ($state['probe'] -eq 'running' -or $state['build'] -eq 'running') {
        throw 'An interrupted stage is inconclusive; inspect its cloud terminal state before another execution.'
    }
}
$directory = Split-Path -Parent $StatePath
New-Item -ItemType Directory -Force -Path $directory | Out-Null
function Save-ProbeState { $state | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 -LiteralPath $StatePath }
$projectId = Get-RequiredProductionEnv 'GCP_PROJECT_ID'
$region = Get-RequiredProductionEnv 'GCP_REGION'
$repository = Get-RequiredProductionEnv 'GCP_RUNNER_REPOSITORY'
$imageName = Get-RequiredProductionEnv 'GCP_RUNNER_IMAGE_NAME'
$gcloud = Join-Path $PSScriptRoot 'gcloud.ps1'
# A probe-specific tag never changes runner:latest or the scheduled control Jobs.
$tag = "$region-docker.pkg.dev/$projectId/$repository/${imageName}:probe-$commit"
try {
    if ($state['build'] -ne 'success') {
        $state['build'] = 'running'; Save-ProbeState
        & $gcloud builds submit --config cloudbuild.runner.yaml --ignore-file .gcloudignore-runner `
            --region $region --project $projectId --substitutions "_RUNNER_IMAGE=$tag" $repoRoot
        if ($LASTEXITCODE -ne 0) { $state['build'] = 'failed'; throw 'Probe image build failed.' }
        $state['build'] = 'success'; Save-ProbeState
    }
    $digest = ((& $gcloud artifacts docker images describe $tag --project $projectId --format 'value(image_summary.digest)') -join '').Trim()
    if ($LASTEXITCODE -ne 0 -or $digest -notmatch '^sha256:[0-9a-f]{64}$') { throw 'Probe digest unavailable.' }
    $image = "$($tag -replace ':probe-[0-9a-f]{40}$','')@$digest"
    $state['image_digest'] = $digest
    if ($state['probe'] -ne 'success') {
        $state['probe'] = 'running'; Save-ProbeState
        & (Join-Path $PSScriptRoot 'run_control_postdeploy_live_probe.ps1') -ExpectedCommit $commit -ImmutableImage $image -ExtendedControlProbe -ChargeWindowFixture:$ChargeWindowFixture
        $state['probe'] = 'success'; Save-ProbeState
    }
    $proof = Get-Content "artifacts/deployment_state/live-proof-$commit.json" -Raw | ConvertFrom-Json -AsHashtable
    & (Join-Path $PSScriptRoot 'assert_control_probe_evidence.ps1') -Proof $proof -RequireController
    foreach ($name in @('generated_plan', 'monitor_plan')) {
        $record = $proof['controller_probe'][$name]
        $packed = [IO.MemoryStream]::new([Convert]::FromBase64String($record['raw_gzip_base64']))
        $gzip = [IO.Compression.GZipStream]::new($packed, [IO.Compression.CompressionMode]::Decompress)
        $output = [IO.MemoryStream]::new()
        try { $gzip.CopyTo($output); $raw = $output.ToArray() } finally { $gzip.Dispose(); $packed.Dispose(); $output.Dispose() }
        $sha = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($raw)).ToLowerInvariant()
        if ($sha -ne $record['detail_sha256']) { throw 'Downloaded probe original checksum mismatch.' }
        [IO.File]::WriteAllBytes((Join-Path $directory "$commit-$name.json"), $raw)
    }
    $state['local_originals'] = 'verified'
    $state['status'] = 'complete'; Save-ProbeState
    Write-Host 'Extended controller probe and local original restoration passed.'
} catch {
    $state['status'] = if ($state['probe'] -eq 'running' -or $state['build'] -eq 'running') { 'inconclusive' } else { 'failed' }
    Save-ProbeState
    throw
}

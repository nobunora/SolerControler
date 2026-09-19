param(
    [Parameter(Mandatory = $true)]
    [string]$ExpectedCommit,
    [switch]$SkipBuild,
    [switch]$AllowOutOfWindowLiveProbe,
    [string]$StatePath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot

if ($ExpectedCommit -notmatch '^[0-9a-f]{40}$') {
    throw 'ExpectedCommit must be a full lowercase Git SHA.'
}

$artifactsRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot 'artifacts'))
if (-not $StatePath) {
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    $StatePath = Join-Path $artifactsRoot "deployment_state/control-live-proof-release-$stamp.json"
} elseif (-not [IO.Path]::IsPathRooted($StatePath)) {
    $StatePath = Join-Path $repoRoot $StatePath
}
$StatePath = [IO.Path]::GetFullPath($StatePath)
if (-not $StatePath.StartsWith($artifactsRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'StatePath must remain under artifacts/deployment_state.'
}

$state = [ordered]@{
    schema_version = 1
    kind = 'control_live_proof_release'
    expected_commit = $ExpectedCommit
    started_at = (Get-Date).ToUniversalTime().ToString('o')
    completed_at = $null
    status = 'running'
    production_image_release_completed = $false
    stages = [ordered]@{}
}

function Save-State {
    $parent = Split-Path -Parent $StatePath
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $StatePath -Encoding utf8
}

function Invoke-ReleaseStage {
    param(
        [string]$Name,
        [scriptblock]$Action
    )

    $entry = [ordered]@{
        status = 'running'
        started_at = (Get-Date).ToUniversalTime().ToString('o')
        completed_at = $null
    }
    $state.stages[$Name] = $entry
    Save-State
    try {
        & $Action
        if ($LASTEXITCODE -ne 0) {
            throw "Stage returned a non-zero exit code: $Name"
        }
        $entry.status = 'success'
        $entry.completed_at = (Get-Date).ToUniversalTime().ToString('o')
        Save-State
    } catch {
        $entry.status = 'failed'
        $entry.completed_at = (Get-Date).ToUniversalTime().ToString('o')
        Save-State
        throw
    }
}

try {
    Save-State

    Invoke-ReleaseStage -Name 'pre_release_gate' -Action {
        & (Join-Path $PSScriptRoot 'production_deployment_gate.ps1') -RunPreRelease
    }

    Invoke-ReleaseStage -Name 'expected_commit' -Action {
        $actualCommit = (git rev-parse HEAD).Trim()
        if ($LASTEXITCODE -ne 0 -or $actualCommit -ne $ExpectedCommit) {
            throw "HEAD does not match ExpectedCommit: $actualCommit"
        }
    }

    Invoke-ReleaseStage -Name 'image_only_release_with_live_proof' -Action {
        $releaseArgs = @{ ExpectedCommit = $ExpectedCommit }
        if ($SkipBuild) { $releaseArgs.SkipBuild = $true }
        if ($AllowOutOfWindowLiveProbe) { $releaseArgs.AllowOutOfWindowLiveProbe = $true }
        & (Join-Path $PSScriptRoot 'deploy_control_jobs_image_only.ps1') @releaseArgs
    }
    $state.production_image_release_completed = $true
    Save-State

    Invoke-ReleaseStage -Name 'slot_07_dry_run' -Action {
        & (Join-Path $PSScriptRoot 'run_cloud_job_from_env.ps1') -Slot 07 -DryRun
    }

    $state.status = 'complete'
    $state.completed_at = (Get-Date).ToUniversalTime().ToString('o')
    Save-State
    Write-Host "Control live-proof release completed. State: $StatePath"
} catch {
    $state.status = 'failed'
    $state.completed_at = (Get-Date).ToUniversalTime().ToString('o')
    Save-State
    Write-Error "Control live-proof release failed. State: $StatePath"
    exit 1
}

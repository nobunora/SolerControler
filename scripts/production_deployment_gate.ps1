param(
    [switch]$SkipLocalBackup,
    [switch]$SkipCloudValidation,
    [switch]$RunPreRelease,
    [string]$StatePath = ""
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot

$artifactsRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot 'artifacts'))
if (-not $StatePath) {
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    $StatePath = Join-Path $artifactsRoot "deployment_state/preflight-$stamp.json"
} elseif (-not [IO.Path]::IsPathRooted($StatePath)) {
    $StatePath = Join-Path $repoRoot $StatePath
}
$StatePath = [IO.Path]::GetFullPath($StatePath)
if (-not $StatePath.StartsWith($artifactsRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "StatePath must remain under artifacts/deployment_state."
}

$state = [ordered]@{
    schema_version = 1
    kind = 'production_deployment_preflight'
    repository_commit = ((git rev-parse HEAD).Trim())
    repository_branch = ((git branch --show-current).Trim())
    started_at = (Get-Date).ToUniversalTime().ToString('o')
    updated_at = $null
    status = 'running'
    checks = [System.Collections.Generic.List[object]]::new()
}

function Save-State {
    $state.updated_at = (Get-Date).ToUniversalTime().ToString('o')
    $parent = Split-Path -Parent $StatePath
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $StatePath -Encoding utf8
}

function Get-SafeErrorDetail {
    param([System.Management.Automation.ErrorRecord]$ErrorRecord)

    $message = $ErrorRecord.Exception.Message
    if (-not $message) {
        return 'No error message was available.'
    }
    $safeLines = @(
        $message -split "`r?`n" |
            Where-Object { $_ -notmatch '(?i)password|secret|token|authorization|credential' } |
            Select-Object -First 6
    )
    if (-not $safeLines) {
        return 'Error detail was redacted.'
    }
    return (($safeLines -join ' ') -replace '\s+', ' ').Trim()
}

function Add-SkippedCheck {
    param([string]$Name, [string]$Reason)
    $state.checks.Add([ordered]@{
        name = $Name
        status = 'not_run'
        reason = $Reason
    })
    Save-State
}

function Invoke-GateCheck {
    param(
        [string]$Name,
        [scriptblock]$Action
    )
    $entry = [ordered]@{
        name = $Name
        status = 'running'
        started_at = (Get-Date).ToUniversalTime().ToString('o')
        completed_at = $null
        error_code = $null
        error_detail = $null
    }
    $state.checks.Add($entry)
    Save-State
    try {
        & $Action
        if ($LASTEXITCODE -ne 0) {
            throw "Gate command failed."
        }
        $entry.status = 'success'
        $entry.completed_at = (Get-Date).ToUniversalTime().ToString('o')
        Save-State
    } catch {
        $entry.status = 'failed'
        $entry.completed_at = (Get-Date).ToUniversalTime().ToString('o')
        $entry.error_code = 'command_failed'
        $entry.error_detail = Get-SafeErrorDetail -ErrorRecord $_
        Save-State
        throw
    }
}

try {
    Invoke-GateCheck -Name 'working_tree_clean' -Action {
        $status = @(git status --porcelain --untracked-files=all)
        if ($status.Count -ne 0) {
            throw 'Working tree is not clean.'
        }
    }

    Invoke-GateCheck -Name 'git_diff_check' -Action {
        git diff --check | Out-Null
    }

    Invoke-GateCheck -Name 'env_is_ignored_and_unstaged' -Action {
        if (-not (Test-Path -LiteralPath '.env')) {
            throw '.env is missing.'
        }
        git check-ignore --quiet .env
        if ($LASTEXITCODE -ne 0) {
            throw '.env is not ignored by Git.'
        }
        $stagedEnv = @(git diff --cached --name-only -- .env '.env.*' | Where-Object {
            $_ -and $_ -ne '.env.example'
        })
        if ($stagedEnv.Count -ne 0) {
            throw 'A credential-bearing environment file is staged.'
        }
    }

    if (-not $SkipLocalBackup) {
        Invoke-GateCheck -Name 'local_backup' -Action {
            & (Join-Path $PSScriptRoot 'backup_local.ps1') -Mode all
        }
    } else {
        Add-SkippedCheck -Name 'local_backup' -Reason 'SkipLocalBackup'
    }

    Invoke-GateCheck -Name 'security_check' -Action {
        python (Join-Path $PSScriptRoot 'security_check.py')
    }

    if (-not $SkipCloudValidation) {
        Invoke-GateCheck -Name 'production_validate_only' -Action {
            & (Join-Path $PSScriptRoot 'deploy_production_from_env.ps1') -ValidateOnly
        }
    } else {
        Add-SkippedCheck -Name 'production_validate_only' -Reason 'SkipCloudValidation'
    }

    if ($RunPreRelease) {
        Invoke-GateCheck -Name 'pre_release_integration' -Action {
            & (Join-Path $PSScriptRoot 'pre_release_integration.ps1') -SkipInstall
        }
    } else {
        Add-SkippedCheck -Name 'pre_release_integration' -Reason 'RunPreRelease was not specified'
    }

    $state.status = 'passed'
    Save-State
    Write-Host "Production deployment gate passed. State: $StatePath"
} catch {
    $state.status = 'failed'
    Save-State
    Write-Error "Production deployment gate failed. State: $StatePath"
    exit 1
}

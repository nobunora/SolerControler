param(
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-f0-9]{40}$')][string]$ExpectedCommit,
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$ApprovalNote,
    [string]$OutputDirectory = ''
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv
$arguments = @((Join-Path $PSScriptRoot 'prepare_server_adapter_fixture.py'),
    '--expected-commit', $ExpectedCommit, '--approval-note', $ApprovalNote)
if ($OutputDirectory) { $arguments += @('--output-directory', [IO.Path]::GetFullPath($OutputDirectory, $repoRoot)) }
& python @arguments
if ($LASTEXITCODE -ne 0) { throw 'Private server proof preparation failed; no cloud operation was performed.' }

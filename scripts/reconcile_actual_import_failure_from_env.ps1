param(
    [Parameter(Mandatory=$true)][ValidateSet('prepare','apply','verify')][string]$Stage,
    [Parameter(Mandatory=$true)][string]$StatePath,
    [string]$TargetDate,
    [string]$CompletedAt,
    [string]$ReviewedStateSha256,
    [string]$PreflightStatePath
)
$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv
$arguments = @('scripts/reconcile_actual_import_failure.py', '--stage', $Stage, '--state', $StatePath)
if ($TargetDate) { $arguments += @('--target-date', $TargetDate) }
if ($CompletedAt) { $arguments += @('--completed-at', $CompletedAt) }
if ($ReviewedStateSha256) { $arguments += @('--reviewed-sha', $ReviewedStateSha256) }
if ($PreflightStatePath) { $arguments += @('--preflight', $PreflightStatePath) }
python @arguments
if ($LASTEXITCODE -ne 0) { throw 'Reconciliation did not verify; inspect the state without repeating apply.' }

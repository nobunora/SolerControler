param(
    [Parameter(Mandatory=$true)][ValidateSet('prepare','apply','verify')][string]$Stage,
    [Parameter(Mandatory=$true)][string]$StatePath,
    [string]$ReviewedStateSha256='',
    [string]$PreflightStatePath=''
)
$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv
if ($Stage -eq 'apply') {
    if (-not $PreflightStatePath) { throw 'Display repair requires the successful same-commit preflight.' }
    $gate = Get-Content -Raw -LiteralPath $PreflightStatePath | ConvertFrom-Json
    $head = (git rev-parse HEAD).Trim()
    $prepared = Get-Content -Raw -LiteralPath $StatePath | ConvertFrom-Json
    if ($gate.status -ne 'passed' -or $gate.repository_commit -ne $head -or
        $prepared.prepared.commit -ne $head -or @(git status --porcelain).Count -or
        @($gate.checks | Where-Object { $_.status -ne 'success' }).Count -or -not @($gate.checks).Count) {
        throw 'Display repair requires a clean fixed commit and all mandatory checks successful.'
    }
}
$arguments = @((Join-Path $PSScriptRoot 'repair_dashboard_views.py'), '--stage', $Stage, '--state-path', $StatePath)
if ($ReviewedStateSha256) { $arguments += @('--reviewed-state-sha256', $ReviewedStateSha256) }
python @arguments
if ($LASTEXITCODE -ne 0) { throw 'Display repair is unverified. Preserve state; do not repeat a write.' }

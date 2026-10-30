param(
    [Parameter(Mandatory=$true)][ValidateSet('ValidateSeed', 'InitializeCloud', 'Report')][string]$Mode,
    [string]$StateDirectory,
    [string]$FromDate,
    [string]$ThroughDate,
    [string]$AsOf,
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv

if ($Mode -eq 'Report') {
    if (-not $FromDate -or -not $ThroughDate -or -not $AsOf -or -not $OutputPath) {
        throw 'Report requires FromDate, ThroughDate, AsOf and OutputPath.'
    }
    python scripts/report_pv_ideal_max.py --cloud --from-date $FromDate --through-date $ThroughDate --as-of $AsOf --output $OutputPath
} else {
    if (-not $StateDirectory) { throw 'Seed validation or initialization requires StateDirectory.' }
    if ($Mode -eq 'InitializeCloud') {
        python scripts/initialize_pv_ideal_max_cloud.py --state-dir $StateDirectory --publish
    } else {
        python scripts/initialize_pv_ideal_max_cloud.py --state-dir $StateDirectory
    }
}
if ($LASTEXITCODE -ne 0) { throw 'Ideal maximum operation failed; reconcile before any mutation retry.' }

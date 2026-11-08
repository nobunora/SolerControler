param(
    [Parameter(Mandatory=$true)][ValidateSet('ValidateSeed', 'InitializeCloud', 'PrepareGeometry', 'Report')][string]$Mode,
    [string]$StateDirectory,
    [string]$FromDate,
    [string]$ThroughDate,
    [string]$AsOf,
    [string]$OutputPath,
    [string]$SourceArrayConfig,
    [string]$MigrationManifest
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv

if ($Mode -eq 'PrepareGeometry') {
    if (-not $StateDirectory -or -not $SourceArrayConfig) { throw 'PrepareGeometry requires StateDirectory and SourceArrayConfig.' }
    python scripts/prepare_pv_geometry_migration.py --source-array-config $SourceArrayConfig --output $StateDirectory
} elseif ($Mode -eq 'Report') {
    if (-not $FromDate -or -not $ThroughDate -or -not $AsOf -or -not $OutputPath) {
        throw 'Report requires FromDate, ThroughDate, AsOf and OutputPath.'
    }
    python scripts/report_pv_ideal_max.py --cloud --from-date $FromDate --through-date $ThroughDate --as-of $AsOf --output $OutputPath
} else {
    if (-not $StateDirectory) { throw 'Seed validation or initialization requires StateDirectory.' }
    if ($Mode -eq 'InitializeCloud') {
        $migrationArgs = @()
        if ($MigrationManifest) { $migrationArgs = @('--migration-manifest', $MigrationManifest) }
        python scripts/initialize_pv_ideal_max_cloud.py --state-dir $StateDirectory --publish @migrationArgs
    } else {
        python scripts/initialize_pv_ideal_max_cloud.py --state-dir $StateDirectory
    }
}
if ($LASTEXITCODE -ne 0) { throw 'Ideal maximum operation failed; reconcile before any mutation retry.' }

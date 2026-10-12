param(
    [Parameter(Mandatory=$true)][string]$StatePath,
    [Parameter(Mandatory=$true)][ValidateSet('prepare','source','daily','cost','history','verify')][string]$Stage,
    [string[]]$TargetMonths = @(),
    [string[]]$CsvRunDirs = @(),
    [switch]$Write,
    [switch]$Resume,
    [string]$ReviewedStateSha256 = ''
)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv
if ($Stage -ne 'prepare' -and ($TargetMonths.Count -or $CsvRunDirs.Count)) {
    throw 'Prepared repair evidence cannot be replaced.'
}
if ($TargetMonths.Count) {
    if ($CsvRunDirs.Count) { throw 'Specify existing CSV runs or explicit month download, not both.' }
    foreach ($month in $TargetMonths) {
        if ($month -notmatch '^\d{4}-(0[1-9]|1[0-2])$') { throw 'Invalid target month.' }
    }
    $artifactRoot = if ($env:ARTIFACTS_DIR) { $env:ARTIFACTS_DIR } else { 'artifacts' }
    $before = @(Get-ChildItem -LiteralPath $artifactRoot -Directory -ErrorAction SilentlyContinue | ForEach-Object FullName)
    # The month override exists only in this child process. Never ingest raw
    # through the normal workflow while preparing the guarded repair.
    & pwsh -NoProfile -File (Join-Path $PSScriptRoot 'run_kpnet_import_from_env.ps1') -SkipFirestoreIngest -TargetMonths $TargetMonths
    if ($LASTEXITCODE -ne 0) { throw 'Historical CSV acquisition failed.' }
    $CsvRunDirs = @(Get-ChildItem -LiteralPath $artifactRoot -Directory | Where-Object {
        $_.FullName -notin $before -and (Test-Path -LiteralPath (Join-Path $_.FullName 'kpnet_summary.json'))
    } | ForEach-Object FullName)
    if ($CsvRunDirs.Count -ne 1) { throw 'CSV acquisition must produce one unambiguous new manifest.' }
}
$pythonArguments = @((Join-Path $PSScriptRoot 'repair_monitoring_source.py'), '--state-path', $StatePath, '--stage', $Stage)
if ($CsvRunDirs.Count) { $pythonArguments += '--csv-run-dirs'; $pythonArguments += $CsvRunDirs }
if ($Write) { $pythonArguments += '--write' }
if ($Resume) { $pythonArguments += '--resume' }
if ($ReviewedStateSha256) { $pythonArguments += @('--reviewed-state-sha256', $ReviewedStateSha256) }
& python @pythonArguments
if ($LASTEXITCODE -ne 0) { throw 'Monitoring repair did not verify; inspect the private journal.' }

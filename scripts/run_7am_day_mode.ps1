param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

$env:TIMEZONE = "Asia/Tokyo"
$env:KP_WORKFLOW_MODE = "settings"
$env:DRY_RUN = if ($DryRun) { "true" } else { "false" }

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCmd) {
    & $pythonCmd.Source kpnet_main.py
    exit $LASTEXITCODE
}

$pyCmd = Get-Command py -ErrorAction SilentlyContinue
if ($pyCmd) {
    & $pyCmd.Source -3 kpnet_main.py
    exit $LASTEXITCODE
}

throw "python/py not found. Check PATH settings."

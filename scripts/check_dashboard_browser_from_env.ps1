param(
    [Parameter(Mandatory = $true)][string]$InventoryPath,
    [Parameter(Mandatory = $true)][string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv
python (Join-Path $PSScriptRoot 'check_dashboard_browser.py') --inventory $InventoryPath --output-directory $OutputDirectory
if ($LASTEXITCODE -ne 0) { throw 'Browser acceptance did not pass. Read the safe receipt; do not rerun production deployment.' }

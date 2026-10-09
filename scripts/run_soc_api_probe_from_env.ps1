param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv
[void](Get-RequiredProductionEnv 'KP_SOC_API_CREDENTIALS_JSON')
python -m scripts.kpnet_soc_api_probe
if ($LASTEXITCODE -ne 0) { throw 'Read-only SOC API probe failed; no device setting was changed.' }

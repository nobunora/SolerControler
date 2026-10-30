param(
    [Parameter(Mandatory=$true)][ValidateSet('prepare','apply','verify')][string]$Stage,
    [Parameter(Mandatory=$true)][string]$StatePath,
    [string]$TargetDate='',
    [string]$ReviewedStateSha256=''
)
$ErrorActionPreference='Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv
$repairArguments=@((Join-Path $PSScriptRoot 'repair_forecast_night_projection.py'),'--stage',$Stage,'--state-path',$StatePath)
if($TargetDate){$repairArguments+=@('--target-date',$TargetDate)}
if($ReviewedStateSha256){$repairArguments+=@('--reviewed-state-sha256',$ReviewedStateSha256)}
python @repairArguments
if($LASTEXITCODE -ne 0){throw 'Forecast night projection repair did not verify; do not repeat a write.'}

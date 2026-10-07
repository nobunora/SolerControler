param(
    [string]$ProjectId = '',
    [double]$TargetArtifactRegistryMB = 500.0,
    [switch]$DryRun,
    [string]$BackupPath = '',
    [string]$StatePath = '',
    [string]$AcceptancePath = ''
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv
$configuredProject = Get-RequiredProductionEnv 'GCP_PROJECT_ID'
if ($ProjectId -and $ProjectId -ne $configuredProject) { throw 'Prune project does not match .env.' }
# HISTORICAL_FAILURE_LOCK: image age/tag count cannot prove that an image is
# unused. Never let a routine release prune before production parity, display,
# immutable recovery bytes and fresh Job/service/revision references are proved.
if (-not $BackupPath -or -not $StatePath -or -not $AcceptancePath) {
    Write-Host 'Image pruning deferred: a completed role release, display acceptance and verified recovery backup are required.'
    return
}
& (Join-Path $PSScriptRoot 'prune_separated_images_from_env.ps1') -BackupPath $BackupPath `
    -StatePath $StatePath -AcceptancePath $AcceptancePath -Apply:(!$DryRun)

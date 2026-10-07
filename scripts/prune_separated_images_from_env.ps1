param(
    [Parameter(Mandatory = $true)][string]$BackupPath,
    [Parameter(Mandatory = $true)][string]$StatePath,
    [Parameter(Mandatory = $true)][string]$AcceptancePath,
    [switch]$Apply,
    [switch]$RetireManualBackups
)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv
if ($Apply) {
    $state = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json -AsHashtable
    if ((git rev-parse HEAD).Trim() -ne $state.repository_commit -or @(git status --porcelain --untracked-files=all).Count) {
        throw 'Image deletion requires the deployed commit and a clean worktree.'
    }
}
$review = Join-Path $repoRoot "artifacts/deployment_state/prune-$([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))"
New-Item -ItemType Directory -Force -Path $review | Out-Null
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls $review /inheritance:r /grant:r "*$($sid):(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cleanup review ACL failed.' }
$gcloud = Join-Path $PSScriptRoot 'gcloud.ps1'
$env:SOLAR_BACKUP_READ_TOKEN = ((& $gcloud auth print-access-token 2>$null) -join '').Trim()
if ($LASTEXITCODE -ne 0 -or -not $env:SOLAR_BACKUP_READ_TOKEN) { throw 'Cleanup authentication failed.' }
try {
    $arguments = @('--backup', $BackupPath, '--release-state', $StatePath, '--acceptance', $AcceptancePath, '--output', $review)
    if ($Apply) { $arguments += '--apply' }
    if ($RetireManualBackups) { $arguments += '--retire-manual-backups' }
    & python (Join-Path $PSScriptRoot 'prune_separated_images.py') @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Cleanup blocked; no further resources will be deleted.' }
} finally {
    Remove-Item Env:SOLAR_BACKUP_READ_TOKEN -ErrorAction SilentlyContinue
}

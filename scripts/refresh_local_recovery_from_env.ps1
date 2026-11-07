param(
    [string]$RecoveryRoot = 'artifacts/backups/local-recovery',
    [switch]$Resume,
    [switch]$RestoreOnly,
    [switch]$BackupOnly,
    [string]$SeedPath = '',
    [int]$Port = 8780,
    [int]$EmulatorPort = 8781
)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot
if ($RestoreOnly -and $BackupOnly) { throw 'Choose RestoreOnly or BackupOnly.' }
$artifactsRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot 'artifacts'))
$recoveryPath = [IO.Path]::GetFullPath($(if ([IO.Path]::IsPathRooted($RecoveryRoot)) { $RecoveryRoot } else { Join-Path $repoRoot $RecoveryRoot }))
if (-not $recoveryPath.StartsWith($artifactsRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'RecoveryRoot must be inside this checkout artifacts directory.'
}
$cursor = $recoveryPath
while ($cursor.StartsWith($artifactsRoot, [StringComparison]::OrdinalIgnoreCase)) {
    if ((Test-Path -LiteralPath $cursor) -and ((Get-Item -LiteralPath $cursor).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Recovery directory must not traverse a junction or symbolic link.'
    }
    $cursor = [IO.Path]::GetDirectoryName($cursor)
}
New-Item -ItemType Directory -Force -Path $recoveryPath | Out-Null
$operatorSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls $recoveryPath /inheritance:r /grant:r "*$($operatorSid):(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Recovery directory ACL could not be restricted.' }
$lock = [IO.File]::Open((Join-Path $recoveryPath 'operation.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
try {
    python scripts/local_recovery.py prepare --root $recoveryPath
    if ($LASTEXITCODE -ne 0) { throw 'Recovery publication requires inspection.' }
    if (-not $RestoreOnly) {
        $pending = Join-Path $recoveryPath 'refresh.pending'
        $latest = Join-Path $recoveryPath 'latest'
        $captureWorkers = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
            $_.CommandLine -and $_.CommandLine.Contains('backup_complete_remote.py') -and
            $_.CommandLine.Replace('/', '\').Contains($pending.Replace('/', '\'))
        })
        if ($captureWorkers.Count -gt 0) { throw 'The same backup capture is still running. Do not start another execution.' }
        if ((Test-Path -LiteralPath $pending) -and -not $Resume) {
            throw 'An unfinished capture exists. Inspect its manifest, then use Resume.'
        }
        if ($Resume -and -not (Test-Path -LiteralPath $pending)) { throw 'There is no capture to resume.' }
        $capture = @{ Complete = $true; RequiredOnly = $true; ExcludeDrive = $true; ResumePath = $pending }
        if ($SeedPath) { $capture.SeedPath = $SeedPath }
        if (-not $Resume -and (Test-Path -LiteralPath $latest)) {
            python scripts/local_recovery.py verify --root $recoveryPath
            if ($LASTEXITCODE -ne 0) { throw 'Existing latest backup did not verify.' }
            $capture.SeedPath = $latest
        }
        & scripts/backup_operational_state_from_env.ps1 @capture
        if ($LASTEXITCODE -ne 0) { throw 'Capture incomplete. The previous latest remains unchanged.' }
        python scripts/backup_complete_remote.py --output $pending --verify-only
        if ($LASTEXITCODE -ne 0) { throw 'Offline capture verification failed.' }
        python scripts/local_recovery.py promote --root $recoveryPath
        if ($LASTEXITCODE -ne 0) { throw 'Latest replacement did not complete; inspect publication state.' }
    }
    if (-not $BackupOnly) {
        python scripts/local_recovery.py restore --root $recoveryPath --port $Port --emulator-port $EmulatorPort
        if ($LASTEXITCODE -ne 0) { throw 'Local recovery failed. Inspect private runtime logs.' }
        python scripts/check_local_recovery.py --root $recoveryPath --port $Port --emulator-port $EmulatorPort
        if ($LASTEXITCODE -ne 0) { throw 'Local environment is available but acceptance is incomplete. Inspect runtime/acceptance.' }
    }
} finally {
    $lock.Dispose()
}

param(
    [ValidateSet("source", "db", "all")]
    [string]$Mode = "all",
    [string]$DbPath = "artifacts/solar_monitor.db",
    [string]$OutDir = "artifacts/backups/local",
    [int]$KeepGenerations = 14
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"

function Backup-Source {
    $zipPath = Join-Path $OutDir "source-$stamp.zip"
    $tmpDir = Join-Path $env:TEMP "solar-src-$stamp"
    if (Test-Path $tmpDir) { Remove-Item -Recurse -Force $tmpDir }
    New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

    Get-ChildItem -Force -Path . | Where-Object {
        $_.Name -notin @("artifacts", "__pycache__", ".pytest_cache", ".mypy_cache", ".venv", ".git")
    } | ForEach-Object {
        Copy-Item -Recurse -Force -Path $_.FullName -Destination (Join-Path $tmpDir $_.Name)
    }

    if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
    Compress-Archive -Path (Join-Path $tmpDir "*") -DestinationPath $zipPath -CompressionLevel Optimal
    Remove-Item -Recurse -Force $tmpDir
    Write-Host "[backup] source: $zipPath"
}

function Backup-Db {
    $fullDb = Resolve-Path $DbPath -ErrorAction SilentlyContinue
    if (-not $fullDb) {
        throw "DBが見つかりません: $DbPath"
    }
    $dst = Join-Path $OutDir "solar-db-$stamp.sqlite"
    $script = @"
import sqlite3
src = r'''$($fullDb.Path)'''
dst = r'''$dst'''
conn = sqlite3.connect(src)
try:
    conn.execute("VACUUM INTO ?", (dst,))
finally:
    conn.close()
print(dst)
"@
    $tmp = New-TemporaryFile
    try {
        Set-Content -Path $tmp.FullName -Value $script -Encoding UTF8
        python $tmp.FullName
    } finally {
        Remove-Item $tmp.FullName -ErrorAction SilentlyContinue
    }
    Write-Host "[backup] db: $dst"
}

if ($Mode -in @("source", "all")) { Backup-Source }
if ($Mode -in @("db", "all")) { Backup-Db }

Get-ChildItem -Path $OutDir -File | Sort-Object LastWriteTime -Descending | Select-Object -Skip $KeepGenerations | Remove-Item -Force
Write-Host "[backup] keep generations: $KeepGenerations"

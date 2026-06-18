param(
    [string]$OutRoot = "C:\SolerControler-backups",
    [switch]$IncludeArtifacts
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stageRoot = Join-Path $env:TEMP "soler-backup-$stamp"
$stageRepoRoot = Join-Path $stageRoot "SolerControler"
$zipPath = Join-Path $OutRoot "SolerControler-source-$stamp.zip"

$excludedDirs = @(
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".vscode",
    ".idea",
    "node_modules",
    "dist",
    ".backup",
    "coverage",
    "htmlcov"
)

if (-not $IncludeArtifacts) {
    $excludedDirs += "artifacts"
}

$excludedExactNames = @(
    ".env",
    ".env.local",
    ".env.prod",
    ".env.test",
    "devserver.log",
    "devserver.err.log",
    "firebase-debug.log"
)

$excludedSuffixes = @(
    ".log",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".bak",
    ".pyc",
    ".pyo",
    ".zip",
    ".tsbuildinfo"
)

function Test-IsExcluded {
    param(
        [string]$RelativePath,
        [string]$Name
    )

    $parts = $RelativePath -split "[\\/]"
    foreach ($part in $parts) {
        if ($excludedDirs -contains $part) {
            return $true
        }
    }

    if ($excludedExactNames -contains $Name) {
        return $true
    }

    if ($Name.StartsWith(".env.") -and $Name -ne ".env.example") {
        return $true
    }

    foreach ($suffix in $excludedSuffixes) {
        if ($Name.EndsWith($suffix, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }

    return $false
}

function New-ParentDirectory {
    param([string]$Path)

    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
}

function Get-SourceFiles {
    param([string]$Path)

    $results = New-Object System.Collections.Generic.List[System.IO.FileInfo]
    try {
        $children = Get-ChildItem -LiteralPath $Path -Force -ErrorAction Stop
    } catch {
        return $results
    }

    foreach ($child in $children) {
        $relativePath = $child.FullName.Substring($repoRoot.Length).TrimStart('\', '/')
        if (Test-IsExcluded -RelativePath $relativePath -Name $child.Name) {
            continue
        }

        if ($child.PSIsContainer) {
            $childResults = Get-SourceFiles -Path $child.FullName
            foreach ($item in $childResults) {
                $results.Add($item)
            }
        } else {
            $results.Add([System.IO.FileInfo]$child)
        }
    }

    return $results
}

New-Item -ItemType Directory -Force -Path $OutRoot | Out-Null
if (Test-Path $stageRoot) {
    Remove-Item -Recurse -Force $stageRoot
}
New-Item -ItemType Directory -Force -Path $stageRepoRoot | Out-Null

$sourceFiles = Get-SourceFiles -Path $repoRoot | Sort-Object FullName

foreach ($file in $sourceFiles) {
    $relativePath = $file.FullName.Substring($repoRoot.Length).TrimStart('\', '/')
    $destination = Join-Path $stageRepoRoot $relativePath
    New-ParentDirectory -Path $destination
    Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
}

$manifest = [ordered]@{
    created_at = (Get-Date).ToString("o")
    repo_root = $repoRoot
    output_zip = $zipPath
    source_file_count = $sourceFiles.Count
    include_artifacts = [bool]$IncludeArtifacts
    excluded_dirs = $excludedDirs
    excluded_exact_names = $excludedExactNames
    excluded_suffixes = $excludedSuffixes
    source_files = @(
        foreach ($file in $sourceFiles) {
            $file.FullName.Substring($repoRoot.Length).TrimStart('\', '/')
        }
    )
}

$manifestPath = Join-Path $stageRepoRoot "backup_manifest.json"
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

if (Test-Path $zipPath) {
    Remove-Item -Force $zipPath
}

Compress-Archive -Path (Join-Path $stageRoot "*") -DestinationPath $zipPath -CompressionLevel Optimal

Remove-Item -Recurse -Force $stageRoot

Write-Host "[backup] repo root: $repoRoot"
Write-Host "[backup] output zip: $zipPath"
Write-Host "[backup] files copied: $($sourceFiles.Count)"
Write-Host "[backup] include artifacts: $([bool]$IncludeArtifacts)"

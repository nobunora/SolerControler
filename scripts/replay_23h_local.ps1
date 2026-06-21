param(
    [string]$RunId = "",
    [string]$ForecastDate = "",
    [double]$ForecastSunHours = 5.0,
    [double]$ForecastTempC = 20.0,
    [switch]$DisablePreviousDay1Forecast,
    [switch]$SkipDbPipeline
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

function Resolve-Python {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) { return $py.Source }
    $py3 = Get-Command py -ErrorAction SilentlyContinue
    if ($py3) { return "$($py3.Source) -3" }
    throw "Python not found."
}

function Get-LatestRunDir {
    param([string]$ArtifactsDir)
    $dirs = Get-ChildItem -Path $ArtifactsDir -Directory | Where-Object { $_.Name -match '^\d{8}-\d{6}$' } | Sort-Object Name -Descending
    foreach ($d in $dirs) {
        if (Test-Path (Join-Path $d.FullName "csv")) {
            return $d
        }
    }
    throw "No run directory with CSV found."
}

$artifacts = Join-Path $repoRoot "artifacts"
if (-not (Test-Path $artifacts)) {
    throw "artifacts directory not found: $artifacts"
}

$sourceRunDir = $null
if ($RunId) {
    $candidate = Join-Path $artifacts $RunId
    if (-not (Test-Path $candidate)) {
        throw "RunId not found: $RunId"
    }
    $sourceRunDir = Get-Item $candidate
} else {
    $sourceRunDir = Get-LatestRunDir -ArtifactsDir $artifacts
}

$replayRoot = Join-Path $artifacts "replay"
New-Item -ItemType Directory -Force -Path $replayRoot | Out-Null
$sourceLabel = $sourceRunDir.Name
$stamp = "$(Get-Date -Format 'yyyyMMdd-HHmmss-fff')-$sourceLabel"
$dstRoot = Join-Path $replayRoot $stamp
New-Item -ItemType Directory -Force -Path $dstRoot | Out-Null

$dstRunDir = Join-Path $dstRoot $sourceRunDir.Name
Copy-Item -Recurse -Force -Path $sourceRunDir.FullName -Destination $dstRunDir

if (-not $ForecastDate) {
    $ForecastDate = (Get-Date).AddDays(1).ToString("yyyy-MM-dd")
}

$python = Resolve-Python

Write-Host "[replay] source run: $($sourceRunDir.FullName)"
Write-Host "[replay] replay root: $dstRoot"
Write-Host "[replay] forecast: date=$ForecastDate sun_h=$ForecastSunHours temp_c=$ForecastTempC"
Write-Host "[replay] previous day1 hourly forecast: $(-not $DisablePreviousDay1Forecast)"

$env:ARTIFACTS_DIR = $dstRoot
$env:ENERGY_MODEL_CSV_DIR = (Join-Path $dstRunDir "csv")
$env:FORECAST_DATE_OVERRIDE = $ForecastDate
$env:FORECAST_SUN_HOURS_OVERRIDE = [string]$ForecastSunHours
$env:FORECAST_TEMP_C_OVERRIDE = [string]$ForecastTempC
$env:OPEN_METEO_PREVIOUS_DAY1_FORECAST_ENABLED = if ($DisablePreviousDay1Forecast) { "false" } else { "true" }

if ($python -like "* -3") {
    & py -3 energy_model_main.py
    if ($LASTEXITCODE -ne 0) { throw "energy_model_main.py failed" }
    if ($SkipDbPipeline) {
        Write-Host "[replay] db pipeline: skipped"
        Write-Host "[replay] done"
        Write-Host "[replay] plan: $(Join-Path $dstRoot 'night_charge_plan.json')"
        exit 0
    }
    $env:CLOUD_JOB_SLOT = "23"
    $env:DATA_BACKEND = "sqlite"
    $env:DATA_DB_PATH = (Join-Path $dstRoot "replay.db")
    $env:DATA_DB_WRITE_ONLY_23 = "false"
    $env:DATA_WEEKLY_BACKUP_ENABLED = "false"
    & py -3 db_pipeline_main.py
    if ($LASTEXITCODE -ne 0) { throw "db_pipeline_main.py failed" }
} else {
    & python energy_model_main.py
    if ($LASTEXITCODE -ne 0) { throw "energy_model_main.py failed" }
    if ($SkipDbPipeline) {
        Write-Host "[replay] db pipeline: skipped"
        Write-Host "[replay] done"
        Write-Host "[replay] plan: $(Join-Path $dstRoot 'night_charge_plan.json')"
        exit 0
    }
    $env:CLOUD_JOB_SLOT = "23"
    $env:DATA_BACKEND = "sqlite"
    $env:DATA_DB_PATH = (Join-Path $dstRoot "replay.db")
    $env:DATA_DB_WRITE_ONLY_23 = "false"
    $env:DATA_WEEKLY_BACKUP_ENABLED = "false"
    & python db_pipeline_main.py
    if ($LASTEXITCODE -ne 0) { throw "db_pipeline_main.py failed" }
}

Write-Host "[replay] done"
Write-Host "[replay] plan: $(Join-Path $dstRoot 'night_charge_plan.json')"
Write-Host "[replay] db:   $(Join-Path $dstRoot 'replay.db')"

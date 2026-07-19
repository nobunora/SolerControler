param(
    [string]$ProjectId = "",
    [string]$Region = "us-central1",
    [string]$SchedulerRegion = "us-central1",
    [string]$LegacyRegionToPause = "asia-northeast1",
    [string]$LegacySchedulerRegionToPause = "asia-northeast1",
    [string]$Repository = "solar-controller",
    [string]$ImageName = "runner",
    [string]$Job23Name = "solar-battery-23",
    [string]$Job03Name = "solar-battery-03",
    [string]$Job07Name = "solar-battery-07",
    [string]$SheetsJobName = "solar-sheets-export",
    [string]$SheetsSchedulerName = "solar-sheets-export-daily",
    [string]$RunServiceAccountName = "solar-battery-job-sa",
    [string]$SchedulerServiceAccountName = "solar-battery-scheduler-sa",
    [string]$UsernameSecretName = "kp-monitor-username",
    [string]$PasswordSecretName = "kp-monitor-password",
    [ValidateSet("sqlite", "postgres", "firestore")]
    [string]$DataBackend = "firestore",
    [string]$PgHost = "",
    [string]$PgPort = "5432",
    [string]$PgDatabase = "solar_ops",
    [string]$PgUser = "solar_app",
    [string]$PgPassword = "",
    [string]$PgSslMode = "prefer",
    [switch]$DisableSheetsExport,
    [switch]$DisableDriveBackup,
    [string]$SheetsSpreadsheetId = "",
    [string]$SheetsSpreadsheetTitle = "SolarController Backup",
    [string]$SheetsShareEmail = "",
    [string]$DriveBackupJobName = "solar-drive-backup",
    [string]$DriveBackupSchedulerName = "solar-drive-backup-daily",
    [string]$DriveBackupFolderId = "",
    [string]$DriveBackupSchedule = "10 1 * * *",
    [string]$NightPlanArchiveGcsPrefix = "",
    [double]$MaxArtifactRegistryMB = 500.0,
    [double]$MaxCloudBuildBucketMB = 5120.0,
    [double]$MaxAppDataBucketMB = 5120.0,
    [switch]$SkipArtifactPrune,
    [switch]$SkipCapacityCheck,
    [switch]$FailOnCapacityOverage,
    [switch]$SkipBuild,
    [switch]$RunSmokeTest,
    [switch]$Enable23Scheduler
)

$ErrorActionPreference = "Stop"

if (-not $ImageName) {
    $ImageName = "runner"
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

function Invoke-GCloud {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Args
    )
    $gcloudCmd = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
    if (-not (Test-Path $gcloudCmd)) {
        throw "gcloud.cmd not found: $gcloudCmd"
    }
    & $gcloudCmd @Args
    if ($LASTEXITCODE -ne 0) {
        throw "gcloud failed: $($Args -join ' ')"
    }
}

function Read-DotEnv {
    param([string]$Path)
    $map = @{}
    if (-not (Test-Path $Path)) {
        return $map
    }
    foreach ($line in Get-Content -Path $Path -Encoding UTF8) {
        $raw = $line.Trim()
        if (-not $raw -or $raw.StartsWith("#") -or -not $raw.Contains("=")) {
            continue
        }
        $idx = $raw.IndexOf("=")
        $k = $raw.Substring(0, $idx).Trim()
        $v = $raw.Substring($idx + 1).Trim()
        if ($v.Length -ge 2 -and (($v.StartsWith('"') -and $v.EndsWith('"')) -or ($v.StartsWith("'") -and $v.EndsWith("'")))) {
            $v = $v.Substring(1, $v.Length - 2)
        }
        $map[$k] = $v
    }
    return $map
}

function Get-MonitorCredentials {
    $envMap = Read-DotEnv -Path (Join-Path $repoRoot ".env")

    $username = ""
    $password = ""
    if ($envMap.ContainsKey("KP_MONITOR_USERNAME")) {
        $username = [string]$envMap["KP_MONITOR_USERNAME"]
    }
    if ($envMap.ContainsKey("KP_MONITOR_PASSWORD")) {
        $password = [string]$envMap["KP_MONITOR_PASSWORD"]
    }
    if (-not $username -and $env:KP_MONITOR_USERNAME) {
        $username = [string]$env:KP_MONITOR_USERNAME
    }
    if (-not $password -and $env:KP_MONITOR_PASSWORD) {
        $password = [string]$env:KP_MONITOR_PASSWORD
    }
    if ($username -and $password) {
        return @{ username = $username; password = $password }
    }

    $useHarRaw = ""
    if ($envMap.ContainsKey("KP_USE_HAR_CREDENTIALS")) {
        $useHarRaw = [string]$envMap["KP_USE_HAR_CREDENTIALS"]
    }
    $useHar = $useHarRaw.ToLowerInvariant() -in @("1", "true", "yes", "on")
    if (-not $useHar) {
        throw "KP_MONITOR_USERNAME/PASSWORD are missing in .env and HAR fallback is disabled."
    }

    if (-not $envMap.ContainsKey("KP_HAR_PATH")) {
        throw "KP_HAR_PATH is missing in .env."
    }
    $harPath = [string]$envMap["KP_HAR_PATH"]
    if (-not (Test-Path $harPath)) {
        throw "HAR file not found: $harPath"
    }

    $py = @'
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs

har_path = Path(sys.argv[1])
obj = json.loads(har_path.read_text(encoding="utf-8"))
entries = obj.get("log", {}).get("entries", [])
username = ""
password = ""
for entry in entries:
    req = entry.get("request", {})
    if req.get("method") != "POST":
        continue
    if not str(req.get("url", "")).endswith("/processLogin"):
        continue
    post_text = req.get("postData", {}).get("text", "")
    parsed = parse_qs(post_text, keep_blank_values=True)
    username = parsed.get("loginid", [""])[0]
    password = parsed.get("loginpassword", [""])[0]
    if username and password:
        break
print(json.dumps({"username": username, "password": password}, ensure_ascii=False))
'@
    $pyFile = New-TemporaryFile
    try {
        Set-Content -Path $pyFile.FullName -Value $py -Encoding UTF8
        $jsonText = & python $pyFile.FullName $harPath
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to parse credentials from HAR."
        }
    } finally {
        Remove-Item $pyFile.FullName -ErrorAction SilentlyContinue
    }
    $obj = $jsonText | ConvertFrom-Json
    if (-not $obj.username -or -not $obj.password) {
        throw "Failed to parse credentials from HAR."
    }
    return @{ username = [string]$obj.username; password = [string]$obj.password }
}

function Get-PostgresCredentials {
    param([hashtable]$EnvMap)
    $password = $PgPassword
    if (-not $password -and $EnvMap.ContainsKey("PGPASSWORD")) {
        $password = [string]$EnvMap["PGPASSWORD"]
    }
    if (-not $PgHost -and $EnvMap.ContainsKey("PGHOST")) {
        $script:PgHost = [string]$EnvMap["PGHOST"]
    }
    if (-not $PgDatabase -and $EnvMap.ContainsKey("PGDATABASE")) {
        $script:PgDatabase = [string]$EnvMap["PGDATABASE"]
    }
    if (-not $PgUser -and $EnvMap.ContainsKey("PGUSER")) {
        $script:PgUser = [string]$EnvMap["PGUSER"]
    }
    if (-not $PgPort -and $EnvMap.ContainsKey("PGPORT")) {
        $script:PgPort = [string]$EnvMap["PGPORT"]
    }
    if (-not $PgSslMode -and $EnvMap.ContainsKey("PGSSLMODE")) {
        $script:PgSslMode = [string]$EnvMap["PGSSLMODE"]
    }
    return $password
}

function Ensure-ServiceAccount {
    param([string]$AccountId, [string]$DisplayName)
    $email = "$AccountId@$ProjectId.iam.gserviceaccount.com"
    $exists = $true
    try {
        Invoke-GCloud iam service-accounts describe $email --project $ProjectId | Out-Null
    } catch {
        $exists = $false
    }
    if (-not $exists) {
        Invoke-GCloud iam service-accounts create $AccountId --display-name $DisplayName --project $ProjectId | Out-Null
    }
    return $email
}

function Pause-SchedulerIfExists {
    param([string]$Name, [string]$Location)
    $exists = $true
    try {
        Invoke-GCloud scheduler jobs describe $Name --location $Location --project $ProjectId | Out-Null
    } catch {
        $exists = $false
    }
    if (-not $exists) {
        return
    }
    try {
        Invoke-GCloud scheduler jobs pause $Name --location $Location --project $ProjectId | Out-Null
        Write-Host "Paused legacy scheduler: $Name ($Location)"
    } catch {
        Write-Warning "Failed to pause scheduler $Name ($Location): $_"
    }
}

function Resume-SchedulerIfExists {
    param([string]$Name, [string]$Location)
    try {
        Invoke-GCloud scheduler jobs resume $Name --location $Location --project $ProjectId | Out-Null
    } catch {
        Write-Warning "Scheduler not found or cannot resume: $Name ($Location)"
    }
}

function Delete-SchedulerIfExists {
    param([string]$Name, [string]$Location)
    try {
        Invoke-GCloud scheduler jobs describe $Name --location $Location --project $ProjectId | Out-Null
        Invoke-GCloud scheduler jobs delete $Name --location $Location --project $ProjectId --quiet | Out-Null
        Write-Host "Deleted scheduler: $Name ($Location)"
    } catch {
        Write-Warning "Scheduler not found or cannot delete: $Name ($Location)"
    }
}

function Delete-RunJobIfExists {
    param([string]$Name)
    try {
        Invoke-GCloud run jobs describe $Name --project $ProjectId --region $Region | Out-Null
        Invoke-GCloud run jobs delete $Name --project $ProjectId --region $Region --quiet | Out-Null
        Write-Host "Deleted Cloud Run job: $Name"
    } catch {
        Write-Warning "Cloud Run job not found or cannot delete: $Name"
    }
}

if (-not $ProjectId) {
    $ProjectId = (Invoke-GCloud config get-value project).Trim()
}
if (-not $ProjectId -or $ProjectId -eq "(unset)") {
    throw "GCP project is not set. Use -ProjectId or run gcloud config set project."
}

Write-Host "Project: $ProjectId"
Write-Host "Region: $Region"

if (-not $SkipCapacityCheck) {
    Write-Host "Pre-check storage usage against free-tier limits..."
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "check_gcp_free_tier_capacity.ps1") `
        -ProjectId $ProjectId `
        -MaxArtifactRegistryMB $MaxArtifactRegistryMB `
        -MaxCloudBuildBucketMB $MaxCloudBuildBucketMB `
        -MaxAppDataBucketMB $MaxAppDataBucketMB `
        $(if ($FailOnCapacityOverage) { "-FailOnOverage" })
}

$image = "$Region-docker.pkg.dev/$ProjectId/$Repository/${ImageName}:latest"

Write-Host "Enable required APIs..."
Invoke-GCloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com cloudscheduler.googleapis.com secretmanager.googleapis.com firestore.googleapis.com storage.googleapis.com sheets.googleapis.com drive.googleapis.com --project $ProjectId

$projectNumber = (Invoke-GCloud projects describe $ProjectId --format "value(projectNumber)").Trim()
$computeSa = "$projectNumber-compute@developer.gserviceaccount.com"
$cloudSchedulerServiceAgent = "service-$projectNumber@gcp-sa-cloudscheduler.iam.gserviceaccount.com"

$runSa = Ensure-ServiceAccount -AccountId $RunServiceAccountName -DisplayName "Solar Battery Cloud Run Job SA"
$schedulerSa = Ensure-ServiceAccount -AccountId $SchedulerServiceAccountName -DisplayName "Solar Battery Scheduler Invoker SA"

Write-Host "Grant build runtime IAM to $computeSa"
Invoke-GCloud projects add-iam-policy-binding $ProjectId --member "serviceAccount:$computeSa" --role "roles/artifactregistry.writer" | Out-Null
Invoke-GCloud iam service-accounts add-iam-policy-binding $schedulerSa --member "serviceAccount:$cloudSchedulerServiceAgent" --role "roles/iam.serviceAccountTokenCreator" --project $ProjectId | Out-Null
Invoke-GCloud projects add-iam-policy-binding $ProjectId --member "serviceAccount:$runSa" --role "roles/datastore.user" | Out-Null
Invoke-GCloud projects add-iam-policy-binding $ProjectId --member "serviceAccount:$runSa" --role "roles/serviceusage.serviceUsageConsumer" | Out-Null

if (-not $NightPlanArchiveGcsPrefix) {
    $NightPlanArchiveGcsPrefix = "gs://$ProjectId-solar-db-us/night_charge_plans"
}
$nightPlanArchiveBucket = ""
if ($NightPlanArchiveGcsPrefix -match '^gs://([^/]+)') {
    $nightPlanArchiveBucket = $Matches[1]
}
if (-not $nightPlanArchiveBucket) {
    throw "NightPlanArchiveGcsPrefix must be a gs:// URI."
}
try {
    Invoke-GCloud storage buckets describe "gs://$nightPlanArchiveBucket" --project $ProjectId | Out-Null
} catch {
    Invoke-GCloud storage buckets create "gs://$nightPlanArchiveBucket" --project $ProjectId --location "US" --uniform-bucket-level-access | Out-Null
}
Invoke-GCloud storage buckets add-iam-policy-binding "gs://$nightPlanArchiveBucket" --member "serviceAccount:$runSa" --role "roles/storage.objectAdmin" --project $ProjectId | Out-Null

Write-Host "Ensure Artifact Registry repository..."
$repoExists = $true
try {
    Invoke-GCloud artifacts repositories describe $Repository --location $Region --project $ProjectId | Out-Null
} catch {
    $repoExists = $false
}
if (-not $repoExists) {
    Invoke-GCloud artifacts repositories create $Repository --repository-format docker --location $Region --project $ProjectId
}

if (-not $SkipBuild) {
    Write-Host "Build container image..."
    Invoke-GCloud builds submit --region $Region --tag $image --project $ProjectId .
} else {
    Write-Host "Skip build (using existing image): $image"
}

Write-Host "Prepare monitor credentials..."
$envMap = Read-DotEnv -Path (Join-Path $repoRoot ".env")
$sheetsExportEnabled = -not $DisableSheetsExport.IsPresent
$sheetsIdResolved = $SheetsSpreadsheetId
if (-not $sheetsIdResolved -and $envMap.ContainsKey("SHEETS_SPREADSHEET_ID")) {
    $sheetsIdResolved = [string]$envMap["SHEETS_SPREADSHEET_ID"]
}
$sheetsShareResolved = $SheetsShareEmail
if (-not $sheetsShareResolved) {
    if ($envMap.ContainsKey("SHEETS_SHARE_EMAIL")) {
        $sheetsShareResolved = [string]$envMap["SHEETS_SHARE_EMAIL"]
    } else {
        $activeAccount = (Invoke-GCloud config get-value account).Trim()
        if ($activeAccount -and $activeAccount -ne "(unset)") {
            $sheetsShareResolved = $activeAccount
        }
    }
}
$driveBackupFolderResolved = $DriveBackupFolderId
if (-not $driveBackupFolderResolved -and $envMap.ContainsKey("DRIVE_BACKUP_FOLDER_ID")) {
    $driveBackupFolderResolved = [string]$envMap["DRIVE_BACKUP_FOLDER_ID"]
}
if (-not $DisableDriveBackup.IsPresent -and -not $driveBackupFolderResolved) {
    throw "Drive backup is enabled, but DRIVE_BACKUP_FOLDER_ID is empty. Set it in .env or use -DisableDriveBackup explicitly."
}
if ($sheetsExportEnabled -and -not $sheetsIdResolved) {
    throw "Sheets export is enabled, but SHEETS_SPREADSHEET_ID is empty. Set it in .env or use -DisableSheetsExport explicitly."
}
if ($DisableDriveBackup.IsPresent) {
    $driveBackupFolderResolved = ""
}
$usernameSecret = $UsernameSecretName
$passwordSecret = $PasswordSecretName

function Test-SecretExists {
    param([string]$SecretName)
    try {
        Invoke-GCloud secrets describe $SecretName --project $ProjectId | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Upsert-SecretVersion {
    param([string]$SecretName, [string]$SecretValue)
    $exists = $true
    try {
        Invoke-GCloud secrets describe $SecretName --project $ProjectId | Out-Null
    } catch {
        $exists = $false
    }
    if (-not $exists) {
        Invoke-GCloud secrets create $SecretName --replication-policy automatic --project $ProjectId | Out-Null
    }
    try {
        $currentValue = (Invoke-GCloud secrets versions access latest --secret $SecretName --project $ProjectId) -join "`n"
        if ($currentValue -eq $SecretValue) {
            Write-Host "Secret unchanged: $SecretName"
            return
        }
    } catch {
        Write-Warning "Could not compare latest secret version for $SecretName; adding a new version."
    }
    $tmp = New-TemporaryFile
    try {
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($tmp.FullName, $SecretValue, $utf8NoBom)
        Invoke-GCloud secrets versions add $SecretName --data-file $tmp.FullName --project $ProjectId | Out-Null
    } finally {
        Remove-Item $tmp.FullName -ErrorAction SilentlyContinue
    }
}

$creds = $null
try {
    $creds = Get-MonitorCredentials
} catch {
    if ((Test-SecretExists -SecretName $usernameSecret) -and (Test-SecretExists -SecretName $passwordSecret)) {
        Write-Warning "Monitor credentials were not found locally; reusing existing Secret Manager secrets."
    } else {
        throw
    }
}
if ($null -ne $creds) {
    Upsert-SecretVersion -SecretName $usernameSecret -SecretValue $creds.username
    Upsert-SecretVersion -SecretName $passwordSecret -SecretValue $creds.password
}

$secretEnvList = @(
    "KP_MONITOR_USERNAME=${usernameSecret}:latest",
    "KP_MONITOR_PASSWORD=${passwordSecret}:latest"
)
if ($DataBackend -eq "postgres") {
    $pgPasswordResolved = Get-PostgresCredentials -EnvMap $envMap
    if (-not $PgHost) {
        throw "DataBackend=postgres requires PgHost (or PGHOST in .env)."
    }
    if (-not $pgPasswordResolved) {
        throw "DataBackend=postgres requires PgPassword (or PGPASSWORD in .env)."
    }
    $pgPasswordSecret = "solar-pg-password"
    Upsert-SecretVersion -SecretName $pgPasswordSecret -SecretValue $pgPasswordResolved
    Invoke-GCloud secrets add-iam-policy-binding $pgPasswordSecret --member "serviceAccount:$runSa" --role "roles/secretmanager.secretAccessor" --project $ProjectId | Out-Null
    $secretEnvList += "PGPASSWORD=${pgPasswordSecret}:latest"
}

Write-Host "Grant Secret Accessor to $runSa"
Invoke-GCloud secrets add-iam-policy-binding $usernameSecret --member "serviceAccount:$runSa" --role "roles/secretmanager.secretAccessor" --project $ProjectId | Out-Null
Invoke-GCloud secrets add-iam-policy-binding $passwordSecret --member "serviceAccount:$runSa" --role "roles/secretmanager.secretAccessor" --project $ProjectId | Out-Null

$commonEnv = @(
    "TIMEZONE=Asia/Tokyo",
    "DRY_RUN=false",
    "ARTIFACTS_DIR=artifacts",
    "KP_BASE_URL=https://ctrl.kp-net.com/settingcontrol",
    "KP_USE_HAR_CREDENTIALS=false",
    "KP_WORKFLOW_MODE=settings",
    "KP_SETTINGS_SEQUENCE=forced-only",
    "KP_FORCE_SETTINGS_PROFILE=auto",
    "KP_DYNAMIC_FORCED_PROFILE=true",
    "KP_DYNAMIC_MODE_SWITCH_BY_TIME=true",
    "KP_OPERATION_CONDITIONS_PATH=config/operation_conditions.json",
    "KP_NIGHT_PLAN_PATH=artifacts/night_charge_plan.json",
    "KP_DEFAULT_CHARGE_POWER_KW=4.0",
    "KP_GREEN_MODE_MAX_CHARGE_PERCENT=50",
    "KP_NIGHT_CHARGE_WINDOW_START=23:00",
    "KP_NIGHT_CHARGE_WINDOW_END=07:00",
    "KP_DAY_DISCHARGE_WINDOW_START=07:00",
    "KP_DAY_DISCHARGE_WINDOW_END=23:00",
    "KP_DOWNLOAD_LATEST_MONTH=true",
    "KP_TIMEOUT_SEC=60",
    "FORECAST_LATITUDE=35.67452",
    "FORECAST_LONGITUDE=139.48216",
    "PV_ARRAY_FORECAST_ENABLED=true",
    "PV_ARRAY_CONFIG_PATH=config/pv_arrays.json",
    "PV_ARRAY_CALIBRATION_LOOKBACK_DAYS=45",
    "PV_ARRAY_CALIBRATION_MIN_DAYS=3",
    "PV_ARRAY_CALIBRATION_MIN_FACTOR=0.2",
    "PV_ARRAY_CALIBRATION_MAX_FACTOR=5.0",
    "PV_ARRAY_WEATHER_CALIBRATION_ENABLED=true",
    "PV_ARRAY_WEATHER_CALIBRATION_MIN_DAYS=2",
    "PV_ARRAY_WEATHER_ADJUSTMENT_MIN_RATIO=0.7",
    "PV_ARRAY_WEATHER_ADJUSTMENT_MAX_RATIO=1.3",
    "PV_ARRAY_WEATHER_REGRESSION_ENABLED=true",
    "PV_ARRAY_WEATHER_REGRESSION_MIN_DAYS=7",
    "PV_ARRAY_WEATHER_REGRESSION_BLEND=0.1",
    "PV_ARRAY_WEATHER_REGRESSION_RIDGE=0.01",
    "HOURLY_WEATHER_PV_SHAPE_ENABLED=true",
    "HOURLY_WEATHER_PV_SHAPE_BLEND=0.75",
    "HOURLY_WEATHER_RAIN_PROBABILITY_THRESHOLD=70",
    "HOURLY_WEATHER_RAIN_MM_THRESHOLD=0.1",
    "HOURLY_WEATHER_LOW_SHORTWAVE_W_M2=120",
    "PHYSICAL_PV_FORECAST_ENABLED=true",
    "PHYSICAL_PV_ROOF_PITCH_DEG=21.8014",
    "PHYSICAL_PV_RADIATION_SCALE=0",
    "PHYSICAL_PV_PANEL_WEIGHT_EAST=1.0",
    "PHYSICAL_PV_PANEL_WEIGHT_SOUTH=1.0",
    "PHYSICAL_PV_PANEL_WEIGHT_WEST=1.0",
    "PHYSICAL_PV_MIN_SHORTWAVE_HOURS=4",
    "PHYSICAL_PV_GLOBAL_MIN_DAYS=5",
    "PHYSICAL_PV_DAYPART_MIN_SAMPLES=20",
    "PHYSICAL_PV_BIN_MIN_SAMPLES=30",
    "PHYSICAL_PV_SCALE_MIN=0.5",
    "PHYSICAL_PV_SCALE_MAX=1.8",
    "PHYSICAL_PV_MAX_SHORTWAVE_RATIO=1.2",
    "PHYSICAL_PV_RETIRE_EXISTING_MIN_DAYS=21",
    "DAYTIME_SOC_COST_OPTIMIZATION_ENABLED=true",
    "SOC_COST_DAY_BUY_RATE_YEN_PER_KWH=39.10",
    "SOC_COST_NIGHT_RATE_YEN_PER_KWH=28.85",
    "SOC_COST_SELL_VALUE_RATIO=0.75",
    "SOC_COST_DAY_BUY_PENALTY_FACTOR=1.0",
    "SOC_COST_OPT_STEP_PERCENT=1.0",
    "SOC_COST_RESPECT_MORNING_HEADROOM_CAP=true",
    "SOC_OBJECTIVE_MODE=tiered_expected_net_cost",
    "SOC_TIERED_DAY_BUY_COST_ENABLED=true",
    "SOC_EXPORT_VALUE_MODE=penalty",
    "SOC_EXPORT_PENALTY_YEN_PER_KWH=",
    "SOC_SELL_REVENUE_YEN_PER_KWH=0",
    "SOC_MONTHLY_TIER_LANDING_ENABLED=true",
    "SOC_MONTHLY_TIER_CLOSE_DAY=14",
    "SOC_MONTHLY_TIER_RECENT_DAYS=7",
    "SOC_EXPECTED_REST_OF_MONTH_DAY_BUY_KWH=",
    "SOC_TIER1_UNDERUSE_PENALTY_YEN_PER_KWH=0.2",
    "SOC_TIER1_CROSSING_PENALTY_YEN_PER_KWH=30",
    "SOC_TIER2_EXTRA_PENALTY_YEN_PER_KWH=8",
    "SOC_TIER3_EXTRA_PENALTY_YEN_PER_KWH=20",
    "DAYTIME_NET_SURPLUS_HEADROOM_GUARD_ENABLED=true",
    "DAYTIME_NET_SURPLUS_HEADROOM_MIN_KWH=1.0",
    "DAYTIME_NET_SURPLUS_HEADROOM_RATIO=0.65",
    "DAYTIME_NET_SURPLUS_HEADROOM_MAX_KWH=6.0",
    "DAYTIME_NET_SURPLUS_HEADROOM_MIN_SOLAR_SHARE=0.55",
    "DAYTIME_NET_SURPLUS_HEADROOM_RAIN_RELAX_HOURS=7",
    "DAYTIME_NET_SURPLUS_HEADROOM_LOW_SHORTWAVE_RELAX_HOURS=5",
    "PV_FORECAST_ERROR_MIN_SAMPLE_DAYS=5",
    "PV_FORECAST_ERROR_RATIO_MEAN=1.0",
    "PV_FORECAST_ERROR_RATIO_STD=0.30",
    "PV_CHARGE_OPPORTUNITY_FLOOR_RATIO=0.30",
    "PV_CHARGE_OPPORTUNITY_FLOOR_MIN_PV_KWH=3.0",
    "DAYTIME_PV_HEADROOM_CAP_ENABLED=true",
    "DAYTIME_PV_HEADROOM_CAP_MIN_KWH=0.5",
    "NIGHT_RESERVE_SOC_PERCENT=30",
    "WEATHER_ARCHIVE_CHUNK_DAYS=14",
    "EVENING_LOAD_TEMPERATURE_MIN_EFFECTIVE_SAMPLES=5",
    "LOAD_TEMPERATURE_HIGH_FLOOR_ENABLED=true",
    "LOAD_TEMPERATURE_HIGH_CDH28_THRESHOLD=10",
    "LOAD_TEMPERATURE_HIGH_MAX_C=32",
    "LOAD_COMFORT_MODEL_ENABLED=true",
    "LOAD_COMFORT_MODEL_MIN_SAMPLES=336",
    "CONSUMPTION_MODEL_MIN_TRAINING_DAYS=45",
    "CONSUMPTION_MODEL_FALLBACK_WINDOW_DAYS=14",
    "OCCUPANCY_SCHEDULE_ENABLED=true",
    "OCCUPANCY_SCHEDULE_TAB=occupancy_schedule",
    "OCCUPANCY_AWAY_DEFAULT_FACTOR=0.25",
    "BATTERY_CYCLE_COUNT=0",
    "KP_ENFORCE_HTTPS=true",
    "KP_ALLOWED_HOSTS=ctrl.kp-net.com",
    "DATA_BACKEND=$DataBackend",
    "DATA_DB_PATH=artifacts/solar_monitor.db",
    "DATA_DB_SYNC_ENABLED=false",
    "DATA_DB_WRITE_ONLY_23=false",
    "DATA_WEEKLY_BACKUP_ENABLED=true",
    "DATA_WEEKLY_BACKUP_WEEKDAY=5",
    "DATA_WEEKLY_BACKUP_DIR=artifacts/backups/weekly",
    "NIGHT_PLAN_ARCHIVE_GCS_PREFIX=$NightPlanArchiveGcsPrefix",
    "NIGHT_PLAN_FIRESTORE_INLINE_DETAIL_DAYS=0",
    "DAY_RATE_YEN_PER_KWH=31",
    "COST_TARIFF_MODE=night8_tiered",
    "NIGHT8_DAY_START_HHMM=07:00",
    "NIGHT8_DAY_END_HHMM=23:00",
    "NIGHT8_DAY_TIER1_UPPER_KWH=90",
    "NIGHT8_DAY_TIER2_UPPER_KWH=230",
    "NIGHT8_DAY_RATE_TIER1_YEN=31.80",
    "NIGHT8_DAY_RATE_TIER2_YEN=39.10",
    "NIGHT8_DAY_RATE_TIER3_YEN=43.62",
    "NIGHT8_NIGHT_RATE_YEN=28.85",
    "NIGHT23_SETTINGS_PROFILE=standby",
    "SHEETS_EXPORT_ENABLED=$([string](-not $DisableSheetsExport.IsPresent).ToString().ToLowerInvariant())",
    "SHEETS_EXPORT_SLOT_ONLY=03",
    "SHEETS_EXPORT_TIMEZONE=Asia/Tokyo",
    "SHEETS_SPREADSHEET_ID=$sheetsIdResolved",
    "SHEETS_SPREADSHEET_TITLE=$SheetsSpreadsheetTitle",
    "SHEETS_SHARE_EMAIL=$sheetsShareResolved",
    "DRIVE_BACKUP_FOLDER_ID=$driveBackupFolderResolved",
    "DRIVE_BACKUP_MODE=data"
)
$backendEnv = @()
if ($DataBackend -eq "postgres") {
    $backendEnv = @(
        "PGHOST=$PgHost",
        "PGPORT=$PgPort",
        "PGDATABASE=$PgDatabase",
        "PGUSER=$PgUser",
        "PGSSLMODE=$PgSslMode"
    )
}
if ($DataBackend -eq "firestore") {
    $backendEnv += @(
        "FIRESTORE_PROJECT_ID=$ProjectId",
        "FIRESTORE_DATABASE_ID=(default)"
    )
}
$commonEnv += $backendEnv
$commonEnvArg = [string]::Join(",", $commonEnv)
$secretEnvArg = [string]::Join(",", $secretEnvList)

Write-Host "Deploy Cloud Run jobs..."
Invoke-GCloud run jobs deploy $Job23Name --project $ProjectId --region $Region --image $image --service-account $runSa --task-timeout 1800 --max-retries 1 --set-env-vars "$commonEnvArg,CLOUD_JOB_SLOT=23,SHEETS_EXPORT_ENABLED=false" --set-secrets $secretEnvArg
Invoke-GCloud run jobs deploy $Job03Name --project $ProjectId --region $Region --image $image --service-account $runSa --task-timeout 27000 --max-retries 1 --set-env-vars "$commonEnvArg,CLOUD_JOB_SLOT=03,ADJUST03_REGENERATE_PLAN=true,ADJUST03_SUN_EPSILON_H=0.05,ADJUST03_TEMP_EPSILON_C=0.2,ADJUST03_SOC_EPSILON_PERCENT=1.0,ADJUST03_KWH_EPSILON=0.2,ADJUST03_MIN_TARGET_SOC_PERCENT=30,ADJUST03_FORCE_CHARGE_RATE_FALLBACK_PERCENT_PER_HOUR=40,ADJUST03_FORCE_CHARGE_RATE_MIN_PERCENT_PER_HOUR=25,ADJUST03_FORCE_CHARGE_RATE_MAX_PERCENT_PER_HOUR=50,ADJUST03_FORCE_MONITOR_POLL_SECONDS=180,ADJUST03_FORCE_STOP_SOC_MARGIN_PERCENT=1.0,ADJUST03_COMPLETION_CONFIRM_BEFORE_MINUTES=5,ADJUST03_FORCE_MONITOR_CUTOFF_HHMM=07:00,ADJUST03_POST_CHARGE_HOLD_PROFILE=standby" --set-secrets $secretEnvArg
Invoke-GCloud run jobs deploy $Job07Name --project $ProjectId --region $Region --image $image --service-account $runSa --task-timeout 1800 --max-retries 1 --set-env-vars "$commonEnvArg,CLOUD_JOB_SLOT=07" --set-secrets $secretEnvArg

Write-Host "Grant run.invoker to scheduler service account..."
Invoke-GCloud run jobs add-iam-policy-binding $Job23Name --project $ProjectId --region $Region --member "serviceAccount:$schedulerSa" --role "roles/run.invoker" | Out-Null
Invoke-GCloud run jobs add-iam-policy-binding $Job03Name --project $ProjectId --region $Region --member "serviceAccount:$schedulerSa" --role "roles/run.invoker" | Out-Null
Invoke-GCloud run jobs add-iam-policy-binding $Job07Name --project $ProjectId --region $Region --member "serviceAccount:$schedulerSa" --role "roles/run.invoker" | Out-Null

function Upsert-SchedulerRunJob {
    param(
        [string]$SchedulerName,
        [string]$Schedule,
        [string]$TargetJobName
    )
    $uri = "https://run.googleapis.com/v2/projects/$ProjectId/locations/$Region/jobs/${TargetJobName}:run"
    $exists = $true
    try {
        Invoke-GCloud scheduler jobs describe $SchedulerName --location $SchedulerRegion --project $ProjectId | Out-Null
    } catch {
        $exists = $false
    }

    if ($exists) {
        Invoke-GCloud scheduler jobs update http $SchedulerName --location $SchedulerRegion --project $ProjectId --schedule $Schedule --time-zone "Asia/Tokyo" --uri $uri --http-method POST --oauth-service-account-email $schedulerSa | Out-Null
    } else {
        Invoke-GCloud scheduler jobs create http $SchedulerName --location $SchedulerRegion --project $ProjectId --schedule $Schedule --time-zone "Asia/Tokyo" --uri $uri --http-method POST --oauth-service-account-email $schedulerSa | Out-Null
    }
}

Write-Host "Create or update Cloud Scheduler jobs..."
Upsert-SchedulerRunJob -SchedulerName "solar-battery-run-23" -Schedule "0 23 * * *" -TargetJobName $Job23Name
Upsert-SchedulerRunJob -SchedulerName "solar-battery-run-03" -Schedule "0 4 * * *" -TargetJobName $Job03Name
Upsert-SchedulerRunJob -SchedulerName "solar-battery-run-07" -Schedule "0 7 * * *" -TargetJobName $Job07Name
Delete-SchedulerIfExists -Name $SheetsSchedulerName -Location $SchedulerRegion
Delete-SchedulerIfExists -Name $DriveBackupSchedulerName -Location $SchedulerRegion
Delete-RunJobIfExists -Name $SheetsJobName
Delete-RunJobIfExists -Name $DriveBackupJobName

Write-Host "Keep 23:00 scheduler enabled for battery mode control."
Resume-SchedulerIfExists -Name "solar-battery-run-23" -Location $SchedulerRegion

if ($LegacySchedulerRegionToPause -and ($LegacySchedulerRegionToPause -ne $SchedulerRegion)) {
    Write-Host "Pause legacy Tokyo schedulers (keep resources, stop execution)..."
    Pause-SchedulerIfExists -Name "solar-battery-run-23" -Location $LegacySchedulerRegionToPause
    Pause-SchedulerIfExists -Name "solar-battery-run-03" -Location $LegacySchedulerRegionToPause
    Pause-SchedulerIfExists -Name "solar-battery-run-07" -Location $LegacySchedulerRegionToPause
}

if ($RunSmokeTest) {
    Write-Host "Run smoke test (07 job with DRY_RUN=true)..."
    Invoke-GCloud run jobs execute $Job07Name --region $Region --project $ProjectId --wait --update-env-vars DRY_RUN=true
}

Write-Host ""
Write-Host "Done."
Write-Host "Image: $image"
Write-Host "Jobs: $Job23Name (23:00 mode control), $Job03Name (04:00 night controller + export/backup), $Job07Name (07:00)"
Write-Host "Schedulers: solar-battery-run-23, solar-battery-run-03, solar-battery-run-07"
Write-Host "Sheets export: integrated into $Job03Name"
Write-Host "Drive backup: integrated into $Job03Name"
Write-Host "Drive backup folder: $driveBackupFolderResolved"

if (-not $SkipArtifactPrune) {
    Write-Host ""
    Write-Host "Prune old Artifact Registry digests..."
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "prune_artifact_registry.ps1") `
        -ProjectId $ProjectId `
        -TargetArtifactRegistryMB $MaxArtifactRegistryMB
}

if (-not $SkipCapacityCheck) {
    Write-Host ""
    Write-Host "Post-check storage usage against free-tier limits..."
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "check_gcp_free_tier_capacity.ps1") `
        -ProjectId $ProjectId `
        -MaxArtifactRegistryMB $MaxArtifactRegistryMB `
        -MaxCloudBuildBucketMB $MaxCloudBuildBucketMB `
        -MaxAppDataBucketMB $MaxAppDataBucketMB `
        $(if ($FailOnCapacityOverage) { "-FailOnOverage" })
}

param(
    [string]$ProjectId = "",
    [string]$Region = "us-central1",
    [string]$SchedulerRegion = "us-central1",
    [string]$LegacyRegionToPause = "asia-northeast1",
    [string]$LegacySchedulerRegionToPause = "asia-northeast1",
    [string]$Repository = "solar-controller",
    [string]$ImageName = "runner",
    [string]$Job23Name = "solar-battery-23",
    [string]$Job07Name = "solar-battery-07",
    [string]$RunServiceAccountName = "solar-battery-job-sa",
    [string]$SchedulerServiceAccountName = "solar-battery-scheduler-sa",
    [ValidateSet("sqlite", "postgres", "firestore")]
    [string]$DataBackend = "firestore",
    [string]$PgHost = "",
    [string]$PgPort = "5432",
    [string]$PgDatabase = "solar_ops",
    [string]$PgUser = "solar_app",
    [string]$PgPassword = "",
    [string]$PgSslMode = "prefer",
    [switch]$SkipBuild,
    [switch]$RunSmokeTest
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

if (-not $ProjectId) {
    $ProjectId = (Invoke-GCloud config get-value project).Trim()
}
if (-not $ProjectId -or $ProjectId -eq "(unset)") {
    throw "GCP project is not set. Use -ProjectId or run gcloud config set project."
}

Write-Host "Project: $ProjectId"
Write-Host "Region: $Region"

$image = "$Region-docker.pkg.dev/$ProjectId/$Repository/${ImageName}:latest"

Write-Host "Enable required APIs..."
Invoke-GCloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com cloudscheduler.googleapis.com secretmanager.googleapis.com firestore.googleapis.com --project $ProjectId

$projectNumber = (Invoke-GCloud projects describe $ProjectId --format "value(projectNumber)").Trim()
$computeSa = "$projectNumber-compute@developer.gserviceaccount.com"
$cloudSchedulerServiceAgent = "service-$projectNumber@gcp-sa-cloudscheduler.iam.gserviceaccount.com"

$runSa = Ensure-ServiceAccount -AccountId $RunServiceAccountName -DisplayName "Solar Battery Cloud Run Job SA"
$schedulerSa = Ensure-ServiceAccount -AccountId $SchedulerServiceAccountName -DisplayName "Solar Battery Scheduler Invoker SA"

Write-Host "Grant build runtime IAM to $computeSa"
Invoke-GCloud projects add-iam-policy-binding $ProjectId --member "serviceAccount:$computeSa" --role "roles/artifactregistry.writer" | Out-Null
Invoke-GCloud iam service-accounts add-iam-policy-binding $schedulerSa --member "serviceAccount:$cloudSchedulerServiceAgent" --role "roles/iam.serviceAccountTokenCreator" --project $ProjectId | Out-Null
Invoke-GCloud projects add-iam-policy-binding $ProjectId --member "serviceAccount:$runSa" --role "roles/datastore.user" | Out-Null

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
    Invoke-GCloud builds submit --tag $image --project $ProjectId .
} else {
    Write-Host "Skip build (using existing image): $image"
}

Write-Host "Prepare monitor credentials..."
$envMap = Read-DotEnv -Path (Join-Path $repoRoot ".env")
$creds = Get-MonitorCredentials
$usernameSecret = "kp-monitor-username"
$passwordSecret = "kp-monitor-password"

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
    $tmp = New-TemporaryFile
    try {
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($tmp.FullName, $SecretValue, $utf8NoBom)
        Invoke-GCloud secrets versions add $SecretName --data-file $tmp.FullName --project $ProjectId | Out-Null
    } finally {
        Remove-Item $tmp.FullName -ErrorAction SilentlyContinue
    }
}

Upsert-SecretVersion -SecretName $usernameSecret -SecretValue $creds.username
Upsert-SecretVersion -SecretName $passwordSecret -SecretValue $creds.password

$secretEnvList = @(
    "KP_MONITOR_USERNAME=${usernameSecret}:latest",
    "KP_MONITOR_PASSWORD=${passwordSecret}:latest"
)
if ($DataBackend -eq "postgres") {
    $pgPasswordResolved = Get-PostgresCredentials -EnvMap $envMap
    if (-not $PgHost) {
        throw "DataBackend=postgres では PgHost (または .env の PGHOST) が必要です。"
    }
    if (-not $pgPasswordResolved) {
        throw "DataBackend=postgres では PgPassword (または .env の PGPASSWORD) が必要です。"
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
    "KP_NIGHT_PLAN_PATH=artifacts/night_charge_plan.json",
    "KP_DEFAULT_CHARGE_POWER_KW=1.8",
    "KP_NIGHT_CHARGE_WINDOW_START=23:00",
    "KP_NIGHT_CHARGE_WINDOW_END=07:00",
    "KP_DAY_DISCHARGE_WINDOW_START=07:00",
    "KP_DAY_DISCHARGE_WINDOW_END=23:00",
    "KP_DOWNLOAD_LATEST_MONTH=true",
    "KP_TIMEOUT_SEC=60",
    "FORECAST_LATITUDE=35.67452",
    "FORECAST_LONGITUDE=139.48216",
    "NIGHT_RESERVE_SOC_PERCENT=10",
    "BATTERY_CYCLE_COUNT=0",
    "KP_ENFORCE_HTTPS=true",
    "KP_ALLOWED_HOSTS=ctrl.kp-net.com",
    "DATA_BACKEND=$DataBackend",
    "DATA_DB_PATH=artifacts/solar_monitor.db",
    "DATA_DB_SYNC_ENABLED=false",
    "DATA_DB_WRITE_ONLY_23=true",
    "DATA_WEEKLY_BACKUP_ENABLED=true",
    "DATA_WEEKLY_BACKUP_WEEKDAY=5",
    "DATA_WEEKLY_BACKUP_DIR=artifacts/backups/weekly",
    "DAY_RATE_YEN_PER_KWH=31",
    "COST_TARIFF_MODE=night8_tiered",
    "NIGHT8_DAY_START_HHMM=07:00",
    "NIGHT8_DAY_END_HHMM=23:00",
    "NIGHT8_DAY_TIER1_UPPER_KWH=90",
    "NIGHT8_DAY_TIER2_UPPER_KWH=230",
    "NIGHT8_DAY_RATE_TIER1_YEN=31.80",
    "NIGHT8_DAY_RATE_TIER2_YEN=39.10",
    "NIGHT8_DAY_RATE_TIER3_YEN=43.62",
    "NIGHT8_NIGHT_RATE_YEN=28.85"
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
Invoke-GCloud run jobs deploy $Job23Name --project $ProjectId --region $Region --image $image --service-account $runSa --task-timeout 1800 --max-retries 1 --set-env-vars "$commonEnvArg,CLOUD_JOB_SLOT=23" --set-secrets $secretEnvArg
Invoke-GCloud run jobs deploy $Job07Name --project $ProjectId --region $Region --image $image --service-account $runSa --task-timeout 1800 --max-retries 1 --set-env-vars "$commonEnvArg,CLOUD_JOB_SLOT=07" --set-secrets $secretEnvArg

Write-Host "Grant run.invoker to scheduler service account..."
Invoke-GCloud run jobs add-iam-policy-binding $Job23Name --project $ProjectId --region $Region --member "serviceAccount:$schedulerSa" --role "roles/run.invoker" | Out-Null
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
Upsert-SchedulerRunJob -SchedulerName "solar-battery-run-07" -Schedule "0 7 * * *" -TargetJobName $Job07Name

if ($LegacySchedulerRegionToPause -and ($LegacySchedulerRegionToPause -ne $SchedulerRegion)) {
    Write-Host "Pause legacy Tokyo schedulers (keep resources, stop execution)..."
    Pause-SchedulerIfExists -Name "solar-battery-run-23" -Location $LegacySchedulerRegionToPause
    Pause-SchedulerIfExists -Name "solar-battery-run-07" -Location $LegacySchedulerRegionToPause
}

if ($RunSmokeTest) {
    Write-Host "Run smoke test (07 job with DRY_RUN=true)..."
    Invoke-GCloud run jobs execute $Job07Name --region $Region --project $ProjectId --wait --update-env-vars DRY_RUN=true
}

Write-Host ""
Write-Host "Done."
Write-Host "Image: $image"
Write-Host "Jobs: $Job23Name (23:00), $Job07Name (07:00)"
Write-Host "Schedulers: solar-battery-run-23, solar-battery-run-07"

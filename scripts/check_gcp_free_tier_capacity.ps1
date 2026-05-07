param(
    [string]$ProjectId = "",
    [double]$MaxArtifactRegistryMB = 500.0,
    [double]$MaxCloudBuildBucketMB = 5120.0,
    [double]$MaxAppDataBucketMB = 5120.0,
    [switch]$FailOnOverage
)

$ErrorActionPreference = "Stop"

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

if (-not $ProjectId) {
    $ProjectId = (Invoke-GCloud config get-value project).Trim()
}
if (-not $ProjectId -or $ProjectId -eq "(unset)") {
    throw "GCP project is not set. Use -ProjectId or run gcloud config set project."
}

function To-MB([double]$bytes) {
    return [math]::Round(($bytes / 1024.0 / 1024.0), 3)
}

$result = [ordered]@{
    project_id = $ProjectId
    timestamp_utc = (Get-Date).ToUniversalTime().ToString("o")
    artifact_registry_total_mb = 0.0
    artifact_registry_active_image_total_mb = 0.0
    artifact_registry_repositories = @()
    cloudbuild_bucket_mb = 0.0
    app_data_bucket_mb = 0.0
    limits_mb = @{
        artifact_registry = $MaxArtifactRegistryMB
        cloudbuild_bucket = $MaxCloudBuildBucketMB
        app_data_bucket = $MaxAppDataBucketMB
    }
    overage = @()
}

# Artifact Registry usage (all locations)
$reposJson = Invoke-GCloud artifacts repositories list --project $ProjectId --format json | ConvertFrom-Json
$artifactTotalBytes = 0.0
$activeImageTotalBytes = 0.0
foreach ($repo in $reposJson) {
    $sizeBytes = 0.0
    if ($repo.PSObject.Properties.Name -contains "sizeBytes") {
        $sizeBytes = [double]$repo.sizeBytes
    }
    $artifactTotalBytes += $sizeBytes
    $repoEntry = [ordered]@{
        name = $repo.name
        format = $repo.format
        size_mb = (To-MB $sizeBytes)
        active_image_mb = 0.0
    }
    if ([string]$repo.format -eq "DOCKER" -and $repo.registryUri) {
        try {
            $imgsJsonText = Invoke-GCloud artifacts docker images list $repo.registryUri --include-tags --format json
            $imgs = @()
            if ($imgsJsonText) {
                $imgs = @($imgsJsonText | ConvertFrom-Json)
            }
            $repoActiveBytes = 0.0
            foreach ($img in $imgs) {
                $bytes = 0.0
                if ($img.metadata -and $img.metadata.imageSizeBytes) {
                    $bytes = [double]$img.metadata.imageSizeBytes
                }
                $repoActiveBytes += $bytes
            }
            $repoEntry.active_image_mb = To-MB $repoActiveBytes
            $activeImageTotalBytes += $repoActiveBytes
        } catch {
            Write-Warning "failed to list active docker images for $($repo.registryUri): $_"
        }
    }
    $result.artifact_registry_repositories += $repoEntry
}
$result.artifact_registry_total_mb = To-MB $artifactTotalBytes
$result.artifact_registry_active_image_total_mb = To-MB $activeImageTotalBytes

# Buckets usage
$bucketLines = Invoke-GCloud storage buckets list --project $ProjectId --format "value(name)"
$cloudBuildBucket = ""
$appDataBucket = ""
foreach ($line in $bucketLines) {
    $name = [string]$line
    if (-not $name) { continue }
    if ($name -match "_cloudbuild$") { $cloudBuildBucket = $name }
    if ($name -match "-solar-db-us$") { $appDataBucket = $name }
}

function Get-BucketMB([string]$bucketName) {
    if (-not $bucketName) { return 0.0 }
    $du = Invoke-GCloud storage du -s "gs://$bucketName"
    $first = [string]$du | Select-Object -First 1
    $parts = ($first -split "\s+") | Where-Object { $_ -ne "" }
    if ($parts.Count -lt 1) { return 0.0 }
    return To-MB ([double]$parts[0])
}

$result.cloudbuild_bucket_mb = Get-BucketMB -bucketName $cloudBuildBucket
$result.app_data_bucket_mb = Get-BucketMB -bucketName $appDataBucket

if ($result.artifact_registry_active_image_total_mb -gt $MaxArtifactRegistryMB) {
    $result.overage += ("Artifact Registry active images {0}MB > {1}MB" -f $result.artifact_registry_active_image_total_mb, $MaxArtifactRegistryMB)
}
if ($result.cloudbuild_bucket_mb -gt $MaxCloudBuildBucketMB) {
    $result.overage += ("Cloud Build bucket {0}MB > {1}MB" -f $result.cloudbuild_bucket_mb, $MaxCloudBuildBucketMB)
}
if ($result.app_data_bucket_mb -gt $MaxAppDataBucketMB) {
    $result.overage += ("App data bucket {0}MB > {1}MB" -f $result.app_data_bucket_mb, $MaxAppDataBucketMB)
}

Write-Host "=== Free Tier Capacity Check ==="
Write-Host "Project: $ProjectId"
Write-Host ("Artifact Registry total (repo metric): {0} MB" -f $result.artifact_registry_total_mb)
Write-Host ("Artifact Registry active images: {0} MB (limit {1} MB)" -f $result.artifact_registry_active_image_total_mb, $MaxArtifactRegistryMB)
Write-Host ("Cloud Build bucket: {0} MB (limit {1} MB)" -f $result.cloudbuild_bucket_mb, $MaxCloudBuildBucketMB)
Write-Host ("App data bucket: {0} MB (limit {1} MB)" -f $result.app_data_bucket_mb, $MaxAppDataBucketMB)
Write-Host ""
Write-Host "Artifact repositories:"
foreach ($r in $result.artifact_registry_repositories) {
    Write-Host ("- {0}: {1} MB ({2})" -f $r.name, $r.size_mb, $r.format)
}
Write-Host ""

if ($result.overage.Count -gt 0) {
    Write-Warning "Free tier overage detected:"
    foreach ($o in $result.overage) {
        Write-Warning "- $o"
    }
    if ($FailOnOverage) {
        throw "Capacity exceeds configured free-tier limits."
    }
} else {
    Write-Host "All tracked storage usage is within configured free-tier limits."
}

$result | ConvertTo-Json -Depth 8

param(
    [string]$ProjectId = "",
    [double]$TargetArtifactRegistryMB = 500.0,
    [switch]$DryRun
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

$targets = @(
    # Keep the latest digest for each active Cloud Run image so cold starts and
    # redeployments have an Artifact Registry source to pull from.
    @{ location = "us-central1"; repository = "solar-controller"; image = "runner"; keep = 1 },
    @{ location = "us-central1"; repository = "solar-dashboard"; image = "dashboard"; keep = 1 },
    # Legacy Tokyo image repository: also keep 0 digests (repo itself is kept).
    @{ location = "asia-northeast1"; repository = "solar-controller"; image = "runner"; keep = 0 }
)

Write-Host "Artifact prune start: project=$ProjectId dryRun=$DryRun"

foreach ($t in $targets) {
    $imagePath = "{0}-docker.pkg.dev/{1}/{2}/{3}" -f $t.location, $ProjectId, $t.repository, $t.image
    $rows = @()
    try {
        $jsonText = Invoke-GCloud artifacts docker images list $imagePath --include-tags --format json
        if ($jsonText) {
            $rows = @($jsonText | ConvertFrom-Json)
        }
    } catch {
        Write-Warning "Skip listing $imagePath : $_"
        continue
    }
    if (-not $rows -or $rows.Count -eq 0) {
        Write-Host "- $imagePath : no digests"
        continue
    }

    $dedup = @{}
    foreach ($r in $rows) {
        $digest = [string]$r.version
        if (-not $digest) {
            $digest = [string]$r.digest
        }
        if (-not $digest) { continue }
        if ($digest -notmatch "^sha256:") { continue }
        if (-not $dedup.ContainsKey($digest)) {
            $dedup[$digest] = $r
        }
    }
    $uniq = @($dedup.Values)
    $sorted = $uniq | Sort-Object -Property @{Expression = { [string]$_.createTime }; Descending = $true}
    $keep = [int]$t.keep
    if ($keep -lt 0) { $keep = 0 }
    $toKeep = @($sorted | Select-Object -First $keep)
    $toDelete = @()
    if ($sorted.Count -gt $keep) {
        $toDelete = @($sorted | Select-Object -Skip $keep)
    }

    Write-Host ("- {0} : total={1}, keep={2}, delete={3}" -f $imagePath, $sorted.Count, $toKeep.Count, $toDelete.Count)
    foreach ($d in $toDelete) {
        $digest = [string]$d.version
        if (-not $digest) {
            $digest = [string]$d.digest
        }
        if (-not $digest) { continue }
        $ref = "$imagePath@$digest"
        if ($DryRun) {
            Write-Host ("  DRYRUN delete {0}" -f $ref)
            continue
        }
        Write-Host ("  delete {0}" -f $ref)
        Invoke-GCloud artifacts docker images delete $ref --delete-tags --quiet | Out-Null
    }
}

Write-Host "Artifact prune completed."

$checkScript = Join-Path $PSScriptRoot "check_gcp_free_tier_capacity.ps1"
if (Test-Path $checkScript) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $checkScript `
        -ProjectId $ProjectId `
        -MaxArtifactRegistryMB $TargetArtifactRegistryMB
}

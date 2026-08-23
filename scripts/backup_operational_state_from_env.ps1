param(
    [string]$OutDir = "artifacts/backups/operational"
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv

Assert-ProductionEnv @(
    'GCP_PROJECT_ID',
    'GCP_REGION',
    'GCP_SCHEDULER_REGION',
    'GCP_DASHBOARD_SERVICE',
    'KP_MONITOR_USERNAME_SECRET',
    'KP_MONITOR_PASSWORD_SECRET'
)

$projectId = Get-RequiredProductionEnv 'GCP_PROJECT_ID'
$region = Get-RequiredProductionEnv 'GCP_REGION'
$schedulerRegion = Get-RequiredProductionEnv 'GCP_SCHEDULER_REGION'
$dashboardService = Get-RequiredProductionEnv 'GCP_DASHBOARD_SERVICE'
$secretNames = @(
    Get-RequiredProductionEnv 'KP_MONITOR_USERNAME_SECRET'
    Get-RequiredProductionEnv 'KP_MONITOR_PASSWORD_SECRET'
)
$gcloud = Join-Path $PSScriptRoot 'gcloud.ps1'
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$generationDir = Join-Path $OutDir $stamp
New-Item -ItemType Directory -Force -Path $generationDir | Out-Null

function Invoke-JsonRead {
    param([string[]]$Arguments)

    $result = & $gcloud @Arguments --quiet --format=json 2>$null
    if ($LASTEXITCODE -ne 0) {
        return [ordered]@{ status = 'unavailable'; reason = 'read_failed' }
    }
    try {
        return ($result -join "`n") | ConvertFrom-Json
    } catch {
        return [ordered]@{ status = 'unavailable'; reason = 'invalid_json' }
    }
}

function ConvertTo-SafeObject {
    param(
        [AllowNull()][object]$Value,
        [string]$ContextName = ''
    )

    if ($null -eq $Value) { return $null }
    if ($Value -is [string] -or $Value -is [ValueType]) { return $Value }
    if ($Value -is [System.Collections.IDictionary]) {
        $dictionary = [ordered]@{}
        foreach ($entry in $Value.GetEnumerator()) {
            $dictionary[[string]$entry.Key] = ConvertTo-SafeObject -Value $entry.Value -ContextName ([string]$entry.Key)
        }
        return $dictionary
    }
    if ($Value -is [System.Collections.IEnumerable] -and $Value -isnot [string]) {
        return @($Value | ForEach-Object { ConvertTo-SafeObject -Value $_ -ContextName $ContextName })
    }

    $properties = [ordered]@{}
    $objectName = $ContextName
    $nameProperty = $Value.PSObject.Properties['name']
    if ($nameProperty) { $objectName = [string]$nameProperty.Value }
    foreach ($property in $Value.PSObject.Properties) {
        $name = [string]$property.Name
        if ($name -match '(?i)password|token|authorization|privatekey|secretvalue|credential') {
            $properties[$name] = '[REDACTED]'
        } elseif ($name -eq 'value' -and $objectName -match '(?i)password|token|secret|credential|key') {
            $properties[$name] = '[REDACTED]'
        } else {
            $properties[$name] = ConvertTo-SafeObject -Value $property.Value -ContextName $objectName
        }
    }
    return $properties
}

function Write-SafeJson {
    param(
        [string]$Name,
        [object]$Value
    )

    $path = Join-Path $generationDir $Name
    $safe = ConvertTo-SafeObject -Value $Value
    $safe | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $path -Encoding utf8
    return $path
}

$jobNames = @('solar-battery-23', 'solar-battery-03', 'solar-battery-07', 'solar-battery-settings-roundtrip')
$schedulerNames = @('solar-battery-run-23', 'solar-battery-run-03', 'solar-battery-run-07')
$jobState = [ordered]@{}
foreach ($name in $jobNames) {
    $jobState[$name] = Invoke-JsonRead @('run', 'jobs', 'describe', $name, '--project', $projectId, '--region', $region)
}
$serviceState = Invoke-JsonRead @('run', 'services', 'describe', $dashboardService, '--project', $projectId, '--region', $region)
$schedulerState = [ordered]@{}
foreach ($name in $schedulerNames) {
    $schedulerState[$name] = Invoke-JsonRead @('scheduler', 'jobs', 'describe', $name, '--project', $projectId, '--location', $schedulerRegion)
}
$secretState = [ordered]@{}
foreach ($name in $secretNames) {
    $secretState[$name] = Invoke-JsonRead @('secrets', 'describe', $name, '--project', $projectId)
}
$iamState = Invoke-JsonRead @('projects', 'get-iam-policy', $projectId)

$files = @()
$files += [IO.Path]::GetFileName((Write-SafeJson -Name 'cloud_run_jobs.json' -Value $jobState))
$files += [IO.Path]::GetFileName((Write-SafeJson -Name 'cloud_run_dashboard.json' -Value $serviceState))
$files += [IO.Path]::GetFileName((Write-SafeJson -Name 'cloud_scheduler_jobs.json' -Value $schedulerState))
$files += [IO.Path]::GetFileName((Write-SafeJson -Name 'secret_metadata.json' -Value $secretState))
$files += [IO.Path]::GetFileName((Write-SafeJson -Name 'project_iam_policy.json' -Value $iamState))

$manifest = [ordered]@{
    schema_version = 1
    backup_type = 'operational_state'
    captured_at_utc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    project_id = $projectId
    region = $region
    scheduler_region = $schedulerRegion
    dashboard_service = $dashboardService
    secret_names = $secretNames
    files = $files
    secret_values_included = $false
    restore_implementation_included = $false
}
Write-SafeJson -Name 'manifest.json' -Value $manifest | Out-Null
Write-Host "Operational state backup completed: $generationDir"

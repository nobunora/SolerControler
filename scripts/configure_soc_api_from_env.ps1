param([switch]$ValidateOnly)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv
$bundle = Get-RequiredProductionEnv 'KP_SOC_API_CREDENTIALS_JSON'
$secretName = Get-RequiredProductionEnv 'KP_SOC_API_CREDENTIALS_SECRET'
if ($secretName -notmatch '^[A-Za-z0-9_-]+$') { throw 'Invalid SOC API secret reference.' }
try {
    $parsed = $bundle | ConvertFrom-Json -AsHashtable
    foreach ($value in @($parsed.authorization, $parsed.api_key, $parsed.user.id, $parsed.user.password,
                         $parsed.gateway.id, $parsed.gateway.password)) {
        if ($value -isnot [string] -or -not $value -or $value -match '[\r\n]') { throw 'Invalid credential field.' }
    }
    if (-not $parsed.authorization.StartsWith('Basic ')) { throw 'Unsupported authorization scheme.' }
} catch { throw 'SOC API credential configuration is missing or invalid.' }
if ($ValidateOnly) { Write-Host 'SOC API credential configuration passed. No mutation performed.'; return }

$project = Get-RequiredProductionEnv 'GCP_PROJECT_ID'
$region = Get-RequiredProductionEnv 'GCP_REGION'
$gcloud = Join-Path $PSScriptRoot 'gcloud.ps1'
function Invoke-SocCloud([string[]]$Arguments) {
    $output = (& $gcloud @Arguments 2>&1) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw 'SOC API secret provisioning failed.' }
    return $output
}
$jobs = @('solar-battery-03', 'solar-battery-settings-roundtrip')
$accounts = @()
foreach ($job in $jobs) {
    $executions = @(Invoke-SocCloud @('run', 'jobs', 'executions', 'list', '--job', $job,
        '--project', $project, '--region', $region, '--limit', '5', '--format', 'json') | ConvertFrom-Json -AsHashtable)
    foreach ($execution in $executions) {
        if ($execution.status -and -not $execution.status.completionTime) { throw 'SOC credential binding refused while a target job is active.' }
    }
    $resource = Invoke-SocCloud @('run', 'jobs', 'describe', $job, '--project', $project,
        '--region', $region, '--format', 'json') | ConvertFrom-Json -AsHashtable
    $account = [string]$resource.spec.template.spec.template.spec.serviceAccountName
    if (-not $account) { throw 'SOC target service account is missing.' }
    $accounts += $account
}
$names = Invoke-SocCloud @('secrets', 'list', '--project', $project, '--filter', "name:$secretName", '--format', 'value(name)')
if (-not @($names.Split("`n") | Where-Object { ($_ -split '/')[-1].Trim() -eq $secretName }).Count) {
    Invoke-SocCloud @('secrets', 'create', $secretName, '--project', $project, '--replication-policy', 'automatic') | Out-Null
}
$current = ''
try { $current = Invoke-SocCloud @('secrets', 'versions', 'access', 'latest', '--secret', $secretName, '--project', $project) } catch { $current = '' }
if ($current.Trim() -ne $bundle.Trim()) {
    $temporary = New-TemporaryFile
    try {
        [IO.File]::WriteAllText($temporary.FullName, $bundle, [Text.UTF8Encoding]::new($false))
        Invoke-SocCloud @('secrets', 'versions', 'add', $secretName, '--project', $project, '--data-file', $temporary.FullName) | Out-Null
    } finally { Remove-Item -LiteralPath $temporary.FullName -Force }
}
foreach ($account in ($accounts | Sort-Object -Unique)) {
    Invoke-SocCloud @('secrets', 'add-iam-policy-binding', $secretName, '--project', $project,
        '--member', "serviceAccount:$account", '--role', 'roles/secretmanager.secretAccessor') | Out-Null
}
foreach ($job in $jobs) {
    Invoke-SocCloud @('run', 'jobs', 'update', $job, '--project', $project, '--region', $region,
        '--update-secrets', "KP_SOC_API_CREDENTIALS_JSON=${secretName}:latest") | Out-Null
}
Write-Host 'SOC API credentials provisioned and bound to the monitor and acceptance probe.'

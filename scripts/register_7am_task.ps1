param(
    [string]$TaskName = "SolarBattery-7am-DayMode",
    [datetime]$RunAt = ((Get-Date).Date.AddDays(1).AddHours(7)),
    [switch]$Daily
)

$ErrorActionPreference = "Stop"

$scriptPath = (Resolve-Path (Join-Path $PSScriptRoot "run_7am_day_mode.ps1")).Path
$psExe = (Get-Command powershell.exe).Source

$action = New-ScheduledTaskAction `
    -Execute $psExe `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`"" `
    -WorkingDirectory (Split-Path $scriptPath -Parent)

if ($Daily) {
    $trigger = New-ScheduledTaskTrigger -Daily -At $RunAt
} else {
    $trigger = New-ScheduledTaskTrigger -Once -At $RunAt
}

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Run KP-NET day-mode setting at 07:00 (green mode, SOC lower 0%)." `
    -Force | Out-Null

Write-Host "Registered task: $TaskName"
Write-Host "RunAt: $RunAt"
Write-Host "Daily: $($Daily.IsPresent)"

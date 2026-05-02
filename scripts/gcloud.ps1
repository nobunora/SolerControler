param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$ErrorActionPreference = "Stop"

# If this script is launched from Windows PowerShell 5.1, re-launch under pwsh (PowerShell 7+).
if ($PSVersionTable.PSEdition -ne "Core") {
    $pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($pwshCmd) {
        & $pwshCmd.Source -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath @Args
        exit $LASTEXITCODE
    }
}

$gcloudCmd = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
if (-not (Test-Path $gcloudCmd)) {
    throw "gcloud.cmd not found at: $gcloudCmd"
}

& $gcloudCmd @Args
exit $LASTEXITCODE

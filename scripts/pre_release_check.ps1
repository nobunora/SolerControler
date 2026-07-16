param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

& (Join-Path $PSScriptRoot "pre_release_integration.ps1") -SkipInstall:$SkipInstall
if ($LASTEXITCODE -ne 0) { throw "integration pre-release checks failed" }

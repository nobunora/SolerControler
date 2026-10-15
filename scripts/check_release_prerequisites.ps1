param()

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$rules = Join-Path $root 'docs/current/ops/DEPLOYMENT_FAILURE_RULES_JA.md'
if (-not (Test-Path -LiteralPath $rules)) { throw 'Deployment failure rules are missing.' }
Write-Host (Get-Content -LiteralPath $rules -Raw)
Write-Host "Deployment rules SHA256: $((Get-FileHash -LiteralPath $rules -Algorithm SHA256).Hash)"

# The image import smoke test uses isolated Python: user-site success is insufficient.
python -I -c "from zoneinfo import ZoneInfo; ZoneInfo('Asia/Tokyo')"
if ($LASTEXITCODE -ne 0) {
    throw 'Isolated Python cannot load Asia/Tokyo. Repair the selected interpreter environment (tzdata/system zoneinfo), then rerun. No installation was attempted.'
}
Write-Host 'Release environment prerequisites passed; this is not release acceptance.'

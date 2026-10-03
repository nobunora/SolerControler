param(
    [switch]$SkipInstall,
    [switch]$CheckPrerequisites
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

function Assert-RequiredCommand {
    param([string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command was not found: $Name"
    }
}

function Get-TrackedJavaScriptFiles {
    $files = @(git ls-files -- "*.js")
    if ($LASTEXITCODE -ne 0) { throw "git ls-files failed" }
    return $files
}

foreach ($commandName in @("python", "git", "node", "npx")) {
    Assert-RequiredCommand -Name $commandName
}

if ($CheckPrerequisites) {
    $null = @(Get-TrackedJavaScriptFiles)
    Write-Host "Quality gate prerequisites passed."
    exit 0
}

if (-not $SkipInstall) {
    python -m pip install -r .\requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
}

# The release contract is fixed in docs/current/ops/QUALITY_GATE_JA.md.
# Every check below is mandatory; exploratory tools are not release checks.
Write-Host "Running mandatory code-quality checks before tests."

python -m ruff check .
if ($LASTEXITCODE -ne 0) { throw "ruff failed" }

$pythonScriptsDirectory = python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
$importLinter = Join-Path $pythonScriptsDirectory "lint-imports.exe"
if (-not (Test-Path $importLinter)) { throw "Import Linter executable was not found: $importLinter" }
& $importLinter
if ($LASTEXITCODE -ne 0) { throw "import-linter failed" }

python -m mypy app scripts --no-incremental
if ($LASTEXITCODE -ne 0) { throw "full project mypy failed" }

$javaScriptFiles = @(Get-TrackedJavaScriptFiles)
if ($javaScriptFiles.Count -gt 0) {
    npx --yes oxlint @javaScriptFiles
    if ($LASTEXITCODE -ne 0) { throw "oxlint failed" }
    foreach ($file in $javaScriptFiles) {
        node --check $file
        if ($LASTEXITCODE -ne 0) { throw "JavaScript syntax check failed: $file" }
    }
}

python -m compileall app main.py kpnet_main.py energy_model_main.py cloud_job_runner.py db_pipeline_main.py dashboard_server.py
if ($LASTEXITCODE -ne 0) { throw "compileall failed" }

python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "pytest failed" }

node .\tests\test_dashboard_calculations.js
if ($LASTEXITCODE -ne 0) { throw "dashboard JavaScript tests failed" }

node .\tests\test_dashboard_modules.js
if ($LASTEXITCODE -ne 0) { throw "dashboard JavaScript module tests failed" }

node .\tests\test_dashboard_bootstrap.js
if ($LASTEXITCODE -ne 0) { throw "dashboard JavaScript bootstrap test failed" }

python .\scripts\security_check.py
if ($LASTEXITCODE -ne 0) { throw "security_check failed" }

Write-Host "Local pre-release checks passed."

param(
    [switch]$SkipInstall,
    [switch]$EnforceQualityAudit,
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
    python -m pip install -r .\requirements-dev.txt uv
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
}

function Invoke-AdvisoryQualityCheck {
    param(
        [string]$Name,
        [scriptblock]$Command
    )

    $output = @(& $Command 2>&1)
    $exitCode = $LASTEXITCODE
    $output | Select-Object -First 40
    if ($exitCode -eq 0) {
        Write-Host "$Name passed."
        return
    }

    $message = "$Name reported diagnostics (exit code $exitCode)."
    if ($EnforceQualityAudit) {
        throw $message
    }
    Write-Warning "$message Run with -EnforceQualityAudit after the findings are resolved."
}

Write-Host "Running code-quality audit before tests."

python -m ruff check .
if ($LASTEXITCODE -ne 0) { throw "ruff failed" }

$pythonScriptsDirectory = python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
$importLinter = Join-Path $pythonScriptsDirectory "lint-imports.exe"
if (-not (Test-Path $importLinter)) { throw "Import Linter executable was not found: $importLinter" }
& $importLinter
if ($LASTEXITCODE -ne 0) { throw "import-linter failed" }

$pythonExecutable = python -c "import sys; print(sys.executable)"
Invoke-AdvisoryQualityCheck -Name "ty" -Command {
    python -m uv tool run ty check . --python $pythonExecutable --output-format concise
}
Invoke-AdvisoryQualityCheck -Name "deptry" -Command {
    python -m uv tool run deptry .
}

$javaScriptFiles = @(Get-TrackedJavaScriptFiles)
if ($javaScriptFiles.Count -gt 0) {
    npx --yes oxlint @javaScriptFiles
    if ($LASTEXITCODE -ne 0) { throw "oxlint failed" }
    Invoke-AdvisoryQualityCheck -Name "tsc" -Command {
        npx --yes --package typescript tsc --allowJs --checkJs --noEmit --target ES2022 --lib ES2022,DOM @javaScriptFiles
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

python -m mypy app scripts --no-incremental
if ($LASTEXITCODE -ne 0) { throw "full project mypy failed" }

python .\scripts\security_check.py
if ($LASTEXITCODE -ne 0) { throw "security_check failed" }

Write-Host "Local pre-release checks passed."

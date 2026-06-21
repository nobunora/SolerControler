$ErrorActionPreference = "Stop"

$Repo = "C:\VSC\SolerControler"
$PromptPath = Join-Path $Repo "docs\prompts\scheduled_soc_decision_design_ja.md"
$LogDir = Join-Path $Repo "logs"
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogPath = Join-Path $LogDir "codex-soc-decision-design-$Timestamp.log"
$LastMessagePath = Join-Path $LogDir "codex-soc-decision-design-$Timestamp.final.md"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$CodexCommand = Get-Command codex -ErrorAction Stop
$RgCommand = Get-Command rg -ErrorAction SilentlyContinue
if ($RgCommand) {
    $RgDir = Split-Path -Parent $RgCommand.Source
    if (($env:Path -split ';') -notcontains $RgDir) {
        $env:Path = "$RgDir;$env:Path"
    }
}

"[$(Get-Date -Format o)] Starting scheduled Codex SOC decision design task" | Out-File -LiteralPath $LogPath -Encoding utf8
"Repo: $Repo" | Out-File -LiteralPath $LogPath -Encoding utf8 -Append
"Prompt: $PromptPath" | Out-File -LiteralPath $LogPath -Encoding utf8 -Append
"Codex: $($CodexCommand.Source)" | Out-File -LiteralPath $LogPath -Encoding utf8 -Append
if ($RgCommand) {
    "rg: $($RgCommand.Source)" | Out-File -LiteralPath $LogPath -Encoding utf8 -Append
} else {
    "rg: not found" | Out-File -LiteralPath $LogPath -Encoding utf8 -Append
}
"" | Out-File -LiteralPath $LogPath -Encoding utf8 -Append

Set-Location -LiteralPath $Repo

try {
    $StartTime = Get-Date
    $CmdLine = 'type "{0}" | "{1}" --ask-for-approval never exec --cd "{2}" --sandbox danger-full-access --output-last-message "{3}" - >> "{4}" 2>&1' -f `
        $PromptPath, $CodexCommand.Source, $Repo, $LastMessagePath, $LogPath
    & cmd.exe /d /c $CmdLine
    $ExitCode = $LASTEXITCODE
} catch {
    $ExitCode = 1
    "[$(Get-Date -Format o)] ERROR: $($_.Exception.Message)" | Out-File -LiteralPath $LogPath -Encoding utf8 -Append
}

"ElapsedMinutes: $([math]::Round(((Get-Date) - $StartTime).TotalMinutes, 2))" | Out-File -LiteralPath $LogPath -Encoding utf8 -Append
"[$(Get-Date -Format o)] Finished with exit code $ExitCode" | Out-File -LiteralPath $LogPath -Encoding utf8 -Append
exit $ExitCode

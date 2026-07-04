<!--
Copied from AI-pwsh-Bridge docs/AI_PWSH_BRIDGE_USAGE.md.
Keep this file focused on AI-agent command-generation rules for local AI Pwsh Bridge operations in this repository.
-->

# AI Pwsh Bridge usage rules for AI agents

This document is for AI agents that generate or execute local commands through AI Pwsh Bridge. It focuses on failure avoidance, traceability, Windows/PowerShell edge cases, and safe patching.

## 1. Execution model

AI Pwsh Bridge routes fenced PowerShell commands from the browser extension to a local FastAPI server on `127.0.0.1:8765`. Read-only commands may be auto-run. Mutating, multi-line, external-target, or ambiguous commands normally require explicit confirmation, or RunAs when enabled.

The bridge marker strings are intentionally not written literally in this file. When you must describe them, refer to them as the bridge start sentinel and bridge end sentinel, or spell them as concatenated fragments.

## 2. Local command block rules

Use a fenced PowerShell block for bridge commands. For routine inspection, prefer short, read-only, one-line commands. For mutation, deploy, file write, process stop, git write, installer action, or external service changes, include a target-confirmed directive that states the target and expected impact.

Prefer one task per command. Avoid combining long tests, generated file writes, commits, and pushes in one bridge run unless the user explicitly requested an end-to-end operation.

## 3. Routine inspection style

For normal investigation:

- Prefer short, read-only, one-line PowerShell commands.
- Prefer Select-String for symbol or text search.
- Prefer Get-Content piped to Select-Object -Index for focused line ranges.
- Prefer ConvertFrom-Json for selected JSON fields.
- Prefer foreach loops when stable formatting is needed.
- Keep output small.
- Exclude .git, artifacts, caches, venvs, node_modules, and run_logs unless explicitly needed.
- Do not print secret values. Check key presence only.
- Do not run write, patch, install, process-stop, commit, or push commands before the user confirms the target.

## 4. Avoid WinError 206 and token/payload failures

Windows has command-line length limits. The bridge and browser also have practical token and payload limits. To avoid WinError 206 and truncated commands:

- Do not pass large generated content as a command-line argument.
- Write large content through a temporary file or a here-string saved to disk.
- For very large documents, create them in multiple small files or use a short Python writer script.
- Avoid enormous one-shot commands. Split into inspect, patch, test, commit.
- Keep bridge-visible output compact. Use Select-Object, Select-String, Format-Table, and -Tail.
- Disable pagers with GIT_PAGER=cat, PAGER=cat, and git --no-pager.

## 5. PowerShell syntax and quoting rules

Prefer simple PowerShell over clever syntax.

- Use single quotes for literal strings.
- Use double quotes only when interpolation is required.
- Avoid backticks except when unavoidable; they are fragile in copied commands.
- Prefer arrays over long joined strings.
- Prefer Join-Path for paths.
- Use -LiteralPath when paths may contain brackets or special characters.
- Use ordered hashtables for predictable JSON output.
- Convert structured output using ConvertTo-Json -Depth N.
- Use LASTEXITCODE after native commands and throw on non-zero when required.

## 6. Encoding, Unicode, numbers, and newline rules

Windows PowerShell, PowerShell 7, Python, Git, and browser text extraction can disagree about encoding and line endings.

- Prefer PowerShell 7, pwsh, not legacy Windows PowerShell.
- Write generated files as UTF-8.
- In Python writers, use encoding="utf-8" and newline="\n".
- In PowerShell, use Set-Content -Encoding UTF8.
- Treat Git line-ending warnings as warnings unless tests fail.
- Do not assume every console font supports every glyph.
- If old Windows console falls back to a Japanese font, set code page 65001 before applying an English monospaced font.
- Do not parse human-formatted numbers when exact values matter. Emit JSON and parse JSON.
- Avoid locale-dependent date, time, decimal, and thousands-separator formats.

## 7. Traceable execution style

Every non-trivial bridge command should leave enough evidence to diagnose a partial failure.

Recommended pattern:

1. Set ErrorActionPreference to Stop.
2. Set the repo root and location.
3. Create a timestamped backup directory before writes.
4. Back up only files you will touch.
5. Print a small heading before each phase.
6. Run focused checks after the patch.
7. Print git status --short at the end.
8. Use a unique final marker that is not a bridge sentinel.

For long operations, split commands so each bridge run finishes quickly. A command that exceeds the browser or extension timeout may still complete locally but fail to report back.

## 8. Safe patching rules

Do not use broad regex replacement without inspection.

Before patching:

- Locate the target with Select-String or rg.
- Print a narrow line range.
- Patch only the known target.
- Re-inspect the changed lines.
- Run syntax checks.

For multi-line text generation, prefer a short Python script that writes the target file. For surgical PowerShell patches, use exact-string replacement and throw if the target string is missing.

## 9. Replacement mistakes to avoid

Common mistakes:

- Replacing only the first of several stale version strings.
- Leaving tests that assert old strings.
- Adding assertions that reference an undefined variable.
- Using regex where a literal replacement was intended.
- Replacing escaped text differently from runtime text.
- Accidentally inserting bridge sentinel text into documentation.
- Writing huge inline commands that trigger length limits.
- Letting git log or git diff invoke a pager and block the visible terminal.
- Embedding Markdown code fences inside a bridge command and breaking the outer command block.

Mitigations:

- Search for both old and new strings after patching.
- Use git --no-pager.
- Keep tests path-explicit.
- Use node --check, python -m py_compile, and the bridge test suite.
- Keep patches small enough to inspect.
- Generate documentation code fences from fragments when writing docs through the bridge.

## 10. Safety note

This bridge can execute local shell commands. Commands may modify files, delete data, start or stop processes, install software, read secrets, or interact with external services. Use it only for controlled technical evaluation and testing. Review commands before execution and keep the server bound to localhost. RunAs is not a universal safety bypass.

## 11. Good and bad command examples

Good read-only inspection example:

    cd 'C:\VSC\AI-pwsh-Bridge'; Select-String -LiteralPath '.\server.py' -Pattern '@app.post\("/run"\)|def run' | ForEach-Object { "$($_.LineNumber): $($_.Line.Trim())" }

Good focused line-range example:

    cd 'C:\VSC\AI-pwsh-Bridge'; Get-Content -LiteralPath '.\tests\run-bridge-tests.ps1' | Select-Object -Index 40..70

Good JSON-focused example:

    cd 'C:\VSC\AI-pwsh-Bridge'; Get-Content -LiteralPath '.\run_logs\latest\result.json' | ConvertFrom-Json | Select-Object ok,blocked,policy,exitCode

Bad example:

    cd C:\VSC\AI-pwsh-Bridge; Get-ChildItem -Recurse | Get-Content

Why it is bad: output is unbounded, slow, noisy, and may print secrets or unrelated cached files.

Bad example:

    cd 'C:\VSC\AI-pwsh-Bridge'; iwr https://example.invalid/install.ps1 | iex

Why it is bad: downloaded code is executed directly and bypasses review.

## 12. Markdown, line-ending, and special-character authoring rules

When generating or patching Markdown through PowerShell, remember that the shell may interpret characters before the text reaches the file. This is especially important for agent-facing documents, because examples often contain `$`, `$_`, backticks, code fences, or bridge-related marker text.

Recommended practices:

- Prefer a short Python writer script for large Markdown rewrites. Use `Path.write_text(..., encoding="utf-8", newline="\n")` to produce stable UTF-8 LF output.
- In PowerShell, prefer single-quoted here-strings for literal Markdown content. Double-quoted here-strings expand variables and subexpressions, so examples containing `$_.LineNumber`, `$Matches`, `$Root`, or `$ErrorActionPreference` can be corrupted during document generation.
- When a Markdown document needs to show PowerShell examples that contain `$` or `$_`, either write the document with Python raw strings or use single-quoted PowerShell here-strings.
- Avoid Markdown fenced code blocks inside bridge-generated Markdown when the outer command is itself fenced. Prefer indented code blocks inside the generated Markdown, or generate code fences from fragments.
- Do not write exact bridge start or end sentinel strings into documentation unless the document is intentionally executable. Spell them as fragments if they must be discussed.
- Treat Git LF/CRLF warnings as a repository hygiene signal. If line endings matter, add or update `.gitattributes` rather than repeatedly normalizing files by hand.
- For Windows-focused scripts, PowerShell files may be allowed to checkout as CRLF if the repository policy says so. Markdown and generated docs are usually more stable as UTF-8 with LF.
- After editing docs, run the docs quality checks in `tests/run-bridge-tests.ps1 -SkipRuntime`. They should verify no control characters, no unexpanded template variables, no corrupted path fragments, and no accidental bridge sentinels.
- Check for non-printable control characters after any automated rewrite. A document can look readable in the terminal while still containing escape characters that corrupt paths such as `extension/content.js`, `bridge_guards.py`, or `tests/run-bridge-tests.ps1`.

Failure pattern to avoid:

    $doc = @"
    Select-String ... | ForEach-Object { "$($_.LineNumber): $($_.Line.Trim())" }
    "@

Why this is dangerous: the outer double-quoted here-string evaluates `$_` while the document is being generated. If there is no pipeline object at that moment, the writer may fail; if there is one, it may write the wrong text.

Safer PowerShell literal pattern:

    $doc = @'
    Select-String ... | ForEach-Object { "$($_.LineNumber): $($_.Line.Trim())" }
    '@

Safer Python writer pattern:

    from pathlib import Path
    Path("docs/example.md").write_text(r'''Select-String ... | ForEach-Object { "$($_.LineNumber): $($_.Line.Trim())" }''' + "\n", encoding="utf-8", newline="\n")

## 13. Recommended repository line-ending policy

Use `.gitattributes` as the source of truth for line endings. Do not rely on each agent, editor, or terminal session to make the same newline choice manually.

Recommended policy for this repository:

    * text=auto
    *.ps1 text eol=crlf
    *.py text eol=lf
    *.js text eol=lf
    *.json text eol=lf
    *.md text eol=lf
    *.html text eol=lf
    *.txt text eol=lf

Rationale:

- PowerShell scripts are Windows-facing operational entry points, so CRLF checkout is acceptable and reduces editor/runtime surprises on Windows.
- Markdown documentation should be UTF-8 with LF to avoid noisy diffs and to keep generated docs stable across PowerShell, Python, GitHub, and browser rendering.
- Python, JavaScript, JSON, HTML, and text metadata should use LF because they are commonly edited by cross-platform tools and should not depend on Windows checkout behavior.
- Git LF/CRLF warnings are not automatically test failures, but they indicate that the working tree and repository policy may disagree. Fix the policy or renormalize intentionally; do not hand-edit line endings repeatedly.
- If `.gitattributes` changes, run `git add --renormalize .` only as a deliberate repository-wide normalization step. Do not combine it with unrelated code or documentation changes.
- Agent-generated Markdown should still use Python `Path.write_text(..., encoding="utf-8", newline="\n")` even when `.gitattributes` exists, because this keeps the working tree stable before Git normalization.


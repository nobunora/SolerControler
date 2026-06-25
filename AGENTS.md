# AGENTS.md

Keep context small. Act from evidence.

## Core Rules

- Read this file first.
- Use PowerShell 7 (`pwsh`) by default.
- Do not scan all docs or all source files.
- Start with metadata: `git status --short`, shallow listings, and `rg --files`.
- Use `rg` before opening files.
- Open only the needed index, file, and line range.
- Full-file reads are allowed only for short files or when structure is required.
- If broader reading is needed, say why before doing it.
- Follow existing design, names, boundaries, and error handling.
- Do not guess specs, compatibility, security, money, or external contracts. Verify or ask.
- Do not rename public APIs, DB fields, env keys, or integration fields unless required.
- Keep one main responsibility per file or function.
- Keep diffs small and focused. Do not mix feature work, refactor, and formatting.
- Do not leave debug code, commented-out code, or temporary bypasses.
- Do not add dependencies unless need, maintenance cost, and risk are clear.
- Report briefly: changed files, reason, checks run, risks, and open questions.

## Read Next

- `docs/00_index.md`
- Then choose one category index only when needed.
- Project overview: `README.md` only when project specs or entrypoints are needed.

## Compatibility Links

- Token policy: `docs/codex_token_usage_rules.md`
- Design judgment: `docs/design_intent_rules.md`
- Review guide: `docs/code_review.md`
- Bad patterns: `docs/bad_patterns.md`
- Report template: `docs/report_template.md`

## Working Rules

- Prefer focused tests near changed code first.
- If behavior might change, say so explicitly.
- For UI state, save flow, orchestration, preview, progress, or entrypoint wiring, read `docs/11_refactor_and_release.md` before editing.
- Do not parallelize checks that fight with the same watcher, browser, or dev server.
- If a command cannot run, give the exact reason and the command a human should run.
- Keep generated, cache, build, log, and artifact paths out of normal reads.

## Subagents

- Use subagents only when they reduce total work.
- Give each subagent a clear role, scope, and stop condition.
- Do not repeat the same exploration in parent and subagent.
- Parent integrates results and resolves conflicts.

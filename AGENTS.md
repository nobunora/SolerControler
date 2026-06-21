# AGENTS.md

Keep context small and act from evidence.

## Core Rules

- Read this file first. Open other docs only when needed.
- Use PowerShell 7 (`pwsh`) by default; use Windows PowerShell only for compatibility issues.
- Follow existing design, names, boundaries, and error handling.
- Do not guess specs, compatibility, security, or external contracts. Ask or verify.
- Do not rename public APIs, DB fields, env keys, or integration fields unless required.
- Report briefly: changed files, reason, checks run, risks.

## Reference Docs

- Token policy: `docs/codex_token_usage_rules.md`
- Design judgment: `docs/design_intent_rules.md`
- Review guide: `docs/code_review.md`
- Bad patterns: `docs/bad_patterns.md`
- Report template: `docs/report_template.md`
- Project overview: `README.md`

## Search Workflow

- Start with metadata: `rg --files`, `Get-ChildItem`, or shallow directory listings.
- Use `rg` before reading files. Full-file reads are a last resort.
- Read only relevant line ranges and avoid rereading unchanged ranges.
- Search symbols first, then inspect only the needed function/class blocks.
- Exclude generated or bulky paths: `node_modules`, `dist`, `build`, `.next`, `.venv`, caches, logs, artifacts, and `.git`.
- After discovery, summarize what matters and move to implementation.

## Subagents

- Use subagents only when they reduce total work.
- Give each subagent a clear role, scope, and stop condition.
- Do not repeat the same exploration in both parent and subagent.
- Parent agent integrates results, resolves conflicts, and avoids duplicate token spend.

## Context Hygiene

- Keep stable prompts, schemas, and project rules unchanged to maximize caching.
- Do not paste large logs, CSVs, generated files, or test output; summarize them.
- Prefer focused tests near changed code first.

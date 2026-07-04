# AGENTS.md

Read this first. Keep work evidence-based, small, and reviewable.

## Defaults

- Use PowerShell 7 (`pwsh`) unless it fails.
- Start with `git status --short`, shallow listings, and `rg`.
- Open only the files and line ranges needed for the task.
- Do not scan all docs, all source, generated files, caches, logs, or artifacts.
- Prefer focused tests near the changed code.

## Change Style

- Make the smallest meaningful change that fixes the verified cause.
- Keep one logical unit per patch: do not mix fixes, refactors, formatting, and cleanup.
- Follow existing names, boundaries, data fields, env keys, and error handling.
- Do not add dependencies or change external contracts without user approval.
- Leave no debug code, temporary bypasses, or commented-out code.

## Subagents

- Use subagents only when they reduce total work.
- Treat each subagent as a brilliant specialist with very short memory.
- Give each subagent a role, exact scope, files or commands to inspect, expected output, and stop condition.
- Parent must not repeat the same exploration, implementation, or verification; parent only integrates results and resolves conflicts.
- Close each subagent as soon as its task is complete.

## More Rules

- Token and exploration rules: read `docs/current/agent/codex_token_usage_rules.md` only when the task needs broad code search, MCP/tool exploration, subagents, or simulation loops.
- Design and refactor judgment: read `docs/current/agent/agent_working_rules.md` only when changing architecture, splitting work across subagents, or when a patch would touch several files.
- AI Pwsh Bridge command-generation and Markdown authoring rules: read `docs/current/agent/ai_pwsh_bridge_usage_rules.md` before generating executable bridge commands, patching Markdown through PowerShell, or handling LF/CRLF warnings.
- Report template: read `docs/current/agent/report_template.md` only after the user explicitly asks for a report.
- Do not create milestone reports or files under `docs/completed/reports/` unless the user explicitly asks.

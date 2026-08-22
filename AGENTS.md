# AGENTS.md

Read this first. Keep work evidence-based, small, and reviewable.

## Defaults

- Use PowerShell 7 (`pwsh`) unless it fails.
- Start with `git status --short`, shallow listings, and `rg`.
- Open only the files and line ranges needed for the task.
- Do not scan all docs, all source, generated files, caches, logs, or artifacts.
- Prefer focused tests near the changed code.
- Before running any tests, invoke the `code-quality-audit` Skill and complete its applicable Ruff, ty, deptry, Oxlint, and tsc checks. Triage findings before test execution; fix only verified issues and report configuration gaps separately.

## Change Style

- Make the smallest meaningful change that fixes the verified cause.
- Keep one logical unit per patch: do not mix fixes, refactors, formatting, and cleanup.
- Follow existing names, boundaries, data fields, env keys, and error handling.
- Do not add dependencies or change external contracts without user approval.
- Leave no debug code, temporary bypasses, or commented-out code.

## Historical Failure Protected Regions (Mandatory)

- Before changing a symbol or constant listed in `docs/current/agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md`, read that document and the cited commit evidence.
- Comments containing `HISTORICAL_FAILURE_LOCK` are operational constraints. Do not refactor, simplify, rename, or change the behavior below the comment without explicit user approval and a regression test for the historical failure.
- If a requested change touches a protected region, record the affected lock, the historical failure it prevents, and the additional validation in the change description before editing.
- Do not remove a lock comment because the current tests pass. Update the protection document and its guard test only when the protected contract is intentionally retired or replaced.

## Production Operations (Mandatory)

- Before every production deployment, read and follow `docs/current/ops/PRODUCTION_DEPLOYMENT_RUNBOOK_JA.md` in full. Its validation, safe-resume, verification, and stop conditions are mandatory; do not rely on memory or a previous run.
- For production deployment or interrupted-deployment recovery, use the `solar-production-deployment` Skill; its judgment boundaries are part of this repository's operating procedure.
- For every production deployment, validation, data import, backup, or Cloud Run Job execution, use the repository scripts below. Do not reconstruct equivalent `gcloud` or credential-bearing commands ad hoc.
- Before a production deployment, run `pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 -ValidateOnly`.
- Prefer `pwsh -NoProfile -File scripts/production_deployment_gate.ps1 -RunPreRelease` as the single pre-deployment gate; it creates a local backup, runs security/validation checks, and records non-sensitive results under `artifacts/deployment_state/`.
- Deploy with `pwsh -NoProfile -File scripts/deploy_production_from_env.ps1` only after validation and relevant tests pass.
- Pass `-StatePath artifacts/deployment_state/production-<timestamp>.json` to the deployment wrapper. On interruption, resume with the same path and `-Resume`; only stages explicitly marked `success` may be skipped. `running` is inconclusive, not success.
- Import KP-NET data with `scripts/run_kpnet_import_from_env.ps1`, run Drive backup with `scripts/run_drive_backup_cloud_from_env.ps1`, and execute a production job with `scripts/run_cloud_job_from_env.ps1`.
- For a daily actual-versus-forecast review, run `scripts/run_kpnet_import_from_env.ps1` first, then `scripts/run_kpnet_soc_gap_report.ps1 -SkipDownload`. Do not rebuild this workflow with direct credential or cloud commands.
- Read project IDs, regions, resource IDs, account identifiers, and other deployment settings from the Git-ignored `.env` through `scripts/production_env.ps1`. Keep credentials and sensitive identifiers out of commands, tracked files, reports, and chat output.
- Run `python scripts/security_check.py` before committing or pushing production-operation changes. Confirm `.env` remains ignored and unstaged.
- Lower-level scripts and direct cloud commands are for focused diagnosis only. Do not use them for a production mutation unless the canonical wrapper cannot perform the task; record the reason and preserve the same `.env`/secret-handling rules.

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
- Skill authoring rules: read `docs/current/agent/skill_creation_rules.md` before creating or modifying a reusable Skill.
- Report template: read `docs/current/agent/report_template.md` only after the user explicitly asks for a report.
- Do not create milestone reports or files under `docs/completed/reports/` unless the user explicitly asks.

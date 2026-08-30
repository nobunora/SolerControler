# Report Template

Use this only when a detailed report is needed. Keep normal final reports shorter.

## Pre-Work Check

- Change purpose:
- Change target:
- Scope not changed:
- Existing code or references reviewed:
- Relationship to existing design:
- Unknowns:
- Human confirmation needed:

## Final Report

- Change summary:
- Design intent:
- Alignment with existing design:
- Alternatives not chosen:
- Files changed:
- Scope not changed:
- Tests:
- Human confirmation points:
- Remaining risks:

## If Behavior Changed

- What changed:
- Affected users or features:
- Affected inputs or conditions:
- Impact on data:
- Impact on APIs:
- Impact on tests:
- Rollback feasibility:
- Human confirmation needed:

## If Temporary Workaround Exists

- Workaround:
- Why needed:
- Removal condition:
- Permanent fix plan:
- Related issue:

## Tracked Report Hygiene

Apply these rules when a report or raw investigation evidence is committed to the repository.

- Create files under `docs/completed/reports/` only when the user explicitly requests a persistent report. Prefer the PR body or normal final report for ordinary work.
- Use repository-relative Markdown links for tracked source/test/docs references. Do not use `C:\...`, `C:/...`, `/home/...`, or another machine-local absolute path as a source link.
- If a local path is needed to explain the reproduction environment, show it only as plain text and also provide repository-relative links for tracked files.
- Do not force-add files ignored by `.gitignore`. In particular, do not force-add `*.log` merely to preserve command output.
- When raw evidence must be tracked and the user has requested it, use a sanitized `.txt` or `.md` file. Keep it concise and record commands plus the evidence needed to reproduce the conclusion instead of a full unbounded terminal transcript.
- Never record `.env` values, credentials, tokens, private keys, secret-bearing command output, or sensitive cloud/account identifiers. Record only non-sensitive status or key presence when needed.
- For tool-generated investigations, record the repository branch/commit, tool version, analysis scope, exclusions/partial parses, relevant query/search method, confidence limitations, source/test verification, and remaining unknowns.
- Clearly separate tool-generated candidates from facts verified in source or tests. Do not phrase an `in_degree = 0`, low-confidence edge, similarity score, lint warning, or other heuristic as a confirmed defect without verification.
- Historical completed reports are evidence, not current architecture. They may be excluded from CodebaseMemory indexing; current source, tests, `AGENTS.md`, and `docs/current/` take precedence when they disagree.

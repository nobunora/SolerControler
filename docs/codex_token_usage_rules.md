# Codex Token Rules

Use this file only when token or context discipline matters.

## Default Path

1. Confirm the user request.
2. Read `AGENTS.md`.
3. Run `git status --short`.
4. Narrow targets with `rg --files` and `rg`.
5. Read only needed line ranges.

Read `README.md` or other docs only when the task needs specs, operations, or design judgment.

## Read Policy

- Do not read all docs.
- Use `docs/00_index.md` first, then one category index.
- Prefer symbol search and targeted ranges.
- Full-file reads are for short files or required structure.
- Avoid rereading unchanged ranges.
- Keep command output short.
- Summarize logs, CSVs, generated files, and test output.

## Implementation Policy

- Check the nearest existing implementation first.
- Keep the diff focused.
- Add abstractions only when they fit an existing boundary or remove real duplication.
- Ask or verify before changing specs, compatibility, security, money, or external contracts.

## Verification Policy

- Run nearest tests first.
- Run broad checks only for wide impact or final confidence.
- Never report a check as run if it was not.

## Report Policy

Keep reports short unless `docs/report_template.md` is needed:

- what changed
- why it changed
- what was checked
- risks or unverified items

# Codex Token-Saving Rules

This document defines the operating rules for keeping token usage low when Codex works in this repository.

## Basic Policy

Codex should not read every document every time. It should read only the documents that are actually needed.

The goal is not to increase omissions. The goal is to reach the necessary decision inputs while avoiding unrelated documents and large outputs in the conversation.

## Initial Steps

At the start of work, confirm the following in the minimum order:

1. The user's request
2. `AGENTS.md`
3. `git status --short`
4. Narrowing the target files with `rg`
5. The required range of the target files

Read `README.md` and docs only when specification review, operational review, or design judgment becomes necessary.

## When to Read Documents

- `README.md`: when project specifications, operating procedures, or the entry point to related documents is needed.
- `docs/design_intent_rules.md`: when design judgment, responsibility boundaries, specification interpretation, or compatibility judgment is needed.
- `docs/code_review.md`: when performing post-implementation self-review, requesting review, or checking change impact.
- `docs/bad_patterns.md`: when touching larger implementations, refactoring, exception handling, dependency additions, or security-related areas.
- `docs/report_template.md`: when a detailed final report, PR description, or organization of unknowns is needed.
- Business-spec docs: read only the documents directly related to the change target from the related-docs list in `README.md`.

## How to Read

- First search with `rg` and read only the matching sections.
- Read the entire file only when the document is short or when the full structure is required.
- When rereading the same file, limit the read to the range that was not confirmed previously.
- Keep command output to the minimum necessary number of lines.
- Do not paste large diffs or logs into the final report; explain only the key points.

## During Implementation

- Prefer checking the nearest existing implementation first.
- Do not mix in refactoring outside the scope of the change.
- Add a new abstraction only when it fits the existing pattern or actually reduces duplication or complexity.
- If an unknown item affects specification, compatibility, security, or an external contract, ask a human before implementing.

## During Verification

- Run tests that are close to the changed area first.
- Run broader tests or lint only when the impact range is wide or as a final check.
- Do not report a test as executed if it was not actually run.

## During Reporting

Keep the report concise.

- What changed
- Why it changed
- What was checked
- Remaining risks or unverified items

Use `docs/report_template.md` only when a detailed template is needed.

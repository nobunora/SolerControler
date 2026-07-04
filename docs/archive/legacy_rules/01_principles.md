# Principles

## Evidence First

- Read the smallest set of files that can answer the question.
- Use search and metadata before opening whole files.
- Treat unknowns as unknowns. Verify them or ask.

## Change Discipline

- Preserve existing behavior unless behavior change is the point.
- Make the smallest change that solves the real problem.
- Keep feature work, refactoring, and formatting separate when possible.
- If the task is a refactor, freeze current behavior before changing structure.
- If the task is a release or deploy, require the project's release gate first.

## Contract Discipline

- Do not guess specs, compatibility, security, or external contracts.
- Do not rename public names, persistent fields, or integration fields unless required.
- If a change touches a contract, explain the impact explicitly.
- If a control has both a label and a command, derive both from the same state source.

## Comment Discipline

- Write comments for reasons and constraints, not for obvious mechanics.
- If the code needs a long explanation, consider a smaller function or a clearer name first.

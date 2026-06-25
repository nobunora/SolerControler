# Design Intent Rules

Use this file when design, boundary, naming, compatibility, or behavior judgment is needed.

## Evidence First

- Preserve existing behavior unless behavior change is the goal.
- Match existing design, names, boundaries, and error handling.
- Treat unknown specs, compatibility, security, and external contracts as unknown.
- Verify from code or docs before implementing. Ask when evidence is missing.

## Responsibility

Keep these responsibilities separate when practical:

- input validation
- data retrieval
- permission checks
- state selection
- transformation
- persistence
- external API calls
- logging
- error conversion
- presentation formatting
- retry handling
- cache control

Match existing granularity when extra splitting would make the flow harder to read.

## Boundaries

- Keep entrypoints thin.
- Put domain policy in domain code.
- Keep shared modules dependency-light.
- Add abstractions only when they remove duplication, clarify intent, or match an existing boundary.
- If a control has both a label and command, derive both from the same state snapshot.
- If state grows many booleans, prefer a state machine or discriminated union.

## Naming

- Use names that reveal domain meaning, state, unit, scope, or purpose.
- Avoid generic names unless they match local vocabulary.
- Do not rename public APIs, DB fields, env keys, persisted fields, or integration fields for style.
- Do not rename widely used internal names unless the requested change requires it.

Avoid by default:

- `data`, `result`, `item`, `value`, `temp`, `obj`, `info`
- `payload`, `params`, `config`
- `handler`, `manager`, `processor`, `service`, `helper`, `util`
- `process`, `execute`, `handle`, `run`, `update`, `fix`, `check`, `validate`

## Comments

- Comment reasons and constraints, not obvious mechanics.
- If code needs a long comment, first consider a clearer name or smaller function.

## Final Report

Mention behavior impact, compatibility impact, tests, human-confirmation points, and remaining risks when relevant.

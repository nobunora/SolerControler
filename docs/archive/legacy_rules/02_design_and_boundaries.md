# Design and Boundaries

## Responsibility Split

Keep these concerns separate whenever the design allows it:

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

## File and Function Boundaries

- Prefer one main responsibility per file.
- Prefer one responsibility per function.
- Keep entrypoints thin. They should wire modules together, not own policy.
- Put domain decisions in domain code, not in UI or plumbing code.
- If a control, state flag, or action label can diverge, derive them from the same state snapshot.
- If a state is getting many booleans, prefer a state machine or discriminated union before adding more flags.
- If reset order matters, keep the order explicit and add a test before abstracting it away.

## Dependency Direction

- Keep shared modules dependency-light.
- Keep outer layers dependent on inner layers, not the other way around.
- Do not let presentation code reach into storage or protocol details directly when an adapter can absorb the detail.

## Abstraction Rule

- Add a new abstraction only when it removes duplication, clarifies intent, or matches an existing boundary.
- Do not add an abstraction just because it might be useful later.
- If a boundary move changes behavior, split the move from the behavior change.
- For refactors, move code without changing behavior first, then change behavior in a separate step.

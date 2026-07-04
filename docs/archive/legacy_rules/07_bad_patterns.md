# Bad Patterns

## Avoid These

- code that works now but has no intent
- duplicate logic with tiny edits
- hard-coded values without a basis
- swallowed exceptions
- debug code left behind
- commented-out old implementations
- unnecessary abstraction
- superficial fixes that hide the root cause
- missing boundary checks
- adding options or state that the spec did not ask for
- adding dependencies without a clear need
- noisy diffs that mix unrelated work
- untyped or `any` escapes without a strong reason

## Rule of Thumb

Working code is the minimum bar, not the finish line.

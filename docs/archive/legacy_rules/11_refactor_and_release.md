# Refactor and Release

## Refactor Guardrails

- No feature + refactor mix.
- No formatting churn with behavior change.
- No UI text/save/state/contract drift without request.
- Label + command from same state.
- Reset order stays explicit.

## Stateful UI Rule

- UI save/preview/progress/workflow = behavior.
- Protect current behavior first.
- Prefer state machine over flag soup.

## Release Gate

- Serial release gate.
- No parallel watcher/browser conflicts.
- No deploy before gate.

## Typical Serial Order

1. static checks
2. unit tests
3. browser or flow checks
4. build/package check

## Commit Rule

- One commit, one sentence.
- Split behavior tweak from refactor.
- Fix current scope first.

## Review Rule

- Name behavior changes.
- Say who/what is affected.
- Say rollback cost.

# Development Playbook

## Work Intake

- Metadata first.
- Search first.
- Read only needed docs.
- Risky area? Read the matching safety/release doc.

## Scope Control

- One task, one purpose.
- Split big work into phases.
- Smallest reviewable cut.

## Refactor Flow

- Freeze behavior first.
- Add tests before moving stateful code.
- Refactor ≠ feature.
- No unrelated cleanup.

## Implementation Flow

- Thin entrypoint.
- Policy in the right layer.
- Use domain names.
- Simple over abstract.

## Verification Flow

- Nearest tests first.
- Release gate for wide/risky changes.
- Serial if watchers conflict.
- Record the commands.

## Collaboration Flow

- Subagents only if cheaper.
- Clear role, scope, stop.
- No duplicate search.
- Short, specific summaries.

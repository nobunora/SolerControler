# Scripts

## Standard Script Contract

These names are intentionally generic so each project can bind them to its own toolchain.

| Script | Intent |
| --- | --- |
| `doctor` | Env check. |
| `check` | Quick verify. |
| `typecheck` | Static check. |
| `test` | Main tests. |
| `build` | Build/package. |
| `refactor:check` | Serial refactor gate. |
| `release:check` | Serial release gate. |

## Script Rules

- Keep script names stable once a project starts using them.
- Prefer scripts over ad hoc one-off commands.
- Serial when watcher/browser conflicts exist.
- Document live-port/browser deps.
- Gate deploy/publish behind preflight.
- No parallel build + watcher flow checks.

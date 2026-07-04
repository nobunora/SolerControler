# Safety and Poka-Yoke

## Hard-Coding

- Do not hard-code values without a reason.
- If a value is fixed by design, name it as a constant and explain why it is fixed.
- Keep values that may change in config, environment, or data files instead of scattering them through code.

## Guardrails

- Fail fast on malformed or unexpected input.
- Validate risky data before expensive work, allocation, or side effects.
- Prefer reversible or idempotent steps for destructive flows when possible.

## Debug and Temporary Code

- Do not leave debug logs, debug statements, commented-out code, or temporary bypasses behind.
- Do not keep TODOs that are really unfinished work.

## Security and External Risk

- Do not guess on authentication, authorization, tokens, secrets, or billing.
- Treat file uploads, external URLs, webhooks, and sensitive logs as high-risk surfaces.
- Verify before changing behavior in security-sensitive areas.

## Dependency Rule

- Add new dependencies only when the need is clear and the maintenance and security cost are acceptable.
- Prefer the standard library or existing project tools first.

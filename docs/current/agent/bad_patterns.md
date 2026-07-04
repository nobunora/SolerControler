# Bad Patterns

Use this file when touching large implementations, refactors, exceptions, dependencies, or risky surfaces.

## Avoid

- code that works now but has no clear intent
- duplicate logic with tiny edits
- hard-coded values without a basis
- swallowed exceptions
- debug code left behind
- commented-out old implementations
- temporary bypasses
- unnecessary abstraction
- superficial fixes that hide the root cause
- missing boundary checks
- options or state not requested by the spec
- dependencies without a clear need
- noisy diffs that mix unrelated work
- untyped or `any` escapes without a strong reason

## Hard-Coding

- Do not hard-code secrets, credentials, URLs, env names, IDs, dates, money, timeouts, retries, permissions, or file paths.
- If fixed by design, name it as a constant and explain why.
- Put changeable values in config, env, DB, or data files.

## Exceptions

- Do not swallow exceptions casually.
- If swallowing is required, state which exception is ignored, why it is safe, whether logging is needed, and how other exceptions flow.

## Security

Do not guess when changing:

- authentication or authorization
- sessions, cookies, CSRF, XSS, SQL injection, SSRF, CORS
- encryption, tokens, API keys, secrets
- personal data, sensitive logs, file uploads, external URLs, webhooks
- admin, billing, payments, or money-related behavior

## Dependencies

Before adding a dependency, check:

- existing project tools
- standard library
- maintenance status
- license
- security risk
- bundle or runtime impact
- testability

Explain any new dependency in the final report.

# bad_patterns.md

## Purpose of This File

This file defines the bad patterns that are common in AI-generated code, along with the detailed rules for what is prohibited and how to improve them.

It is a rule set for eliminating each bad pattern directly, not for vaguely "cleaning things up all at once."

## Prohibition on Code That Works But Has No Intent

Codex must not write code like the following:

- Code that only works for now
- Code that only passes the tests
- Code that satisfies only part of the specification
- Code whose reasoning cannot be explained
- Code that is not connected to the existing design
- Code whose judgment rationale is unclear to a human reader
- Temporary code that is meant to be fixed later
- Code that still contains debug remnants
- Code with unnecessary branches left behind
- Code with unused variables, functions, or types left behind
- Code that copies similar logic and makes minor changes
- Code that works around issues with hard-coded temporary values

Working is the minimum requirement, not a sufficient condition.

## No Duplicate Code

Codex must not casually copy existing code and make small changes to it.

If duplication seems necessary, check the following:

- Is it really the same responsibility?
- What is the specification-level difference?
- Would commonization make it harder to read?
- How large is the risk of changing the existing code?
- Is it likely to need to be changed together in the future?
- Is it likely that only one side will change?

If duplication is kept, explain why it is not being unified.

If it is unified, explain why it is being unified now.

## No Hard-Coding

Codex must not hard-code values without a basis.

Items especially prohibited:

- Fixed user IDs
- Fixed environment names
- Fixed URLs
- Fixed credentials
- Fixed timeout values
- Fixed retry counts
- Fixed amounts of money
- Fixed dates
- Fixed permission names
- Fixed file paths
- Fixed display text
- Fixed magic numbers

If hard-coding is necessary, it must satisfy the following:

- The constant name expresses the meaning.
- Why the value is what it is must be explained in a comment or document.
- Values that may change should be separated into config files, environment variables, DB values, or similar.
- The meaning of the value must be verifiable in tests.

Bad example:

```ts
if (retryCount > 3) {
  throw error;
}
```

Good example:

```ts
const MAX_PAYMENT_RETRY_COUNT = 3;

// Temporary failures from the payment gateway usually recover within two retries.
// If the failure continues past three attempts, treat it as permanent and avoid double charging.
if (retryCount > MAX_PAYMENT_RETRY_COUNT) {
  throw error;
}
```

## No Debug Code Left Behind

Codex must not leave debug code behind.

Examples of prohibited code:

- Leaving `console.log`
- Leaving `print`
- Leaving temporary `alert`
- Leaving `debugger`
- Leaving placeholder `return`
- Leaving test IDs
- Writing only `TODO` and leaving it unfinished
- Leaving old implementations commented out
- Temporarily swallowing errors
- Temporarily skipping authentication
- Temporarily relaxing validation

Any required logging must be added as formal logging according to the existing logging policy.

## No Commented-Out Code

Codex must not leave old implementations commented out.

Reasons:

- Human readers cannot tell whether it is needed or not
- Future readers may mistake it for a candidate implementation
- It looks like AI-generated debris
- It increases review burden
- If it is really needed, Git history can be used to find it

If it must remain exceptionally, write a clear reason, deadline, and removal condition.

## Exception-Handling Rules

Codex must not casually swallow exceptions.

Bad example:

```ts
try {
  await updateUser(user);
} catch (e) {
  // ignore
}
```

If exceptions are swallowed, explicitly state the following:

- Why it is okay to ignore it
- Which exceptions are ignored
- How other exceptions are handled
- Whether logging is needed
- Whether the user needs to be notified
- Whether it should be retried
- Whether it should be rethrown to the upper layer

Good example:

```ts
try {
  await markNotificationAsRead(notificationId);
} catch (error) {
  if (isAlreadyArchivedNotificationError(error)) {
    // If the notification is already archived, marking it as read is unnecessary.
    // Treat this as a successful user action.
    return;
  }

  throw error;
}
```

## Security Rules

Codex must not guess when implementing security-sensitive processing.

If the work involves any of the following, ask a human:

- Authentication
- Authorization
- Session
- Cookies
- CSRF
- XSS
- SQL Injection
- SSRF
- CORS
- Encryption
- Tokens
- API keys
- Personal information
- Privilege escalation
- File uploads
- External URL access
- Webhooks
- Admin features
- Billing
- Payments
- Information that may be logged

For security-related processing, prioritize safety over convenience.

## Dependency-Addition Rules

Codex must not casually add new libraries.

Before adding one, check the following:

- Can the existing dependencies handle it?
- Can the standard library handle it?
- Is the reason for adding it clear?
- Is the maintenance status acceptable?
- Is the license acceptable?
- Is there an impact on bundle size?
- Is there a security risk?
- Does it fit the existing design?
- Can tests be written?

If a new dependency is added, explain the reason in the final report.

## No AI-Style Overengineering

Codex must not make large design changes that were not requested.

Bad examples:

- Adding a large abstraction for a small fix
- Converting a simple existing function into a complex class structure
- Creating extension points that are not used
- Generalizing something just because it might be useful later
- Over-commonizing processing that is only used in one place
- Adding options that are not in the specification
- Adding configuration items that are not in the specification
- Adding error categories that are not in the specification
- Adding state management that is not in the specification
- Adding caching that is not in the specification

Keep the change as small as possible.

However, if it is truly necessary under the existing design, do it only after explaining why.

## No AI-Style Underimplementation

Codex must not make superficial fixes that only satisfy the surface of the request.

Bad examples:

- Changing only the expected values in tests to make them pass
- Ignoring errors to make them pass
- Silencing type errors with `any`
- Adding only a null check and leaving the root cause untouched
- Hiding symptoms without checking the root cause of an existing bug
- Passing only some of the specification cases
- Not checking boundary conditions
- Not checking asynchronous race conditions
- Not checking existing callers
- Not reading the reason for existing test failures

Fix the cause, not the symptom.

## Type-Related Rules

Codex should use types to express intent.

Bad examples:

- Unnecessary `any`
- Leaving unnecessary `unknown` in place

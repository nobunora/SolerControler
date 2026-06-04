# code_review.md

## Purpose of This File

This file defines the self-review that Codex must always perform after implementation, and the review points that humans should use when reviewing.

The review should check not only whether it works, but also whether the reasoning can be followed.

## Post-Implementation Self-Review

After implementation, Codex must check the following:

- Whether the specification is satisfied
- Whether any out-of-spec changes were made
- Whether the code matches the existing design
- Whether the names are specific
- Whether the naming relies only on generic words
- Whether responsibilities are mixed
- Whether there is unnecessary abstraction
- Whether there is unnecessary duplication
- Whether exception handling is appropriate
- Whether there are any security-risky changes
- Whether any debug code remains
- Whether any unused code remains
- Whether comments explain the reason
- Whether the tests are sufficient
- Whether the diff is easy for a human to review
- Whether the change reason can be explained
- Whether it will still be readable in six months

## Pre-Implementation Review

Check the following:

- Whether the change purpose is clear
- Whether the change target is clear
- Whether the scope that will not be changed is clear
- Whether you can explain which part of the existing design it matches
- Whether you checked past chat history, memory, specifications, README, and issues as far as available and necessary
- Whether you told a human if past context could not be checked
- Whether you filled in unknowns by guessing
- Whether you left any unknowns that should be confirmed by a human unresolved

## Design Review

Check the following:

- Whether this change matches the existing responsibility boundaries
- Whether a new responsibility was forced into an unsuitable existing place
- Whether a new abstraction is truly necessary
- Whether the reason for the abstraction can be explained
- Whether you checked similar existing implementations
- Whether you can explain why the code differs from the existing implementation if it does
- Whether the work has become a local optimization based on only one file
- Whether future maintainers can follow the design intent

## Naming Review

Check the following:

- Whether you avoided using generic nouns such as data, result, item, value, temp, and obj too casually
- Whether you avoided overusing low-meaning names such as handler, manager, processor, service, helper, and util
- Whether you avoided naming functions only with generic verbs such as process, execute, handle, run, update, fix, check, and validate
- Whether the name reveals the domain meaning
- Whether the name reveals the state
- Whether the name reveals the processing target
- Whether the name reveals the processing purpose
- Whether the type, function, and variable names match the existing naming rules
- Whether public API names, DB table names, DB column names, configuration keys, environment variable names, and external integration field names were not changed only for naming improvement
- Whether widely used names in the existing code were not renamed for reasons unrelated to the target change

## Responsibility Review

Check the following:

- Whether input validation and data retrieval are too mixed
- Whether permission checks and state updates are too mixed
- Whether presentation formatting and persistence are mixed
- Whether external API calls and domain decisions are too mixed
- Whether logging makes the main logic hard to read
- Whether retry logic is too mixed with the essential processing
- Whether the inputs and outputs of functions are clear
- Whether side effects are clear

## Exception Handling Review

Check the following:

- Whether exceptions are being swallowed
- If exceptions are swallowed, whether the reason is clear
- Which exceptions are handled, and which are not
- Whether unexpected exceptions are propagated upward
- Whether logs are written where they are needed
- Whether sensitive information is kept out of logs
- Whether user-facing errors and internal errors are separated
- Whether retriable failures and non-retriable failures are separated

## Test Review

Check the following:

- Whether normal cases were checked
- Whether error cases were checked
- Whether boundary values were checked
- Whether null/undefined was checked
- Whether empty arrays, empty strings, and zero were checked
- Whether insufficient permissions were checked
- Whether external API failures were checked
- Whether timeouts were checked
- Whether retries were checked
- Whether compatibility with existing data was checked
- Whether there is a regression test for a past bug
- Whether important cases explicitly requested by the user were checked
- Whether you can explain why tests were not added if none were added
- Whether you provided the reason and the command the human should run if tests could not be executed

## Security Review

Check the following:

- Whether authentication was bypassed
- Whether authorization was weakened
- Whether the placement of permission checks is appropriate
- Whether personal information is written to logs
- Whether API keys or tokens are embedded in code
- Whether external URL access is validated properly
- Whether file upload validation is appropriate
- Whether SQL injection is possible
- Whether XSS is possible
- Whether CSRF is possible
- Whether CORS is too open
- Whether webhook validation is weakened
- Whether changes related to billing, payments, or money were made by guessing

## Diff Review

Check the following:

- Whether the scope of the change is minimal
- Whether unrelated formatting was mixed in
- Whether unrelated refactoring was mixed in
- Whether changes with different purposes were mixed into the same diff
- Whether the entire file was reordered
- Whether formatting-only changes were mixed with substantive changes
- Whether so much generated code was added that review is difficult
- Whether the final report identifies the parts a human should focus on

## Existing Behavior Review

Check the following:

- Whether existing behavior was changed
- If existing behavior changed, whether that fact was made explicit
- Which inputs cause different results
- Who is affected
- Whether existing data is affected
- Whether existing APIs are affected
- Whether existing tests are affected
- Whether rollback is possible
- Whether human confirmation is needed

## Final Report Review

Codex must include the following in the final report:

- Change summary
- Design intent
- Alignment with the existing design
- Alternatives and why they were not chosen
- Files changed
- Scope that was not changed
- Tests
- Points a human should confirm
- Remaining risks

## Prohibited Final Reports

Codex must not give reports like the following:

- "Fixed it" only
- "Tested it" only
- "There is no problem" only
- "Improved readability" only
- "Matched the existing code" only
- "It was a minor change" only
- Reporting tests as executed when they were not
- Reporting specifications as confirmed when they were not
- Hiding unknowns
- Hiding risks
- Writing guesses as facts

## Commit Message Review

Commit messages should include not only what was done, but why it was done.

Bad example:

```txt
fix user error
```

Good example:

```txt
Fix expired trial user deactivation guard

Trial users with already-cancelled subscriptions were being processed twice.
This adds an explicit guard before deactivation to preserve idempotency.
```

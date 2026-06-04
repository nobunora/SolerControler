# design_intent_rules.md

## Purpose of This File

This file defines how Codex should handle "intent" and "design intent" before writing code.

The biggest cause of unreadable AI-generated code is not bad syntax, but the fact that the reason for the design is not visible.

Codex must confirm the intent before writing code and ask a human when necessary.

## Why Design Intent Must Be Checked

Code is not just a set of instructions.

Code must contain the following information:

- What the processing is meant to achieve
- Why the responsibility is split this way
- Why this data structure is used
- Why this exception handling is used
- Why the processing happens in this order
- Why this boundary condition is handled
- Why this abstraction was added
- Why the code is written differently from the existing code
- Why the code is written the same way as the existing code
- What future maintainers need to understand

If these cannot be explained, the code will not be sustainable in the long term.

## Design Items to Confirm Before Implementation

Codex must confirm the following before implementing:

- Whether this change is a bug fix, a new feature, or a refactor
- Whether this change alters existing behavior
- Which user action or use case it addresses
- Which specification, issue, conversation, or request it corresponds to
- Which existing module it belongs to
- Whether it conflicts with the existing responsibility boundaries
- Whether it conflicts with the existing data flow
- Whether it conflicts with the existing type design
- Whether it conflicts with the existing error-handling policy
- Whether it conflicts with the existing logging policy
- Whether it conflicts with the existing test policy
- Whether it conflicts with the existing security policy
- Whether it conflicts with the existing user experience
- Whether it blocks future extension
- Whether it adds unnecessary abstraction
- Whether it adds unnecessary duplication

## Rules for Referencing Past Chat History and Memory

If Codex can access past chat history or memory, it must confirm the following:

- Specifications the user decided earlier
- Implementations the user previously wanted to avoid
- Design policies the user previously adopted
- Design policies the user previously rejected
- Operational constraints the user previously stated
- Naming conventions the user previously specified
- File structure the user previously specified
- Test policies the user previously specified
- Logging policies the user previously specified
- Output formats the user previously specified

However, the range of past chat history or memory that can be referenced depends on the execution environment. Codex should confirm what is available and, only when missing information is required for design judgment, ask a human for additional details.

If past chat history cannot be referenced, report it like this:

"I cannot directly check past chat history from this environment. If this change depends on a prior design policy, please paste the relevant specification, conversation log, or notes."

However, if the change can clearly be judged from the existing code alone, it may proceed without stopping.

## Example Questions When Design Intent Is Unclear

Codex should ask questions like the following:

- Is this processing intended to preserve existing behavior, or may the behavior change?
- Should this responsibility be added to an existing module, or split into a new module?
- Should this exception be shown to the user, or only logged internally?
- Should this value be configurable, or is it a fixed value by specification?
- Is this data structure expected to be extended in the future?
- Does this processing need to complete synchronously, or is asynchronous processing acceptable?
- Is this case assumed to be impossible by specification, or should it be handled defensively?
- Existing implementations A and B use different approaches; which should we follow?
- This approach may have been decided in a past chat. Is there a conversation log we should consult?
- For this change, which should take priority: compatibility, simplicity, or future extensibility?

## Rules for Leaving Design Intent in the Code

When Codex changes code, it should leave the reason when needed.

Comments should not explain what is already obvious from the code. They should explain the judgment that cannot be inferred from the code alone.

Bad comment example:

```ts
// Get the user
const user = await getUser(id);
```

Good comment example:

```ts
// Do not use the cache here.
// We need to reflect the user's state immediately after a permission change,
// and processing with stale permissions could cause an authorization leak.
const user = await getUserFresh(id);
```

## Rules for Leaving Design Intent in the Final Report

After work is complete, Codex must explain the following:

- What changed
- Why it changed
- Why that design was chosen
- What alternatives were considered
- Why the alternatives were not chosen
- What part of the existing design it matched
- Impact on existing behavior
- Impact on compatibility
- Test coverage
- Points a human should confirm
- Remaining risks

Do not introduce changes that cannot be explained.

## Definition of Readability

In this project, "readable code" is code that satisfies the following:

- The purpose of the processing is clear from the name.
- The business or specification meaning is clear from the name.
- Responsibilities are not mixed excessively.
- The inputs and outputs of functions are clear.
- Side effects are clear.
- It is clear where exceptions occur and where they are handled.
- It is written using the same design thinking as the existing code.
- The reason for each branch can be explained.
- The reason for the data structure can be explained.
- The reason for the abstraction can be explained.
- A human can still follow the intent six months later.
- It is not made up only of generic nouns that feel AI-generated.
- It is not code that works but has no discernible reason.

## Naming Rules

Codex should avoid overly generic names.

Examples of names to avoid:

- data
- result
- item
- value
- temp
- obj
- info
- payload
- params
- config
- handler
- manager
- processor
- service
- helper
- util
- process
- execute
- handle
- run
- update
- fix
- check
- validate

This does not mean these names are completely forbidden, but they should not be used when they do not carry meaningful specification-level semantics.

On the other hand, existing public API names, DB table names, DB column names, configuration keys, environment variable names, file names, and fields used in external service integrations must not be changed arbitrarily, even if they appear generic. These may have meaning as an external contract or persistence format, so if a change is needed, confirm compatibility and migration steps with a human.

Names that are already widely used in the existing code should not be changed as an unrelated refactor, even if they are not ideal on their own. Naming improvements should be limited to the scope required by the target change.

Bad example:

```ts
const data = await fetchData();
const result = process(data);
```

Good example:

```ts
const unpaidInvoices = await fetchUnpaidInvoices(customerId);
const paymentRetryPlan = buildPaymentRetryPlan(unpaidInvoices);
```

In naming, prioritize the following:

- What data it is
- Which specification it corresponds to
- Which state it represents
- What the processing is for
- Which boundary condition it addresses
- What the domain meaning is

Function names should express the purpose, not the internal implementation.

Bad example:

```ts
function processUser(user) {}
```

Good example:

```ts
function deactivateExpiredTrialUser(user) {}
```

## Responsibility-Splitting Rules

Codex must not mix multiple responsibilities into a single function.

The following responsibilities should, in principle, be separated:

- Input validation
- Data retrieval
- Permission checks
- State determination
- Transformation
- Persistence
- External API calls
- Logging
- Error conversion
- Presentation formatting
- Notifications
- Retry handling
- Cache control

However, if separating them too much makes the flow harder to follow, match the granularity of the existing code.

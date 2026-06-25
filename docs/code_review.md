# Code Review

Use this file for self-review or review requests.

## Pre-Implementation

- Is the purpose clear?
- Is the target clear?
- Is the non-goal scope clear?
- Does the plan match existing design?
- Are unknowns verified or explicitly left for a human?

## Post-Implementation

- Does the change satisfy the request?
- Did any out-of-scope change slip in?
- Does the code match existing design?
- Are names specific and domain-meaningful?
- Are responsibilities mixed?
- Is there unnecessary abstraction or duplication?
- Is exception handling appropriate?
- Are there security-risky changes?
- Does debug, commented-out, or unused code remain?
- Do comments explain reasons where needed?
- Are tests sufficient for the risk?
- Is the diff easy to review?
- Can the intent still be understood in six months?

## Test Review

- Cover normal, error, boundary, empty, null or missing inputs as relevant.
- Cover compatibility with existing data when contracts or persistence are touched.
- Cover external failures, timeouts, retries, and permissions when relevant.
- If tests cannot run, state why and name the exact command to run.

## Existing Behavior

If behavior changed, state:

- what changed
- affected users, inputs, data, APIs, or tests
- rollback feasibility
- human confirmation needed

## Final Report

Include:

- change summary
- design intent
- alignment with existing design
- alternatives not chosen
- files changed
- scope not changed
- tests
- human confirmation points
- remaining risks

# Code Review

## Post-Implementation Self-Review

- Does the change satisfy the request?
- Did any out-of-scope change slip in?
- Does the code match the existing design?
- Are the names specific?
- Are responsibilities mixed?
- Is there unnecessary abstraction?
- Is there unnecessary duplication?
- Is exception handling appropriate?
- Are there any security-risky changes?
- Does any debug code remain?
- Does any unused code remain?
- Do comments explain the reason where needed?
- Are the tests sufficient?
- Is the diff easy for a human to review?
- Can the change still be understood in six months?

## Pre-Implementation Review

- Is the change purpose clear?
- Is the change target clear?
- Is the scope that will not change clear?
- Can the change be explained in terms of the existing design?
- Are there any unknowns that should be confirmed first?

## Final Report Review

- Change summary
- Design intent
- Alignment with the existing design
- Alternatives and why they were not chosen
- Files changed
- Scope not changed
- Tests
- Points a human should confirm
- Remaining risks
- If behavior changed, explicitly name the affected inputs, users, or APIs.

# Testing

## What to Cover

- normal cases
- error cases
- boundary values
- empty inputs
- null or missing inputs
- regression cases
- compatibility with existing data
- external failure paths
- timeouts and retries when relevant
- permission or authorization cases when relevant

## Order of Execution

- Start with tests closest to the changed code.
- Add focused regression tests before broad refactors.
- Run broader checks only when the impact range is wider.
- If a browser/dev-server watcher conflicts with a UI flow check, run them serially, not in parallel.
- If a release gate exists, use it as a serial sequence instead of ad hoc command ordering.

## Fixture Rule

- Keep fixtures small when possible.
- Generate large or expensive data when a real large fixture is needed.

## Reporting Rule

- Never claim a test ran if it did not.
- If a test cannot run, say why and name the exact command a human should run.
- If behavior changed, call that out and mention the affected tests.
- If a check failed because of the test environment, say whether the same command should be retried alone.

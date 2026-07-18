# Phase 04: Forced-Charge Orchestration

## Objective

Reduce `cloud_job_runner.py` forced-charge complexity without changing safety behavior.

Move decisions into pure state transitions. Keep KP-NET calls, sleeping, time reads, logging, and persistence in a thin orchestration shell.

## Prerequisites

Phase 03 must be completed.

Read only:

1. `AGENTS.md`
2. `00_EXECUTION_PROTOCOL.md`
3. This file
4. The latest Phase 03 handoff in `PROGRESS.md`
5. `app/forced_charge/state_machine.py`
6. Direct symbol ranges and tests named by the handoff

Locate targets:

    rg -n "_monitor_partial_forced_and_stop|forced_charge|target_soc|cutoff|timeout|retry|sleep|stop" cloud_job_runner.py app/forced_charge app/settings tests/test_forced_charge_state_machine.py tests/test_cloud_job_runner.py

Do not read all of `cloud_job_runner.py`.

## Safety contracts

Preserve exactly:

- Start conditions
- Continue conditions
- Target-SoC stop conditions
- Time cutoff
- Timeout
- Retry count and delay
- Missing-SoC behavior
- Read-error behavior
- Command-error behavior
- Fail-safe stop behavior
- Existing persisted status and diagnostic fields
- Existing environment keys and defaults

Any uncertainty in these contracts blocks the phase.

## Target architecture

Use this separation:

    external observation
        -> typed observation
        -> pure state transition
        -> typed decision and next state
        -> orchestration executes command
        -> execution result becomes next observation

The pure transition must not:

- Call KP-NET
- Read current time
- Sleep
- Read environment variables
- Persist state
- Log as control flow
- Catch network exceptions

The orchestration shell may perform those actions but must not duplicate transition policy.

## Step 04.1: Freeze transition behavior

Use existing Phase 01 safety characterization tests.

Ensure coverage for:

- Initial start
- Already above target
- Progress below target
- Reaching target
- Cutoff reached
- Timeout reached
- Missing SoC
- Temporary read failure
- Permanent read failure
- Start-command failure
- Stop-command failure
- Retry exhaustion
- Already stopped or inactive state

Add only missing characterization tests.

Do not call real devices or services.

## Step 04.2: Define typed inputs and outputs

Use focused immutable models.

Likely concepts:

- `ForcedChargeObservation`
- `ForcedChargeState`
- `ForcedChargeDecision`
- `ForcedChargeCommand`
- `ForcedChargeSettings`

Names may differ if existing types already cover the concept.

A decision should explicitly represent:

- Start
- Continue
- Stop
- Skip
- Retry
- Fail

Do not encode decisions as loosely structured dictionaries or magic strings when a typed enum or model is practical.

Preserve existing serialized values at the serializer boundary.

## Step 04.3: Inject time

The pure transition receives the relevant time or elapsed duration as input.

The orchestration shell owns the clock.

Requirements:

- Preserve timezone
- Preserve cutoff comparison semantics
- Preserve inclusive or exclusive boundary behavior
- Preserve timeout starting point
- Avoid direct `datetime.now()` inside domain logic

Add exact-boundary tests for cutoff and timeout.

## Step 04.4: Inject external ports

Define narrow Protocols only for actual orchestration needs.

Possible ports:

- Read current SoC
- Send start command
- Send stop command
- Load or save forced-charge status
- Sleep or scheduling clock

Do not create one generic KP-NET client Protocol exposing unrelated operations.

Production adapters may wrap existing functions. Public entrypoints should remain compatible.

## Step 04.5: Extract one decision branch at a time

Recommended order:

1. Stop because target SoC is reached
2. Stop because cutoff is reached
3. Stop because timeout is reached
4. Continue while below target
5. Missing or failed SoC read
6. Start-command handling
7. Stop-command handling
8. Retry exhaustion and final fail-safe

For each branch:

1. Add or confirm a characterization test.
2. Add the pure transition result.
3. Route the existing branch through it.
4. Run nearest tests.
5. Commit separately when the branch is safety-critical.

Do not rewrite the entire monitor loop at once.

## Step 04.6: Thin the monitor loop

After all decisions use the state machine, `_monitor_partial_forced_and_stop` should primarily:

1. Obtain time.
2. Read external state.
3. Build a typed observation.
4. Call the transition function.
5. Execute the returned command.
6. Persist and log the result.
7. Sleep only when instructed.
8. Repeat or return.

The loop may retain operational logging and exception translation.

It must not independently recalculate stop or retry policy already represented by the state machine.

Line count is not the acceptance criterion. Single ownership of policy is.

## Step 04.7: Move settings to the boundary

For forced-charge environment values:

1. Record current keys and defaults.
2. Preserve blank-string and invalid-value behavior.
3. Parse once using `app/settings/forced_charge.py` or the nearest focused settings module.
4. Pass typed settings to orchestration and state logic.
5. Keep compatibility entrypoints for existing callers.

Do not migrate unrelated cloud-job settings.

Never output secret values.

## Step 04.8: Exception handling

Preserve fail-safe behavior.

At external boundaries:

- Catch specific transport or device errors where known.
- Retain original cause with exception chaining.
- Convert expected failures into typed observations or execution results.
- Allow unexpected programming errors to remain visible unless current safety behavior requires a final stop attempt.

Before narrowing an existing broad catch, test what happens when:

- Read fails
- Start fails
- Stop fails
- Persistence fails
- Logging fails, if logging is currently non-fatal

Do not make a previously non-fatal condition fatal, or the reverse.

## Required tests

Run in small groups:

    python -m pytest -q tests/test_forced_charge_state_machine.py
    python -m pytest -q tests/test_cloud_job_runner.py
    python -m pytest -q tests/test_kpnet_settings_intent.py tests/test_kpnet_workflow.py

Then:

    python -m compileall -q app cloud_job_runner.py
    git diff --check

Run targeted mypy for changed forced-charge modules when available.

## Stop conditions

Stop when:

- Safety behavior cannot be proven.
- Existing tests and current implementation disagree.
- A real device command is required for validation.
- Cutoff or timeout timezone semantics are unclear.
- A new state would change persisted compatibility.
- A transition requires unrelated cloud-job behavior.
- The refactor would require a full runner rewrite.
- Targeted tests fail before the relevant change.

## Completion gate

Phase 04 is complete only when:

- Transition policy exists in one state-machine boundary.
- The monitor loop does not duplicate decision policy.
- Time and external services are injected.
- Safety branches have deterministic tests.
- Environment keys and defaults remain compatible.
- No real external writes were used in tests.
- Targeted tests, compile checks, and `git diff --check` pass.
- Safety-critical changes are committed in small units.
- `PROGRESS.md` records the resulting state model and Phase 05 target symbols.

## Required handoff to Phase 05

Provide:

- State, observation, decision, and command types
- External ports introduced
- Compatibility wrappers retained
- Exact tests covering fail-safe behavior
- Remaining broad catches and their justification
- Forced-charge settings moved
- Maximum six dashboard files or symbol ranges to inspect
- Forced-charge files Phase 05 must not reread
## Vision alignment for this phase

This phase must be interpreted through `VISION_AND_DECISION_PRINCIPLES.md`.

Before performing any step in this phase, answer:

- Which forced-charge decisions currently exist in more than one place?
- Which decisions belong to a pure state model and which actions belong to the imperative shell?
- Which fail-safe, timeout, stop, retry, telemetry, and operator-visible behaviors must remain unchanged?
- What local simplification could weaken safety while making the function look cleaner?
- What evidence will prove that the same inputs produce the same decisions before and after extraction?

The objective is not a shorter monitor function. The objective is one explicit owner for forced-charge decisions.

## Why this phase is necessary

Forced-charge monitoring currently combines state interpretation, safety policy, timeout handling, device actions, logging, telemetry, and loop control.

When these responsibilities remain interleaved:

- The same stop decision can be encoded in several branches.
- Safety behavior is difficult to test without running imperative code.
- A small change to logging or device interaction can affect decision flow.
- Retry and timeout semantics can drift from state-machine expectations.
- Broad exception handling can obscure whether the system failed safe.
- Future integrations may copy part of the monitor logic instead of reusing one decision owner.

This is a safety and maintainability risk, not merely a long-function problem.

## Phase-specific final target

At the end of this phase:

- Forced-charge policy is expressed as pure, deterministic state transitions or decisions.
- The imperative shell performs reads, writes, waits, logs, and telemetry according to those decisions.
- Safety stops, completion, timeout, retry, and failure behavior have one explicit owner.
- Existing operator-visible behavior and external contracts remain stable.
- The monitor loop is understandable as coordination rather than embedded policy.
- New device or telemetry adapters can be added without copying forced-charge decisions.

The target is not to eliminate the loop.

The target is to prevent the loop from defining safety policy.

## How this phase contributes to the final architecture

The final architecture requires orchestrators to coordinate work without duplicating business or safety decisions.

This phase contributes by:

- Moving decision logic into a pure and testable owner
- Keeping device access and timing as boundary concerns
- Making fail-safe behavior explicit
- Reducing the repository context required to understand forced-charge outcomes
- Allowing simulation and exhaustive state testing without hardware
- Preventing future runners from implementing their own interpretation of stop and retry policy

## Phase-specific local-optimization risks

Do not optimize this phase by:

- Narrowing or removing exception handling without proving equivalent fail-safe behavior
- Combining distinct stop reasons into one generic outcome
- Changing timeout or retry order to simplify control flow
- Treating missing telemetry as success
- Moving imperative actions into the state model
- Hiding all runtime data in one untyped context dictionary
- Replacing explicit states with booleans that lose meaning
- Removing operator-visible logs or reason codes
- Splitting the function into helpers while leaving policy duplicated across them
- Declaring success because the original function has fewer lines
- Testing only happy-path completion

A structurally cleaner implementation is invalid if it weakens safety semantics.

## Required evidence for completion

Behavior evidence must include:

- Characterization of current completion, stop, timeout, retry, and failure paths
- Pure-state tests for every meaningful transition
- Old-versus-new decision parity for representative traces
- Tests for missing, stale, malformed, and contradictory observations
- Confirmation of preserved reason codes, logs, telemetry, and operator-visible outcomes
- Confirmation that device actions occur in the same safe order
- Failure-injection tests showing that unsafe continuation does not occur

Ownership evidence must include:

- The exact decisions removed from the imperative monitor
- The new state or decision owner and its responsibility
- Proof that the imperative shell no longer reinterprets state outcomes
- Proof that retries and timeouts are not separately encoded in multiple layers
- Evidence that a new runner can reuse the decision owner without copying policy

## Phase alignment decision

Before marking this phase complete, answer:

1. Does one component determine whether forced charging should continue, stop, retry, fail, or complete?
2. Does the imperative shell execute decisions without silently changing them?
3. Are all safety-relevant outcomes explicit and testable?
4. Have timeout, retry, and missing-data semantics remained stable?
5. Can device access fail without bypassing the fail-safe decision path?
6. Has the context required to understand the policy decreased?
7. Would a future runner reuse the same decision owner?

If safety policy still exists in both the state model and the loop, the phase is incomplete.

## What later phases must not undo

Later phases must not:

- Add new forced-charge policy branches directly to runner loops
- Bypass the state or decision owner for a special device path
- Replace explicit outcomes with ambiguous booleans
- Hide safety-relevant errors only to keep orchestration running
- Remove parity or failure-injection tests when changing integrations
- Couple the decision owner to KP-NET, logging, sleep, environment access, or a specific telemetry backend

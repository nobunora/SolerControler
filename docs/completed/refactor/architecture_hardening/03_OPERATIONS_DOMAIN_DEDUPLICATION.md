# Phase 03: Operations Domain Deduplication

## Objective

Remove duplicated Operations business calculations from SQLite, PostgreSQL, and Firestore implementations while preserving all storage contracts.

The highest-priority target is `recalc_cost_daily`. Other Operations rules follow only after daily-cost migration is stable.

## Prerequisites

Phase 02 must be completed.

Read only:

1. `AGENTS.md`
2. `00_EXECUTION_PROTOCOL.md`
3. This file
4. The latest Phase 02 handoff in `PROGRESS.md`
5. `app/operations/domain.py`
6. Direct definitions, callers, and tests named by the handoff

Locate targets with:

    rg -n "recalc_cost_daily|upsert_battery_metrics|update_model_params|update_prediction_hit_rates" app tests

Do not read all backend modules. Open only the target function and nearby mapping or transaction code.

## Target architecture

The desired flow is:

    backend rows or documents
        -> backend-specific mapper
        -> typed domain input
        -> pure domain calculation
        -> typed domain result
        -> backend-specific persistence

Business rules must exist once.

Backend-specific code may retain:

- SQL or Firestore queries
- Transactions and commit behavior
- Row or document mapping
- Retry behavior
- Backend-specific error translation
- Persistence batching
- Existing logging around external I/O

## Non-goals

Do not:

- Change tariff values or tier rules
- Change accounting-day boundaries
- Change database or Firestore schemas
- Introduce schema migrations
- Replace all repositories with one generic abstraction
- Migrate all Operations functions in one commit
- Change dashboard assembly
- Change energy-model behavior
- Add a new dependency
- Perform real cloud writes during validation

## Step 03.1: Confirm current parity

Use Phase 01 characterization fixtures.

For `recalc_cost_daily`, compare all existing implementations with identical logical inputs.

Required comparison dimensions:

- Empty day
- Import only
- Export only
- Tier boundaries
- Values immediately below and above boundaries
- Missing intervals
- Zero values
- Negative or malformed values according to current behavior
- Timezone day boundary
- Rounding
- Returned value
- Persisted fields

If implementations differ:

1. Do not choose a preferred backend.
2. Identify the smallest differing rule.
3. Record actual outputs in `PROGRESS.md`.
4. Mark Phase 03 blocked pending a product or domain decision.

## Step 03.2: Define typed daily-cost inputs and results

Prefer a focused module such as:

    app/operations/cost_daily.py

The final name may differ when an existing cohesive module is clearly better.

Create only the models required by the calculation.

Possible concepts:

- One interval or aggregate energy record
- Tariff parameters
- Accounting-day context
- Daily-cost calculation input
- Daily-cost result

Requirements:

- Immutable when practical
- Explicit units in names where ambiguity exists
- No database clients
- No environment reads
- No current-time reads
- No persistence methods
- Existing serialized field names remain outside the pure model unless they are true domain terms

Do not create one model containing every Operations field.

## Step 03.3: Extract the pure calculation

Use the existing implementation as the source of behavior, not an idealized redesign.

The pure function must receive every variable that affects its result.

It must not directly use:

- Database queries
- Firestore reads
- Environment variables
- Current time
- Logging as control flow
- Global tariff state

Add direct unit tests covering every characterization fixture.

Compare the new function against each old implementation before migrating any adapter.

## Step 03.4: Migrate SQLite first

SQLite is the first adapter because it is easiest to validate locally.

Migration sequence:

1. Keep the old function available.
2. Map SQLite rows into typed domain inputs.
3. Call the new pure calculation.
4. Persist the typed result using existing columns and transaction behavior.
5. Compare old and new calculated values in tests.
6. Run SQLite and Operations tests.
7. Remove only the duplicated SQLite calculation after parity is proven.

Do not change SQL shape merely to make the new domain function convenient.

Required tests:

    python -m pytest -q tests/test_operations_domain.py tests/test_operations_db.py

Also run any direct SQLite pipeline tests named in the Phase 02 handoff.

Commit SQLite migration separately.

## Step 03.5: Migrate PostgreSQL second

Start only after the SQLite commit is green.

Migration sequence:

1. Reuse the same typed inputs and pure calculation.
2. Keep PostgreSQL query, transaction, and error behavior intact.
3. Add or reuse backend parity fixtures.
4. Compare planned persisted values.
5. Run PostgreSQL-related tests without connecting to production.
6. Remove only PostgreSQL's duplicated calculation.

Do not create PostgreSQL-specific branches inside the pure calculation.

If backend-specific normalization is required, keep it in the PostgreSQL mapper and document why.

Commit PostgreSQL migration separately.

## Step 03.6: Migrate Firestore third

Start only after SQLite and PostgreSQL migrations are committed and green.

Migration sequence:

1. Map Firestore documents to the same typed domain input.
2. Preserve collection, document, field, and batch-write behavior.
3. Compare planned document updates in tests.
4. Preserve missing-document and partial-document handling.
5. Remove Firestore's duplicated calculation after parity passes.

Required tests include:

    python -m pytest -q tests/test_firestore_dashboard_metrics.py tests/test_dashboard_backend_parity.py

Also run direct Firestore Operations tests identified by search.

Do not perform real Firestore writes.

Commit Firestore migration separately.

## Step 03.7: Remove compatibility code

After all three adapters use the pure function:

- Remove unreachable duplicate calculation bodies.
- Retain public wrapper names when external callers may import them.
- Confirm wrappers delegate without adding behavior.
- Remove temporary comparison branches.
- Remove temporary debug output.
- Keep parity tests as permanent regression tests.

Do not delete a compatibility wrapper solely to reduce line count.

## Step 03.8: Migrate remaining Operations rules

Proceed one rule family at a time.

Recommended order:

1. Input normalization shared by backend adapters
2. Battery metric calculations
3. Model parameter calculations
4. Prediction hit-rate calculations
5. Other repeated derived metrics

For each family, repeat:

1. Characterize
2. Compare backends
3. Add pure function
4. Migrate SQLite
5. Migrate PostgreSQL
6. Migrate Firestore
7. Remove duplicates

Do not start the next family while the previous family has unexplained parity differences.

## Repository abstraction rule

Create a Protocol only when at least two real consumers require the same operation.

A repository Protocol should describe use-case needs, not expose a generic database API.

Good:

    load_daily_energy(...)
    save_daily_cost(...)

Bad:

    execute(query)
    get_collection(name)
    save_anything(data)

Do not force all backends into one implementation class.

## Environment boundary rule

When Operations code reads environment variables:

- Preserve each key and default.
- Parse in the composition root or backend factory.
- Pass typed settings to adapters.
- Migrate only Operations-related keys.
- Never print secret values.

Conflicting defaults are a stop condition.

## Error-handling rule

Preserve current transaction and failure behavior.

Do not remove broad fail-safe catches without characterization tests.

New domain code should raise focused validation or domain errors. Adapters may translate backend errors while retaining the original cause.

Do not turn a previously fatal persistence error into a warning, or the reverse, during this phase.

## Required checks after each adapter

Run the nearest tests first.

Then run:

    python -m compileall -q app
    git diff --check

Run targeted mypy for new Operations modules:

    python -m mypy app/operations

If unrelated existing errors prevent a clean result, record exact errors and confirm no new errors were introduced.

## Stop conditions

Stop when:

- Existing backends disagree on business output.
- Tariff or rounding behavior is not provable.
- A schema change appears necessary.
- A pure function requires backend identity to choose a business rule.
- Timezone behavior differs between adapters.
- Real external writes are required for validation.
- Migration would modify Phase 04 through Phase 06 owned files substantially.
- A targeted test fails before the relevant source change.

## Completion gate

Phase 03 is complete only when:

- `recalc_cost_daily` business logic exists once.
- SQLite, PostgreSQL, and Firestore adapters use the same pure calculation.
- Each backend was migrated and committed separately.
- Parity tests remain in the suite.
- Schemas and persistent field names are unchanged.
- Transaction and error behavior remain compatible.
- Remaining repeated Operations rules are either migrated or explicitly queued.
- Targeted tests, compile checks, and `git diff --check` pass.
- `PROGRESS.md` records removed duplication and Phase 04 target symbols.

## Required handoff to Phase 04

Provide:

- New Operations domain modules and models
- Backend mappers added
- Compatibility wrappers retained
- Exact tests proving parity
- Any unresolved backend-specific normalization
- Operations environment reads moved
- Maximum six forced-charge files or symbol ranges to inspect
- Operations files Phase 04 must not reread
## Vision alignment for this phase

This phase must be interpreted through `VISION_AND_DECISION_PRINCIPLES.md`.

Before performing any step in this phase, answer:

- Which operations rule currently has multiple owners?
- Which part is true business policy and which part is backend mapping or persistence?
- Which schema, precision, transaction, null, and failure contracts must remain backend-specific?
- What local optimization could make one backend cleaner while increasing system drift?
- What parity evidence will prove that all backends now execute the same business meaning?

The objective is not identical backend code. The objective is one owner for shared operations policy.

## Why this phase is necessary

Operations behavior such as daily cost recalculation is implemented across SQLite, PostgreSQL, and Firestore paths.

When each backend owns both persistence mechanics and business decisions:

- Tariff or aggregation rules can drift.
- Bug fixes may reach only one backend.
- Rounding, missing-data, date-boundary, and overwrite behavior can diverge.
- Tests may validate each implementation separately without proving semantic parity.
- A future feature requires editing several backend modules.
- Backend migration can silently change business results.

This is a system-level correctness risk, not merely duplicated code.

## Phase-specific final target

At the end of this phase:

- Shared operations policy has one pure and testable owner.
- Backend adapters fetch, map, persist, and manage transactions.
- SQLite, PostgreSQL, and Firestore produce equivalent domain results for equivalent inputs.
- Real backend differences remain explicit in adapter code.
- Compatibility entrypoints continue to work while delegating to the shared domain path.
- A tariff or aggregation rule change can be made once and verified across all backends.

The target is not one universal persistence implementation.

The target is one business calculation with multiple explicit adapters.

## How this phase contributes to the final architecture

The final architecture requires one clear owner for each business meaning.

This phase removes one of the highest-risk forms of duplicated ownership: the same operational calculation being defined independently by storage technology.

It contributes by:

- Moving calculation and decision rules into pure domain code
- Restricting adapters to data access and translation
- Establishing parity tests across persistence backends
- Making backend selection an infrastructure concern
- Reducing the files required to understand or change an operations rule
- Creating a repeatable pattern for later backend-boundary work

## Phase-specific local-optimization risks

Do not optimize this phase by:

- Forcing all backends through one generic repository with unreadable conditionals
- Treating SQL and Firestore behavior as identical when transaction semantics differ
- Moving database field names into the domain model
- Changing rounding or overwrite behavior to simplify the shared function
- Choosing one backend as the implicit source of truth without contract evidence
- Rewriting all operations functions at once
- Removing compatibility wrappers before callers are migrated
- Duplicating small policy fragments inside adapter callbacks
- Declaring success after one backend delegates to the shared implementation
- Comparing only final totals while ignoring dates, missing values, or persisted fields

Backend code may remain different. Business meaning must not.

## Required evidence for completion

Behavior evidence must include:

- Characterization of the current rule before extraction
- Pure-domain tests for normal, boundary, missing-data, and rounding cases
- Old-versus-new parity for each backend
- Cross-backend parity for equivalent input data
- Persistence checks for field names, dates, precision, overwrite behavior, and transaction handling
- Failure-path checks for partial reads, write failures, and unsupported data

Ownership evidence must include:

- The exact business decisions removed from each backend
- The new domain owner and its narrow responsibility
- Proof that adapters contain only mapping, persistence, and backend-specific control
- Proof that a rule change no longer requires editing multiple backend implementations
- A list of intentional backend differences and why they remain outside the domain owner

## Phase alignment decision

Before marking this phase complete, answer:

1. Is the business result determined in one place?
2. Are backend differences explicit rather than hidden in generic branches?
3. Can equivalent inputs be compared across all supported backends?
4. Have public entrypoints and persisted contracts remained stable?
5. Would a future tariff or aggregation change require editing only the domain owner and tests?
6. Has the amount of repository context needed to understand the rule decreased?
7. Is any duplicated policy still present in callbacks, mappers, or fallback paths?

If one backend still defines its own interpretation of the rule, the phase is incomplete.

## What later phases must not undo

Later phases must not:

- Reintroduce operations calculations into repository or backend classes
- Add backend-specific exceptions to the domain rule without a documented product reason
- Pass raw database records through unrelated orchestrators
- Remove parity tests when changing schemas or repositories
- Turn the domain owner into a persistence-aware service
- Bypass the shared rule for convenience in a new entrypoint

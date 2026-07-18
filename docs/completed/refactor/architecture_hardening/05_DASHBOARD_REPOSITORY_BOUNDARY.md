# Phase 05: Dashboard Repository Boundary

## Objective

Separate backend-specific data access from dashboard assembly while preserving the public dashboard output.

The target is not a full dashboard rewrite. The goal is to stop SQLite, PostgreSQL, and Firestore loaders from owning duplicate normalization and presentation rules.

## Prerequisites

Phase 04 must be completed.

Read only:

1. `AGENTS.md`
2. `00_EXECUTION_PROTOCOL.md`
3. This file
4. The latest Phase 04 handoff in `PROGRESS.md`
5. `app/dashboard/models.py`
6. `app/dashboard/service.py`
7. Direct symbol ranges and tests named by the handoff

Locate targets:

    rg -n "_load_sqlite_slice|_load_postgres_slice|_load_firestore_slice|load_dashboard|latest_schedule|Dashboard" app/dashboard_data.py app/dashboard dashboard_server.py tests

Do not read all of `app/dashboard_data.py`.

## Contracts to preserve

Preserve:

- Public function names and parameters
- Dashboard JSON field names
- Value types
- Missing and empty behavior
- Ordering used by callers or tests
- Timezone conversion
- Cache and fallback behavior
- Backend selection behavior
- Diagnostic fields
- Existing HTTP response behavior
- Existing database and Firestore schemas

Do not normalize backend differences unless parity tests prove they represent the same logical concept.

## Target architecture

Use this flow:

    backend
        -> repository adapter
        -> typed canonical records
        -> shared dashboard assembler
        -> existing serializer or public dictionary

Repositories own:

- Queries
- Document reads
- Row or document mapping
- Backend-specific pagination
- Backend-specific error translation

Shared dashboard code owns:

- Cross-source assembly
- Common sorting
- Derived presentation values
- Common missing-value policy
- Canonical dashboard models

The serializer owns existing external field names.

## Non-goals

Do not:

- Change dashboard layout or API shape
- Change database or Firestore schemas
- Replace all loaders at once
- Create a generic query abstraction
- Move Operations calculations into dashboard code
- Add new caching
- Change polling intervals
- Change HTTP status behavior
- Perform real production reads or writes for tests
- Rewrite `dashboard_server.py` unless a narrow composition change is required

## Step 05.1: Freeze public output

Use existing dashboard characterization and backend parity tests.

Create or confirm fixtures for:

- Complete data
- Empty backend
- Missing optional source
- Partial schedule data
- Multiple schedule entries
- Timezone boundary
- Zero values
- `None` values
- Stale or fallback data
- Backend read failure according to current behavior

Assert the public result before extracting internal models.

Do not over-specify dictionary insertion order unless the caller depends on it.

## Step 05.2: Identify canonical concepts

Inspect only the return structures of the three backend loaders.

Group fields by concept, such as:

- Current energy state
- Daily aggregates
- Battery metrics
- Forecast summary
- Schedule entries
- Operations metrics
- Data freshness or diagnostics

Reuse existing models in `app/dashboard/models.py` where they fit.

Add a new model only when:

- At least two backends produce the concept
- The concept has stable semantics
- The model removes repeated raw dictionary handling
- Serialization can preserve current field names

Do not create one giant dashboard snapshot model with many unrelated optional fields unless the existing public contract already requires that exact aggregate.

## Step 05.3: Define narrow repository ports

A repository Protocol should expose use-case operations.

Good examples:

    load_current_energy()
    load_daily_metrics()
    load_schedule()
    load_diagnostics()

Bad examples:

    execute_sql()
    query_collection()
    fetch_any(name)
    get_raw_data()

Do not require every backend to implement an operation it cannot meaningfully provide. Use separate small Protocols when capabilities differ.

Create Protocols only after at least two adapters demonstrate the same use-case need.

## Step 05.4: Extract shared mappers and assembler

Move common transformation logic out of backend loaders only after parity is proven.

Shared code may include:

- Common timestamp normalization
- Common ordering
- Common typed model construction
- Common derived display values
- Final dashboard assembly
- Final serialization

Shared code must not branch on backend name.

Backend-specific handling remains in adapter mappers when source semantics differ.

Do not move SQL column names or Firestore field paths into domain models.

## Step 05.5: Migrate SQLite first

Migration sequence:

1. Keep the existing SQLite loader callable.
2. Add a SQLite repository adapter.
3. Map rows into canonical typed records.
4. Assemble output through the shared service.
5. Compare old and new public results.
6. Run SQLite and dashboard tests.
7. Remove only duplicated SQLite transformation code.

Required tests:

    python -m pytest -q tests/test_dashboard_data.py tests/test_dashboard_backend_parity.py

Also run direct SQLite dashboard tests named in the Phase 04 handoff.

Commit SQLite migration separately.

## Step 05.6: Migrate PostgreSQL second

Start only after SQLite is green and committed.

Migration sequence:

1. Reuse canonical records and shared assembler.
2. Preserve PostgreSQL query and transaction behavior.
3. Keep PostgreSQL-specific parsing in its mapper.
4. Compare old and new public results.
5. Run PostgreSQL dashboard tests.
6. Remove duplicated PostgreSQL transformation code.

Do not add PostgreSQL branches to the shared assembler.

Commit PostgreSQL migration separately.

## Step 05.7: Migrate Firestore third

Start only after SQLite and PostgreSQL migrations are stable.

Migration sequence:

1. Map documents into the same canonical records.
2. Preserve collection paths and field names.
3. Preserve partial-document behavior.
4. Preserve fallback and freshness semantics.
5. Compare old and new public output.
6. Remove duplicated Firestore transformation code.

Required tests include:

    python -m pytest -q tests/test_firestore_dashboard_metrics.py tests/test_dashboard_backend_parity.py

Do not use real Firestore access.

Commit Firestore migration separately.

## Step 05.8: Thin `app/dashboard_data.py`

After all adapters use repositories and the shared assembler, `app/dashboard_data.py` should primarily:

1. Select or construct the configured backend adapter.
2. Call the shared dashboard service.
3. Preserve compatibility entrypoints.
4. Translate final result into the existing public shape.
5. Retain only boundary-level fallback and logging.

It must not contain three copies of the same presentation calculation.

Do not split the file solely to reduce line count. Split by ownership.

## Step 05.9: Cache and fallback rules

Before moving cache or fallback logic, characterize:

- Cache key
- Cache lifetime
- Stale-data acceptance
- Backend failure behavior
- Fallback source order
- Diagnostic output
- Thread-safety assumptions

Place cache policy in one clear boundary.

Do not introduce a new cache implementation in this phase.

If different backends intentionally use different fallback policy, document and retain it.

## Step 05.10: Error handling

Preserve current caller-visible behavior.

At repository boundaries:

- Translate expected backend errors consistently.
- Retain original causes.
- Do not hide programming errors as empty data.
- Do not turn current empty fallback behavior into an exception without approval.
- Do not convert current fatal errors into silent fallback behavior.

Add tests before narrowing broad catches.

## Required checks after each backend

Run nearest tests, then:

    python -m compileall -q app dashboard_server.py
    git diff --check

Run targeted mypy when available:

    python -m mypy app/dashboard app/dashboard_data.py

Record unrelated pre-existing errors separately.

## Stop conditions

Stop when:

- Backends produce different logical values for the same fixture.
- Public output semantics are unclear.
- Cache or fallback behavior is not covered by tests.
- A repository would expose generic database operations.
- A canonical model requires backend identity.
- Schema changes appear necessary.
- Real external access is required for validation.
- The change substantially enters Phase 06 energy-model ownership.
- Targeted tests fail before the relevant change.

## Completion gate

Phase 05 is complete only when:

- Backend data access is separated from shared dashboard assembly.
- SQLite, PostgreSQL, and Firestore were migrated separately.
- Public output shape remains compatible.
- Backend-specific source mapping stays in adapters.
- Common transformation and sorting rules exist once.
- Cache and fallback behavior remain characterized.
- Parity tests remain in the suite.
- Targeted tests, compile checks, and `git diff --check` pass.
- `PROGRESS.md` records remaining dashboard debt and Phase 06 target symbols.

## Required handoff to Phase 06

Provide:

- Canonical dashboard models added
- Repository ports and adapters added
- Shared assembler entrypoint
- Compatibility wrappers retained
- Exact parity tests
- Cache and fallback ownership
- Maximum six energy-model files or symbol ranges to inspect
- Dashboard files Phase 06 must not reread
## Vision alignment for this phase

This phase must be interpreted through `VISION_AND_DECISION_PRINCIPLES.md`.

Before performing any step in this phase, answer:

- Which dashboard meaning is currently reconstructed differently by each backend path?
- Which responsibility belongs to repositories, canonical models, shared assembly, and presentation?
- Which field names, missing-value behavior, ordering, time ranges, and public response contracts must remain unchanged?
- What local optimization could make one backend path cleaner while increasing cross-backend divergence?
- What evidence will prove that equivalent source data produces equivalent dashboard meaning?

The objective is not to make all data sources look identical internally. The objective is one shared interpretation after explicit source mapping.

## Why this phase is necessary

Dashboard data assembly currently mixes backend access, source-specific mapping, fallback behavior, normalization, aggregation, and public response construction.

When these responsibilities remain combined:

- SQLite, PostgreSQL, and Firestore paths can produce subtly different outputs.
- Public field meaning may depend on the selected backend.
- Missing values, ordering, timestamps, and aggregation windows may drift.
- Presentation changes can require edits inside persistence code.
- Repository changes can accidentally alter user-visible behavior.
- Raw backend records can leak through several layers.
- Tests may verify one backend without proving shared dashboard semantics.

This creates both product inconsistency and high regression risk.

## Phase-specific final target

At the end of this phase:

- Each backend repository fetches source data and maps it into explicit canonical models.
- Shared assembly code owns backend-independent dashboard interpretation.
- Presentation or serialization code owns public response formatting.
- Equivalent canonical input produces equivalent dashboard output regardless of backend.
- Backend-specific field names, queries, and failure mechanics remain inside adapters.
- Existing public fields, units, ordering, time ranges, and fallback behavior remain compatible.
- Adding or modifying a backend does not require copying dashboard business assembly.

The target is not one giant repository abstraction.

The target is explicit source adapters feeding one shared dashboard meaning.

## How this phase contributes to the final architecture

The final architecture requires external systems to remain boundary concerns and business meaning to have one owner.

This phase contributes by:

- Separating source retrieval from dashboard interpretation
- Introducing canonical typed data between repositories and assembly
- Removing duplicated aggregation and fallback decisions from backend paths
- Making public response construction independent of persistence technology
- Reducing the files required to understand dashboard behavior
- Making cross-backend parity directly testable
- Preventing raw records and storage schemas from becoming application contracts

## Phase-specific local-optimization risks

Do not optimize this phase by:

- Designing canonical models around one backend's schema
- Creating one repository interface with many optional methods and backend conditionals
- Moving presentation field names into repositories
- Changing missing-value or fallback behavior to simplify normalization
- Returning raw dictionaries because typed mapping appears verbose
- Combining query, normalization, aggregation, and serialization in a new service class
- Rewriting every dashboard endpoint simultaneously
- Declaring success after only one backend uses the shared assembly
- Ignoring ordering, timezone, precision, or empty-series differences
- Hiding source-specific failures behind indistinguishable empty results
- Treating fewer backend branches as sufficient evidence

A shared pipeline is valid only when source differences remain explicit and dashboard meaning becomes more consistent.

## Required evidence for completion

Behavior evidence must include:

- Characterization of current public dashboard responses
- Tests for normal, empty, partial, stale, and malformed source data
- Old-versus-new parity for each migrated backend
- Cross-backend parity from equivalent canonical inputs
- Checks for field names, units, ordering, timestamps, aggregation windows, and null behavior
- Checks for fallback and error behavior
- Serialization contract tests for user-visible output

Ownership evidence must include:

- The exact assembly or interpretation decisions removed from repositories
- The canonical models and the meanings they own
- The shared assembly owner and its narrow responsibility
- Proof that repositories now perform only source access and mapping
- Proof that presentation code no longer depends on backend records
- Evidence that a dashboard rule change no longer requires editing multiple backend paths

## Phase alignment decision

Before marking this phase complete, answer:

1. Does one shared component determine backend-independent dashboard meaning?
2. Are source-specific differences explicit in repository adapters?
3. Can equivalent inputs be compared across all supported backends?
4. Have public response contracts remained stable?
5. Are canonical models cohesive rather than containers for every possible source field?
6. Has raw backend data stopped leaking into shared assembly and presentation?
7. Would a future dashboard rule change require editing one owner?
8. Has the context required to understand one dashboard use case decreased?

If each backend still assembles its own interpretation of the dashboard, the phase is incomplete.

## What later phases must not undo

Later phases must not:

- Add dashboard aggregation or fallback policy directly to repositories
- Expose raw backend records to presentation code
- Add backend-specific branches to shared assembly without a documented semantic reason
- Expand canonical models into generic storage-record containers
- Remove cross-backend parity tests when changing schemas
- Bypass the shared assembly for a new endpoint or data source
- Couple presentation contracts to database field names

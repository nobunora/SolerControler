# Architecture Hardening Vision and Decision Principles

## Why this work is necessary

SolerControler already has useful behavior and substantial test coverage, but critical decisions are spread across large modules, backend-specific implementations, environment access, and loosely typed data.

The immediate risk is not only code size. The larger risk is that the same business meaning can evolve differently depending on which file, backend, or operational path is changed.

Examples include:

- Daily cost rules drifting between SQLite, PostgreSQL, and Firestore
- Forced-charge safety policy being implemented partly in a state machine and partly in a runner loop
- Dashboard presentation logic changing differently by backend
- Energy-model fallback and optimization behavior becoming coupled to file, network, or environment details
- Local fixes improving one module while making the whole system harder to reason about

Without a shared target, a technically correct local change can still be architecturally harmful.

## Final target

The final target is a system in which one business meaning has one clear owner.

The desired architecture has these properties:

- Business rules are implemented once in pure, testable code.
- SQLite, PostgreSQL, Firestore, KP-NET, weather APIs, files, and environment variables remain boundary concerns.
- Orchestrators coordinate work but do not duplicate business policy.
- Typed models carry stable domain meaning between boundaries.
- Public behavior, safety rules, schemas, field names, units, and fallback order remain compatible unless deliberately changed.
- Each module has a narrow reason to change.
- A future maintainer can identify where a decision belongs without reading the whole repository.
- Tests prove behavior at domain, adapter, and integration boundaries.
- Refactoring reduces the cost and risk of later feature work.

The goal is not maximum abstraction, minimum line count, or complete elimination of legacy code.

The goal is controlled ownership of behavior.

## System-level success criteria

A change contributes to the final target when it improves one or more of the following without damaging the others:

1. Single ownership of business policy
2. Clear separation between pure decisions and external effects
3. Stable public and persistence contracts
4. Deterministic testing of important behavior
5. Reduced backend or entrypoint drift
6. Smaller context required to understand and modify one use case
7. Explicit types and units at important boundaries
8. Safer future changes with lower regression risk

## The local-optimization risk

A sub-agent may be assigned one file, function, backend, or test group. That assignment is only a work boundary. It is not the architectural goal.

Common forms of local optimization include:

- Moving code without clarifying ownership
- Creating a generic abstraction only to reduce duplication counts
- Making one backend cleaner while increasing semantic differences
- Reducing argument count by hiding unrelated values in a large context object
- Narrowing an exception because it looks cleaner while changing fail-safe behavior
- Splitting a large file into several files that still share the same mixed responsibilities
- Replacing explicit compatibility code with an implicit breaking change
- Improving one test by weakening the contract it should protect
- Introducing a new model whose only purpose is to wrap an unstructured dictionary
- Optimizing line count instead of reducing duplicated decision ownership

A locally attractive change must be rejected when it moves the repository away from the final target.

## Mandatory alignment questions

Before changing code, answer:

1. What system-level problem does this step reduce?
2. Which business meaning or responsibility should have one owner after this step?
3. What external or public contracts must remain unchanged?
4. What local optimization could this step accidentally introduce?
5. What evidence will show that the whole system is better, not only the edited file?

After changing code, answer:

1. Is business policy now owned in fewer places?
2. Is the new boundary clearer than the old one?
3. Did any backend, entrypoint, or fallback path become a special case?
4. Did the change add hidden coupling, a large context object, or a generic abstraction?
5. Do tests prove both preserved behavior and the intended ownership improvement?
6. Can the next maintainer understand the use case with less repository context?

If these questions cannot be answered, the step is not complete.

## Decision hierarchy

When choices conflict, use this order:

1. Safety and externally visible behavior
2. Persistence and compatibility contracts
3. Single ownership of business meaning
4. Testability and deterministic evidence
5. Clear module boundaries
6. Type quality
7. Reduced duplication
8. Reduced file size or line count

Lower priorities must not override higher priorities.

## Scope discipline

Each phase has an intentionally narrow scope.

When an important issue belongs to another phase:

1. Record it in `PROGRESS.md`.
2. Explain how it relates to the final target.
3. Do not solve it incidentally.
4. Provide exact symbols and evidence for the later phase.

Deferring a change is correct when the current phase cannot prove it safely.

## Evidence required for architectural improvement

A structural change should provide evidence in at least two categories.

### Behavior evidence

- Characterization tests
- Old-versus-new parity tests
- Boundary cases
- Failure and fallback cases
- Stable serialized or persisted output

### Ownership evidence

- One duplicated rule removed
- One orchestrator branch delegated to a state machine
- One backend mapper separated from shared assembly
- One external dependency moved behind a narrow port
- One repeated parameter group replaced by a cohesive typed model

### Context evidence

- Fewer files required to understand the use case
- Smaller symbol ranges required for future edits
- Clearer handoff information
- Reduced need to inspect backend-specific code for domain decisions

Tests alone do not prove better architecture. A cleaner file alone does not prove preserved behavior. Both are required.

## Required handoff language

Every phase handoff must state:

- Why the completed work was necessary
- How it contributes to the final target
- Which business meaning now has clearer ownership
- Which contracts were preserved
- Which local-optimization risks were considered
- What system-level evidence supports completion
- What was intentionally deferred and why
- What the next phase must not undo

## When to stop

Stop and record a blocker when:

- The local task conflicts with the final target.
- A shared abstraction would erase meaningful differences.
- The change cannot preserve a higher-priority contract.
- Tests can prove behavior but not ownership improvement.
- Ownership can improve only through an unplanned cross-phase rewrite.
- A product, safety, tariff, or persistence decision is required.
- The agent cannot explain why the system is better after the change.

Stopping is preferable to completing a locally clean but globally harmful change.

## Definition of done for the full program

The architecture-hardening program is done when:

- Important business rules have one identifiable owner.
- External systems are accessed through explicit boundaries.
- Orchestrators coordinate rather than decide.
- Backend adapters map and persist rather than define business policy.
- Large entrypoints no longer require whole-repository context for routine changes.
- Public behavior and safety contracts are verified.
- Remaining legacy compatibility is explicit and justified.
- New feature work can be added without copying policy into another backend or runner.
- `PROGRESS.md` explains not only what changed, but why the final system is safer and easier to evolve.

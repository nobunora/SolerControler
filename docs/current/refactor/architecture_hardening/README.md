# Architecture Hardening Plan

This directory contains the staged instructions for improving the SolerControler architecture without changing existing behavior.

The goal is not a full rewrite. The plan removes the highest structural risks in this order:

1. Duplicated business rules across SQLite, PostgreSQL, and Firestore
2. Oversized orchestrators such as `cloud_job_runner.py`
3. Mixed backend access and view-model construction in `app/dashboard_data.py`
4. Mixed responsibilities in `energy_model_main.py`
5. Hidden dependencies caused by environment access, raw dictionaries, and broad exception handling

## Required reading for each sub-agent

Read only these files at the start of a phase:

1. Repository root `AGENTS.md`
2. `VISION_AND_DECISION_PRINCIPLES.md`
3. `00_EXECUTION_PROTOCOL.md`
4. The assigned phase file
5. The current status and latest handoff in `PROGRESS.md`

Do not read other phase files, `docs/completed`, `docs/archive`, or entire oversized source files unless the assigned phase explicitly requires it.

## Phase order

Do not reorder phases. Start a phase only after the previous phase satisfies its completion gate.

| Phase | Instruction file | Objective |
|---|---|---|
| 01 | `01_BASELINE_AND_GUARDRAILS.md` | Freeze behavior contracts and comparison baselines |
| 02 | `02_SHARED_BOUNDARIES.md` | Remove exact clones and establish small typed boundaries |
| 03 | `03_OPERATIONS_DOMAIN_DEDUPLICATION.md` | Centralize business calculations duplicated by storage backend |
| 04 | `04_FORCED_CHARGE_ORCHESTRATION.md` | Move forced-charge control toward a pure state machine |
| 05 | `05_DASHBOARD_REPOSITORY_BOUNDARY.md` | Separate backend reads from dashboard assembly |
| 06 | `06_ENERGY_MODEL_DECOMPOSITION.md` | Split the energy model by use case and external port |
| 07 | `07_FINAL_INTEGRATION_AND_CLOSEOUT.md` | Run final checks and record remaining risks |

## Why this order is required

- Refactoring before establishing comparison fixtures can silently change rounding, missing-value handling, and fallbacks.
- Exact duplicate removal is low risk and creates shared components for later phases.
- Operations duplication has the highest risk of backend-specific rule drift and already has parity tests.
- Forced charging contains safety-critical external effects, so transitions must become testable before thinning the runner.
- Dashboard and energy-model changes have broad impact and should start only after common boundaries are stable.
- Environment access is not migrated globally. Each phase moves only the settings it directly touches.
## Program vision and alignment rule

Every document in this directory must be interpreted through `VISION_AND_DECISION_PRINCIPLES.md`.

Before starting any phase or step, the assigned agent must be able to explain:

- Why the change is necessary at the system level
- Which business meaning should have clearer ownership afterward
- Which higher-priority contracts must remain unchanged
- Which form of local optimization could make the whole system worse
- Which evidence will prove both behavior preservation and architectural improvement

A task assignment is only a work boundary. It is not permission to optimize one file, backend, test, or function in isolation.

When a locally attractive change conflicts with the final target of controlled ownership of behavior, the local change must be rejected or deferred.

## Why this staged plan is necessary

The repository contains several areas where one business meaning is spread across backends, orchestrators, entrypoints, raw dictionaries, and environment access.

The plan is staged because the final target cannot be reached safely through one large rewrite. Each phase creates evidence and boundaries required by the next phase.

The sequence prevents a sub-agent from improving one component while accidentally:

- Changing public behavior
- Creating backend drift
- Moving policy into another inappropriate layer
- Replacing explicit duplication with hidden coupling
- Introducing a generic abstraction that erases domain meaning
- Weakening a safety or persistence contract

## Final state this directory is guiding toward

After all implementation phases are complete:

- Important business rules have one clear owner.
- External systems remain explicit adapters.
- Orchestrators coordinate work but do not own duplicate policy.
- Typed models carry stable meaning and units.
- Backends map and persist rather than decide.
- Routine changes require less repository-wide context.
- Tests prove both preserved contracts and improved ownership.
- Remaining compatibility code is explicit and justified.

Each phase document contains its own customized explanation of how that phase contributes to this final state and what it must not optimize locally.

## Implementation status

All seven phases were completed on 2026-07-19. `PROGRESS.md` is the authoritative evidence and handoff log.

The resulting ownership model is:

- Daily-cost policy: `app/operations/cost_daily.py`; storage adapters only map and persist.
- Forced-charge policy: `app/forced_charge/state_machine.py`; the runner executes injected clock/device/status effects.
- Dashboard meaning: canonical models and shared assembly under `app/dashboard`; repositories own source access and mapping.
- Energy planning: focused history/forecast/settings/ports/output modules under `app/energy_plan`, typed optimizer requests, and a thin composition workflow.

Run the primary non-external regression suite with:

    python -m pytest -q -m "not external"

Run static validation with:

    python -m compileall -q app energy_model_main.py cloud_job_runner.py db_pipeline_main.py dashboard_server.py
    python -m mypy app energy_model_main.py cloud_job_runner.py db_pipeline_main.py dashboard_server.py
    python scripts/security_check.py

The repository-wide mypy baseline remains 92 errors in 10 legacy files. New focused modules are type-check clean. Remaining debt, compatibility wrappers, and risk priorities are recorded in the final closeout section of `PROGRESS.md`.

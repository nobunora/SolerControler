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
2. `00_EXECUTION_PROTOCOL.md`
3. The assigned phase file
4. The current status and latest handoff in `PROGRESS.md`

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

# HANDOFF

## Active workspace

- Branch: `codex/dashboard-history-finalize`.
- Base: merged master `61830f3a17f554b969a04aa92a72fd95db9e01a9` (PR #38 complete).
- Active task: finish production deployment and historical reconstruction, then validate the permanent dashboard-history contract.
- Web ChatGPT owns the source changes in this branch. Local Codex owns execution, production-safe reconstruction, deployment, and test runs.

## User-visible objective

Restore historical PV generation and household-consumption forecast-vs-actual state wherever defensible evidence exists, distinguish later reconstructed estimates from original forecasts, keep future days self-maintaining, and prevent accidental regression.

## Source changes already made on this branch

1. `app/dashboard/history_reconstruction.py`
   - original complete mutable/snapshot forecast remains first priority;
   - falls back to `forecast_hourly_reconstructed` only when original forecast rows are absent for the date;
   - requires one complete 24-hour reconstruction run;
   - strips original-vintage fields and exposes explicit reconstruction provenance;
   - extends Firestore dashboard bounds to reconstructed dates.
2. `app/dashboard/aggregation.py`
   - keeps reconstructed PV/load paired from the same hourly source;
   - exposes `forecast_provenance_kind`, `forecast_is_reconstructed`, reconstruction ID/time/model/basis;
   - preserves original current/snapshot/sunshine behavior and legacy rolling-load labeling.
3. `app/dashboard/warnings.py`
   - warns when reconstructed estimates are displayed;
   - warns when recent completed days lack original forecast evidence;
   - warns when recent completed days lack complete actual PV/load evidence.
4. `app/operations/historical_forecast_reconstruction.py`
   - safe separate persistence contract for later model replay;
   - writes only `forecast_hourly_reconstructed` and `forecast_reconstructions`;
   - deterministic reconstruction ID / idempotent retry semantics;
   - no original `issued_at` / `forecast_run_id` masquerade.
5. `scripts/reconstruct_historical_forecast.py`
   - preview by default;
   - `--apply` explicitly required for Firestore mutation;
   - applies only to reconstruction namespaces.
6. `tests/test_dashboard_history_contract.py`
   - named `HISTORICAL_FAILURE_LOCK (2026-09-04)` contract.
7. `scripts/pre_release_integration.ps1`
   - explicitly executes the historical lock before normal integration validation.
8. `.ai/DECISIONS.md`
   - permanent original-vs-reconstructed semantics and migration rule recorded.

## Existing PR #38 facts that remain authoritative

- Actual PV/load fallback is per day/per field.
- Reconstructed daily actual requires all 48 unique JST half-hour values for that field.
- Complete daily metric avoids raw monitoring reads.
- Mutable hourly forecast must be complete before use.
- Immutable fallback selects one complete run only.
- Snapshot cutoff remains 07:00 JST for the legacy producer contract.
- Snapshot PV/load remain one paired vintage.
- 2026-08-30 through 2026-09-02 had no proven original forecast evidence in the prior production matrix.

## Required Codex execution — do not redesign source unless a test proves a defect

### 1. Pull and inspect this branch

Confirm local branch contains the Web implementation above. Do not replace it with a new architecture. If a source defect is found while testing, fix only the smallest directly proven issue on this same branch.

### 2. Run the named contract and standard quality

At minimum run and report exact results for:

- `python -m pytest tests/test_dashboard_history_contract.py -q`
- focused dashboard/history tests including `tests/test_dashboard_data.py`
- full pytest
- Ruff
- full mypy
- standard Import Linter / security checks used by the repository
- official pre-release/integration gate
- GitHub Actions quality on the pushed head

### 3. Read-only production inventory before any reconstruction write

Build a full date-by-date inventory for the retained dashboard history. For each date classify PV actual, load actual, PV forecast, and load forecast as:

- original exact evidence;
- deterministic actual reconstruction from complete raw monitoring;
- later forecast reconstruction candidate;
- irrecoverable with current evidence.

Search original evidence first: daily metrics, complete monitoring samples, mutable forecast, immutable snapshots, sunshine_daily, archived night plans, GCS archive, available backups/raw history.

Do not create a later reconstruction where original contemporaneous forecast evidence can be recovered.

### 4. Reconstruct missing forecasts only with defensible historical inputs

For dates still lacking original forecast evidence, generate the historical plan outside the dashboard path using historical/time-appropriate inputs. Do not use actual PV/load as forecast targets and do not use today's weather forecast to simulate the past.

For every candidate:

1. run `scripts/reconstruct_historical_forecast.py` without `--apply` first;
2. record target date, input provenance, model/version, reconstruction ID, 24-row count, PV/load totals, source plan SHA-256;
3. review the preview;
4. only then rerun with `--apply` if evidence is defensible.

Use one of the allowed basis labels:

- `historical_archive`
- `historical_model_replay`
- `legacy_model_replay`

Never write these later estimates into original forecast collections.

### 5. Deploy dashboard scope from the accepted head

After tests pass, run the official production gate and deploy the dashboard scope using repository tooling. Do not manually execute normal 03 and do not change 23/03/07, the 02:30 forecast owner, SOC/optimizer semantics, or device/settings state.

### 6. Production acceptance

Read-only verify the production API/browser for representative dates:

- original historical forecast + actual;
- snapshot-restored historical forecast + actual;
- later reconstructed forecast + actual, visibly flagged by warning/provenance;
- an irrecoverable date if any, which must remain missing rather than fabricated;
- current/future forecast behavior still normal.

Confirm the recent-history warning signal detects missing original forecast evidence / incomplete actual evidence.

## GitHub protection gap

Read-only inspection on 2026-09-04 shows `master` is currently not protected and has no required status checks. In-repository `HISTORICAL_FAILURE_LOCK` is therefore enforced by the normal quality/pre-release workflow but GitHub itself does not prevent a manual bypass merge/direct push. Do not mutate branch protection without explicit user approval; report this gap in the PR result.

## Merge acceptance

Do not merge until:

- named historical lock passes;
- full standard quality passes;
- production dashboard deployment succeeds;
- recoverable historical values are restored/reconstructed with correct provenance;
- later reconstruction is clearly distinguishable from original forecast evidence;
- future original forecast -> immutable snapshot -> historical dashboard path is demonstrated;
- completed-day actual path is demonstrated;
- no protected control contract changed.

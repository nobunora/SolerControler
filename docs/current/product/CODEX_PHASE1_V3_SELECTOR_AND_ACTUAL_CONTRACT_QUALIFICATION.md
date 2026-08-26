# Codex Phase 1 v3 selector repair and actual-source contract qualification

## 0. Normative instruction

This document is a **mandatory implementation specification** for the next Phase 1 validation-only repair. It is not a design suggestion.

Codex MUST implement the requirements below exactly. Do not reinterpret, simplify, broaden, optimize, retune, or replace the specified algorithm, evidence contract, storage boundary, or state transition.

Normative words:

- **MUST / MUST NOT**: required. Any deviation is a blocker.
- **SHALL / SHALL NOT**: same force as MUST / MUST NOT.
- **MAY**: optional only where explicitly stated.

If repository or source-data evidence is insufficient to prove a requirement, **do not guess**. Fail closed, record `BLOCKED`, and stop that part of the work.

This work remains validation-only. No shadow-selected value may enter production forecasting, energy planning, SOC optimization, battery commands, or device control.

---

## 1. Review result that triggers this repair

Review the current `master` implementation before editing. The reviewed implementation commit is:

```text
b028b59a1c89ef12711cad23f77c74e3262f8a30
feat: implement Phase 1 v2 shadow parity isolation
```

The following findings are authoritative blockers.

### 1.1 v2 selector window is wrong

The frozen adaptive-gate policy requires selector scoring over exactly the trailing **21 calendar days** for the same hour.

The current implementation does not satisfy that contract:

```text
_candidate_history(...)
    -> starts at target_day - 45 days

_select_model(...)
    -> calls _candidate_history(...)
    -> therefore scores the selector on up to 45 days
```

This is not a cosmetic issue. Candidate construction intentionally uses a 45-day history, while selector scoring intentionally uses 21 days. Those two windows MUST be independent.

Therefore the current result report's selector-parity PASS statement is not sufficient evidence and MUST be corrected.

### 1.2 v2 is not valid adoption evidence

Because selector behavior under `phase1-v2` is semantically different from the frozen `21-day / per-hour / 2%` gate, **no `phase1-v2` decision or outcome may count toward production-adoption evidence**.

Existing v2 rows MUST NOT be deleted or rewritten. They remain superseded diagnostic evidence only.

### 1.3 GitHub Actions found a new changed-code type diagnostic

The GitHub Actions quality run for the reviewed implementation reports a new shadow-specific `ty` diagnostic in `app/operations/shadow_gate.py` around the compatibility form of `build_shadow_decision()`:

```text
error[invalid-assignment]
Object of type Connection | dict[str, Any]
is not assignable to dict[str, Any] | None
```

This is a changed-code regression and MUST be fixed. Do not classify it as unrelated repository debt.

The full Python test suite passed in that run, but passing tests do not override the selector-window bug or the new changed-code type diagnostic.

### 1.4 actual completeness remains correctly blocked

The current result report correctly states that the monitoring-data cadence/finalization contract has not been proven and therefore:

```text
actual completeness: BLOCKED
30-day prospective collection: NOT_STARTED
production adoption: NOT_ELIGIBLE
```

Do not weaken this fail-closed behavior.

---

## 2. Read before editing

Before making any code change, read all of the following:

1. `AGENTS.md`
2. `docs/current/agent/agent_working_rules.md`
3. `docs/current/product/HOURLY_PV_ADAPTIVE_GATE_SIMULATION.md`
4. `scripts/diagnose_hourly_pv_adaptive_gate.py`
5. `scripts/diagnose_hourly_pv_correction_limits.py`
6. `scripts/diagnose_hourly_pv_regime_bias.py`
7. `docs/current/product/CODEX_PHASE1_SHADOW_PARITY_AND_ISOLATION_HARDENING.md`
8. `docs/current/product/CODEX_PHASE1_SHADOW_PARITY_ISOLATION_HARDENING_RESULT_JA.md`
9. current `app/operations/shadow_gate.py`
10. current `scripts/forecast_shadow.py`
11. current `tests/test_shadow_gate.py`
12. `app/kpnet/config.py`
13. `app/kpnet/monitoring_history.py`
14. `app/operations/monitoring_csv.py`
15. `app/domain/monitoring.py`

Do not modify production correction logic while performing this task.

---

## 3. Allowed implementation scope

Expected implementation files are limited to:

- `app/operations/shadow_gate.py`
- `scripts/forecast_shadow.py`
- `tests/test_shadow_gate.py`
- one small shadow-only actual-contract helper/config loader if required
- one sanitized result document under `docs/current/product/`
- one explicit shadow-only contract file under `config/` **only if the actual contract is proven**

A small read-only diagnostic script MAY be added under `scripts/` solely to qualify monitoring cadence and finalization semantics.

The following MUST NOT be changed as part of this work:

- `app/forecasting/correction_model.py`
- production PV forecast formulas or correction parameters
- physical PV equations/calibration
- forecast provider weights
- `forecast_hourly` latest-value behavior
- energy-plan forecast values
- SOC objective or constraints
- charge/discharge behavior
- device-control behavior
- production scheduler hooks
- production cloud-job success/failure semantics

If satisfying this document appears to require any of those changes, stop and report `BLOCKED` instead of changing them.

---

## 4. Policy version reset: v3 is mandatory

The repaired policy identity MUST be exactly:

```text
policy_name    = gate_per_hour_window21d_margin02pct
policy_version = phase1-v3
```

Do not continue using `phase1-v2` after the selector-window repair.

Rules:

- v1 remains historical/superseded diagnostic evidence.
- v2 remains historical/superseded diagnostic evidence.
- v1 and v2 MUST NOT seed v3 candidate history.
- v1 and v2 MUST NOT seed v3 selector history.
- v1 and v2 MUST NOT appear in v3 primary metrics.
- v1 and v2 MUST NOT count toward the v3 30-day horizon.
- do not migrate, relabel, rewrite, or copy old rows into v3.

Any future semantic change to candidate values, candidate eligibility, selector scoring, target-domain eligibility, actual aggregation, outcome completeness, primary-vintage selection, or primary-report filtering MUST use another policy version. Never silently change semantics under `phase1-v3`.

---

## 5. Frozen target domain remains unchanged

Primary v3 targets MUST satisfy exactly:

```text
7 <= target_hour <= 22
baseline_forecast_pv_kwh > 0.0
```

Do not use dynamic sunrise/sunset.
Do not include hour 06.
Do not include hour 23.
Do not include zero-baseline targets.

Rows outside the frozen domain MAY be retained only as diagnostics and MUST NOT enter v3 candidate history, selector history, primary metrics, or adoption-day counts.

Use the stable exclusion reason:

```text
outside_frozen_policy_domain
```

---

## 6. Candidate history and selector history MUST be separate

This section is the critical repair.

Do not use one generic history function for both candidate construction and selector scoring.

Implement two logically and visibly separate queries/helpers.

### 6.1 Candidate history: 45 days

Use a helper equivalent to:

```python
def _candidate_history_45d(
    conn: sqlite3.Connection,
    target_day: date,
    hour: int,
) -> list[sqlite3.Row]:
    ...
```

Exact date window:

```text
D - 45 days <= prior target date < D
```

Historical row requirements:

```text
same clock hour
valid phase1-v3 prospective_primary decision
one deterministic primary vintage per target date/hour
finalized complete v3 outcome
prior baseline prediction > 0
prior row belongs to frozen target domain
```

`_candidate_predictions()` MUST use the 45-day candidate-history helper.

### 6.2 Selector history: 21 days

Use a separate helper equivalent to:

```python
def _selector_history_21d(
    conn: sqlite3.Connection,
    target_day: date,
    hour: int,
) -> list[sqlite3.Row]:
    ...
```

Exact date window:

```text
D - 21 days <= prior target date < D
```

Historical row requirements:

```text
same clock hour
valid phase1-v3 prospective_primary decision
one deterministic primary vintage per target date/hour
finalized complete v3 outcome
prior baseline prediction > 0
prior row belongs to frozen target domain
```

`_select_model()` MUST use only the 21-day selector-history helper.

### 6.3 Forbidden implementation

The following is explicitly forbidden:

```python
rows = _candidate_history_45d(...)
# then use rows to compute selector MAE
```

The following is also forbidden:

```python
rows = generic_history(..., days=45)
# then rely on callers to remember to slice correctly
```

The 21-day boundary MUST be enforced inside the selector-history implementation itself so a caller cannot accidentally score 22–45-day-old rows.

---

## 7. Candidate definitions remain frozen

Do not retune candidates while repairing the selector.

### 7.1 `production_like_45d`

For target `(D, h)`, start from valid `_candidate_history_45d` rows and additionally require:

```text
target shortwave > 0
prior shortwave > 0
same frozen coarse weather class
0.7 * target_shortwave <= prior_shortwave <= 1.3 * target_shortwave
```

Residual:

```text
residual_i = actual_pv_kwh_i - baseline_prediction_kwh_i
```

If no eligible residuals:

```text
prediction = baseline
```

Otherwise:

```text
center = median(residuals)
variance = 0.6^2 if n == 1 else mean((residual_i - center)^2)
weight = n/(n+2) * 0.6^2/(0.6^2 + variance)
prediction = max(0, baseline + weight * center)
```

No recency weighting. No parameter tuning.

### 7.2 `same_hour_bias_45d_hl7d` and `hl14d`

Use valid `_candidate_history_45d` rows.

Require at least 2 observations.

Explicitly forbidden:

```text
weather filter
shortwave filter
shortwave ±30% gate
shortwave distance
similarity weighting
production-like shrinkage
```

Use additive residual and only this weight:

```text
exp(-ln(2) * age_days / half_life_days)
```

Use a recency-weighted median.

With fewer than 2 valid observations, return baseline.

---

## 8. Selector definition is exact

For target `(D, h)`, selector scoring MUST use `_selector_history_21d()` only.

Exact semantics:

```text
same clock hour only
D - 21 <= prior date < D
minimum 3 distinct historical dates
baseline remains an explicit fallback
```

For each historical selector row, use the candidate predictions that were frozen on that historical decision before its actual outcome was known. Do not recompute historical candidate predictions using today's code or later data.

Compute:

```text
baseline_mae = mean(abs(actual_i - historical_baseline_prediction_i))

candidate_mae[model] =
    mean(abs(actual_i - historical_frozen_candidate_prediction_i))
```

Choose the lowest candidate MAE.

Accept that candidate only when:

```text
best_candidate_mae < baseline_mae * 0.98
```

The comparison is strict.

At exact equality:

```text
best_candidate_mae == baseline_mae * 0.98
```

MUST select baseline.

With fewer than 3 distinct dates, MUST select baseline with:

```text
insufficient_shadow_history
```

Do not pool different hours.
Do not use target-day outcomes.
Do not use rows older than 21 days for selector scoring.

---

## 9. Mandatory regression that catches the 45-day selector bug

Existing tests did not catch the v2 bug because all selector fixtures were inside the latest 21 days.

Add deterministic tests that intentionally distinguish the two windows.

### 9.1 Old rows would incorrectly flip baseline to correction

Use one fixed target such as:

```text
D = 2026-09-30
hour = 10
```

Create:

- at least 3 valid selector rows inside `D-21 .. D-1`;
- additional valid rows in `D-45 .. D-22`.

Choose frozen errors so:

```text
correct 21-day selector -> baseline
incorrect 45-day selector -> correction candidate
```

Assert:

- selected model is baseline;
- selector `sample_count` includes only 21-day rows;
- selector `distinct_days` includes only 21-day dates.

### 9.2 Old rows would incorrectly flip correction to baseline

Create a second fixture where:

```text
correct 21-day selector -> correction candidate
incorrect 45-day selector -> baseline fallback
```

Assert the correction candidate is selected.

### 9.3 Candidate 45-day behavior must remain intact

In a separate test, prove that a row between 22 and 45 days old:

- can still affect `production_like_45d` or same-hour candidate construction when otherwise eligible;
- cannot affect selector scoring.

This prevents accidentally fixing the bug by shortening every history to 21 days.

---

## 10. Remove the one-connection compatibility signature

The v2 compatibility signature weakens both typing and the physical-isolation API contract.

Replace it with an explicit two-connection form. Use this semantic signature:

```python
def build_shadow_decision(
    source_conn: sqlite3.Connection,
    shadow_conn: sqlite3.Connection,
    snapshot: dict[str, Any],
    *,
    decision_at: str,
    cutoff_at: str,
    source_code_version: str,
    evidence_class: str = PRIMARY_EVIDENCE_CLASS,
) -> dict[str, Any]:
    ...
```

The exact parameter names MAY remain as above and SHOULD remain as above unless repository style requires a trivial naming change.

Mandatory rules:

- remove `Connection | dict[...]` union dispatch;
- remove the `if snapshot is None` compatibility path;
- do not allow one connection to silently serve as both source and shadow DB;
- update every caller and test to the explicit three-positional-object contract;
- primary source code version remains non-empty and not `unknown`;
- do not add a new convenience wrapper that recreates the one-connection behavior.

The new changed-code `ty` diagnostic in `shadow_gate.py` MUST disappear in GitHub Actions.

---

## 11. Actual-source contract qualification is a separate gate

Do not start v3 outcome collection until the monitoring actual contract is qualified.

The repository default:

```text
KP_CSV_AGGR_TYPE = 30分データ
```

is useful evidence but is **not sufficient proof** of the active historical contract because configuration may be overridden and timestamp/value/finalization semantics are not established by that default alone.

### 11.1 Qualification must be read-only

Inspect source evidence without mutating production/source data.

Use:

- source SQLite opened read-only;
- existing downloaded KP-NET CSV files read-only;
- repository configuration and parsing code;
- documentation/source metadata already present locally.

Do not contact or write production services merely to satisfy this task.

### 11.2 Required evidence to inspect

Determine, with evidence, all of the following:

1. active aggregation type for the evidence being qualified;
2. expected sampling/interval cadence;
3. valid minute marks within an hour;
4. whether timestamp denotes interval start, interval end, or another convention;
5. whether `発電電力量[kWh]` / `pv_kwh` is interval energy, cumulative energy, instantaneous power, or another quantity;
6. site timezone mapping used by source timestamps;
7. duplicate timestamp behavior;
8. missing interval representation;
9. null/blank PV representation;
10. whether historical values can arrive late or be revised after first ingestion;
11. what condition makes an hour final enough to freeze an append-only actual;
12. whether cadence/configuration changed across the intended prospective period.

### 11.3 Required aggregate diagnostics

Generate sanitized diagnostics over an appropriate available sample without committing private operational PV values.

At minimum calculate:

```text
minute-of-hour frequency distribution
inter-sample delta distribution
sample-count-per-hour distribution
duplicate timestamp count
null-PV count
hours with unexpected minute marks
hours with missing expected intervals
hours whose source rows changed across repeated ingestion, if that can be determined
```

Do not commit raw household/site PV values.

Safe aggregate counts/distributions MAY be committed.

### 11.4 Verdict is binary

The qualification verdict MUST be exactly one of:

```text
PROVEN
BLOCKED
```

`PROVEN` is allowed only if cadence, expected interval set, timestamp semantics, PV value semantics, timezone semantics, and finalization rule are all supported by evidence.

If any required semantic remains unknown, verdict MUST be `BLOCKED`.

Forbidden verdicts include:

```text
probably 30-minute
likely complete
assumed complete
expected to be complete
```

Do not infer an expected count merely from the most common sample count.

---

## 12. Contract file may exist only after PROVEN

If and only if the actual-source qualification verdict is `PROVEN`, create one explicit shadow-only contract file, recommended path:

```text
config/shadow_actual_contract.json
```

It MUST contain at least:

```json
{
  "contract_id": "...",
  "source": "kpnet",
  "aggregation_type": "...",
  "timezone": "Asia/Tokyo",
  "interval_minutes": 0,
  "expected_minute_marks": [],
  "expected_samples_per_hour": 0,
  "timestamp_semantics": "interval_start_or_interval_end",
  "value_semantics": "interval_energy_kwh",
  "finalization_rule": "...",
  "evidence_revision": "..."
}
```

Replace placeholder values only with evidence-backed values.

`contract_id` MUST be deterministic from the semantic contract, or otherwise versioned explicitly so any semantic change creates a new contract ID.

If qualification is BLOCKED:

- do not create a fake contract file;
- do not use defaults as a substitute;
- keep v3 outcome collection blocked.

---

## 13. Remove boolean trust bypass from normal outcome collection

The normal sidecar path MUST NOT accept a boolean equivalent to:

```text
completeness_contract_proven=True
```

as sufficient authority to persist a primary outcome.

The current low-level test fixture may be refactored, but the real CLI/runtime path MUST require a validated semantic contract object loaded from the approved contract file.

Preferred contract-aware API shape:

```python
persist_shadow_outcomes(
    shadow_conn,
    actuals,
    *,
    recorded_at=...,
    actual_contract=validated_contract,
)
```

A test-only helper MAY construct an in-memory validated contract fixture. Production/sidecar CLI MUST NOT expose a `--trust-completeness` or similar bypass.

---

## 14. Contract-aware hourly completeness, only after PROVEN

If the actual contract is PROVEN, primary outcome persistence MUST verify the **expected interval identities**, not only sample count.

For each target hour, verify at least:

```text
exact expected minute marks/timestamps are present
no expected interval is missing
no duplicate expected interval exists
PV value for every expected interval is non-null and finite
source timezone mapping is valid
recorded_at satisfies the proven finalization rule
```

A count-only check such as:

```python
sample_count == expected_sample_count
```

is insufficient by itself because duplicate or wrong-minute rows could satisfy the count.

If any requirement fails:

- do not persist a primary immutable outcome;
- record a diagnostic reason;
- keep the decision available for audit.

Stable reasons SHOULD include:

```text
actual_contract_missing
actual_contract_mismatch
actual_hour_not_finalized
actual_interval_missing
actual_interval_duplicate
actual_interval_unexpected
actual_pv_missing
```

Do not silently repair source rows.

---

## 15. Actual evidence provenance to persist

For every accepted v3 primary outcome, persist non-sensitive provenance sufficient to audit completeness, including:

```text
actual_contract_id
actual_source
actual_sample_count
expected_sample_count
first_sample_at
last_sample_at
actual_coverage_status
```

Also persist a deterministic evidence fingerprint derived from the source rows used for the outcome, for example from canonicalized:

```text
timestamp
pv_kwh
source_csv if available
ingested_at if available
```

The fingerprint is for mutation/audit detection. Do not publish the raw household PV values in result documentation.

---

## 16. Collection state machine MUST be explicit

Do not hard-code every report forever to BLOCKED, but also do not automatically authorize production after 30 days.

Use the following conceptual states.

### 16.1 No proven actual contract

```text
actual_contract_status = BLOCKED
prospective_collection_status = NOT_STARTED
production_adoption_status = NOT_ELIGIBLE
```

### 16.2 Proven contract, collection started but <30 valid calendar days

```text
actual_contract_status = PROVEN
prospective_collection_status = COLLECTING
production_adoption_status = NOT_ELIGIBLE
```

### 16.3 At least 30 valid calendar days collected

Even after the minimum horizon is reached:

```text
prospective_collection_status = MINIMUM_HORIZON_REACHED
production_adoption_status = REVIEW_REQUIRED
```

Do **not** output `ELIGIBLE`, `APPROVED`, or enable production merely because 30 days elapsed.

Human/user review remains mandatory.

Report at least:

```text
policy_name
policy_version
actual_contract_status
actual_contract_id
prospective_collection_status
production_adoption_status
first_valid_v3_target_date
last_valid_v3_target_date
distinct_valid_v3_target_dates
valid_primary_decision_count
finalized_primary_outcome_count
coverage
missing decision/outcome counts and reasons
outside-domain count
source-code revision distribution
actual-contract-id distribution
```

A wall-clock duration of 30 days is not sufficient if prospective decision/outcome gaps make the evidence incomplete. Report gaps separately.

---

## 17. When the 30-day v3 horizon may start

The v3 prospective horizon MUST NOT start merely when this implementation is committed.

It starts only when all of the following are true:

1. v3 selector-window repair is merged and verified;
2. no new changed-code shadow quality diagnostic remains;
3. actual-source qualification verdict is `PROVEN`;
4. the approved actual contract is loaded by the sidecar;
5. the first valid v3 primary decision is frozen before its target outcome can be known;
6. that decision is created using the exact v3 code and source-code revision.

If actual-source qualification remains BLOCKED, the horizon remains NOT_STARTED.

Do not backdate the start date.
Do not convert retrospective rows into primary rows.

---

## 18. Sidecar isolation remains mandatory

Maintain the existing physical separation:

```text
source DB
    read-only
    forecast snapshots + monitoring evidence

shadow DB
    writable
    v3 decisions + outcomes + diagnostics + report metadata
```

Mandatory:

- source and shadow paths resolve to different files;
- source open uses SQLite read-only mode;
- no writable fallback;
- no DDL or schema migration on source;
- `ensure_shadow_schema()` only on shadow DB;
- source hash/schema/data unchanged after decision/outcome/report cycle;
- no production workflow imports/calls the sidecar;
- a sidecar failure cannot affect production success/failure, retries, timing, forecast, SOC, or control.

Do not add a production scheduler hook in this task.

---

## 19. Required tests

All relevant existing Phase 1 tests remain required. Add at least the following new regression coverage.

### Selector-window repair

1. selector uses only `D-21 .. D-1`;
2. row at exactly `D-21` is included;
3. row at `D-22` is excluded from selector;
4. row at `D-45` can still affect 45-day candidate history;
5. 22–45-day-old rows cannot alter selector MAE;
6. fixture where wrong 45-day selector would select correction but correct 21-day selector falls back;
7. inverse fixture where wrong 45-day selector would fall back but correct 21-day selector selects correction;
8. selector reported `sample_count` and `distinct_days` reflect 21-day history only;
9. strict 2% equality falls back;
10. strictly better than 2% may select candidate.

### Policy/version isolation

11. v1 cannot enter v3 candidate history;
12. v2 cannot enter v3 candidate history;
13. v1 cannot enter v3 selector history;
14. v2 cannot enter v3 selector history;
15. reports do not mix v1/v2/v3 primary metrics.

### API/typing/isolation

16. `build_shadow_decision` requires explicit source connection, shadow connection, and snapshot;
17. one-connection compatibility call is no longer supported;
18. source/shadow same path fails before any shadow schema mutation;
19. source DB remains unchanged after a complete test cycle;
20. GitHub Actions reports no `shadow_gate.py` changed-code `ty` diagnostic.

### Actual contract blocked path

21. no contract -> no primary outcome;
22. BLOCKED qualification -> no contract file and no primary outcome;
23. sample count alone cannot prove completeness;
24. normal CLI has no boolean trust bypass.

### Actual contract PROVEN fixture

If the repository evidence is sufficient to reach PROVEN, also test:

25. exact expected interval marks -> accepted;
26. missing expected interval -> rejected;
27. duplicate expected interval -> rejected;
28. unexpected minute mark -> rejected;
29. correct count but wrong interval identities -> rejected;
30. null PV -> rejected;
31. non-finite PV -> rejected;
32. before finalization -> rejected;
33. after proven finalization + exact evidence -> accepted;
34. actual contract ID is persisted;
35. evidence fingerprint is deterministic;
36. retry is idempotent.

### Report state

37. unproven contract -> BLOCKED / NOT_STARTED / NOT_ELIGIBLE;
38. proven contract with <30 days -> PROVEN / COLLECTING / NOT_ELIGIBLE;
39. >=30 valid days -> MINIMUM_HORIZON_REACHED / REVIEW_REQUIRED;
40. report never auto-authorizes production.

If actual qualification remains BLOCKED, tests 25–36 may use a fully explicit synthetic contract fixture, but the real sidecar MUST remain blocked and no real contract file may be fabricated.

---

## 20. Required validation commands

Follow repository instructions and run the code-quality audit before tests.

At minimum run:

```powershell
python -m ruff check .
python -m pytest tests/test_shadow_gate.py -q
python -m pytest -q
```

Run the repository-required applicable `ty`, mypy, Import Linter, deptry, Oxlint, and tsc checks according to repository tooling.

For changed shadow code, the acceptance criterion is:

```text
no new changed-code Ruff diagnostic
no new changed-code ty diagnostic
no new changed-code mypy diagnostic
no new shadow import-boundary violation
focused tests pass
full Python suite passes
```

Existing unrelated repository debt may be reported separately but MUST NOT be used to hide a new shadow-specific diagnostic.

Specifically verify that the current `shadow_gate.py` type diagnostic from the v2 implementation is gone.

---

## 21. Required result document

Create a follow-up result document under `docs/current/product/`.

It MUST explicitly report PASS / FAIL / BLOCKED for:

```text
v2 selector-window bug confirmed
v3 policy version active
45-day candidate history preserved
21-day selector history enforced
D-21 inclusion
D-22 exclusion
45d-vs-21d flip regression #1
45d-vs-21d flip regression #2
strict 2% threshold parity
v1 excluded from v3
v2 excluded from v3
build_shadow_decision explicit two-DB API
one-connection compatibility removed
source DB read-only
shadow DB physically separate
source DB unchanged
changed-code ty diagnostic removed
actual aggregation type evidence
actual cadence evidence
actual timestamp semantics evidence
actual value semantics evidence
actual finalization semantics evidence
actual contract qualification verdict: PROVEN / BLOCKED
contract file present only if PROVEN
boolean completeness bypass absent from real CLI
interval-identity completeness validation
actual contract ID persistence
actual evidence fingerprint
prospective collection status
30-day horizon start date or NOT_STARTED
production adoption status
production forecast non-interference
SOC/control non-interference
focused tests
full tests
GitHub Actions result
```

If the actual contract remains BLOCKED, state exactly which semantic cannot be proven and what additional external/source evidence would be required. Do not weaken the condition.

---

## 22. Production boundary

Regardless of local results, synthetic parity, or early future v3 performance, this task does not authorize production integration.

No v3 shadow-selected value may feed:

- `forecast_hourly`;
- final production PV forecast;
- production correction output;
- physical PV model;
- energy plan;
- SOC optimizer;
- battery target;
- charge/discharge command;
- device control.

Production adoption requires a later separate process:

1. v3 implementation and actual contract are valid;
2. at least 30 calendar days of trustworthy v3 prospective evidence are collected;
3. collection gaps are reviewed;
4. frozen-domain aggregate and per-hour/recent-window behavior is reviewed;
5. offline SOC / purchased-energy / purchase-cost counterfactual analysis is completed;
6. explicit user approval is obtained;
7. a separate production implementation PR is created and reviewed.

No earlier step authorizes production integration.

---

## 23. Stop condition

Stop when one of these two states is reached.

### Success state

```text
phase1-v3 selector = exact 21-day frozen selector
candidate history = exact frozen 45-day definitions
changed-code shadow type regression = fixed
source/shadow DB isolation = preserved
actual contract = PROVEN
contract-aware outcome validation = implemented
sidecar = ready to begin v3 prospective collection
production integration = unchanged/off
```

### Blocked state

If the monitoring actual contract cannot be proven:

```text
phase1-v3 selector repair = completed and tested
actual contract = BLOCKED
primary outcome collection = blocked
30-day horizon = NOT_STARTED
production adoption = NOT_ELIGIBLE
```

In the blocked state, do not invent cadence/finalization semantics and do not proceed to production.
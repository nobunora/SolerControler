# Codex Phase 1 shadow parity and isolation hardening

## 0. Normative instruction

This document is a **mandatory implementation specification**, not a design suggestion.

Codex MUST implement the behavior below exactly. Do not reinterpret, simplify, generalize, optimize, retune, or replace the specified policy. Do not make unrelated refactors. Do not change production forecasting, energy planning, SOC optimization, battery commands, or device control.

Normative words:

- **MUST / MUST NOT**: required. Any deviation is a blocker.
- **SHALL / SHALL NOT**: same force as MUST / MUST NOT.
- **MAY**: optional only where explicitly stated.

If repository evidence is insufficient to implement a requirement safely, **do not guess**. Fail closed, record the blocker in the result report, and stop that part of the implementation.

This work remains validation-only. No shadow-selected value may enter any production decision path.

---

## 1. Read before editing

Before changing code, read all of the following:

1. `AGENTS.md`
2. `docs/current/agent/agent_working_rules.md`
3. `docs/current/product/HOURLY_PV_ADAPTIVE_GATE_SIMULATION.md`
4. `scripts/diagnose_hourly_pv_adaptive_gate.py`
5. `scripts/diagnose_hourly_pv_correction_limits.py`
6. `scripts/diagnose_hourly_pv_regime_bias.py`
7. `docs/current/product/CODEX_PHASE1_PROSPECTIVE_SHADOW_VALIDATION.md`
8. `docs/current/product/CODEX_PHASE1_PROSPECTIVE_SHADOW_HARDENING.md`
9. `docs/current/product/CODEX_PHASE1_PROSPECTIVE_SHADOW_VALIDATION_RESULT_JA.md`
10. current `app/operations/shadow_gate.py`
11. current `scripts/forecast_shadow.py`
12. current `tests/test_shadow_gate.py`

Do not edit production correction code while doing this task.

---

## 2. Allowed implementation scope

The implementation PR SHALL be limited to the shadow-validation path and its tests/documentation.

Expected files:

- `app/operations/shadow_gate.py`
- `scripts/forecast_shadow.py`
- `tests/test_shadow_gate.py`
- a Phase 1 follow-up result document under `docs/current/product/`

A small new shadow-only helper module MAY be added only if needed to keep source-DB read-only access separate from shadow-DB writes. Do not move or refactor unrelated production code.

The following files/behaviors MUST NOT be changed except for a test-only import needed to prove non-interference:

- `app/forecasting/correction_model.py`
- production PV forecast formulas
- physical PV equations/calibration
- forecast provider weights
- `forecast_hourly` latest-value behavior
- energy-plan forecast values
- SOC objective/constraints
- charge/discharge logic
- device-control logic
- production cloud-job success/failure semantics

If the requested implementation appears to require one of those changes, stop and report it as a blocker instead of changing it.

---

## 3. Frozen policy identity

The repaired policy version MUST be exactly:

```text
policy_name    = gate_per_hour_window21d_margin02pct
policy_version = phase1-v2
```

`phase1-v1` MUST remain stored as historical/superseded diagnostic evidence. Do not delete, update, rewrite, migrate-in-place, or count v1 rows as v2.

Primary adoption reports MUST select exactly `policy_version = 'phase1-v2'`.

Any later semantic change affecting candidate values, target eligibility, selector scoring, actual aggregation, evidence filtering, or primary-vintage selection MUST use a new policy version. Never silently change semantics under `phase1-v2`.

The minimum 30-calendar-day prospective horizon starts at the first **valid `phase1-v2` primary decision**. v1 time does not count.

---

## 4. Exact target-domain contract

The frozen adaptive-gate experiment evaluates only target rows satisfying both conditions:

```text
7 <= target_hour <= 22
baseline_forecast_pv_kwh > 0.0
```

Implement one explicit helper with equivalent semantics, e.g.:

```python
def is_frozen_policy_target(hour: int, baseline_kwh: float) -> bool:
    return 7 <= hour <= 22 and baseline_kwh > 0.0
```

The exact function name MAY differ; behavior MUST NOT differ.

For `prospective_primary` decision collection:

- rows outside this domain MUST NOT be persisted as primary decisions;
- rows outside this domain MUST NOT enter candidate history;
- rows outside this domain MUST NOT enter selector history;
- rows outside this domain MUST NOT enter primary MAE, signed bias, coverage, application rate, fallback rate, or adoption-day counts.

The collector SHALL count skipped rows under the stable exclusion reason:

```text
outside_frozen_policy_domain
```

Do not change the frozen hours. Do not infer sunrise/sunset dynamically. Do not include 06:00 or 23:00. Do not include zero-PV target rows.

---

## 5. Exact historical-row eligibility

For every historical row used by any candidate:

```text
same target clock hour
strictly earlier calendar date
age_days in [1, 45]
prior baseline forecast > 0.0
prior row is a valid phase1-v2 primary row
prior outcome is finalized and complete
only one deterministic primary vintage per realized target date/hour/policy/version
```

Historical rows with baseline forecast `<= 0.0` MUST be excluded from all correction-candidate histories.

Do not use `phase1-v1` rows to seed `phase1-v2` candidate history or selector history.

Do not use legacy mutable `forecast_hourly` rows as primary historical evidence.

---

## 6. Exact `production_like_45d` candidate

Build a history stream independent from the same-hour-bias stream.

For target `(D, h)`, a historical row is eligible for `production_like_45d` only if all conditions are true:

```text
same hour h
1 <= age_days <= 45
prior baseline forecast > 0
prior target belongs to the frozen policy domain
prior outcome is valid phase1-v2 primary evidence
target shortwave > 0
prior shortwave > 0
weather_class(prior weather code) == weather_class(target weather code)
0.7 * target_shortwave <= prior_shortwave <= 1.3 * target_shortwave
```

Use the same coarse weather-class mapping as the frozen diagnostic. Do not invent a different grouping.

Residual for each eligible prior row:

```text
residual_i = actual_pv_kwh_i - baseline_prediction_kwh_i
```

If there are no eligible residuals:

```text
production_like_45d = baseline
```

Otherwise calculate exactly:

```text
center = median(residuals)

if n == 1:
    variance = 0.6^2
else:
    variance = mean((residual_i - center)^2)

weight = n/(n+2) * 0.6^2/(0.6^2 + variance)

production_like_45d = max(0, baseline + weight * center)
```

Use `spread_kwh = 0.6` exactly. Do not retune it. Do not add recency weighting. Do not change the variance denominator. Do not replace median with mean.

---

## 7. Exact same-hour-bias candidates

This is the most important parity repair.

The history for BOTH:

```text
same_hour_bias_45d_hl7d
same_hour_bias_45d_hl14d
```

MUST be independent from `production_like_45d` history.

A historical row is eligible if and only if:

```text
same clock hour
1 <= age_days <= 45
prior baseline forecast > 0
prior target belongs to the frozen policy domain
prior outcome is valid phase1-v2 primary evidence
```

For these two candidates, DO NOT apply any of the following:

```text
weather-class filter          -> FORBIDDEN
weather-code equality         -> FORBIDDEN
shortwave > 0 requirement     -> FORBIDDEN
shortwave ±30% gate           -> FORBIDDEN
shortwave log-distance        -> FORBIDDEN
similarity-distance weighting -> FORBIDDEN
production-like shrinkage     -> FORBIDDEN
```

Residual:

```text
residual_i = actual_pv_kwh_i - baseline_prediction_kwh_i
```

Minimum history:

```text
if number of valid residuals < 2:
    candidate = baseline
```

For half-life `H` in `{7.0, 14.0}`:

```text
weight_i = exp(-ln(2) * age_days_i / H)
center   = weighted_median(residual_i, weight_i)
candidate = max(0, baseline + center)
```

Only recency weight is allowed. No other factor may multiply `weight_i`.

Refactor the current helper so its input cannot accidentally carry/use shortwave distance. A preferred contract is conceptually:

```python
same_hour_bias_prediction(
    baseline: float,
    residuals_and_ages: list[tuple[float, int]],
    *,
    half_life_days: float,
) -> float
```

Do not leave an unused `distance` field in this v2 path if doing so could reintroduce similarity weighting later.

---

## 8. Exact selector contract

For target day `D` and target hour `h`, selector scoring MUST use only finalized, valid `phase1-v2` primary outcomes satisfying:

```text
target_hour == h
D - 21 days <= historical target_date < D
```

Do not pool different hours.

Use at most one deterministic primary sample per realized `(target_date, target_hour, policy_name, policy_version)`.

If fewer than 3 distinct historical dates are available:

```text
selected_model = baseline
reason = insufficient_shadow_history
```

Otherwise compute historical MAE for the same four stored candidate predictions:

```text
baseline
production_like_45d
same_hour_bias_45d_hl7d
same_hour_bias_45d_hl14d
```

For each correction candidate:

```text
candidate_mae = mean(abs(actual - candidate_prediction))
```

Baseline:

```text
baseline_mae = mean(abs(actual - baseline_prediction))
```

Choose the correction candidate with the minimum candidate MAE using deterministic model-name ordering as the final tie breaker.

Adopt a correction candidate ONLY when:

```text
best_candidate_mae < baseline_mae * 0.98
```

The comparison is strict `<`.

If the best candidate is equal to `baseline_mae * 0.98`, it MUST fall back to baseline.

If no candidate passes:

```text
selected_model = baseline
reason = baseline_fallback
```

Do not retune 21 days. Do not retune 2%. Do not change minimum distinct days from 3. Do not introduce statistical significance tests in place of this rule.

---

## 9. Primary-vintage selection

All legitimate forecast vintages MAY remain auditable, but primary scoring/reporting MUST use exactly one pre-outcome decision per realized target hour.

The winner MUST be chosen using pre-outcome information only.

Use the existing deterministic ordering unless a test proves it violates the frozen prospective contract:

```text
latest eligible decision_at
then latest eligible forecast_issued_at
then shadow_decision_id as deterministic tie-breaker
```

Never choose the primary vintage by looking at actual error.

All non-winning legitimate vintages MUST be excluded from primary metrics under:

```text
diagnostic_vintage
```

---

## 10. Source DB and shadow DB MUST be physically separate

The current single-DB CLI contract is not acceptable for prospective v2 collection.

The command-line interface MUST use separate paths.

### 10.1 CLI arguments

For `decision` mode and `outcome` mode, require BOTH:

```text
--source-db-path <path>
--shadow-db-path <path>
```

For `report` mode, require:

```text
--shadow-db-path <path>
```

`--source-db-path` MAY be accepted by report mode but MUST NOT be required or written.

The old ambiguous single argument:

```text
--db-path
```

MUST NOT silently mean both source and shadow storage in v2. Either remove it from the v2 CLI or reject it with a clear error directing the operator to the two explicit paths.

Before opening databases:

```text
resolve(source_db_path) != resolve(shadow_db_path)
```

If equal, terminate with non-zero exit before any schema creation or write.

### 10.2 Source DB connection

The source DB MUST be opened with SQLite read-only URI semantics (`mode=ro` or equivalent).

Requirements:

- source file must already exist;
- if read-only open fails, terminate non-zero;
- do not retry writable;
- do not create parent directories for source;
- do not call `sqlite.open_db()` if that helper can create/write the DB;
- do not call `ensure_schema()` on source;
- do not call `ensure_shadow_schema()` on source;
- do not execute CREATE/ALTER/DROP/INSERT/UPDATE/DELETE/REPLACE on source;
- do not change WAL/journal/schema PRAGMA on source.

The source connection may only read immutable/current evidence required by the collector, primarily:

```text
forecast_hourly_snapshots
monitoring_samples
```

### 10.3 Shadow DB connection

The shadow DB is the only writable database.

`ensure_shadow_schema()` MUST run only on the shadow connection.

The v2 shadow schema MUST contain enough target evidence in each decision row so candidate history does not need mutable production latest rows.

At minimum each v2 decision MUST persist:

```text
forecast_snapshot_id
forecast_run_id
forecast_issued_at
target_at
target_date
target_hour
baseline_prediction_kwh
forecast_weather_code
frozen coarse weather class or enough data to recompute it deterministically
forecast_shortwave_radiation_w_m2
candidate predictions
selector score summary
selected model
selected prediction
policy name/version
decision_at
cutoff_at
source_code_version
evidence class / primary eligibility reason
```

Candidate and selector histories MUST read previous v2 decision/outcome rows from the shadow DB.

Do not use `forecast_hourly` latest rows as v2 historical evidence.

### 10.4 Required isolation test

Add an integration-style SQLite test that:

1. creates a source DB fixture containing snapshot + monitoring tables/data;
2. computes SHA-256 of source DB bytes before the shadow cycle;
3. opens source through the exact production v2 read-only helper;
4. runs `decision -> outcome -> report` using a separate shadow DB;
5. closes both DBs;
6. computes SHA-256 of source DB bytes again;
7. asserts before-hash == after-hash;
8. asserts no `forecast_shadow_*` table exists in source;
9. asserts expected `forecast_shadow_*` rows exist in shadow.

If SQLite creates external temporary files during a read, the test still MUST prove the source database contents/schema are unchanged and the source connection itself is read-only.

---

## 11. Source-code revision is mandatory for v2 primary decisions

Every `phase1-v2` primary decision MUST contain a non-empty source-code revision.

Do not write `unknown`, empty string, or null for a primary v2 row.

For CLI `decision` mode, add/require an explicit argument such as:

```text
--source-code-version <git-sha>
```

The value MUST be non-empty. If omitted/empty in `prospective_primary` mode, exit non-zero before persisting decisions.

The independently scheduled sidecar must pass the deployed/repository revision used to calculate the decision.

Primary reports MUST expose counts by `source_code_version`.

A code revision change alone does not automatically invalidate the study, but any semantic change listed in Section 3 requires a new policy version.

---

## 12. Actual-source semantics and completeness: fail closed

Current logic `sample_count > 0` is insufficient and MUST NOT remain the v2 completeness rule.

Before implementing v2 primary outcome completeness, inspect and document:

- what each `monitoring_samples.pv_kwh` row represents;
- expected interval/cadence;
- timestamp meaning (start/end of interval);
- expected rows per target hour;
- how missing intervals appear;
- whether a later ingestion can append a missing interval;
- configured timezone behavior.

Repository code currently proves timestamp parsing/storage but does not, by itself, prove a trustworthy expected rows-per-hour contract. Therefore:

### 12.1 If the cadence/completeness contract IS provable

Implement an explicit completeness predicate based on that contract.

Persist enough evidence to audit it, including where applicable:

```text
actual_sample_count
expected_sample_count
first_sample_at
last_sample_at
coverage status
actual_source
```

A primary outcome MUST be persisted only when:

```text
target hour is past finalization grace
AND source completeness predicate == true
```

### 12.2 If the cadence/completeness contract IS NOT provable

DO NOT invent `expected_sample_count`.

DO NOT treat `sample_count > 0` as complete.

DO NOT persist any new v2 primary outcome.

Instead:

```text
record diagnostic reason = actual_completeness_contract_unproven
result report = BLOCKED for v2 outcome collection
```

The rest of the parity/storage implementation may proceed, but the 30-day prospective evidence clock MUST NOT start until trustworthy primary outcomes can be finalized.

### 12.3 Regression requirements

Tests MUST include:

- target hour not ended -> no primary outcome;
- target hour ended but partial samples -> no primary outcome;
- complete samples -> exactly one primary outcome;
- retry same complete outcome -> zero duplicate;
- late attempt after incomplete diagnostic -> complete outcome may be inserted once completeness is later proven;
- incomplete diagnostic never becomes a primary score sample.

---

## 13. Exact parity test against frozen diagnostic

Previous parity tests are insufficient. Add a deterministic end-to-end parity test with two independent computation paths.

### 13.1 Oracle path

Use the frozen diagnostic helpers from:

- `scripts/diagnose_hourly_pv_correction_limits.py`
- `scripts/diagnose_hourly_pv_regime_bias.py`
- `scripts/diagnose_hourly_pv_adaptive_gate.py`

Do not call the new shadow helper from the oracle path.

### 13.2 Shadow path

Use the actual v2 shadow implementation and SQLite shadow history.

### 13.3 Required fixture cases

The same synthetic causal fixture MUST contain all of these:

1. target hour inside 07-22 with positive forecast;
2. target hour outside 07-22;
3. target baseline == 0;
4. same-hour prior row with same weather/shortwave match;
5. same-hour prior row with different weather class;
6. same-hour prior row outside shortwave ±30%;
7. same-hour prior row with shortwave <= 0;
8. same-hour prior row with baseline forecast == 0;
9. fewer than 2 valid same-hour rows;
10. enough rows for hl7d and hl14d to differ;
11. a row that same-hour bias MUST use but production-like MUST reject;
12. at least 3 distinct 21-day selector dates;
13. a candidate MAE exactly equal to `baseline_mae * 0.98` to verify strict fallback;
14. a candidate MAE strictly below the 0.98 threshold to verify selection.

### 13.4 Exact comparisons

For every eligible target, assert equality/tight numeric tolerance for:

```text
baseline
production_like_45d
same_hour_bias_45d_hl7d
same_hour_bias_45d_hl14d
selected model
fallback/selection reason
```

Also assert that excluded target/history rows do not appear in the v2 primary sample universe.

The test MUST fail against the current pre-fix v1 same-hour implementation.

A single hard-coded expected number without an independent oracle is not sufficient.

---

## 14. Separate sidecar orchestration only

Prospective v2 collection MUST run as a validation sidecar, independent of production planning/control.

Do not import or invoke the shadow sidecar from:

- production forecast generation;
- energy-plan workflow;
- SOC optimization;
- battery command workflow;
- device-control runner.

Required phases:

```text
Phase A: pre-outcome decision collection
    source DB: read-only
    shadow DB: write decision

Phase B: post-finalization outcome collection
    source DB: read-only
    shadow DB: append finalized outcome

Phase C: report
    shadow DB only
```

A sidecar failure MUST NOT:

- abort production;
- change production exit code;
- trigger a production retry;
- delay battery/control execution;
- change any production forecast/plan/value.

Document exact manual/scheduler commands for A/B/C.

Do not add an automatic production scheduler hook in this PR.

---

## 15. Collection health/gap reporting

The v2 report MUST include at least:

```text
policy_name
policy_version
first_valid_v2_target_date
last_valid_v2_target_date
distinct_valid_v2_target_dates
eligible_target_count
valid_primary_decision_count
finalized_primary_outcome_count
coverage
excluded_count_by_reason
missing_decision_count
missing_finalized_outcome_count
selection_count_by_model
selection_count_by_hour
baseline_mae_kwh
shadow_selected_mae_kwh
relative_mae_improvement_percent
baseline_signed_bias_kwh
shadow_selected_signed_bias_kwh
correction_application_rate
baseline_fallback_rate
source_code_version_counts
```

Recent windows MUST remain available for 7/14/21 days.

Do not claim the study has reached 30 days merely because 30 wall-clock days elapsed.

Before adoption review, the report MUST identify collection gaps. A gap does not automatically fail the study, but it MUST be explicitly reviewed.

No retrospective recreation after an outcome was knowable may fill a missing primary decision. Such rows are diagnostic only.

---

## 16. Tests required before the implementation PR can be called complete

Add/adjust focused tests so the following are explicit PASS/FAIL assertions:

1. frozen target domain includes exactly hours 07-22 with target baseline > 0;
2. target hour 06 excluded;
3. target hour 23 excluded;
4. target forecast 0 excluded;
5. prior forecast 0 excluded from production-like;
6. prior forecast 0 excluded from same-hour bias;
7. production-like uses coarse weather class;
8. production-like enforces shortwave ±30%;
9. production-like uses shrinkage formula exactly;
10. same-hour bias accepts different-weather prior rows;
11. same-hour bias accepts out-of-band-shortwave prior rows;
12. same-hour bias ignores shortwave distance entirely;
13. same-hour bias uses recency-only weights;
14. same-hour bias falls back with <2 observations;
15. selector uses same hour only;
16. selector uses prior dates only;
17. selector uses 21-day window;
18. selector requires 3 distinct dates;
19. selector strict `< 0.98 * baseline` behavior;
20. multiple vintages yield one primary score sample;
21. future-issued snapshot rejected;
22. post-target decision rejected as primary;
23. retrospective diagnostic excluded from primary metrics;
24. v1 excluded from v2 primary history/report;
25. source DB == shadow DB path rejected;
26. source DB opens read-only;
27. source DB schema/data unchanged after full shadow cycle;
28. shadow schema exists only in shadow DB;
29. empty source-code version rejected for v2 primary decision;
30. incomplete actual cannot become primary outcome;
31. complete actual can become one primary outcome;
32. retry is idempotent;
33. end-to-end candidate parity with frozen oracle;
34. end-to-end selector parity with frozen oracle;
35. report exposes required health/gap/version fields;
36. no production/SOC/control import or data-flow dependency introduced.

Run the repository-required code-quality audit before tests.

Then run at minimum:

```powershell
python -m pytest tests/test_shadow_gate.py -q
python -m pytest -q
```

Also run the applicable repository quality checks required by `AGENTS.md` / working rules. Separate existing unrelated repository debt from new/changed-code failures.

Any new `shadow_gate.py`, `forecast_shadow.py`, or new shadow-only helper diagnostic is a blocker.

---

## 17. Required local smoke validation

Use copies/fixtures only. Do not write production cloud data.

Perform one v2 smoke cycle with physically separate SQLite files:

```text
source fixture/copy DB -> read-only
shadow validation DB   -> writable
```

Required evidence in the result report:

```text
source DB path and shadow DB path were different: PASS
source connection read-only: PASS
source DB unchanged after smoke: PASS
v2 decision rows inserted: N
v2 outcome rows inserted: N or BLOCKED because actual completeness contract unproven
v1 rows counted in v2 report: 0
outside-domain primary rows: 0
source_code_version empty primary rows: 0
```

Do not publish private site values, credentials, project identifiers, or household generation values.

---

## 18. Required result report format

Create/update a follow-up result document and include this exact checklist with PASS / FAIL / BLOCKED / NOT RUN:

```text
frozen target-domain parity (07-22, forecast > 0):
prior positive-forecast eligibility:
production-like weather/shortwave history parity:
production-like shrinkage formula parity:
same-hour history parity (no weather gate):
same-hour history parity (no shortwave gate):
same-hour weighting parity (recency only):
same-hour minimum-history parity:
end-to-end frozen candidate parity:
end-to-end frozen selector parity:
strict 2% threshold parity:
v1 excluded from v2 primary evidence:
v2 policy version active:
30-day horizon reset to first valid v2 decision:
source DB path != shadow DB path:
source DB opened read-only:
source DB unchanged after shadow cycle:
shadow schema only in shadow DB:
source-code version mandatory:
actual-source semantics documented:
actual completeness rule proven:
partial actual rejected:
prospective gap/health reporting:
sidecar failure non-interference:
production forecast non-interference:
SOC/control non-interference:
focused tests:
full test suite:
changed-code Ruff:
changed-code ty:
Import Linter:
Oxlint/tsc if applicable:
Firestore parity: NOT RUN unless a safe non-production environment exists
PostgreSQL parity: NOT RUN unless a safe non-production environment exists
```

If actual completeness remains unproven, the result MUST say:

```text
actual completeness rule proven: BLOCKED
30-day prospective evidence collection: NOT STARTED
production adoption eligibility: NOT ELIGIBLE
```

Do not call Phase 1 v2 evidence collection ready if that blocker remains.

---

## 19. Production adoption is explicitly forbidden in this PR

Even if local tests or early v2 outcomes are excellent, this PR MUST NOT connect shadow outputs to production.

No shadow-selected value may feed:

- `forecast_hourly`;
- final PV forecast;
- correction model output;
- physical PV model;
- energy plan;
- SOC optimization;
- battery target;
- charge/discharge commands;
- device control.

Production adoption requires a later, separate decision after ALL of the following:

1. v2 semantics pass this specification;
2. actual completeness is proven;
3. at least 30 calendar days of valid v2 prospective primary evidence have been collected;
4. collection gaps are reviewed;
5. exact frozen-domain MAE/bias results are reviewed;
6. 7/14/21-day and per-hour stability are reviewed;
7. offline SOC / purchased-energy / purchase-cost counterfactual impact is reviewed;
8. the user explicitly approves production adoption;
9. a separate production implementation PR is created and reviewed.

No earlier step authorizes production integration.

---

## 20. Stop condition

Stop this implementation task when, and only when:

```text
phase1-v2 candidate semantics == frozen diagnostic semantics
AND target sample universe == frozen diagnostic sample universe
AND source DB is physically read-only/separate from shadow DB
AND v1 is excluded from v2 evidence
AND incomplete actuals fail closed
AND required parity/isolation tests pass
AND sidecar remains production-independent
```

If actual completeness cannot be proven from repository/source evidence, stop with that item BLOCKED. Do not work around it by weakening the gate.

Do not enable adaptive correction in production.
# Codex Phase 1 shadow parity and isolation hardening

## Purpose

Review of `CODEX_PHASE1_PROSPECTIVE_SHADOW_VALIDATION_RESULT_JA.md` and the current merged Phase 1 implementation found that the shadow path is still correctly isolated from production planning/control, but several issues must be fixed **before the 30-day prospective evidence window can be considered valid for adoption**.

This is still validation-only work. Do not enable any shadow-selected value in production forecasting, SOC optimization, battery scheduling, or device control.

The required follow-up has two goals:

1. make the prospective shadow experiment exactly comparable to the frozen diagnostic that selected the policy;
2. physically isolate shadow writes from the production SQLite database while preserving read-only access to forecast snapshots and actual monitoring evidence.

## Blocking findings

### 1. Primary sample universe does not match the frozen experiment

The frozen adaptive-gate diagnostic constructs evaluation records only when:

```text
07 <= hour <= 22
forecast_pv_kwh > 0
```

See `scripts/diagnose_hourly_pv_adaptive_gate.py::build_prediction_records()` and `scripts/diagnose_hourly_pv_correction_limits.py::target_rows()`.

The current Phase 1 shadow CLI creates decisions for all 24 hours and the primary report can aggregate all finalized primary rows. This changes the statistical population relative to the frozen experiment. Nighttime/zero-forecast rows can dilute aggregate MAE and coverage even though the selector was chosen on a different sample universe.

**Required fix:**

- Define an explicit policy-domain eligibility function matching the frozen experiment:
  - target hour is 07 through 22 inclusive;
  - target baseline forecast is strictly positive.
- Rows outside this domain may be retained for audit/diagnostic purposes, but they must not enter:
  - primary selector history;
  - primary candidate-history samples;
  - primary MAE/bias/coverage;
  - the 30-day adoption evidence count.
- Report excluded counts under a stable reason such as `outside_frozen_policy_domain`.
- The primary report must state its sample-universe contract explicitly.

### 2. `same_hour_bias` does not currently match the frozen candidate

The frozen same-hour candidates intentionally ignore weather-vector similarity. Their history is:

```text
same clock hour
strictly prior day
within 45 calendar days
prior forecast > 0
minimum 2 observations
```

The only weight for the 7-day and 14-day variants is recency:

```text
exp(-ln(2) * age_days / half_life_days)
```

There is **no weather-class gate, no shortwave ±30% gate, and no shortwave-distance similarity weight** for these two candidates.

See `scripts/diagnose_hourly_pv_regime_bias.py::history()`, `weighted_location()`, and `predict()`.

The current `app/operations/shadow_gate.py` builds the residual list used by `same_hour_bias_prediction()` only inside the production-like weather/shortwave match, and then multiplies the recency weight by a shortwave-distance similarity term. That is not the candidate evaluated by the frozen adaptive-gate simulation.

**Required fix:**

Use two independent history streams:

```text
production_like history:
    same hour
    strictly prior day
    <=45 days
    prior baseline forecast > 0
    target/prior shortwave > 0
    same coarse weather class
    prior shortwave within target ±30%

same_hour_bias history:
    same hour
    strictly prior day
    <=45 days
    prior baseline forecast > 0
    no weather-class filter
    no shortwave filter
    no similarity-distance weight
```

For `same_hour_bias_45d_hl7d` and `same_hour_bias_45d_hl14d`:

- require at least 2 valid prior observations;
- use additive residuals;
- use recency-weighted median only;
- return baseline when history is insufficient.

Do not change the frozen candidate definition during this repair.

### 3. Positive-forecast eligibility must also apply to historical candidate rows

The frozen diagnostic's source row set and candidate selection exclude prior rows with `forecast <= 0`.

The prospective implementation must independently enforce prior-baseline positivity when building candidate histories. Do not rely on the fact that most daylight rows happen to be positive.

Add regression coverage for prior rows with zero forecast so they cannot affect candidate residuals.

### 4. Current finalized-actual check is not a complete completeness proof

The current Phase 1 path waits until target-hour end plus a grace period, but then treats any `sample_count > 0` as complete. That can permanently freeze an incomplete actual if the monitoring source normally contains multiple interval rows per target hour.

`sample_count > 0` is not, by itself, evidence that the target hour is complete.

**Required fix:**

First inspect and document the actual `monitoring_samples` contract:

- what `pv_kwh` represents (interval energy, hourly energy, cumulative value, etc.);
- expected sampling cadence or interval boundaries;
- how source ingestion represents missing intervals;
- whether daylight-saving/timezone handling is relevant to this deployment.

Then implement a fail-closed completeness rule appropriate to that contract.

At minimum, primary outcome persistence must not occur unless the implementation can prove that the source data needed for the whole target hour is finalized and sufficiently complete.

Persist useful non-sensitive completeness metadata, for example where applicable:

```text
actual_sample_count
expected_sample_count or expected interval count
first_sample_at
last_sample_at
coverage/completeness status
actual source/provenance
```

If the repository does not contain enough information to define a trustworthy completeness rule, do **not** guess a threshold. Mark the outcome incomplete/diagnostic and report the missing contract as a blocker.

Add regression tests showing that a partially populated hour cannot become an immutable primary outcome.

## Required exact parity test

The previous parity fixtures were not sufficient to catch the same-hour history mismatch above.

Add a deterministic end-to-end parity harness that evaluates the **same causal synthetic fixture** through both:

- the frozen diagnostic candidate logic; and
- the Phase 1 prospective shadow candidate logic.

The test must compare, for eligible target rows:

```text
baseline
production_like_45d
same_hour_bias_45d_hl7d
same_hour_bias_45d_hl14d
selected model under 21-day / per-hour / 2% gate
```

The fixture must deliberately include:

- same-hour prior rows with different weather classes;
- same-hour prior rows outside shortwave ±30%;
- zero-forecast prior rows;
- fewer-than-two-history cases;
- enough history to exercise both half-lives;
- a case where the same-hour candidate should use a row that production-like must reject.

A hand-written expected numeric constant alone is not enough. Prefer an independent oracle path using the frozen diagnostic helpers, or otherwise preserve two clearly independent implementations in the test so shared bugs cannot make the test pass trivially.

## Policy-version reset

The current prospective implementation identifies the policy as `phase1-v1`, but the candidate/sample-universe mismatches mean existing v1 decisions must **not** be counted toward production-adoption evidence.

After the above semantic fixes:

- increment the policy version, for example to `phase1-v2`;
- do not rewrite or delete existing `phase1-v1` rows;
- preserve v1 only as superseded/diagnostic evidence;
- primary reports for adoption must not mix v1 and v2;
- restart the minimum 30-calendar-day prospective horizon from the first valid v2 primary decision.

Any future change that can alter candidate values, selector decisions, target eligibility, actual aggregation, or primary evidence filtering must require another policy-version change rather than silently mixing semantics under one version.

Primary decisions should also record a non-empty source-code revision where practical, and reports should expose revision distribution so an audit can identify code changes during the collection period.

## Physical storage isolation

The current `scripts/forecast_shadow.py` opens a single SQLite database and both reads production evidence and creates/writes `forecast_shadow_*` tables in that same database.

Even though those tables do not feed production logic, this is weaker isolation than intended for a validation-only experiment.

For prospective collection, split the data paths:

```text
production/source database
    read-only
    - forecast_hourly_snapshots
    - monitoring_samples
    - other source evidence only if explicitly required

shadow evidence database
    writable
    - forecast_shadow_decisions
    - forecast_shadow_outcomes
    - forecast_shadow_outcome_diagnostics
    - shadow-only metadata/health records
```

### Source database requirements

- Add a distinct source DB argument/configuration such as `--source-db-path`.
- Open the source SQLite DB in read-only mode (`mode=ro` or equivalent).
- Never call `ensure_schema()`, `ensure_shadow_schema()`, migration DDL, WAL-changing PRAGMA, or any write statement on the source connection from the shadow collector.
- If read-only open fails, fail closed. Do not silently reopen the source DB writable.
- Do not alter `forecast_hourly`, `forecast_hourly_snapshots`, `monitoring_samples`, or any other production table from the shadow collector.

### Shadow database requirements

- Add a separate writable path such as `--shadow-db-path`.
- Run `ensure_shadow_schema()` only against the shadow DB.
- Store enough immutable forecast evidence in each shadow decision to make later scoring/audit independent of mutable source tables.
- At minimum, preserve the snapshot identity plus the target-hour fields required by the frozen candidate definitions (including baseline forecast, weather evidence used by production-like, and shortwave evidence), together with policy/source-code version.
- Candidate history and selector history should come from prior valid shadow decisions/outcomes, not by mutating or depending on a production latest-value table.

If a prior snapshot must be looked up from the source DB by immutable snapshot ID, that lookup must remain read-only and must not make historical primary evidence depend on mutable latest rows.

### Isolation regression

Add a test that opens a source SQLite DB read-only and proves a complete `decision -> outcome -> report` shadow cycle writes only to the shadow DB.

The test should verify that the source DB file/schema/data are unchanged by the shadow operation.

## Prospective collection orchestration

The current explicit CLI is useful, but a 30-day prospective study is only valid if decisions are reliably frozen before outcomes arrive.

Provide a **separate validation-sidecar collection path**. It must not be called by the production forecast/SOC/control workflow and failure must not affect production execution.

The sidecar should support two independent phases:

```text
pre-outcome decision collection
    -> read immutable source snapshot read-only
    -> freeze v2 shadow decision into shadow DB

after-finalization outcome collection
    -> read finalized monitoring evidence read-only
    -> append outcome into shadow DB
```

Requirements:

- Do not import or call the sidecar from the production planning/control path.
- A shadow failure must never abort, retry, delay, or change a production control decision.
- Document how to schedule the sidecar independently.
- Record collection health/gap information so missed decision windows cannot be mistaken for valid negative evidence.
- Do not retrospectively recreate a missed primary decision after the outcome could have been known; classify such attempts as diagnostic only.

Useful report fields should include:

```text
first valid v2 target date
last valid v2 target date
distinct valid prospective target dates
eligible decision count
finalized outcome count
missing decision/outcome counts and reasons
policy-domain exclusion count
code revision distribution
```

Do not treat merely waiting 30 wall-clock days as sufficient if collection gaps prevent a trustworthy prospective sample. Report the gaps and require explicit review before adoption.

## Production boundary remains unchanged

This hardening is still **validation infrastructure only**.

Do not modify or connect any shadow output to:

- `forecast_hourly` latest forecast values;
- production PV correction outputs;
- physical PV equations or calibration;
- provider weights;
- energy-plan forecast values;
- SOC optimization objectives or constraints;
- charge/discharge commands;
- device control.

Even if v2 shadow results look strongly favorable during implementation or early collection, production integration is out of scope.

Production adoption requires all of the following as a separate decision:

1. at least 30 calendar days of valid v2 prospective evidence with collection gaps explicitly reviewed;
2. primary evaluation on the exact frozen policy sample universe;
3. recent-window and per-hour stability review;
4. downstream SOC / purchased-energy / purchase-cost counterfactual review using a safe offline interface;
5. explicit user approval to adopt;
6. a separate production implementation PR.

## Required Codex result report

After implementation, update the Phase 1 validation result or add a follow-up result document with explicit PASS/FAIL for:

```text
frozen target-domain parity (07-22, forecast > 0)
production-like history parity
same-hour history parity (no weather/shortwave gate)
same-hour weighting parity (recency only)
zero-forecast history exclusion
end-to-end frozen-diagnostic parity test
actual-source semantics documented
actual completeness fail-closed gate
v1 evidence excluded from adoption
v2 policy version active
30-day horizon reset to first valid v2 decision
source DB opened read-only
shadow DB physically separate
source DB unchanged after shadow cycle
sidecar failure non-interference
prospective gap/health reporting
production forecast non-interference
SOC/control non-interference
focused tests
full test suite
changed-code ty/Ruff/import-linter status
Firestore parity: PASS / FAIL / NOT RUN
PostgreSQL parity: PASS / FAIL / NOT RUN
```

Separate unrelated repository-wide quality debt from new Phase 1 diagnostics.

## Stop condition

Stop after the v2 validation-only collector is parity-correct, physically isolated, fail-closed on incomplete actuals, and ready to collect trustworthy prospective evidence.

Do not enable production adaptive correction in this follow-up PR.
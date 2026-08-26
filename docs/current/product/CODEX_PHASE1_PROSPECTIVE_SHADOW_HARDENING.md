# Codex Follow-up Instructions: Harden Phase 1 Prospective Shadow Evidence

## Purpose

PR #10 implements the first Phase 1 prospective shadow path. The implementation is correctly isolated from production forecast, SOC planning, and battery control, but review found several evidence-integrity issues that must be fixed before prospective collection starts.

This document supplements `CODEX_PHASE1_PROSPECTIVE_SHADOW_VALIDATION.md`. Where this document is stricter, follow this document for PR #10 and later Phase 1 collection.

The core policy remains frozen:

```text
selector granularity: per hour
selector score window: trailing 21 calendar days
required improvement over baseline: 2% MAE
fallback: baseline
candidate set: unchanged
```

Do not change production behavior and do not retune the policy.

## 1. Hard prospective-eligibility boundary

A row must not become primary prospective evidence merely because it has a timestamp or a quality flag.

For a primary prospective decision, prove all of the following:

```text
forecast_issued_at <= forecast eligibility cutoff
shadow decision is created before the target outcome can be known
decision_at < target_at
actual/outcome data is not read by decision construction
forecast snapshot was eligible at the declared cutoff
```

A decision created after `target_at` is retrospective and must never enter:

- the 21-day selector score history;
- primary prospective MAE/bias metrics;
- the 30-day production-adoption evidence set.

Do not merely append a warning flag and continue treating the row as valid.

If retrospective execution is useful for tests or diagnostics, persist it only under an explicit evidence class such as `retrospective_diagnostic`, and exclude that class from all primary selector/report queries.

The normal collection CLI should fail closed when asked to create a primary prospective decision for a target whose decision deadline has already passed.

## 2. Do not use one current cutoff to backfill historical targets

PR #10 currently allows a date range and one `--cutoff-at` value. A current-time cutoff applied to old target dates can select forecast vintages that were not available at the original operational decision time.

For primary prospective collection, either:

1. allow only target hours whose outcome is still in the future and use the real operational cutoff for those targets; or
2. derive and persist a deterministic per-target operational cutoff from an explicit schedule/contract.

Do not infer a historical cutoff from the time the validation command happens to be rerun.

Backfill can exist only as explicitly labeled retrospective diagnostic evidence.

## 3. Freeze only finalized actual outcomes

`forecast_shadow_outcomes` is append-only, so a partial actual must never be inserted as if it were final.

Before inserting an outcome, prove that the target hour is complete and that the actual source is sufficiently complete for the repository's monitoring contract.

At minimum:

- `outcome_recorded_at` must be after the target hour has ended, with any required ingestion grace period;
- reject or skip the current/incomplete hour;
- record actual-source provenance and completeness metadata where available;
- record sample/count or another completeness indicator when the source supports it;
- do not use `INSERT OR IGNORE` to permanently lock an accidentally partial actual.

If actual completeness cannot be established, do not create a primary outcome row. Record a missing/incomplete diagnostic instead.

Add a regression test that attempts to record an outcome while the target hour is still incomplete and proves that no primary outcome is persisted.

## 4. One primary score sample per target date/hour

Append-only forecast snapshots may legitimately contain multiple vintages for one target hour. Retaining those vintages is correct, but the fixed offline selector was evaluated with one forecast sample per `(target_date, target_hour)`.

Do not let multiple shadow decisions for regenerated vintages silently multiply the statistical weight of one realized target hour.

Define a deterministic primary-evidence rule. Recommended structure:

```text
all legitimate decisions/vintages -> retained for audit
exactly one primary prospective decision per target_date/target_hour/policy -> selector score + primary report
other decisions -> diagnostic/vintage comparison only
```

The primary decision must be determined using information available before the target outcome, not selected after seeing which vintage performed best.

Do not mutate or delete older decisions to achieve this. Use an immutable primary-evidence classification or a deterministic query rule that is fully reproducible from pre-outcome timestamps/cutoffs.

Add tests proving that two valid vintages for the same target hour produce two retained decision records but contribute only one sample to selector MAE and primary prospective reporting.

## 5. Candidate definitions must match the frozen simulation

The purpose of Phase 1 is to prospectively test the candidate policy selected by the frozen offline experiment, not a similar new policy.

Review found candidate-definition drift in PR #10.

### `production_like_45d`

The frozen diagnostic uses the production-shaped shrinkage rule from `scripts/diagnose_hourly_pv_correction_limits.py::production_like_prediction`:

```text
center = median(residuals)
variance = spread^2 when n == 1,
           otherwise mean((residual - center)^2)
weight = n/(n+2) * spread^2/(spread^2 + variance)
prediction = max(0, baseline + weight * center)
default spread = 0.6 kWh
```

PR #10 currently applies the raw median residual without this shrinkage. Fix it.

Weather matching must also use the same coarse weather-class semantics as the production/offline diagnostic, not exact equality of raw weather codes if those semantics differ.

Use the repository's existing weather-class helper or reproduce its stable mapping in the shadow module without changing production code.

### `same_hour_bias_45d_hl7d` / `hl14d`

The frozen diagnostic requires at least two valid prior same-hour observations. With fewer than two, the candidate equals baseline.

Retain the existing weighted-median decay semantics:

```text
weight = exp(-ln(2) * age_days / half_life_days)
```

Do not let a single historical residual create a non-baseline candidate if the frozen simulation would have fallen back to baseline.

### Candidate-parity test

Add a deterministic fixture that computes the four candidates with both:

- the frozen diagnostic implementation; and
- the Phase 1 shadow implementation.

For the same causal fixture, predictions must match within a tight numerical tolerance. This parity test is required before prospective collection.

Do not import an offline script into production runtime code if that would create an architectural dependency violation. Shared pure logic may be extracted only if it remains a small, reviewable change and preserves current production behavior.

## 6. Timezone-safe actual-hour aggregation

PR #10's CLI groups monitoring rows with raw timestamp substrings. Confirm that this exactly matches the storage timezone contract.

If timestamps can be UTC, offset-aware, or mixed, replace substring bucketing with the repository's existing timezone-aware hourly aggregation logic or an equivalent explicit conversion using the configured site timezone.

Add tests around a UTC/JST boundary so one physical hour cannot be assigned to the wrong target date/hour.

Do not change existing production monitoring semantics as part of this fix.

## 7. Primary reporting must exclude invalid evidence

The primary prospective report must use only rows that are eligible for the adoption study.

Exclude from primary metrics:

- retrospective/backfilled decisions;
- decisions created after the target outcome could be known;
- incomplete/partial outcomes;
- diagnostic-only vintages that are not the primary per-hour evidence row;
- rows with hard evidence-integrity failures.

Report excluded counts and reasons separately. Never silently drop them.

`valid_decision_count` must mean actually valid primary decisions, not simply every stored decision row.

Keep an all-record diagnostic report if useful, but label it separately from the primary prospective report.

## 8. Production isolation remains mandatory

Even after these fixes, Phase 1 remains validation-only.

Do not connect shadow output to:

- `forecast_hourly` values;
- the production correction path;
- the physical PV model;
- provider weights;
- energy-plan/SOC optimization;
- charge/discharge commands;
- device control.

A favorable local test or early prospective result is not authority to enable production behavior.

Production adoption requires:

```text
Phase 1 implementation converged
-> fixed policy collected prospectively for >= 30 calendar days
-> primary evidence reviewed
-> downstream SOC/cost counterfactual reviewed
-> explicit adoption decision
-> separate production implementation PR
```

## 9. Required PR #10 follow-up tests

Before PR #10 is ready for review, add tests proving at least:

1. a decision after `target_at` cannot become primary prospective evidence;
2. historical backfill with a current cutoff cannot enter primary evidence;
3. an incomplete target hour cannot create a final primary outcome;
4. multiple vintages are retained but do not double-weight selector/report metrics;
5. `production_like_45d` matches the frozen shrinkage formula;
6. coarse weather-class matching matches the frozen diagnostic;
7. same-hour candidates require the same minimum history as the frozen diagnostic;
8. candidate predictions match the frozen diagnostic on a deterministic fixture;
9. actual-hour aggregation is timezone-safe;
10. primary reports exclude invalid/diagnostic evidence and expose exclusion counts;
11. no production/SOC/control consumer imports or reads the shadow-selected value.

Run the repository-required quality audit before tests, then focused tests and the full suite.

## 10. Required result update

Update PR #10's result report with explicit PASS/FAIL lines for:

```text
prospective eligibility hard gate
retrospective evidence exclusion
finalized-actual gate
multi-vintage primary-sample deduplication
production-like candidate parity
same-hour candidate parity
weather-class parity
timezone-safe actual aggregation
primary-report evidence filtering
production/SOC/control non-interference
```

If any item is not implemented, keep PR #10 Draft and report it as incomplete.

## Stop condition

When the shadow implementation can collect causally valid, finalized, non-double-counted evidence with candidate definitions matching the frozen experiment, stop.

Do not implement production adaptive correction in PR #10.
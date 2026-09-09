# Dashboard regression investigation

## Scope

Investigation-only tracking document for the dashboard regressions reported on 2026-09-03 JST.

Reported symptoms:

- Historical actual results are missing from the dashboard.
- Forecast/history data are missing from the dashboard.
- Predicted/forecast SOC display that previously existed has disappeared.
- Previously working dashboard behavior appears to regress after unrelated changes.

## Investigation questions

1. What is the last-known-good commit/PR for each symptom?
2. What is the first-known-bad commit or smallest bounded regression range?
3. Is the underlying data missing, no longer generated, no longer published, rejected by schema/date filtering, or only not rendered?
4. For predicted SOC, is the regression in the producer, persisted schema, API/loading layer, or frontend rendering?
5. Did recent scheduler/control changes accidentally alter dashboard contracts or shared data ownership?
6. Are regressions caused primarily by implementation/architecture, missing regression coverage, merge sequencing, or task instructions that failed to preserve explicit behavioral invariants?

## Required evidence before fixing

- Last-known-good / first-known-bad evidence for each symptom.
- Relevant file/function/data-contract diffs with causal explanation.
- Determination whether historical data remain recoverable from persisted artifacts.
- Root-cause classification with evidence and confidence.
- Minimal restoration plan for history/forecast and predicted SOC.
- Non-regression plan: characterization tests, schema/contract tests, golden/fixture dashboard tests, and CI gates where appropriate.

## Constraints

- Investigation first; no production behavior changes in this phase.
- Do not manually trigger production scheduler runs merely for investigation.
- Preserve 23/03/07 scheduler ownership.
- Preserve 06:45/06:50/06:55 production fences.
- Do not delete or rewrite production historical data.
- Keep investigation and subsequent reviewed work on the same Draft PR unless there is a strong repository-management reason not to.

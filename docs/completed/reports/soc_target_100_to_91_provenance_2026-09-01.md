# SOC target 100% to 91% provenance investigation 2026-09-01

## Conclusion

```text
PRIMARY_CAUSE = PROVENANCE_BLOCKED
SECONDARY_CAUSE = DIFFERENT_DATE_VALUE
USER/DASHBOARD_VALUE = 100% / source=battery_daily_metrics.setting_soc_target_percent / date=2026-08-29
BASE_PLAN_TARGET = NOT_PROVEN
FINAL_PLAN_TARGET = NOT_PROVEN
03_RUNTIME_TARGET = 91%
03_STOP_SOC = 96%
OPTIMIZER_MODE = UNKNOWN_FOR_HISTORICAL_PLAN
ACTIVE_CONSTRAINTS = NOT_PROVEN_FOR_HISTORICAL_PLAN
SOURCE_CHANGE = NO
DEPLOYED = NOT_REQUIRED
```

The 03 runtime result is proven by the existing sanitized execution evidence: it consumed a 91.00% contract target, reached 96.00% from the realtime source, and stopped with `target_reached`. The 100% value is proven as a 2026-08-29 daily metric value, not as the 2026-09-01 plan's base or final target.

The historical `night_charge_plan.json` used by the 2026-09-01 03 execution was not found in tracked reports, local deployment/runtime artifacts, or the configured GCS archive. The current local plan was explicitly excluded because it is dated 2026-07-17. Firestore archive lookup did not complete within the read-only check window; no write was attempted. Therefore the causal 100% -> 91% transition cannot be proven, and no production source is changed.

## Provenance table

| stage | field | value | source | evidence grade |
|---|---|---:|---|---|
| user/dashboard | `setting_soc_target_percent` | 100 | `battery_daily_metrics`, date 2026-08-29, existing investigation evidence | PROVEN, DIFFERENT_DATE |
| base planner | `target_soc_7_percent_base` | NOT_PROVEN | historical plan unavailable | UNKNOWN |
| optimizer | final target | NOT_PROVEN | historical plan unavailable | UNKNOWN |
| plan | `result.target_soc_7_percent` | NOT_PROVEN | historical plan unavailable | UNKNOWN |
| runtime | contract target | 91 | existing sanitized Cloud Run execution evidence | PROVEN |
| runtime | stop SOC | 96 | existing sanitized Cloud Run execution evidence | PROVEN |

## Source contract verified

- `app/energy_plan/optimization.py` records the pre-optimizer value as `target_soc_7_percent_base` and replaces `target_soc_7_percent` with the optimizer result when optimization succeeds; the legacy fallback has the same base/final boundary.
- `app/energy_plan/result_builder.py` records `raw_target_soc_7_percent` and `final_target_soc_7_percent` in `decision_rationale`.
- `app/energy_plan/night_plan.py` validates `forecast.date` and reads `result.target_soc_7_percent`.
- `app/runtime/cloud_job.py` reads only `result.target_soc_7_percent` from `night_charge_plan.json`; the 03 runtime does not calculate 91% from the 100% dashboard value.
- `app/operations/firestore.py` writes `battery_daily_metrics.setting_soc_target_percent` from the extracted daily metric target. The dashboard schedule also binds this value to the matching metric date.

The current deployed 03 job template was inspected read-only and has `DAYTIME_SOC_COST_OPTIMIZATION_ENABLED=true` and `ADJUST03_REGENERATE_PLAN=true`. This is current template configuration, not proof of the historical 2026-09-01 plan output. The source defines `morning_pv_headroom_guard`, `daytime_net_surplus_headroom_guard`, and `historical_daytime_soc_gain_guard` caps, but their 2026-09-01 values and candidate comparison are not available.

## Historical artifact search

- Tracked reports: 100% evidence is dated 2026-08-29; the existing 03 follow-up proves only the 2026-09-01 runtime target and stop result.
- Local `artifacts/**/night_charge_plan.json`: 129 files scanned; none had forecast date or generation timestamp matching 2026-09-01.
- Current `artifacts/night_charge_plan.json`: excluded from historical evidence; `forecast.date=2026-07-17`, `base=100`, `final=0`.
- Configured GCS archive: read-only object existence check for the 2026-09-01 plan returned absent.
- Firestore archive: read-only lookup did not complete; no data was written.

## Behavior decision

Classification `PROVENANCE_BLOCKED` requires stopping before source modification. The available evidence does not distinguish optimizer-intended final target, an unintended cap, a plan/date mismatch, or an unenforced hard target. Fixing the optimizer, disabling caps, forcing 100%, changing 03 runtime behavior, or treating the current plan as historical would violate the active operating instruction.

## Tests / CI / deployment

- Source/tests were not changed, so source-change test and CI gates were not rerun for this report-only outcome.
- Production deployment: `NOT_REQUIRED`; no source behavior changed and no live mutation or 03 Job execution was performed.
- No Firestore or KP-NET write was performed.

## CodebaseMemory

- Transport: local CLI was used as requested; MCP was not used.
- Project: `C-VSC-SolerControler`.
- Final CLI index status: `ready`, 6070 nodes, 18495 edges, skipped 0, parse-partial 9.
- Shared artifact was generated once after the active instruction update; `schema_version=2`, indexed commit `d9feee9`.
- Excluded/parse-partial files are best-effort coverage limits; the relevant source files were verified directly with `rg` and focused reads.

## Secrets

```text
secrets exposed = NO
```

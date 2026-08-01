# Readable Code Audit Report

監査日: 2026-08-01

## Scope

対象は、テスト・ドキュメント・生成物・キャッシュ・`artifacts`・外部依存を除く Python / JavaScript / PowerShell / HTML / CSS / shell / SQL ソース107ファイルです。

判定基準は、指定された4記事と `readable-code-audit` Skill のチェックリストです。既存仕様・外部API・DB契約を確認できないものは、違反と断定せず「要確認」としました。

## Summary

| 区分 | 件数 | 対応 |
|---|---:|---|
| 長大または複数責務の関数 | 50 | 維持が妥当なものには skip コメントを追加。残りは分割候補 |
| 同一責務の重複実装 | 2グループ | DB差異を確認したうえで共通化候補 |
| `dict[str, Any]` 等の弱いデータ契約 | 9サブシステム | 外部境界は維持、内部境界は型付きモデル候補 |
| 汎用引数・公開契約の不明瞭さ | 3 | 外部契約か確認が必要 |
| YAGNI候補 | 0（断定なし） | 仕様確認なしに削除しない |
| 有効なスキップコメント | 20 | 理由を付記し、次回監査で再確認 |

## 1. Long or multi-responsibility functions

行数は関数定義から末尾までの概算です。長いこと自体を機械的な違反とはせず、責務を分離できるか確認しました。

| File and line | Function | Approx. lines | Rule | Decision |
|---|---|---:|---|---|
| `cloud_job_runner.py:1107` | `_monitor_partial_forced_and_stop` | 289 | STRUCT-01/04 | Keep for now: owns one complete monitor lifecycle; skip comment added |
| `app/dashboard_data.py:757` | `_load_sqlite_slice` | 240 | STRUCT-01/02/04 | Keep adapter boundary; skip comment added |
| `app/dashboard_data.py:1012` | `_load_postgres_slice` | 240 | STRUCT-01/02/04 | Keep adapter boundary; skip comment added |
| `app/forecast_correction.py:872` | `build_forecast_correction` | 235 | STRUCT-01/04 | Keep one diagnostic snapshot; skip comment added |
| `app/dashboard_data.py:1700` | `_load_firestore_slice` | 199 | STRUCT-01/02/04 | Keep document aggregation boundary; skip comment added |
| `energy_model_main.py:2451` | `_run_soc_optimization` | 221 | STRUCT-01/04 | Keep snapshot orchestration; skip comment added |
| `scripts/analyze_hourly_weather_vectors.py:463` | `main` | 214 | STRUCT-01/02/04 | Keep reproducible CLI pipeline; skip comment added |
| `app/kpnet_workflow.py:835` | `_build_dynamic_forced_profile` | 210 | STRUCT-01/04 | Keep atomic profile resolution; skip comment added |
| `app/pv_array_forecast.py:422` | `calibrate_performance_ratio_for` | 201 | STRUCT-01/04 | Keep auditable calibration result; skip comment added |
| `app/pv_physical_forecast.py:371` | `build_physical_pv_candidate` | 180 | STRUCT-01/04 | Keep candidate plus decision path; skip comment added |
| `app/forecast_correction.py:643` | `_evening_temperature_correction` | 178 | STRUCT-01/04 | Review next: split prior selection and correction calculation |
| `app/operations_db.py:343` | `ingest_sunshine_from_night_plan` | 167 | STRUCT-01/04 | Review next: separate parse, validation, and persistence |
| `app/operations_db.py:113` | `ensure_schema` | 160 | STRUCT-04 | Keep ordered migration; skip comment added |
| `energy_model_main.py:1641` | `_historical_daytime_soc_gain_guard` | 154 | STRUCT-01/04 | Review next: separate history loading and guard decision |
| `scripts/analyze_multi_day_weather_contribution.py:375` | `main` | 150 | STRUCT-01/02/04 | Keep reproducible CLI pipeline; skip comment added |
| `app/postgres_ops.py:254` | `ingest_sunshine_from_night_plan` | 144 | STRUCT-01/04 | Review next: separate parse, validation, and persistence |
| `app/postgres_ops.py:62` | `ensure_schema` | 144 | STRUCT-04 | Keep ordered migration; skip comment added |
| `app/dashboard_data.py:378` | `_build_latest_schedule_from_events` | 144 | STRUCT-01/04 | Review next: isolate candidate ranking |
| `app/soc_cost_optimizer.py:613` | `optimize_soc_by_expected_cost` | 142 | STRUCT-01/04 | Review next: separate scenario generation and selection |
| `app/kpnet_workflow.py:1691` | `_run_settings_phase` | 140 | STRUCT-01/04 | Review next: separate command generation and persistence |
| `app/firestore_ops.py:105` | `ingest_sunshine_from_night_plan` | 120 | STRUCT-01/04 | Review next: separate parse, validation, and persistence |
| `energy_model_main.py:2674` | `_build_energy_model_output` | 119 | STRUCT-01/04 | Review next: split output sections |
| `app/main.py:82` | `main` | 119 | STRUCT-01/02/04 | Review next: separate browser, download, and persistence paths |
| `app/sheets_export.py:810` | `run_export` | 115 | STRUCT-01/04 | Keep atomic export result; skip comment added |
| `app/soc_decision_feedback.py:415` | `build_soc_decision_prior` | 108 | STRUCT-01/04 | Keep one domain decision calculation; skip comment added |
| `app/comfort_load_forecast.py:170` | `predict_hourly_comfort_load` | 108 | STRUCT-01/04 | Review next: split feature preparation and model execution |
| `scripts/kpnet_soc_gap_report.py:253` | `build_report` | 104 | STRUCT-01/04 | Review next: split data loading and Markdown rendering |
| `app/soc_cost_optimizer.py:269` | `_build_forecast_scenarios` | 101 | STRUCT-01/04 | Review next: split bucket normalization and scenario creation |
| `app/energy_model.py:239` | `optimize_target_soc_for_daytime` | 100 | STRUCT-01/04 | Review next: isolate candidate scoring |
| `dashboard_server.py:243` | `do_GET` | 98 | STRUCT-01/02/04 | Review next: route dispatch and response serialization |
| `scripts/backtest_hourly_pv_weather_similarity.py:80` | `run` | 97 | STRUCT-01/04 | Keep replay pipeline; skip comment added |
| `energy_model_main.py:494` | `_soc_cost_model_from_env` | 97 | STRUCT-01/04 | Review next: parse environment by domain group |
| `app/kpnet_workflow.py:737` | `from_env` | 96 | STRUCT-01/04 | Review next: split environment parsing and validation |
| `app/soc_decision_feedback.py:207` | `build_soc_decision_feedback` | 95 | STRUCT-01/04 | Review next: separate feature extraction and result assembly |
| `app/dashboard_data.py:555` | `_build_dashboard_warnings` | 94 | STRUCT-01/04 | Review next: group warning rules by domain |
| `app/forced_charge/state_machine.py:201` | `decide_transition` | 90 | STRUCT-01/04 | Keep explicit state table; no skip needed yet |
| `db_pipeline_main.py:180` | `_ingest_postgres` | 94 | STRUCT-01/04 | Review next: isolate connection and row ingestion |
| `energy_model_main.py:2126` | `_prepare_night_charge` | 88 | STRUCT-01/04 | Review next: split forecast inputs and charge calculation |
| `db_pipeline_main.py:90` | `_ingest_sqlite` | 88 | STRUCT-01/04 | Review next: isolate connection and row ingestion |
| `cloud_job_runner.py:340` | `_persist_03_monitor_schedule_to_firestore` | 88 | STRUCT-01/04 | Review next: separate document construction and write |
| `app/kpnet_workflow.py:1047` | `_build_dynamic_green_profile` | 87 | STRUCT-01/04 | Review next: split rule evaluation and profile construction |
| `app/db_sync.py:286` | `_sqlite_upsert_row` | 87 | STRUCT-01/04 | Review next: separate normalization and SQL execution |
| `energy_model_main.py:988` | `_forecast_from_env_or_api` | 84 | STRUCT-01/04 | Review next: isolate source selection and normalization |
| `energy_model_main.py:1158` | `_archive_weather_history` | 83 | STRUCT-01/04 | Review next: separate archive selection and persistence |
| `scripts/archive_night_charge_plans.py:21` | `main` | 81 | STRUCT-01/04 | Review next: split dry-run and apply paths |
| `app/kpnet_workflow.py:1414` | `_build_payload` | 81 | STRUCT-01/04 | Review next: split field mapping and serialization |
| `app/config.py:71` | `from_env` | 79 | STRUCT-01/04 | Review next: group environment parsing by subsystem |

## 2. Duplicate responsibilities

### 2.1 Schema initialization

`app/operations_db.py:113`, `app/postgres_ops.py:62`, and `app/firestore_ops.py:42` each expose `ensure_schema`. The names are intentionally shared as a storage-backend interface, but the implementations are not interchangeable. Keep the public adapter name; consider a protocol or backend-specific migration module.

### 2.2 Sunshine ingestion

`app/operations_db.py:343`, `app/postgres_ops.py:254`, and `app/firestore_ops.py:105` each implement `ingest_sunshine_from_night_plan`. Parsing and domain validation appear conceptually shared, while persistence is backend-specific. Extract only the verified parse/validation portion; do not merge DB writes blindly.

## 3. Weak data contracts

The following areas use broad dictionaries extensively and should be reviewed under `NAME-02`, `STRUCT-05`, and `STRUCT-06`:

- `app/dashboard_data.py:29` onward: dashboard payloads and backend rows
- `app/energy_plan/models.py:9` onward: persisted plan sections
- `app/forecast_correction.py:27` onward: forecast and diagnostic payloads
- `app/soc_decision_feedback.py:37` onward: plan, actual, and feedback documents
- `app/drive_backup.py:171` onward: backup payloads
- `app/sheets_export.py:148` onward: sheet rows and export tables
- `scripts/analyze_hourly_weather_vectors.py:87` onward: weather API payloads
- `scripts/analyze_multi_day_weather_contribution.py:75` onward: monitoring/weather rows
- `scripts/backtest_hourly_pv_weather_similarity.py:80` onward: replay payloads

These are not all automatic violations. External JSON, Firestore, and spreadsheet boundaries may require flexible types. The recommended boundary is: parse flexible input once, then use typed domain objects internally.

## 4. Generic arguments and public contracts

- `app/operations/domain.py:140`: `tiered_increment_cost(**kwargs)` is intentionally a compatibility pass-through, and has a skip comment. Confirm that the wrapper is still needed.
- `app/kpnet_workflow.py:1175`: `_post(..., **kwargs)` is an HTTP adapter pass-through, and has a skip comment. Keep only provider/request options actually used.
- `app/energy_plan/ports.py:14`: port methods use broad row dictionaries. This is an interface boundary, but the row schema should be documented or replaced with a typed protocol.

## 5. Documentation and naming

- Public Python functions and JavaScript module functions are not uniformly documented. Add docstrings/JSDoc where behavior, units, side effects, or fallback rules are not obvious; do not add boilerplate to trivial getters.
- Broad names such as `data`, `result`, `value`, `out`, and `raw` occur throughout analysis and persistence code. Replace them when they cross a function boundary or represent a domain object; local short-lived variables may remain.
- No YAGNI violation was conclusively identified without product requirements. Do not remove environment options or fallback branches solely because current call sites are sparse.

## 6. Intentional exceptions

The added skip comments are local and reasoned. They are not blanket exclusions. Future audits must verify that each stated contract still exists and must report stale skip comments.

## Checks

- Skill validation: passed (`quick_validate.py`)
- Source inventory: 107 files
- Existing behavior tests: Python `394 passed, 1 skipped`; JavaScript `3 passed`
- `git diff --check`: passed
- No refactoring behavior changes were made in this audit.

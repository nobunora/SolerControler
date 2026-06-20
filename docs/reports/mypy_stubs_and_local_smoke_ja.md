# mypy stub追加とローカル実アクセスsmoke レポート

## 概要

`shared_utils_constants_refactor_ja.md` の次アクションに従い、外部stub追加を試行し、KPNet実アクセスを含むローカル動作確認を実施した。

## changed files

- `docs/reports/shared_utils_constants_refactor_ja.md`
- `docs/reports/mypy_stubs_and_local_smoke_ja.md`

## commands run

- `python -m pip install types-requests types-google-cloud-firestore scikit-learn-stubs`
- `python -m pip install types-requests scikit-learn-stubs`
- `python -m mypy app --strict`
- `python -m compileall app tests`
- `python -m pytest`
- `python scripts\security_check.py`
- `python .\kpnet_main.py` with `KP_WORKFLOW_MODE=csv`, `DRY_RUN=true`, `KP_CSV_TARGET_MONTHS=2026-06`
- `python .\energy_model_main.py` with `ENERGY_MODEL_CSV_DIR` pointing to downloaded KPNet CSV
- `python .\db_pipeline_main.py` with local SQLite smoke DB
- dashboard slice load smoke via `app.dashboard_data.load_dashboard_slice`

## test/build results

- `types-requests`: installed
- `scikit-learn-stubs`: installed
- `types-google-cloud-firestore`: unavailable on PyPI
- `python -m mypy app --strict`: 110 errors in 16 files
- `python -m compileall app tests`: OK
- `python -m pytest`: 116 passed
- `python scripts\security_check.py`: OK
  - 既存警告: `KP_USE_HAR_CREDENTIALS=true`

## KPNet smoke results

- Login: success
- Available months: `2026-06`, `2026-05`, `2026-04`
- Downloaded month: `2026-06`
- CSV path: `artifacts/local-smoke-kpnet/20260620-222658/csv/infoMeasureMulti30Min_EU_00HX25X02077_202606_20260620222700.csv`
- Plot generated: `artifacts/local-smoke-kpnet/20260620-222658/kpi_plot.png`
- Logout: success

## local pipeline smoke results

- `energy_model_main.py`: generated `artifacts/local-smoke-kpnet/20260620-222658/night_charge_plan.json`
- `db_pipeline_main.py`: completed with SQLite backend
- Smoke DB: `artifacts/local-smoke-kpnet/local_smoke.db`
- Table counts:
  - `monitoring_samples`: 956
  - `pipeline_runs`: 1
  - `sunshine_daily`: 2
  - `forecast_hourly`: 24
  - `model_parameters`: 18
  - `cost_daily`: 20
- Dashboard slice load: success

## known limitations

- 在宅予定シート読み込みはADCスコープ不足でスキップされた。任意入力のため計画生成は継続成功。
- settingsフェーズは安全のため実行していない。ダッシュボードの `settings_completion_unconfirmed` 警告はこのsmoke条件では想定内。
- `mypy app --strict` 全体は未達。stub追加後も既存型注釈不足と未typed外部ライブラリが残る。

## next recommended milestone

- Phase 4Aとして、`config.py`, `operations_db.py`, `kpnet_workflow.py` の順に対象ファイル単位でstrict mypyを通す。

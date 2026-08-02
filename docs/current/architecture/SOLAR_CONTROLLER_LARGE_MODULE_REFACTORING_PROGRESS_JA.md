# SolarController 巨大モジュール分割 進捗ログ

開始日: 2026-08-02
開始コミット: `f44ea06`

## R-0 基準・所有表

| 対象 | 分割先 | 主な責務 | 公開契約 | 固有検証 |
|---|---|---|---|---|
| `app/forecasting/correction.py` | `correction_history_io.py`, `correction_weather.py`, `correction_model.py` | 履歴I/O、気象I/O、純粋補正計算 | correction公開関数・dictキー | correction compatibility / energy model |
| `app/energy_plan/workflow.py` | 既存 `forecast_inputs.py`, `soc_constraints.py`, `optimization.py` と追加output境界 | plan入力、制約、最適化、output | `build_energy_plan`, `main` | Energy Plan回帰 |
| `app/kpnet/workflow.py` | `kpnet/rules.py`, `kpnet/csv_visualization.py` | 時間ルール、CSV可視化、profile payload | `run_kpnet_workflow`, `main` | KP-NET回帰 |
| `app/runtime/cloud_job.py` | 既存runtime modulesと追加slot境界 | slot orchestration、retry、terminal transition | `main`, slot順序 | Cloud Job回帰 |
| `app/forecasting/pv_array.py` | 既存 `pv_array_adapters.py`, `pv_array_calibration.py`, `pv_array_selection.py` | provider、calibration、候補選択 | PV公開関数・dictキー | PV array回帰 |
| `app/dashboard/data.py` | 既存repositoriesと追加 `aggregation.py` | 会計期間、集計、共通slice | dashboard loader | Dashboard回帰 |

### 基準測定

- `python -m pytest -q`: 前回基準 `438 passed, 1 skipped`
- `python -m mypy app scripts --no-incremental`: 前回基準 0エラー
- 実サービス接続: なし
- R-0開始ゲート: `git status --short` 空、`git log -1` は `f44ea06`

## 固定順記録

R-0完了後はR-1のみ開始する。各カードの開始コミット、変更ファイル、固有テスト、mypy、compileall、diff check、完了コミットをこのログへ追記する。

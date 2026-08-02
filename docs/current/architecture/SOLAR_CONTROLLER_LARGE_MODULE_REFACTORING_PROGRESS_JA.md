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

## R-0完了 / R-1完了

- R-0完了コミット: `59be414`
- R-1変更: correctionのforecast history I/O、weather取得、thermal state生成を `app/forecasting/correction_history_io.py` へ移動。`correction.py` は補正計算と公開workflowを保持し、既存patch境界はimportで維持した。
- R-1検証: `63 passed, 1 skipped`、forecasting mypy 0件、compileall、diff check成功。

## R-2完了

- R-2変更: 月次請求期間、対象日前のdaytime buy、残期間推計を `app/energy_plan/monthly_projection.py` へ移動。workflowには実行順と既存private名の薄い正規importだけを残した。
- R-2検証: Energy Plan指定回帰 `108 passed`、Energy Plan mypy 0件、compileall、diff check成功。

## R-3完了

- R-3変更: KP-NETのNightWindowContract、HH:MM解析、時間窓判定、timezone now取得を `app/kpnet/rules.py` へ機械的に移動。workflowの既存private名と例外契約は正規importで維持した。
- R-3検証: `59 passed, 1 skipped`、KP-NET mypy 0件、compileall、diff check成功。初回の簡略化抽出は12件失敗したため破棄し、元実装の契約を保った再抽出のみを採用した。

## R-4完了

- R-4変更: Cloud Jobのdelay時刻、cutoff秒数、HH:MM正規化、03時target date調整を `app/runtime/schedule.py` へ機械的に移動。slot実行順、retry、terminal transitionはcloud_job.pyに保持した。
- R-4検証: `71 passed`、runtime/forced_charge mypy 0件、compileall、diff check成功。

## R-5完了

- R-5変更: PV performance calibrationの履歴集計、weather class、archive weather読込、calibration本体を `app/forecasting/pv_array_calibration.py` の正規所有へ移動。provider I/Oは `pv_array_adapters.py`、候補選択は `pv_array_selection.py` に保持し、`pv_array.py` は公開workflowと互換再exportを担当する。
- R-5検証: `19 passed, 1 skipped`、forecasting mypy 0件、compileall、diff check成功。

## R-6完了

- R-6変更: Dashboardの会計期間、月次cost、PV/負荷の日次集計を `app/dashboard/aggregation.py` へ移動。`data.py` は共通slice組立て、backend選択、公開loader、互換再exportを保持した。
- R-6検証: Dashboard指定回帰 `46 passed`、Dashboard mypy 0件、compileall、diff check成功。

## R-7監査是正

- 全体ゲート初回で、R-1移動後の `correction.py` 互換再export 5件がmypyの明示export検査に失敗した。
- `correction.py` に公開互換名の `__all__` を追加し、全体mypy 0件、関連回帰 `63 passed, 1 skipped`、compileall、diff checkを確認した。
- 旧34互換モジュールへのimport検索は0件。移動対象の実装定義は各正規モジュール側にのみ存在することを確認した。

## 完了判定撤回

- 独立再監査で、R-1〜R-6の各カードに未移動責務が残ることを確認した。
- 指示書・進捗ログ・監査結果を `docs/current/architecture/` へ戻し、R-1から固定順で是正する。

## R-1再是正完了

- 温度特徴量、ridge regression、temperature correction、hourly multiplier、PV補正を `correction_model.py` へ移動した。
- correctionの永続dict構築を `correction_result.py` へ移動し、`correction.py` は入力取得、policy、実行順、公開wrapperを保持した。
- 既存のweather monkeypatch境界は明示callbackで維持した。
- 検証: `63 passed, 1 skipped`、forecasting mypy 0件、compileall、diff check成功。

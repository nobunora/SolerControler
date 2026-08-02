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

## R-2再是正完了

- PV不確実性、plan quality、decision説明、環境値境界を `plan_quality.py` へ移動した。
- hourly PV候補構築とphysical/correction選択を `pv_selection.py` へ移動した。
- 最終PlanDocumentとEnergyModelOutput組立てを `result_builder.py` へ移動し、workflowはport配線と実行順を保持した。
- optimizationの内部importを正規 `plan_quality` へ更新した。
- 検証: Energy Plan回帰 `108 passed`、Energy Plan mypy 0件、compileall、diff check成功。

## R-3再是正完了

- 時間ルールに続き、運用条件、SOC/充電推定、動的forced/green profile、payload組立てを `profile_builder.py` へ移動した。
- CSV月選択、CSV点解析、plot生成を `csv_visualization.py` へ移動した。
- KpNetConfig、KpNetClient、CSV/settings phase、workflow実行順は `workflow.py` に保持し、clientの内部importを正規profile builderへ更新した。
- 検証: `59 passed, 1 skipped`、KP-NET mypy 0件、compileall、diff check成功。

## R-4再是正完了

- command実行、secret mask、retry設定とretry loopを `command_adapter.py` へ移動した。
- 03時plan再生成、DB/settings配線、fail-safe standbyを `adjust03_plan.py` へ移動した。
- 23時、03時、07時slot配線を `slot_orchestration.py` へ移動した。
- monitor lifecycle、terminal transition、公開mainは `cloud_job.py` に保持し、既存monkeypatch境界は遅延callbackで維持した。
- 検証: `71 passed`、runtime/forced_charge mypy 0件、compileall、diff check成功。

## R-5再是正完了

- Open-Meteoのrequest/payload mappingとForecast.SolarのURL、series正規化、provider結果組立てを `pv_array_adapters.py` へ移動した。
- 時間帯別ensemble値、hourly map、最終ensemble組立てを `pv_array_selection.py` へ移動した。
- `pv_array.py` は公開workflow、配列設定、Open-Meteo物理発電量計算、calibration/provider配線に限定し、公開互換名は明示importで維持した。
- 検証: `19 passed, 1 skipped`、forecasting mypy 0件、compileall、diff check成功。

## R-6再是正完了

- backend非依存のempty response、meta、warning、snapshot-to-slice組立てを `slice_assembler.py` へ移動した。
- SQLite query snapshotからsliceを構築するloaderとrepository実装を `sqlite_repository.py` へ移動した。
- PostgreSQL/Firestoreの互換repository objectを `backend_repositories.py` へ移し、各provider moduleの共通assembler importを正規化した。
- `data.py` はbackend選択、cache、Firestore互換portと公開loaderを保持し、既存monkeypatch境界は薄い互換関数で維持した。
- 検証: Dashboard指定回帰 `46 passed`、Dashboard mypy 0件、compileall、diff check成功。

## R-7独立監査によるR-3追加是正

- 旧 `workflow.py` に残っていたCSRF/HTML解析、HAR資格情報解析、URL検証、download filename正規化を未移動責務として検出した。
- これらを `client_support.py` へ移し、`client.py` は正規support moduleを直接参照、`workflow.py` は公開互換importだけを保持する形へ修正した。
- 検証: KP-NET指定回帰 `59 passed, 1 skipped`、KP-NET mypy 0件、compileall、diff check成功。

## R-7完了

- 初回全体ゲートでDashboard repository互換名3件の明示export不足を検出し、`data.py.__all__` を是正した。
- その後の独立構造監査でR-3のHTML/auth helper残存を検出・是正し、R-7を最初から再実行した。
- `pre_release_local.ps1 -SkipInstall` 成功、全回帰 `438 passed, 1 skipped`、全体mypy 0件（156 source files）、security check、compileall、diff check成功。
- R-0所有表と旧モジュールAST定義・呼出し経路の照合で、未移動実装と実装二重保持は0件。完了判定を確定した。

## 完了判定再撤回

- completed移送後の独立監査で、R-1の気象I/O未分離、R-2の未使用重複、R-3/R-6の単独import失敗、R-6のrepository重複を確認した。
- 文書をcurrentへ戻し、R-1、R-2、R-3、R-6を是正してR-7を再実行する。

## R-1追加是正完了

- Open-Meteo境界を `correction_weather.py` へ分離し、履歴I/Oから完全に除去した。
- thermal stateとcorrection snapshot計算を `correction_model.py` へ移し、`correction.py` は公開policy、orchestration、既存monkeypatch互換portのみを保持した。
- 検証: `63 passed, 1 skipped`、forecasting mypy 0件、compileall、diff check成功。

## R-2追加是正完了

- 月次投影で既に正規所有先となった `monthly_projection.py` と重複し、呼出しもないHH:MM/window helperを `workflow.py` から削除した。
- 検証: Energy Plan回帰 `111 passed`、Energy Plan mypy 0件、compileall、diff check成功。

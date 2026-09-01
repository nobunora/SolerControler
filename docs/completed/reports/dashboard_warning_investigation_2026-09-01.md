# ダッシュボード警告調査報告 2026-09-01

## Pre-Work Check

- Change purpose: ダッシュボードに表示された「目標SOC未達」「設定完了未確認」の状況と根拠を確認する。
- Change target: Firestoreをバックエンドとするダッシュボードの警告生成・表示経路、対象日の実績と設定実行記録。
- Scope not changed: 本番コード、Firestore、Scheduler、Cloud Run Job、設定値、デプロイ状態は変更していない。
- Existing code or references reviewed: [warnings.py](../../../app/dashboard/warnings.py:17)、[schedule.py](../../../app/dashboard/schedule.py:123)、[firestore_repository.py](../../../app/dashboard/firestore_repository.py:483)、[firestore.py](../../../app/operations/firestore.py:423)、[dashboard.js](../../../static/dashboard.js:398)、[test_dashboard_data.py](../../../tests/test_dashboard_data.py:1071)。
- Relationship to existing design: ダッシュボードは保存済みデータを読み取り、`build_dashboard_warnings()` の判定結果を表示する設計であり、画面から制御は行わない。
- Unknowns: 2026-08-31の03 Cloud Run Jobは成功完了しているが、なぜ設定イベントがFirestoreへ残っていないかは、Cloud Runアプリケーションログと書込み経路の追加調査が必要。
- Human confirmation needed: 今回はなし。未確認の原因を解消する修正は別作業とする。

## Final Report

### 結論

1. 「目標SOC未達」は、現行の判定入力（2026-08-29、目標100%、PV充電終了時SOC56%）に一致して表示されている。ただし同日の実績CSVでは07:00〜14:30にSOC100%へ到達しており、「一度も目標へ到達していない」という意味ではない。現行値は「最後のPV発電かつ充電ありサンプル時点」のSOCである。
2. 「設定完了未確認」は、表示終了日2026-08-31に対する設定完了イベントを確認できないため表示されている。2026-08-30/31の計画文書・実行文書は存在せず、設定イベントの最新は2026-08-28 21:28:55Zである。
3. Schedulerは無効ではない。03 Schedulerは`ENABLED`で、2026-08-31 18:00:01Z（JST 2026-09-01 03:00）の最終試行があり、対応するCloud Run Job実行は2026-08-31 21:10:01Zに`Completed successfully`となっている。したがって、現在確認できる事実は「Scheduler停止」ではなく、「Job成功完了後も設定イベントが表示用Firestoreへ確認できない」状態である。

### 表示経路と判定条件

- Firestoreの全体データ範囲は2026-04-17〜2026-08-31、ダッシュボードの最新表示終了日は2026-08-31。
- [load_firestore_slice()](../../../app/dashboard/firestore_repository.py:483) が `battery_daily_metrics`、`settings_events`、`monitoring_samples` 等を読み取る。
- [build_dashboard_warnings()](../../../app/dashboard/warnings.py:17) は、最新battery行について `pv_charge_end_soc_percent + 5.0 < setting_soc_target_percent` の場合に `soc_target_unreached` を追加する。
- 設定完了は `applied`、`skipped-no-change`、`skipped-no-charge` のいずれかであることが必要で、該当しなければ `settings_completion_unconfirmed` を追加する。[SETTINGS_COMPLETED_STATUSES](../../../app/dashboard/schedule.py:14)
- [dashboard.js](../../../static/dashboard.js:398) はAPIの警告配列を最大6件描画するだけで、警告内容を独自に再判定していない。

### 警告1: 目標SOC未達

Firestoreの最新 `battery_daily_metrics` は次の値だった。

| 項目 | 値 |
|---|---:|
| 対象日 | 2026-08-29 |
| 目標SOC | 100.0% |
| `pv_charge_end_soc_percent` | 56.0% |
| `pv_charge_end_at` | 2026-08-29 16:30 |
| 判定差 | 44.0ポイント |
| `source_status` | applied |
| `plan_quality_status` | normal |

このため、警告条件は明確に成立する。

一方、8/29のKP-NET実績CSVを公式スクリプトで再集計すると、次の事実も確認できた。

- 夜間SOCは0.0%から06:30の100.0%まで上昇。
- 日中SOCは07:00〜14:30に100.0%を維持。
- 15:00以降に91%→80%→70%→56%と低下し、19:00には0%。
- 日中PV実績は2.174kWh、最終PV予測は5.286kWh（差分 -3.112kWh）。
- 日中買電は実績14.392kWh、計画時期待値8.344kWh。

[recalc_battery_pv_charge_end_soc()](../../../app/operations/firestore.py:423) は、PV量と充電量がともに正のサンプルのうち、日ごとの最後の時刻を `pv_charge_end_soc_percent` として保存する。したがって56%は16:30の実測値として正しいが、日中最大SOC100%とは異なる指標である。現時点では、画面表示の実装不具合とは断定できず、警告タイトルの意味と指標の意味にずれがあり得る、という調査結果である。

### 警告2: 設定完了未確認

ダッシュボードの入力状態は次のとおりだった。

| 項目 | 値 |
|---|---|
| 表示終了日 / schedule plan date | 2026-08-31 |
| `latest_schedule.status` | fallback-default |
| `schedule_source` | 未設定 |
| `settings_completed` | false |
| 2026-08-30/31の `night_charge_plans` | 文書なし |
| 2026-08-30/31の `night_soc_execution` | 文書なし |
| `settings_events` 最新 | 2026-08-28 21:28:55Z、03、standby-mode、applied |

`_schedule_event_candidates()` は、表示対象の `plan_date` とイベント詳細の `plan_date` が一致しないイベント、または詳細の `plan_date` が空のイベントを候補から除外する。[schedule.py](../../../app/dashboard/schedule.py:123) のこの条件により、8/28の最後のstandby適用イベント（詳細の`plan_date`なし）は8/31表示の完了根拠にはならず、scheduleはfallback-defaultになる。その結果、設定完了警告が出る。

Firestoreの `pipeline_runs` では、03スロットの最後の記録は2026-08-28 21:31:41Z（CSV行数2844）で、その後はmanual-csvのデータ取込記録が8/31まで続いていた。これはCSV実績の更新と03設定イベントの更新が別経路であることを示す。

### Scheduler / Cloud Run確認

- 23/03/07 Schedulerはいずれも `ENABLED`。
- 03 Scheduler: cron `0 3 * * *`、timezone `Asia/Tokyo`。
- 03 Scheduler最終試行: 2026-08-31T18:00:01.658827Z。
- 03 Scheduler次回予定: 2026-09-01T18:00:00.609213Z。
- 03 Cloud Run最新実行開始: 2026-08-31T18:00:01.778491Z。
- 03 Cloud Run最新実行終了: 2026-08-31T21:10:01.073904Z。
- 最新実行の状態: `Completed=True`、`ResourcesAvailable=True`、`Started=True`、`ContainerReady=True`、`succeededCount=1`、`Execution completed successfully`。

この確認により、設定完了未確認の直接原因をScheduler無効やCloud Run実行失敗と断定する根拠はない。アプリケーション内の設定処理結果、Firestore書込み条件、またはイベント記録の別経路を追加確認する必要がある。

### Design intent / Alignment

- 変更は調査報告と証跡の保管だけで、実装・設定・本番データは変更していない。
- 警告判定、表示、Firestore集計の現行契約をそのまま検証した。
- 共有CodebaseMemoryは現行HEADへ更新し、`status=ready`、5936 nodes、18328 edgesを確認した。共有artifactは[artifact.json](../../../.codebase-memory/artifact.json:1)に現行HEADを記録している。parse_partial 9件、skipped 0件であり、対象のdashboard/sourceファイルに記録上のparse欠落はない（Coverageはbest-effort）。

### Files changed

- `docs/completed/reports/dashboard_warning_investigation_2026-09-01.md`
- `docs/completed/reports/dashboard_warning_investigation_2026-09-01_evidence.md`
- `.codebase-memory/artifact.json`
- `.codebase-memory/graph.db.zst`

### Scope not changed

- `app/`、`static/`、`templates/`、`tests/`のソースは変更していない。
- Firestoreへの書込み、KP-NET設定、Scheduler起動、Cloud Run Job起動、デプロイは行っていない。

### Tests and quality checks

- `python -m ruff check .`: PASS。
- `python -m mypy app scripts --show-error-codes`: PASS（174 source files）。
- `python -m pytest -q tests/test_dashboard_data.py`: PASS（40 passed）。
- `node tests/test_dashboard_calculations.js`: PASS。
- `node tests/test_dashboard_modules.js`: PASS。
- `node tests/test_dashboard_bootstrap.js`: PASS。
- Import Linter: 実行不可。`lint-imports`未登録、`python -m importlinter`はmodule entrypoint非対応。
- deptry / ty: 未導入。
- Oxlint / tsc: `package.json` / `tsconfig.json`がないためプロジェクト設定として未実行。

### Remaining risks

- 8/29のSOC警告は、目標到達後の夕方SOC低下を最後のPV+充電サンプルで評価している。指標の意味を変更する場合は、別途仕様確認と回帰テストが必要。
- 8/31の03 Jobは成功しているため、設定完了イベント欠落の原因は本報告だけでは確定していない。
- Cloud Runアプリケーションログの詳細なイベント単位確認、Jobが参照したplanの存在、Firestore書込み応答の相関付けは未実施。

## If Behavior Changed

該当なし。動作変更は行っていない。

## Tracked Report Hygiene

生の端末ログ、認証情報、project/account identifier、run ID、secret名は追跡対象に含めず、再現に必要なコマンドとサニタイズ済みの時刻・状態・数値だけを保存した。詳細な採取項目は[証跡ファイル](dashboard_warning_investigation_2026-09-01_evidence.md)に記載する。

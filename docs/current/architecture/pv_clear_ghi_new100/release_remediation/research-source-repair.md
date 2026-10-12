# 過去実測修復と派生データ連鎖の調査

2026-10-12。対象HEAD `1dd8786`。調査・実装仕様のみ。本番操作、source変更、pytest、Cloud実行は行っていない。
ローカル報告のPASSと、本番適用・実SDK競合・料金・校正・asset・DriveのPASSを区別する。

## 読取経路と今回の境界

KP-NET CSV → production CSV reader → `prepare_monitoring_csvs` → `sync_monitoring_csvs` → `monitoring_samples` → 日計/夜間/料金 → 校正の実測入力 → producer snapshot → bootstrap/history asset → browser。
Driveは別の明示backup実行でFirestoreを読み、校正GCS current/originalsとは別系統である。
GET、料金式、SOC/23/03/07制御、過去forecast原本、collection/schema/env/dependencyは変更対象にしない。
実装対象は[R02-source-repair](steps/R02-source-repair.md)と[R03-derived-history](steps/R03-derived-history.md)。通常producerを修復の入口に流用して過去forecastを再生成しない。

## 事実と証拠

| ID | 状態 | 確認事項・根拠 |
| --- | --- | --- |
| SR01 | 確定・ローカル報告 | 9/20は35→48半時間区間、17→24実測時間。13件は17:30〜23:30。load 20.203→32.002、buy 18.766→30.530、day_buy 2.617→11.945 kWh。PV 2.834は不変。[report.md (line 28)](../../../../../artifacts/analysis/production-repair-20261012/report.md) |
| SR02 | 確定・証拠限界 | 全体raw追加33件は9/20の13件＋10/12の20件。179日×6fieldsの1,074照合、raw retry write0。SDK writerへCloud clientを渡していない。[local_repair_replay.main (line 115)](../../../../../artifacts/analysis/production-repair-20261012/local_repair_replay.py)、[report (line 44)](../../../../../artifacts/analysis/production-repair-20261012/report.md) |
| SR03 | 確定・未再計算 | ローカル修復はmonitoring/daily/batteryの3系統のみ。料金・forecast・coefficients等6テーブルは不変。料金正確性の実証ではない。[persist_working (line 105)](../../../../../artifacts/analysis/production-repair-20261012/local_repair_replay.py)、[report残件 (line 61)](../../../../../artifacts/analysis/production-repair-20261012/report.md) |
| SR04 | 確定 | import wrapperのparamはSkipFirestoreIngestのみ。既定は当月周辺。`KP_DOWNLOAD_LATEST_MONTH=true`でも明示`KP_CSV_TARGET_MONTHS`は優先される。[wrapper (line 1)](../../../../../scripts/run_kpnet_import_from_env.ps1)、[_resolve_months (line 14)](../../../../../app/kpnet/csv_visualization.py)、[config (line 60)](../../../../../app/kpnet/config.py) |
| SR05 | 確定 | 既存.envに対象月＋`DATA_MONITORING_FULL_BACKFILL=true`が設定されれば9月取込は可能。通常の4日windowでは9月rowは除外される。Import-ProductionEnvは同名Process envを上書きするため、呼出側で先に環境値を設定するだけでは保証できない。[sync (line 126)](../../../../../app/operations/firestore.py)、[Import-ProductionEnv (line 3)](../../../../../scripts/production_env.ps1) |
| SR06 | 確定 | changed_count>0では料金を最古変更日の暦月1日から取込window末端まで再生成。変更なしretryと同じrunの早期returnではcost再生成なし。[workflow (line 477)](../../../../../app/operations/workflow.py)、[earliest_changed_month_start (line 254)](../../../../../app/operations/monitoring_sync.py) |
| SR07 | 確定 | 料金式は暦月ごとにday buy累積とcounterfactual累積を更新。9.328 kWh day buy訂正は9/20だけでなく9/21〜9/30のtier位置に影響し得る。全体cumulativeは10月以後にも伝わる。金額は未計算。[calculate_daily_costs (line 45)](../../../../../app/operations/cost_daily.py)、[recalc_cost_daily_from (line 441)](../../../../../app/operations/firestore.py) |
| SR08 | 確定 | 料金は半時間ごとのmax(0,load-buy)から計算する。差分11.799−11.764だけで節約量・円を算出してはならない。既存料金式のmissing→0扱いは今回変更しない。不完全入力はpreflightで識別する。[cost_daily (line 45)](../../../../../app/operations/cost_daily.py) |
| SR09 | 確定 | 日計の23時以後は翌日のreview_night_chargeへ入る。9/20訂正時は9/21も再集計対象。safe結果のsemantic差分は9/20と10/12のみで、9/21変更は報告されていない。経路省略可という意味ではない。[recalc_monitoring_daily_metrics (line 738)](../../../../../app/operations/firestore.py)、[safe結果 (line 65)](../../../../../artifacts/analysis/production-repair-20261012/repair-results.safe.json) |
| SR10 | 確定 | manual-csvの_refresh_dashboard_snapshotsはhistory update/full rebuildを要求しない。9/20が31日window内でも、歴史全体の日計訂正を反映した証拠にはならない。[refresh (line 94)](../../../../../app/operations/workflow.py) |
| SR11 | 確定・source障害条件 | native revisionのfull history scopeは全monitoring期間を単一limit3661 queryで読む。recordは>3660を拒否。raw8,493/8,526件が同scopeに含まれればfull rebuildは公開前に失敗する。[epoch (line 548)](../../../../../app/dashboard/snapshots.py)、[monitoring query (line 637)](../../../../../app/dashboard/snapshots.py) |
| SR12 | 確定・未実測 | retained旧訂正の検出、legacy historyのfull rebuild、source drift拒否、GCS CAS、8KiBは存在する。179日実DB全source＋full rebuildで期限内PASSした証拠は未取得。[write (line 384)](../../../../../app/dashboard/snapshots.py)、[_upload_artifact (line 321)](../../../../../app/dashboard/snapshots.py) |
| SR13 | 確定 | 校正state有時はcontext.billing_rows/rows、state無しだけbootstrap全履歴。billing_rowsは現在と直前請求期間＋必要な旧pageを読む。9/20が取得範囲内なら通常更新でも再検証可能。[calibration updater (line 81)](../../../../../app/energy_plan/clear_ghi_update.py)、[load_monitoring_history (line 62)](../../../../../app/energy_plan/monitoring_history.py) |
| SR14 | 確定・条件付き | NEW100実測学習窓は05〜17時の26半時間。今回の13件でその窓に入るのは17:30だけ。PV日合計不変でもcomplete判定/actual_source hashが変わり得る。晴天認証可否・現在pack収録有無は未調査。[observed_day_records (line 87)](../../../../../app/forecasting/clear_ghi_observations.py) |
| SR15 | 確定 | 列挙された過去日なら訂正missingで旧晴天日を除外し、認証成功ならhash/availabilityを更新する。入力に全く現れない過去日はpreviousに残る。長期に範囲外になった訂正を確実に再読する入口は無い。[observed_day_records (line 103)](../../../../../app/forecasting/clear_ghi_observations.py) |
| SR16 | 確定 | ClearGhiStoreはimmutable actual/certification/calibration/stateを保存してからcurrentをCAS更新。control pointerとは独立。旧forecast/採用planを更新してはならない。[publish (line 235)](../../../../../app/forecasting/clear_ghi_store.py) |
| SR17 | 確定 | Drive snapshotはTABLE_SPECS＋PLAN_BACKUP_COLLECTIONS。原本planの埋込はあるがclear_ghi GCS current/originals・dashboard GCS assetは含まれない。[build_firestore_snapshot (line 179)](../../../../../app/backup/drive.py)、[PLAN_BACKUP_COLLECTIONS (line 63)](../../../../../app/backup/drive.py) |
| SR18 | 確定・未実行 | Drive wrapperは一時Job作成/実行/削除、device readbackも有効。本taskのread-only調査では実行不可。backup成功とJob cleanup成功は別判定。[wrapper (line 63)](../../../../../scripts/run_drive_backup_cloud_from_env.ps1) |

## 必要な連鎖と保持対象

1. CSV manifest/hash/対象日/既存8fields一致/48slotsを確認し、9/20の承認対象13件を挿入。10/12の20件は別scopeに分離する。
2. 9/20の日計・PV charge endと9/21前夜窓を保存rawから再生成。raw元値・settings由来target・forecast原本は変えない。
3. costはraw変更件数ではなく修復stateの独立stageとして9/1〜固定endを再生成。8/31以前baselineが妥当か先に確認し、不明なら必要範囲を再承認する。
4. 現行/直前請求期間のplanner入力でday buy訂正が届くことを読取で確認。料金暦月とSOC請求締日は別契約。式や締日を揃える変更は今回しない。
5. 校正は9/20の26samplesと衛星観測を明示再検証。未晴天なら採用しない。旧forecastは当時の原本を保持し、将来runだけが更新packを使う。
6. source revision取得の大規模欠落を解消した後、既存full_history_rebuildでbootstrap/historyを更新。GET DB0・ETag・31日初期・history遅延読取は保持。
7. 事前/事後のDrive data generationと、校正current/immutable stateの別復旧証拠を対応付ける。Driveに校正が自動保存されたと説明しない。

## 未調査・未検証

- 本番raw13件の現在不存在、最新native update_time、修復時刻の競合producer、各collection全件数、full rebuild SDK request/time/read bytesは未検証。
- 料金実単価・実締日・8/31baseline・訂正後円/各日tier差分は未取得。既存.env値をそのまま使う設計であり、数値を発明しない。
- 9/20晴天認証、既存currentの収録日/pack hash、過去訂正以後の学習再fit結果、current generationは未取得。
- SDK transaction abort/commit unknown、Firestore/GCS server競合、Drive quota/実upload/restoreはこの調査では実行していない。
- SQLite source-v2はvalidation projection。未知Firestore fields/native typesを含む災害復旧preimageではない。
- 修復のraw→派生→校正→asset→Driveを横断する原子的transactionは存在しない。stage receiptで部分成功を明示し、古いassetを成功へ書き換えない。

## 保護・独立チェック

保護正本[PROTECTED_HISTORICAL_FAILURE_REGIONS_JA](../../../agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md)を読んだ。
`541cd60`のprevious complete billing period、`93e8c3c`/`7899423`の保存forecast原本/共通表示をgit showで確認した。
変更候補は非制御の修復入口、明示旧日校正再検証、producer-only raw revision paginationに限定する。
historical SOC監視・料金式・TABLE_SPECS/同期provenance・_validate_publication_payload/CAS/epoch比較の本体は変更しない。
親CBM: ready8833/31107、対象definitionとwrite_dashboard_snapshotsの1hopを使用。graphはroutingのみ、結論は上記source/artifact根拠。
今回test実行0。過去ローカルPASSを本番・実SDK・年間校正のPASSに昇格していない。R02/R03を独立reviewへ渡す。

# D06 元データ保存側の修復

## 入口・参照範囲

ユーザーの訂正と[contracts C09](../contracts.md)を正本とする。CSV同期→監視原本→日計保存→既存snapshot→表示の順。

## 変更対象

app/operations/monitoring_sync.py::MonitoringChangeSetにsource_calendar_datesとmaterialization_dates/materialization_dashboard_datesを追加。classify_monitoring_rowsが実CSV供給日を記録する。
app/operations/firestore.pyに_monitoring_energy/_add_monitoring_energyを追加し、recalc_dashboard_daily_metrics/recalc_monitoring_daily_metricsの欠測0化だけを修正する。
app/operations/workflow.py::_ingest_firestoreのdaily再生成条件をchanged_countから供給日有無へ変更する。
tests/test_monitoring_source_repair.pyでproduction CSV readerから保存日計まで確認する。

## 変更禁止

raw CSV/実績値の補間、予測での代入、実機設定、料金・資産・最適化式、forecast原本、CAS、backup、DBschema、新env/flagを変更しない。
cost再生成のchanged_count条件は保持。原値不変の監視documentを再書込しない。dashboardへの新補完経路を作らない。

## クラス・変数差分

source_calendar_datesは読み込んだCSVの対象window内の日付集合（tuple）。既存changed calendar_datesは意味を維持する。
materialization_dashboard_datesは当日と、23時の夜間窓が属する翌日を含む。制御時刻の意味を変えない。
日計energy accumulatorはfloat|None。Noneはstickyな欠測で、有効0と区別する。

## 実装手順・分岐

1. 既存CSV parser/syncで原本のinsert/update/unchangedを識別し、raw書込は変更行だけ維持する。
2. source供給日を記録して日計の再生成対象にする。unchangedでも日計欠落・誤集計を修復する。
3. 既存の有限対象日範囲から保存rawを読み、欠測/非有限/負値/boolをNoneとして保存日計を再生成する。
4. 夜間23〜07窓、PV充電終了SOC、sample_count/first/latest、全数batch境界は保持する。
5. 保存元欠測は構造化reasonを残す。source値がなければ補間して埋めない。
6. 修復後の保存日計を既存producerで画面assetへ反映。古いhistoryは既存23時full rebuildで更新する。

## テスト・PASS条件

実CSV→同期→保存日計、同入力unchanged時の日計修復とraw再書込0、有効0、None/NaN/inf/負値/bool、欠測fieldへの後日訂正、対象window/full backfillを検証する。
夜間窓/朝SOC/実績sample countと既存cost/制御非変更の回帰も実行する。
PASSは修復結果が元データと一致し、dashboard新補完0、原値捏造0、予測原本/料金/backupの変更0。

## 中断条件

保存CSV/原本自体に値がない、外部機器の過去値を新しく推測する必要がある場合は復元不能を明記する。許可された本番操作以外は実行しない。

## 出口・引継ぎ

D05へsource修復と忠実表示を別の証拠として渡す。本番の過去データ修復・履歴再生成の実施状態はローカルテストと分離する。

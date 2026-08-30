# 2026-08-31 SOCギャップ ログ要約

## 目的と取り扱い

これは2026-08-31朝のSOCギャップの原因推定用に、読み取り専用で確認した03時Cloud Run実行と関連データの非機密要約を残すものである。実行ID、プロジェクトID、リージョン、機器ID、認証情報、Firestore生ドキュメント、無制限のログ転記は含めない。

## 時刻・状態の要約

|時刻（UTC）|時刻（JST）|状態/要約|
|---|---|---|
|2026-08-30 18:00:01|2026-08-31 03:00:01|03時実行を作成|
|2026-08-30 18:00:17|2026-08-31 03:00:17|実行開始|
|実行中|03:00頃〜05:38頃|CSVダウンロード、ローカル計画成果物生成、約3分周期のKP-NETログイン/ログアウトを確認|
|2026-08-30 20:38:54|2026-08-31 05:38:54|実行完了。成功数1|

## 実行健全性

- 条件: `Completed=True`、`ResourcesAvailable=True`、`Started=True`、`ContainerReady=True`。
- コンテナdigestは存在した。
- 実行に紐づくログは127件で、WARNING/ERROR severityは0件だった。
- フィルタ済みtext payloadに直接制御キーワードのメッセージはなかった。
- text payloadがないことは、制御がなかったこと、または停止理由がないことの証明ではない。

## 関連する読み取り専用データ状態

- 07:46 JSTの公式KP-NET取込は `pwsh -NoProfile -File scripts/run_kpnet_import_from_env.ps1 -SkipFirestoreIngest` で成功。ローカル成果物は `artifacts/20260831-074620`。
- 2026-08-31の30分CSVは00:00〜07:00の全15行に実測値を記録した。05:30の充電0.486 kWhの後、06:00と06:30は0 kWh、SOCは93%で横ばい。07:00はSOC 86%、放電0.698 kWh。
- Firestoreの`night_charge_plans`、`night_soc_execution`、`battery_daily_metrics`は2026-08-29までを確認。2026-08-30の計画はない。
- 現行03経路はローカル計画を再生成し、Firestore fallback/persistenceを持たないため、当日Firestore文書がないことだけでは失敗とは判定しない。

## 解釈上の制限

- 実行成功は目標SOC到達を証明しない。
- 30分CSVは停止の正確な分単位や停止分岐を示さない。
- 今回の要約は原因推定に必要な範囲に限定し、制御・設定変更、再実行、実機操作は行っていない。

関連する派生時系列は [soc_gap_evidence_2026-08-31.csv](./soc_gap_evidence_2026-08-31.csv)、総合判断は [soc_gap_investigation_2026-08-31.md](./soc_gap_investigation_2026-08-31.md) を参照。

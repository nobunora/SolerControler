# 03時 SOC 厳密到達停止の是正・デプロイ報告（2026-08-31）

## 結論

朝 7 時に SOC が設定値へ到達していなかった事象について、当時の停止理由と監視時 SOC を後追いで確定できる保存証跡は確認できなかった。したがって原因区分は `HISTORICAL_STOP_REASON_NOT_PROVABLE` とする。

一方、03 時監視の停止判定には legacy の停止マージンが適用されており、設定値未達でも停止し得る実装であった。この経路を、監視 SOC が計画 target 以上のときだけ停止する厳密判定へ是正し、本番反映と検証を完了した。

## 事前確認

- 変更目的: 03 時監視が target 未達で待機へ移行しないことを保証する。
- 変更対象: [app/runtime/cloud_job.py](../../../app/runtime/cloud_job.py) の 03 時部分充電監視と、[tests/test_cloud_job_runner.py](../../../tests/test_cloud_job_runner.py) の回帰試験。
- 変更しない範囲: 23/03/07 の責務、06:45/06:50/06:55 の時刻フェンス、SOC 未取得時の安全側待機、強制再適用・リース・永続化・Firestore/DB の扱い。
- CodebaseMemory: `CBM_TRANSPORT_BLOCKED_USER_AUTHORIZED_SOURCE_FALLBACK`。MCP transport が利用不能のため、利用者承認済みの source fallback（対象シンボルの `rg` 検索と限定読取）で確認した。共有グラフの更新・編集は行っていない。
- 不明点: 2026-08-31 の実行について、target、監視 SOC、停止理由を記録したログは後追いで取得できなかった。

## 調査データと原因区分

Luna 高による読み取り専用調査の確定値は次のとおり。

| 項目 | 結果 |
| --- | --- |
| `STOP_MARGIN_PRODUCTION_VALUE` | `1.0` |
| `2026-08-31_EFFECTIVE_TARGET` | `NOT_RETROACTIVELY_PROVEN` |
| `2026-08-31_MONITOR_SOC_AT_STOP` | `NOT_RETROACTIVELY_PROVEN` |
| `CAUSE_CLASS` | `HISTORICAL_STOP_REASON_NOT_PROVABLE` |

既存の SOC ギャップ調査では、該当実行の終了時刻は 05:38:54 JST、05:30 から 06:30 の SOC は 93% と記録されている。03 時 Job の設定値は停止マージン 1.0%、監視間隔 180 秒、確認開始 5 分前、plan 再生成有効、タイムアウト 14,100 秒であった。ただし、これらは当時の停止原因を単独で立証するものではない。

## 実装内容

- 停止判定を `observed SOC >= plan target` に変更した。`stop_soc_margin_percent` は設定・環境変数・既定値の互換性のため残し、観測ログのみに使用する。
- plan 読込後に、target、設定マージン、適用マージン 0.00% を出力する契約ログを追加した。
- 初回読取と各監視ループで、SOC 値・source・observed_at・target・action を flush 付きで出力するログを追加した。
- target 到達、SOC 未取得、監視打切りの各停止理由をログへ追加した。
- `HISTORICAL_FAILURE_LOCK` の 03 時単独制御契約を変更していない。強制再適用、リース、永続化、Firestore/DB、手動引継ぎの追加はない。

## 回帰試験と品質確認

- `test_03_target_100_does_not_stop_at_93_or_99_even_with_legacy_margin`
- `test_03_target_80_does_not_stop_at_79_with_legacy_margin`
- `test_03_target_stop_log_records_target_source_and_reason`
- focused: `15 passed`
- 関連時刻フェンス・ワークフロー試験: `71 passed`
- 全体: `579 passed, 1 skipped`
- `python -m ruff check .`: pass
- 対象2ファイルの mypy: pass
- Import Linter（標準3契約）: 3 kept, 0 broken
- `python scripts/security_check.py`: pass、`.env` は ignored を確認
- GitHub Actions `quality`（ソースコミット）: success

事前ゲートでは既知の、対象外の Import Linter 境界診断、ty の型診断、deptry の依存定義診断、tsc の既存 JavaScript 診断が警告として出力された。強制品質モードは使用していない。今回の変更を原因とする品質失敗ではなく、対象の lint/type/test/CI は成功している。

## 本番反映と検証

- ソースコミット: `5de2cf7f1372458e861d1b1f613504d053838a70` (`fix: require exact SOC target before 03 standby`)
- 公式の production deployment gate: pass（ローカルバックアップ、セキュリティ、同期・パリティ、試験を含む）
- 公式デプロイ状態: `complete`。jobs、dashboard、settings_roundtrip、KP-NET import、Drive backup はすべて `success`。
- settings roundtrip: target 50% の往復確認が成功し、既存設定の復元を確認した。
- 07 slot dry-run: Cloud Run の完了・リソース準備・開始・コンテナ準備チェックを通過した。
- 03 slot: slot=03、監視間隔 180 秒、legacy margin 1.0% を保持していることを確認した。
- Scheduler: 03:00 JST、enabled を確認した。
- デプロイ後の実行イメージ digest とレジストリの最新 runner digest は一致した。

デプロイ状態のローカル記録は `artifacts/deployment_state/production-20260831T085704.json` にある（Git 管理外）。機密値、認証情報、完全なクラウド出力、生ログはこの報告書へ記録していない。

## 影響・ロールバック・残余リスク

- 影響: 03 時の充電監視は target 未満で停止しなくなる。SOC が取得不能な場合は従来どおり安全側で待機する。
- データ/API: 保存データ形式、外部 API、設定キーの変更はない。
- ロールバック: ソースコミットを revert し、同じ公式デプロイ手順で反映できる。ただし target 未達停止の再導入となるため、運用上は推奨しない。
- 残余リスク: 2026-08-31 の歴史的事象の直接原因は証明不足のままである。追加した観測ログにより、以後同種の事象は target・SOC・source・停止理由を実行ログから照合できる。

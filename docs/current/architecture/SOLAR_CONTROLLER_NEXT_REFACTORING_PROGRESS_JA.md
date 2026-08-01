# SolarController 次期改善 進捗ログ

このログは `SOLAR_CONTROLLER_NEXT_REFACTORING_EXECUTION_INSTRUCTIONS_JA.md` のカードごとの検証証跡である。

## 2026-08-01 — T1-0: 基準記録

- 開始コミット: `71133c67ebf6de97d46d39b21903a1d8d9a2f5ea`
- 開始時作業ツリー: `git status --short` は出力なし。
- Python全回帰: `python -m pytest -q` は `428 passed, 1 skipped in 21.78s`。skipは環境変数で明示有効化した場合だけOpen-Meteoへ接続する外部テストである。
- リリース対象mypy: 59エラー、9ファイル。内訳はFirestore 16、PostgreSQL 12、Dashboard server 11、Dashboard data 9、operations workflow 4、weekly/correction/sync 各2、KP-NET 1。
- 全体mypy: 134エラー、20ファイル。リリース対象に加え、分析スクリプト、Drive、Sheets、local_control workflowなどに残件がある。
- 不変条件: 本カードでは実装・設定・外部契約・外部サービスを変更していない。

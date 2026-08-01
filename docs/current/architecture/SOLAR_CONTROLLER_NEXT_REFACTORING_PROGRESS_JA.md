# SolarController 次期改善 進捗ログ

このログは `SOLAR_CONTROLLER_NEXT_REFACTORING_EXECUTION_INSTRUCTIONS_JA.md` のカードごとの検証証跡である。

## 2026-08-01 — T1-0: 基準記録

- 開始コミット: `71133c67ebf6de97d46d39b21903a1d8d9a2f5ea`
- 開始時作業ツリー: `git status --short` は出力なし。
- Python全回帰: `python -m pytest -q` は `428 passed, 1 skipped in 21.78s`。skipは環境変数で明示有効化した場合だけOpen-Meteoへ接続する外部テストである。
- リリース対象mypy: 59エラー、9ファイル。内訳はFirestore 16、PostgreSQL 12、Dashboard server 11、Dashboard data 9、operations workflow 4、weekly/correction/sync 各2、KP-NET 1。
- 全体mypy: 134エラー、20ファイル。リリース対象に加え、分析スクリプト、Drive、Sheets、local_control workflowなどに残件がある。
- 不変条件: 本カードでは実装・設定・外部契約・外部サービスを変更していない。

## 2026-08-01 — T1-1: Firestore adapterの型境界

- 開始コミット: `5e6af8663962565910f2c7a24a207bba38eefaff`
- 変更前検証: Firestore関連テストは `3 passed in 0.82s`。`app/operations/firestore.py` のmypyは16エラーだった。
- 変更: Firestore clientとsnapshotはSDK境界として `Any` を明示し、`open_firestore` の戻り値を明示した。snapshotの `exists` はboolへ正規化した。document path、collection名、payload、merge指定、batch上限、例外処理は変更していない。
- 変更後検証: 同じ3テストは `3 passed in 0.82s`。対象mypyは0エラー。`compileall` と `git diff --check` は成功した。
- 外部安全性: Firestore clientを生成する実行経路はテストでfake化され、実サービスへの接続・書込みは実行していない。

## 2026-08-01 — T1-2: PostgreSQL adapterの型境界

- 開始コミット: `6821c52b119ff29f1424eada9fa078c32869bbd9`
- 変更前検証: PostgreSQL関連テストは `2 passed in 0.45s`。対象mypyは12エラーだった。
- 変更: psycopg connectionを外部SDK境界として `Any` で明示し、`open_postgres` の戻り値を明示した。SQL、placeholder、transaction順、commit/rollback、upsert条件は変更していない。
- 変更後検証: 同じ2テストは `2 passed in 0.46s`。対象mypyは0エラー。`compileall` と `git diff --check` は成功した。
- 外部安全性: PostgreSQL接続は実行していない。

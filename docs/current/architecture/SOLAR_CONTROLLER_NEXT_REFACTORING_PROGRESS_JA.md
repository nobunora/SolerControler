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

## 2026-08-01 — T1-3: Dashboard dataのOptionalとbackend境界

- 開始コミット: `dd9b77a75cfd74a9764877d7a0e93ab3f255fd40`
- 変更前検証: Dashboard data・backend parityテストは `37 passed in 1.04s`。対象mypyは9エラーだった。
- 変更: plan日付に一致するbattery rowを明示的に分離し、欠損rowに `.get` しないようにした。PostgreSQL cursorとFirestore clientを外部境界として `Any` で明示し、Firestore client factoryの戻り値を明示した。欠損時の既存0.0 fallback、schedule表示、query、cache、document構造は変更していない。
- 変更後検証: 同じテストは `37 passed in 1.06s`。対象mypyは0エラー。`compileall` と `git diff --check` は成功した。
- 外部安全性: SQLiteのローカルfixtureとfake backendだけを使用し、Firestore/PostgreSQLには接続していない。

## 2026-08-01 — T1-4: Dashboard serverのHTTP境界

- 開始コミット: `a8deefa340b46bfffe9b93a9b8139700edf97acd`
- 変更前検証: Dashboard serverテストは `4 passed in 0.21s`。対象mypyは11エラーだった。
- 変更: HTTP URL解析値を `ParseResult` として明示し、dashboard payloadのdict値型、session cookie属性、log hook引数を明示した。root/APIで別のslice変数名を使い、意図が異なる値の再定義を解消した。status code、header、認証判定、URL、HTML、JSONは変更していない。
- 変更後検証: 同じ4テストは `4 passed in 0.21s`。対象mypyは0エラー。`compileall` と `git diff --check` は成功した。
- 外部安全性: 実HTTP serverは起動していない。

## 2026-08-01 — T1-5: Operations workflowとsync境界

- 開始コミット: `c4e0df8f9d166b1b454c42c5eba002340b7705f1`
- 変更前検証: DB pipeline・operations関連テストは `25 passed in 1.51s`。workflow 4件、sync 2件のうち、対象ファイル自身の未注釈は3件だった。残るweekly backupの2件は次カードの所有範囲である。
- 変更: SQLite connectionと週次backup接続を外部DB-API境界として `Any` で明示し、Firestore client factoryの戻り値を明示した。backendごとのtransaction順、commit、ingest順、SQL/Firestore処理は変更していない。
- 変更後検証: `--follow-imports=skip` で対象2ファイルのmypyは0エラー。関連テストは `25 passed in 1.65s`。`compileall` と `git diff --check` は成功した。
- 外部安全性: DB、Firestore、backupは実行していない。

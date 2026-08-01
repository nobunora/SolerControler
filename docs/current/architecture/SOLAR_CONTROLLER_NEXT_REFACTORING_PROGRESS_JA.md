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

## 2026-08-01 — T1-6A: Weekly backup

- 開始コミット: `4f612005762f0240372bfa4a08116e0bbc830e10`
- 変更前検証: weekly backupテストは `1 passed in 0.40s`。対象mypyは2エラーだった。
- 変更: DB cursorとconnectionを外部DB-API境界として `Any` で明示した。週判定、SQL、placeholder、保存JSON、出力パス、cleanup例外の扱いは変更していない。
- 変更後検証: 同じテストは `1 passed in 0.44s`。対象mypyは0エラー。`compileall` と `git diff --check` は成功した。
- 外部安全性: 実DBとバックアップ先は使用していない。

## 2026-08-01 — T1-6B: Forecast correction

- 開始コミット: `3f303ca4ad080dc627f8561ee62391f96e1d5f73`
- 変更前検証: forecast correction・energy modelテストは `53 passed in 2.54s`。対象mypyは2エラーだった。
- 変更: forecast historyのweather codeが欠損時はdictキーを追加しないようにした。既存利用側は `.get("weather_code")` で欠損をunknownとして扱うため、従来の `None` と同じ分岐になる。PV、load、shortwave、補正比、provider呼出しは変更していない。
- 変更後検証: 同じテストは `53 passed in 2.78s`。対象mypyは0エラー。`compileall` と `git diff --check` は成功した。
- 外部安全性: Open-Meteo等の実通信は行っていない。

## 2026-08-01 — T1-6C: KP-NET configuration value

- 開始コミット: `1717132eaef50968a6a2255a1e29a2a83361f422`
- 変更前検証: KP-NET workflow・settings intentテストは `47 passed in 1.38s`。対象mypyは1エラーだった。
- 変更: 夜間充電window契約を `TypedDict` として明示し、logical durationがintである不変条件を型に表した。window算出、時刻範囲、device schedule、payload、環境変数は変更していない。
- 変更後検証: 同じテストは `47 passed in 1.35s`。対象mypyは0エラー。`compileall` と `git diff --check` は成功した。
- 外部安全性: KP-NETへ接続していない。

## 2026-08-01 — T1-7: リリース用mypyゲート確認

- 開始コミット: `69571c0`。
- 検証: 指示書のリリース対象mypyコマンドをそのまま実行し、`41 source files` が0エラーで成功した。
- 構文・形式検査: `compileall` と `git diff --check` は成功した。
- 判定: 作成時点の59エラーは0件になった。外部サービス、本番処理、設定値は変更・実行していない。

## 2026-08-01 — T2-1: Drive backup型境界

- 開始コミット: `48f9e1adfbc74b228456892599d4143b56e3d554`
- 変更前検証: Drive backupテストは `3 passed in 0.85s`。対象mypyは14エラーだった。
- 変更: ソートキーの値型、Drive service戻り値、SDK refresh、Drive API JSON response、upload条件を実際の外部境界に合わせて明示した。folder IDがない場合は従来どおりuploadしない。backup名、manifest、hash、出力パス、実サービス呼出し条件は変更していない。
- 変更後検証: 同じテストは `3 passed in 0.87s`。対象mypyは0エラー。`compileall` と `git diff --check` は成功した。
- 外部安全性: Drive/Firestoreへの実接続、upload、backup実行は行っていない。

## 2026-08-01 — T2-2: Sheets adapter型境界

- 開始コミット: `42ea90a69ad4d1ea612a955f8d66e0a92ed04599`
- 変更前検証: 対象mypyは9エラーだった。Sheets exportには外部サービスを使わない個別テストが存在しないため、import検査と型検査を境界確認に用いた。
- 変更: Google Sheets/Drive serviceを外部SDK境界として `Any` で明示し、service factoryの戻り値を明示した。spreadsheet作成、共有、tab、header、meta、table書込みのpayloadと例外処理は変更していない。
- 変更後検証: `import app.exports.sheets` と対象mypyは成功。`compileall` と `git diff --check` は成功した。
- 外部安全性: Google Sheets/Driveの作成・共有・書込みは実行していない。

## 2026-08-01 — T2-3: Local control workflow型境界

- 開始コミット: `7eb4948`。
- 変更前検証: local controlの対象テストは `5 passed in 0.14s`。対象mypyは10エラーだった。
- 変更: JSON summary payloadを `dict[str, object]` として明示し、history recordはJSON payloadを再読込せず、既に型付けされたforecast・metrics・decision・apply resultから組み立てた。summary内容、historyキー、書込み順、local/remoteの分岐は変更していない。
- 変更後検証: 同じテストは `5 passed in 0.14s`。対象mypyは0エラー。`compileall` と `git diff --check` は成功した。
- 外部安全性: Playwright、ブラウザ、CSVダウンロード、蓄電池設定は実行していない。

## 2026-08-01 — T2-4: 時間別気象ベクトル解析型境界

- 開始コミット: `b3f4ee7`。
- 変更: Open-Meteo request parameterの値型、NumPy予測配列、欠損したcomfort featureの分岐を明示した。featureが構築不能な行は従来どおり学習・予測対象にしない。分析式、入力CSV、通信先、出力形式は変更していない。
- 検証: 対象mypyは0エラー。`compileall` と `git diff --check` は成功した。外部API、DB、分析本体は実行していない。

## 2026-08-01 — T2-5: 複数日気象寄与解析型境界

- 開始コミット: `682be99`。
- 変更前検証: 対象mypyは5エラーだった。
- 変更: Open-Meteo request parameterの許容値型、NumPy予測配列、欠損したcomfort featureの分岐を明示した。featureが構築不能な時刻は学習行に追加しないため、欠損値を数値へ偽装しない。
- 変更後検証: 対象mypyは0エラー。`compileall` と `git diff --check` は成功した。
- 外部安全性: Open-Meteo API、DB、分析本体は実行していない。

## 2026-08-01 — 補正: archive型抑制の除去

- 検出理由: T2-6の対象mypy実行で、依存先 `app/backup/night_plan_archive.py` の未使用 `type: ignore` 2件が検査を妨げた。
- 変更・検証: obsoleteな抑制とその説明を削除し、archive対象mypyは0エラー、`tests/test_night_plan_archive.py` は `2 passed`。コミットは `ac15e0f`。
- 外部安全性: Cloud Storageの読書きは実行していない。

## 2026-08-01 — T2-6: 物理PV履歴再計算型境界

- 開始コミット: `ac15e0f`。
- 変更前検証: 対象には5件の型エラーがあり、さらに依存archiveの未使用抑制2件を上記の独立コミットで解消した。
- 変更: Firestore documentを実行時に辞書へ正規化し、forecast・気象行・既存最適化値の型を明示した。不正なdocumentや時刻は従来同様スキップする。Firestore/Storage clientのfakeだけで再計算行をSQLiteへ保存する個別テストを追加した。
- 変更後検証: 対象mypyは0エラー。`tests/test_recompute_physical_pv_history.py tests/test_pv_physical_forecast.py` は `3 passed`。`compileall` と `git diff --check` は成功した。
- 外部安全性: Firestore、Cloud Storage、本番DBへの接続・書込みは実行していない。

## 2026-08-01 — 補正: archive Storage import境界

- 検出理由: 未使用抑制の除去後、google cloud stubの公開APIに合わせたimport形式が必要と判明した。
- 変更・検証: 実行時のStorage client生成は同じまま `google.cloud.storage.Client` を直接importした。対象mypyは0エラー、`tests/test_night_plan_archive.py` は `2 passed`。コミットは `210d0ae`。
- 外部安全性: Cloud Storageの読書きは実行していない。

## 2026-08-01 — T2-7: KP-NET SOC差分レポート型境界

- 開始コミット: `210d0ae`。
- 変更: SOCが欠損していない行だけから最小・最大値候補を作り、`float | None` を数値として扱わないようにした。欠損SOCの無視、表示値、集計値は変わらない。
- 変更後検証: 対象mypyは0エラー。`tests/test_kpnet_soc_gap_report.py` は `5 passed`。`compileall` と `git diff --check` は成功した。
- 外部安全性: Firestore、CSVダウンロード、レポートの実運用出力は実行していない。

## 2026-08-01 — T2-8: 夜間充電計画アーカイブ型境界

- 開始コミット: `9854479`。
- 変更: 読み込んだ詳細計画のforecastを辞書へ正規化してから日付を参照するようにした。詳細が不正な場合のskip、dry-run既定、`--apply`時だけの永続書込みは変更していない。
- 変更後検証: 対象mypyは0エラー。`tests/test_night_plan_archive.py` は `2 passed`。`compileall` と `git diff --check` は成功した。
- 外部安全性: Firestore/Cloud Storageへの書込みを伴うCLI本体は実行していない。

## 2026-08-01 — T2-9: Cloud Job monitor型境界

- 開始コミット: `a17206c`。
- 変更: 初期SOC未取得時にstandbyへ戻す補助関数のdevice、status、SOC読取値を既存Portと値オブジェクトで明示した。standby実行後に停止理由を永続化する順序、強制充電の判断、環境値は変更していない。
- 変更後検証: 対象mypyは0エラー。`tests/test_cloud_job_runner.py tests/test_forced_charge_state_machine.py` は `69 passed`。`compileall` と `git diff --check` は成功した。
- 外部安全性: Cloud Job、Firestore、蓄電池設定、CSV取得を実行していない。

## 2026-08-01 — T2-10: Dashboard data互換export型境界

- 開始コミット: `4e9befc`。
- 変更: 互換モジュールが再exportする3つのデータモデルを、実際の定義元 `app.dashboard.models` から明示importした。公開名、repository、loader、互換import先は変更していない。
- 変更後検証: 対象mypyは0エラー。`tests/test_dashboard_data.py tests/test_dashboard_backend_parity.py` は `37 passed`。`compileall` と `git diff --check` は成功した。

## 2026-08-01 — T2-11およびPhase T2型検査完了

- `scripts/backup_drive.py` は先行するDrive adapter型境界の修正により、着手時点で対象mypyが0エラーだった。`tests/test_drive_backup.py` は `3 passed`、`compileall` と `git diff --check` は成功した。
- Phase T2の全体再検査: `python -m mypy app scripts --no-incremental` は **118 source filesで0エラー**。実サービスへの接続、backup作成・upload、Firestore操作は行っていない。
- Phase T2完了検査: 全テストは `429 passed, 1 skipped in 20.44s`、`python scripts/security_check.py` と `git diff --check` は成功した。

## 2026-08-01 — I-1: local_control正規import

- 変更: `workflow.py` 内だけで、browser・CSV・decision・historyの4 importを同一責務の `app.local_control.*` 正規パスへ置換した。関数名、呼出し順、例外処理は変更していない。
- 検証: `tests/test_decision.py tests/test_local_control_config_compatibility.py tests/test_local_control_models_compatibility.py` は `5 passed`。対象mypyと `git diff --check` は成功した。

## 2026-08-01 — I-2: domain正規import

- 変更: 9ファイルの `app.constants`、`app.tariff`、`app.time_windows` importだけを対応する `app.domain.*` 正規パスへ置換した。互換モジュール、名前、ロジック、公開契約は残している。
- 検証: domain・operations・SOC optimizer・Cloud Job・Driveの指定回帰は `75 passed`。対象9ファイルのmypyと `git diff --check` は成功した。

## 2026-08-01 — I-3: 夜間計画archive正規import

- 変更: Firestore操作、Cloud Job、および3つの保守スクリプトの `app.night_plan_archive` importだけを `app.backup.night_plan_archive` に置換した。互換ラッパーと実行時の保存・復元契約は残している。
- 検証: archive・Firestore・Cloud Job・KP-NETの指定回帰は `52 passed`。対象5ファイルのmypyと `git diff --check` は成功した。

## 2026-08-01 — I-4: pre-release正規モジュール検査

- 変更: release gateの明示mypy対象をdomain正規パスへ移した。`app/energy_plan` と `app/operations` は再帰対象であるため、同じファイルを二重指定するとmypyが重複モジュールとして停止することを実行で確認し、範囲を減らさず二重指定のみ除去した。
- 検証: `tests/test_production_deploy_scripts.py` は `21 passed`。`pwsh -NoProfile -File scripts/pre_release_local.ps1 -SkipInstall` は `429 passed, 1 skipped`、mypy `40 source filesで0エラー`、security check成功で完走した。

## 2026-08-01 — I-5: 内部旧import監査

- 検証: 指示書に列挙された34互換名を対象に、`app` と `scripts` の実装Pythonファイル（`tests` 除外）でimport文を検査した。検出は **0件**。互換モジュール自体は削除していない。

## 2026-08-01 — D-1: KP-NET監視履歴の特性テスト

- 変更: 実装を変えず、BOM付きCSV、欠損・不正行の除外、空充電量の0.0化、複数CSVの時刻順、最新run内の名前順CSV、CSV未発見時のEnergy Plan/Cloud Jobの異なる契約を固定するテストを追加した。
- 検証: `tests/test_kpnet_monitoring_history.py` は `3 passed`。`git diff --check` は成功した。

## 2026-08-01 — D-2: KP-NET監視履歴の共通境界

- 変更: `app.kpnet.monitoring_history` に、CSVから有効な充電/SOC観測値を時刻順で返す関数と、最新run内のCSVを返す関数を追加した。CSV未発見時は共通関数では空listを返す。既存利用元はまだ変更していない。
- 検証: 特性テストは `3 passed`。新モジュールのmypy、`compileall`、`git diff --check` は成功した。

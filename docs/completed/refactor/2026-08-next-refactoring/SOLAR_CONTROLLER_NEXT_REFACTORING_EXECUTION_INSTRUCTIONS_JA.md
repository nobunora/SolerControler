# SolarController 次期改善 実行指示書

作成日: 2026-08-01  
作成時基準コミット: `0a647674567fcd51e8e5c680b52ed746dc6edd3f`

## 1. この文書の目的

この文書は、前回のモジュール所有先整理後に残った改善を、安全な順番で完了するための実行指示書である。実行担当がLuna軽であっても、独自判断で範囲を広げたり、型エラーを隠したり、外部契約を変えたりしないように、作業単位、変更可能ファイル、検査、停止条件を固定する。

この文書の実行中に、Cloud Run、Firestore、PostgreSQL、Google Drive、Google Sheets、KP-NET、Open-Meteoなどの実サービスへ接続してはならない。実サービスでの確認が必要になった場合は、そのカードを停止し、人間へ確認する。

## 2. 作成時点の確認結果

| 項目 | 作成時点の結果 |
|---|---:|
| Python全回帰 | `428 passed, 1 skipped` |
| 循環import | 0件 |
| リリース前mypy対象 | 59エラー、9ファイル |
| `app` と `scripts` 全体のmypy | 134エラー、20ファイル |
| ルート互換モジュール | 34個 |
| アプリ・スクリプトから互換モジュールへのimport | 22件 |
| wildcard import | 0件 |

1件のskipは、`RUN_EXTERNAL_SITE_TESTS=true` の場合だけOpen-Meteoへ接続する外部テストである。通常のローカル検証ではskipが正しい。

## 3. 完了条件

次のすべてを満たしたときだけ、この計画を完了扱いにする。

1. `python -m mypy app scripts --no-incremental` が0エラーで終了する。
2. `pwsh -NoProfile -File scripts/pre_release_local.ps1 -SkipInstall` が成功する。
3. `app` と `scripts` から34個の互換モジュールへのimportが0件である。
4. KP-NET監視CSVの共通読込処理が一つの正規モジュールにある。
5. 意味が異なる類似処理は無理に統合せず、近接した `readable-code-audit: skip RULE-ID — concrete reason` で差を説明する。
6. 指定した巨大モジュールの分割カードが完了し、各モジュールの責務を「〜して、〜する」と複数責務で説明しなくてよい状態になる。
7. 全Pythonテスト、Dashboard JavaScriptテスト、`compileall`、`git diff --check` が成功する。
8. 外部公開記号、CLIファイル名、環境変数名、JSONキー、CSV列名、DB列名、終了コードが変更されていない。
9. 作業ツリーがクリーンで、各カードが一つのコミットになっている。

### 3.1 固定実行順

次の順序を変えてはならない。途中のカードを飛ばして後続へ進んではならない。

1. T1-0からT1-7
2. T2-1からT2-11
3. I-1からI-5
4. D-1からD-6
5. M-1からM-5。各Mカード内の番号も上から順に一つずつ実行する。
6. C-1からC-3
7. PF-1
8. PF-2

カード開始時点で前カードのコミットが存在せず、または作業ツリーが汚れている場合は停止する。

## 4. 絶対に守る禁止事項

- 複数カードを一つのコミットにまとめない。
- カードに書かれていないファイルを、ついでに整形・改名・整理しない。
- `# type: ignore`、`# noqa`、mypy設定の緩和、検査対象の除外でエラーを隠さない。
- `cast(Any, ...)` や無条件の `Any` で内部ドメイン値の型エラーを消さない。
- テストの期待値を実装結果に合わせて変更しない。
- private関数の移動とアルゴリズム変更を同じカードで行わない。
- DBクエリ、Firestoreパス、Google API payload、KP-NET payloadを型修正のために変更しない。
- `import *` を追加しない。
- 互換モジュールをこの計画内で削除しない。削除には外部利用者を確認したうえで別途人間の承認が必要である。
- 実サービスを利用するテスト、デプロイ、データ取込、バックアップを実行しない。

## 5. 全カード共通の開始手順

カードを始める前に、リポジトリルート `C:\VSC\SolerControler` で次を上から順番に実行する。

```powershell
git status --short
git rev-parse HEAD
```

`git status --short` に1行でも表示された場合は開始しない。前カードの変更か、人間の変更かを確認する。勝手にreset、checkout、削除してはならない。

作業ログは `docs/current/architecture/SOLAR_CONTROLLER_NEXT_REFACTORING_PROGRESS_JA.md` に日本語で追記する。ログファイルがなければ最初のカードで作成し、次を記録する。

- カード番号と目的
- 開始コミットID
- 変更前コマンドと結果
- 変更したファイル
- 変更しなかった外部契約
- 変更後コマンドと結果
- 発生した失敗と修正内容
- 残リスク
- 完了コミットID

## 6. 全カード共通の終了手順

各カード固有のテストが成功した後、次を実行する。

```powershell
python -m compileall -q app scripts cloud_job_runner.py dashboard_server.py db_pipeline_main.py energy_model_main.py kpnet_main.py main.py sheets_export_main.py
git diff --check
git status --short
```

次のいずれかがあればコミットしない。

- テストが1件でも失敗した。
- mypyエラー数がカード開始時より増えた。
- `git diff --check` が空白エラーを報告した。
- 許可されていないファイルに差分がある。
- import時に外部サービスへ接続するようになった。
- 戻り値、例外、ログ、保存データの形を維持できた証拠がない。

## 7. 型エラー修正の判断規則

型エラーは次の順序で直す。

1. 関数に戻り値注釈がないだけなら、実際の全returnを確認して正しい注釈を追加する。
2. 引数注釈がない場合は、全呼出し元を `rg` で確認して最も狭い型を使う。
3. 外部SDKのclient、cursor、document snapshotなど型情報が不足する境界だけは `Any` を許可する。内部のdict、日付、SOC、料金、予測値には使わない。
4. 読取専用dictを受け取る関数は、可能なら `dict` ではなく `Mapping` を使う。
5. `None` の可能性がある値は、境界で `is None` を判定する。`assert` やcastだけで消さない。
6. JSONやFirestore documentは、型を付ける前に `isinstance(value, dict)` などの実行時検証を置く。
7. `Any` を返す外部SDK関数からboolやdictを返す場合は、実行時検証または明示的な正規化を行う。
8. 実行時分岐を追加する場合は、その分岐の個別テストを先に追加する。
9. mypyのためだけに正常系の値、fallback、例外種別を変えてはならない。

## 8. Phase T1: リリース前mypy 59件を0にする

### T1-0: 基準記録

変更可能ファイルは進捗ログだけである。

```powershell
python -m pytest -q
python -m mypy app/time_windows.py app/tariff.py app/monitoring_csv.py app/night_plan.py app/dashboard app/energy_plan app/forced_charge app/kpnet app/operations app/settings --no-incremental
python -m mypy app scripts --no-incremental
```

作成時点では、最初のmypyが59件、全体mypyが134件である。件数が異なっても、現在の出力を正としてログへ全ファイル別件数を記録する。実装は変更しない。

コミット: `docs: record next refactoring baseline`

### T1-1: Firestore adapterの型境界

変更可能ファイル:

- `app/operations/firestore.py`
- `tests/test_firestore_operations.py`
- `tests/test_firestore_dashboard_metrics.py`
- `tests/test_operations_firestore_compatibility.py`
- 進捗ログ

作成時点の対象エラーは16件である。`client`、collection、document、snapshotなど外部SDK境界の引数を確認し、外部SDK値だけに注釈を追加する。Firestore collection名、document ID、merge指定、保存dictは変更しない。

```powershell
python -m pytest -q tests/test_firestore_operations.py tests/test_firestore_dashboard_metrics.py tests/test_operations_firestore_compatibility.py
python -m mypy app/operations/firestore.py --no-incremental
```

完了条件は、このファイルのmypyエラー0件とテスト成功である。

コミット: `refactor: type firestore adapter boundaries`

### T1-2: PostgreSQL adapterの型境界

変更可能ファイル:

- `app/operations/postgres.py`
- `tests/test_postgres_operations.py`
- `tests/test_operations_postgres_compatibility.py`
- 進捗ログ

作成時点の対象エラーは12件である。connectionとcursorの型だけを外部境界として扱う。SQL文、placeholder、commit/rollback順、upsert条件は変更しない。

```powershell
python -m pytest -q tests/test_postgres_operations.py tests/test_operations_postgres_compatibility.py
python -m mypy app/operations/postgres.py --no-incremental
```

コミット: `refactor: type postgres adapter boundaries`

### T1-3: Dashboard dataのOptionalとbackend境界

変更可能ファイル:

- `app/dashboard/data.py`
- `app/dashboard/models.py`
- `tests/test_dashboard_data.py`
- `tests/test_dashboard_backend_parity.py`
- 進捗ログ

作成時点の対象エラーは9件である。特にOptionalなplanやdocumentに対する `.get`、DB cursor、Firestore clientの型を確認する。値がない場合の既存表示、警告、fallbackは変更しない。`None` 分岐を追加する場合は、その欠損ケースをテストへ追加してから実装する。

```powershell
python -m pytest -q tests/test_dashboard_data.py tests/test_dashboard_backend_parity.py
python -m mypy app/dashboard/data.py app/dashboard/models.py --no-incremental
```

コミット: `refactor: type dashboard data boundaries`

### T1-4: Dashboard serverのHTTP境界

変更可能ファイル:

- `app/dashboard/server.py`
- `tests/test_dashboard_server.py`
- 進捗ログ

作成時点の対象エラーは11件である。HTTP handler、header、query、レスポンス文字列の型を明示する。同じ変数名の再定義は、意味の異なる名前へ変える。status code、Content-Type、認証、URL、HTMLを変更しない。

```powershell
python -m pytest -q tests/test_dashboard_server.py
python -m mypy app/dashboard/server.py --no-incremental
```

コミット: `refactor: type dashboard http boundaries`

### T1-5: Operations workflowとsync境界

変更可能ファイル:

- `app/operations/workflow.py`
- `app/operations/sync.py`
- `tests/test_db_pipeline_main.py`
- `tests/test_operations_db.py`
- `tests/test_operations_sync_compatibility.py`
- 進捗ログ

作成時点の対象エラーはworkflow 4件、sync 2件である。backendごとのtransaction順を変更せず、引数と戻り値を明示する。SQLite、PostgreSQL、Firestoreの処理を一つの汎用関数へ統合しない。

```powershell
python -m pytest -q tests/test_db_pipeline_main.py tests/test_operations_db.py tests/test_operations_sync_compatibility.py
python -m mypy app/operations/workflow.py app/operations/sync.py --no-incremental
```

コミット: `refactor: type operations workflow boundaries`

### T1-6A: Weekly backup

変更可能ファイル:

- `app/backup/weekly.py`
- `tests/test_weekly_backup.py`
- 進捗ログ

作成時点の対象エラーは2件である。実際のbackup対象、差分判定、作成日時、出力ファイル名を変更せず、callbackや戻り値の注釈だけを正しくする。

```powershell
python -m pytest -q tests/test_weekly_backup.py
python -m mypy app/backup/weekly.py --no-incremental
```

コミット: `refactor: type weekly backup boundary`

### T1-6B: Forecast correction

変更可能ファイル:

- `app/forecasting/correction.py`
- `tests/test_forecasting_correction_compatibility.py`
- `tests/test_energy_model.py`
- 進捗ログ

作成時点の対象エラーは2件である。dictに `None` が入り得る箇所は、実際の返却契約をテストで確認してから、dict値型を広げるか境界で除外する。補正値や採用条件を変更しない。

```powershell
python -m pytest -q tests/test_forecasting_correction_compatibility.py tests/test_energy_model.py
python -m mypy app/forecasting/correction.py --no-incremental
```

コミット: `refactor: type forecast correction result`

### T1-6C: KP-NET configuration value

変更可能ファイル:

- `app/kpnet/workflow.py`
- `tests/test_kpnet_workflow.py`
- `tests/test_kpnet_settings_intent.py`
- 進捗ログ

作成時点の対象エラーは1件である。`object` を直接 `int()` へ渡さず、既存の入力型を実行時に確認する。不正値時の既存defaultと例外を維持する。

```powershell
python -m pytest -q tests/test_kpnet_workflow.py tests/test_kpnet_settings_intent.py
python -m mypy app/kpnet/workflow.py --no-incremental
```

コミット: `refactor: type kpnet configuration value`

### T1-7: リリース用mypyゲート確認

実装変更は禁止する。次が0エラーになることを確認する。

```powershell
python -m mypy app/time_windows.py app/tariff.py app/monitoring_csv.py app/night_plan.py app/dashboard app/energy_plan app/forced_charge app/kpnet app/operations app/settings --no-incremental
```

失敗した場合は、新しいカードを作らず、エラーを発生させた直前カードへ戻って原因を修正する。

コミット: `test: verify release mypy gate`

## 9. Phase T2: 全体mypyの残り75件を0にする

T1完了後に必ず全体件数を再計測する。作成時点の75件は目安であり、実際の最新件数をログへ記録する。

| カード | 対象 | 作成時件数 | 必須テスト |
|---|---|---:|---|
| T2-1 | `app/backup/drive.py` | 15 | `tests/test_drive_backup.py` |
| T2-2 | `app/exports/sheets.py` | 11 | 欠損値や外部serviceをfakeにしたテストを必要時に追加し、全回帰も行う |
| T2-3 | `app/local_control/workflow.py` | 10 | `tests/test_decision.py tests/test_local_control_config_compatibility.py tests/test_local_control_models_compatibility.py` |
| T2-4 | `scripts/analyze_hourly_weather_vectors.py` | 16 | ローカルfixtureだけを使う個別テストを追加する |
| T2-5 | `scripts/analyze_multi_day_weather_contribution.py` | 5 | ローカルfixtureだけを使う個別テストを追加する |
| T2-6 | `scripts/recompute_physical_pv_history.py` | 6 | Firestoreをfakeにした個別テストを追加する |
| T2-7 | `scripts/kpnet_soc_gap_report.py` | 4 | `tests/test_kpnet_soc_gap_report.py` |
| T2-8 | `scripts/archive_night_charge_plans.py` | 2 | `tests/test_night_plan_archive.py` とfake clientの個別テスト |
| T2-9 | `app/runtime/cloud_job.py` | 2 | `tests/test_cloud_job_runner.py tests/test_forced_charge_state_machine.py` |
| T2-10 | `app/dashboard_data.py` | 3 | `tests/test_dashboard_data.py tests/test_dashboard_backend_parity.py` |
| T2-11 | `scripts/backup_drive.py` | 1 | `tests/test_drive_backup.py` |

各カードで守る手順:

1. 対象ファイルだけのmypy出力をログへコピーする。
2. 型注釈だけで解決できるエラーと、実行時None検査が必要なエラーを分ける。
3. 実行時分岐が必要なら先に個別テストを追加する。
4. 外部SDKを呼ばないfakeを使う。
5. 対象mypy、個別テスト、全体mypyの順に実行する。
6. 対象ファイルのエラーが0で、全体件数が増えていない場合だけコミットする。

各コミットは `refactor: type <対象責務>` とし、カードごとに一つ作る。

Phase完了検査:

```powershell
python -m mypy app scripts --no-incremental
python -m pytest -q
```

## 10. Phase I: 互換モジュールへの内部importを0にする

### I-1: local_control内の旧import

変更可能ファイル:

- `app/local_control/workflow.py`
- 関連テスト
- 進捗ログ

次の置換だけを行う。

| 旧import | 正規import |
|---|---|
| `app.browser_automation` | `app.local_control.browser` |
| `app.csv_utils` | `app.local_control.csv_input` |
| `app.decision` | `app.local_control.decision` |
| `app.history_store` | `app.local_control.history` |

関数名、呼出し順、例外処理は変更しない。

```powershell
python -m pytest -q tests/test_decision.py tests/test_local_control_config_compatibility.py tests/test_local_control_models_compatibility.py
```

コミット: `refactor: use canonical local control imports`

### I-2: domain定数・料金・時間窓の旧import

次の正規パスへimportだけを変更する。

- `app.constants` → `app.domain.constants`
- `app.tariff` → `app.domain.tariff`
- `app.time_windows` → `app.domain.time_windows`

対象ファイル:

- `app/backup/drive.py`
- `app/energy_plan/energy_model.py`
- `app/energy_plan/soc_cost.py`
- `app/kpnet/workflow.py`
- `app/runtime/cloud_job.py`
- `app/dashboard/data.py`
- `app/operations/cost_daily.py`
- `app/operations/domain.py`
- `app/settings/forced_charge.py`

```powershell
python -m pytest -q tests/test_domain_primitives.py tests/test_operations_cost_daily.py tests/test_operations_domain.py tests/test_soc_cost_optimizer.py tests/test_cloud_job_runner.py tests/test_drive_backup.py
```

コミット: `refactor: use canonical domain imports`

### I-3: night plan archiveの旧import

`app.night_plan_archive` を `app.backup.night_plan_archive` へ変更する。

対象ファイル:

- `app/operations/firestore.py`
- `app/runtime/cloud_job.py`
- `scripts/archive_night_charge_plans.py`
- `scripts/kpnet_soc_gap_report.py`
- `scripts/recompute_physical_pv_history.py`

```powershell
python -m pytest -q tests/test_night_plan_archive.py tests/test_firestore_operations.py tests/test_cloud_job_runner.py tests/test_kpnet_soc_gap_report.py
```

コミット: `refactor: use canonical night plan archive imports`

### I-4: pre-release対象を正規パスへ変更

`scripts/pre_release_local.ps1` のmypy対象を次へ変更する。

| 旧パス | 正規パス |
|---|---|
| `app/time_windows.py` | `app/domain/time_windows.py` |
| `app/tariff.py` | `app/domain/tariff.py` |
| `app/monitoring_csv.py` | `app/domain/monitoring.py app/operations/monitoring_csv.py` |
| `app/night_plan.py` | `app/energy_plan/night_plan.py` |

他のコマンド、順序、エラー処理は変更しない。

```powershell
python -m pytest -q tests/test_production_deploy_scripts.py
pwsh -NoProfile -File scripts/pre_release_local.ps1 -SkipInstall
```

コミット: `build: check canonical modules before release`

### I-5: 旧import 0件の確認

次の34名を対象にASTまたは `rg` で `app` と `scripts` のimportを検査する。

`artifact_cleanup`, `browser_automation`, `comfort_load_forecast`, `config`, `constants`, `consumption_forecast`, `csv_merge`, `csv_utils`, `dashboard_data`, `db_sync`, `decision`, `drive_backup`, `energy_model`, `firestore_ops`, `forecast_correction`, `history_store`, `kpnet_workflow`, `main`, `models`, `monitoring_csv`, `night_plan_archive`, `night_plan`, `occupancy_schedule`, `operations_db`, `postgres_ops`, `pv_array_forecast`, `pv_physical_forecast`, `sheets_export`, `soc_cost_optimizer`, `soc_decision_feedback`, `tariff`, `time_windows`, `utils`, `weekly_backup`

互換モジュール自体と互換性テストのimportは許可する。アプリとスクリプトの実装からのimportは0件でなければならない。

互換モジュールは削除しない。外部利用をリポジトリ内検索だけで否定できないためである。

コミット: `test: verify canonical internal imports`

## 11. Phase D: 重複境界処理を整理する

### D-1: KP-NET監視履歴の特性テスト

最初は実装を変更しない。次の既存挙動をローカル一時CSVで固定するテストを追加する。

- UTF-8 BOMを読める。
- 日付、時刻、SOCが欠けた行を除外する。
- 不正数値を除外する。
- 充電量が空なら0.0になる。
- 複数CSVの結果をtimestamp昇順にする。
- artifactsの最新run directoryからCSVを名前順で返す。
- CSVがない場合、Energy PlanはRuntimeError、Cloud Jobは空listという現在の差を維持する。

変更可能ファイルは新しいテストファイルだけである。

コミット: `test: characterize kpnet monitoring history`

### D-2: 共通監視履歴モジュール

`app/kpnet/monitoring_history.py` を作成し、次の二つだけを置く。

- `iter_charge_soc_points(csv_paths: list[Path]) -> list[tuple[datetime, float, float]]`
- `find_latest_kpnet_csv_paths(artifacts_dir: Path) -> list[Path]`

`find_latest_kpnet_csv_paths` はCSVがなければ空listを返す。例外は投げない。Energy Plan側の「見つからなければRuntimeError」は呼出し側に残す。

最初のカードでは新モジュールとテストだけを追加し、既存呼出し元は変更しない。

コミット: `refactor: add kpnet monitoring history boundary`

### D-3: CSV監視点読込の利用元移行

`app/kpnet/workflow.py` と `app/runtime/cloud_job.py` の `_iter_charge_soc_points` を削除し、正規関数をimportする。速度推定式は変更しない。

KP-NET側の単純medianとCloud Job側の14日trend/EWMAは目的が異なる。統合しない。両方の速度推定関数の直前に、必要なら次の形式で差を説明する。

`# readable-code-audit: skip DUP-01 — <二つの推定が異なる具体的な運用理由>`

```powershell
python -m pytest -q tests/test_kpnet_workflow.py tests/test_cloud_job_runner.py
```

コミット: `refactor: share kpnet monitoring csv parsing`

### D-4: 最新CSV探索の利用元移行

`app/energy_plan/workflow.py` と `app/runtime/cloud_job.py` の `_latest_kpnet_csv_paths` を削除する。

- Cloud Jobは `find_latest_kpnet_csv_paths` の空listをそのまま使う。
- Energy Planは空listを受けた場合だけ、現在と同じ日本語RuntimeErrorを投げる薄い関数を残してよい。
- `tests/test_cloud_job_runner.py` が旧private関数をmonkeypatchしているため、正規関数を直接importせず、Cloud Job内に同じ名前の薄い委譲関数を一時的に残す。テスト境界を変更する別カードを作るまでは削除しない。

```powershell
python -m pytest -q tests/test_cloud_job_runner.py tests/test_energy_model_runtime.py tests/test_energy_model.py
```

コミット: `refactor: share latest kpnet csv discovery`

### D-5: 環境変数ヘルパーの正規化

`app/configuration/environment.py` と意味が同じprivate helperだけを置換する。候補は次である。

- `app/energy_plan/workflow.py`: `_load_dotenv_if_present`, `_env_bool`, `_env_float`, `_env_float_clamped`
- `app/operations/workflow.py`: `_env_bool`
- `app/runtime/cloud_job.py`: `_env_int`, `_env_float`

候補ごとに、空文字、不正数値、min/max、defaultの挙動が完全一致するか先にテストする。一致しない場合は統合しない。差の理由をskipコメントで残す。全候補を一度に編集せず、1ファイル1コミットにする。

### D-6: 統合してはいけない時刻解析

次の関数は名前が似ているが契約が異なるため、この計画では統合しない。

- Dashboardの `_parse_hhmm_minutes`: 不正値を `None` にする。
- Cloud Jobの `_parse_hhmm_minutes`: 不正値をdefaultへ置換し、0〜23時・0〜59分へclampする。

両方の直前に差が読み取れる契約コメントまたはdocstringがあることを確認する。なければ `readable-code-audit: skip DUP-01` を具体的理由付きで追加する。ロジックは変更しない。

コミット: `docs: explain distinct time parsing contracts`

## 12. Phase M: 巨大モジュールを段階分割する

このPhaseはT、I、Dがすべて完了し、mypyと全テストが緑になってから始める。一度に複数サブシステムを分割しない。

### M共通手順

1. 移動対象の全関数名を `rg -n '^def |^class '` で列挙する。
2. テストのmonkeypatch文字列を `rg -n 'monkeypatch|patch\(' tests` で検索する。
3. 移動前テストを実行する。
4. 新モジュールへコードをそのまま移す。アルゴリズム、名前、引数、戻り値を変えない。
5. importとmonkeypatch先だけを正規モジュールへ変更する。
6. 移動元に互換関数を残す場合は、委譲だけにして期限と理由をコメントする。
7. 個別テスト、対象mypy、全回帰を実行する。
8. 移動とリファクタリングを同じコミットに入れない。

### M-1: Energy Plan

現在の `app/energy_plan/workflow.py` は約2,863行である。次の順序で一群ずつ移す。

1. `app/energy_plan/weather_history.py`: weather class、hourly summary、Open-Meteo response正規化、archive cache、archive row。
2. `app/energy_plan/forecast_inputs.py`: consumption/PVのhourly profile構築と正規化。
3. `app/energy_plan/soc_constraints.py`: morning headroom、daytime surplus、historical SOC gain、constraint集合。
4. `app/energy_plan/optimization.py`: scenario準備、legacy/current optimizer呼出し、decision整形。
5. `workflow.py` には実行順、入力port、最終output組立て、`build_energy_plan`、`main` を残す。

必須テスト:

```powershell
python -m pytest -q tests/test_energy_model.py tests/test_energy_model_runtime.py tests/test_energy_plan_document.py tests/test_energy_plan_forecast.py tests/test_energy_plan_historical.py tests/test_energy_plan_output.py tests/test_energy_plan_settings.py tests/test_soc_cost_optimizer.py tests/test_soc_optimization_request.py
python -m mypy app/energy_plan --no-incremental
```

`requests.get`、private stage関数をmonkeypatchするテストがある。移動前後でpatch対象だけを変更し、fixtureや期待値を変更してはならない。

### M-2: Dashboard data

現在の `app/dashboard/data.py` は約1,917行である。次の順序で分割する。

1. `app/dashboard/schedule.py`: schedule event候補、優先順位、最新schedule組立て。
2. `app/dashboard/warnings.py`: dashboard warning組立て。
3. `app/dashboard/sqlite_repository.py`: SQLite専用queryとrepository。
4. `app/dashboard/postgres_repository.py`: PostgreSQL専用queryとrepository。
5. `app/dashboard/firestore_repository.py`: Firestore専用readとrepository。
6. `data.py` には共通モデル変換、backend選択、公開 `load_dashboard_slice` と `load_dashboard_data` を残す。

backend固有queryを共通関数へ統合しない。各backendのtransaction・query・document契約が異なるためである。

```powershell
python -m pytest -q tests/test_dashboard_data.py tests/test_dashboard_server.py tests/test_dashboard_backend_parity.py tests/test_firestore_dashboard_metrics.py
python -m mypy app/dashboard --no-incremental
```

### M-3: KP-NET workflow

現在の `app/kpnet/workflow.py` は約1,889行である。次の順序で分割する。

1. `app/kpnet/plan.py`: `NightChargePlan` とplan読取・必須値検証。
2. `app/kpnet/profiles.py`: `ProfileOverrides`、固定/動的profile構築、時間ルール。
3. `app/kpnet/client.py`: `KpNetClient`、HTML解析、payload組立て、login/logout。
4. 監視CSV読込はPhase Dの `monitoring_history.py` を使う。
5. `workflow.py` にはCSV phase、settings phase、`run_kpnet_workflow`、`main` を残す。

```powershell
python -m pytest -q tests/test_kpnet_workflow.py tests/test_kpnet_settings_intent.py tests/test_external_site_access.py
python -m mypy app/kpnet --no-incremental
```

外部siteテストは通常skipのままでよい。環境変数を設定して実通信してはならない。

### M-4: Cloud Job

現在の `app/runtime/cloud_job.py` は約1,485行である。次の順序で分割する。

1. `app/runtime/plan_persistence.py`: planのFirestore保存・復元・monitor結果保存。
2. `app/runtime/soc_reading.py`: realtime/CSV SOC取得とfallback理由。
3. `app/runtime/forced_charge_monitor.py`: 時間・SOC・速度からの停止判断と純粋計算。
4. `cloud_job.py` には23時、3時、7時slotの順序、retry、terminal transition、`main` を残す。

`_monitor_partial_forced_and_stop` の一つのmonitor lifecycleと最終standbyは分断しない。内部の純粋計算とI/O adapterだけを先に抽出する。

```powershell
python -m pytest -q tests/test_cloud_job_runner.py tests/test_forced_charge_state_machine.py tests/test_forced_charge_settings.py
python -m mypy app/runtime app/forced_charge --no-incremental
```

### M-5: Forecasting

Energy Plan、Dashboard、KP-NET、Cloud Job完了後に行う。

1. `app/forecasting/correction.py`: provider/history I/Oと純粋補正計算を別モジュールにする。
2. `app/forecasting/pv_array.py`: Open-Meteo/Forecast.Solar adapter、calibration、candidate選択を別モジュールにする。
3. 公開関数と返却dictのキーを維持する。

```powershell
python -m pytest -q tests/test_forecasting_correction_compatibility.py tests/test_pv_array_forecast.py tests/test_forecasting_pv_array_compatibility.py tests/test_energy_model.py tests/test_external_site_access.py
python -m mypy app/forecasting --no-incremental
```

## 13. Phase C: 自動検査と文書索引

### C-1: pre-release検査を全体mypyへ更新

全体mypyが0件になった後だけ、`scripts/pre_release_local.ps1` のmypyコマンドを次へ置換する。

```powershell
python -m mypy app scripts --no-incremental
```

compileall、pytest、JavaScriptテスト、security checkの順序は変更しない。

コミット: `build: enforce full project mypy check`

### C-2: CI追加

`.github/workflows/quality.yml` を追加する。次だけを実行する。

1. checkout
2. Python 3.12 setup
3. Node setup
4. `python -m pip install -r requirements-dev.txt`
5. `pwsh -NoProfile -File scripts/pre_release_local.ps1 -SkipInstall`

secret、`.env`、GCP認証、Drive認証、KP-NET認証を設定しない。外部site testを有効にしない。

workflowファイルを追加しても、GitHub上での実行確認は外部状態である。local検査成功とYAML構造確認までをコミットし、実際のCI結果は別途確認する。

作成するworkflowは次の形から項目を増やさない。

```yaml
name: quality

on:
  push:
  pull_request:

jobs:
  quality:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
      - name: Install development dependencies
        run: python -m pip install -r requirements-dev.txt
      - name: Run local quality gate
        shell: pwsh
        run: pwsh -NoProfile -File scripts/pre_release_local.ps1 -SkipInstall
```

`RUN_EXTERNAL_SITE_TESTS`、credential、secretをworkflowへ追加してはならない。

コミット: `ci: run local quality gate`

### C-3: 文書索引更新

`docs/README.md` のCurrent Referencesに、この指示書を追加する。実行完了後は次の3ファイルを `docs/completed/refactor/` 配下の一つの完了フォルダへ移し、Completed Workへ追加する。

- この指示書
- 進捗ログ
- 最終監査結果

実行中は `docs/current/architecture/` に残す。

## 14. 最終検証 PF-1

```powershell
python -m compileall -q app scripts cloud_job_runner.py dashboard_server.py db_pipeline_main.py energy_model_main.py kpnet_main.py main.py sheets_export_main.py
python -m pytest -q
node tests/test_dashboard_calculations.js
node tests/test_dashboard_modules.js
node tests/test_dashboard_bootstrap.js
python -m mypy app scripts --no-incremental
python scripts/security_check.py
git diff --check
git status --short
```

期待する結果:

- Pythonテストは失敗0件。外部site testの1件skipは許可する。
- JavaScriptテストはすべて成功。
- mypyは0エラー。
- security checkは成功。
- diff checkは成功。
- 最終コミット後のstatusは空。

## 15. 最終監査 PF-2

`readable-code-audit` を `app`、`scripts`、`tests`、PowerShell、YAMLへ再適用する。次を全件確認する。

- `TOOL-01`: 検査警告を隠していない。
- `STRUCT-01`: 移動後の各モジュールが一つの責務を持つ。
- `STRUCT-03`: 正規所有先への内部importになっている。
- `STRUCT-04`: 長い関数を機械的に分割していない。残す場合は具体的skip理由がある。
- `DUP-01`: 同期すべきCSV解析が一箇所である。意味の違う推定式は区別されている。
- `TEST-02`: テストがprivate配置ではなく契約または安全な実行順を検証している。

未処置項目があれば完了扱いにしない。各項目を新しい小カードにし、変更前テスト、修正、変更後テスト、コミットのサイクルを繰り返す。

## 16. Luna軽が停止して人間へ確認する条件

次の場合は推測して進めない。

- 外部APIやDB clientの実際の戻り型をテスト・コード・公式型情報から決められない。
- Optional値が欠けた場合に、エラーにするかfallbackするか仕様が分からない。
- SQL、Firestore document、料金、SOC、予測値の意味を変えないと型エラーが消えない。
- private関数のmonkeypatch先変更で、テストが何を保証しているか分からなくなった。
- 互換モジュールを削除したくなった。
- 実サービス接続なしでは検証できない。
- 作業ツリーにカード外の変更がある。
- 同じ失敗原因を3回修正してもテストまたはmypyが成功しない。

停止時は、実行コマンド、完全なエラー、変更済みファイル、最後に成功したコミットIDを進捗ログへ記録する。失敗を隠したコミットは作らない。

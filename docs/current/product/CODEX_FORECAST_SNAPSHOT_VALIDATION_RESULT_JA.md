# forecast snapshot Phase 0 検証結果

## 概要

`docs/current/product/CODEX_FORECAST_SNAPSHOT_VALIDATION_JA.md` の指示に従い、最新の`master`上でforecast snapshot契約を検証した。検証日は2026-08-26（Asia/Tokyo）。productionのforecast correction、PV physical model、SOC optimizer、充放電制御は変更していない。

総合判定は **PASS（Firestore/PostgreSQLを除くローカル検証範囲）**。同一planの再取込は冪等で、生成時刻またはforecast evidenceが変わったplanは別vintageとして保持され、cutoff後のforecastは選択されないことを確認した。

## 実施した品質・テスト

| 項目 | 結果 |
|---|---|
| `python -m ruff check .` | PASS |
| Import Linter | PASS（3 contracts kept / 0 broken） |
| focused tests | `8 passed` |
| full suite | `494 passed, 1 skipped` |
| `python -m py_compile app/operations/forecast_snapshot.py app/operations/workflow.py` | PASS |
| `python scripts/security_check.py` | PASS |

ty、deptry、Oxlint、tscは、この環境に実行可能なツールまたは対応する構成がないため未実行。今回のsnapshot変更に関するRuff、Import Linter、pytest、py_compileは合格している。

## 発見した不具合と修正

PR #7でsnapshot identityを`source_run_key`ではなくplan evidenceへ移行した後、workflowの3箇所とfocused testsに旧引数が残っていた。これにより以下の`TypeError`が発生していた。

```text
build_forecast_snapshot_rows() got an unexpected keyword argument 'source_run_key'
persist_forecast_snapshots() got an unexpected keyword argument 'source_run_key'
```

実施した最小修正:

- `app/operations/workflow.py` のSQLite、PostgreSQL、Firestore呼び出しから廃止済み`source_run_key`を削除。
- `tests/test_forecast_snapshot.py`をplanの`issued_at`差分で別vintageを検証するよう更新。
- `source_run_key`を復活させてidentityへ戻す変更は行っていない。これは「同一immutable planは同一identity」「生成時刻またはforecast内容が変われば新vintage」という現行設計と矛盾するためである。

修正コミットは既に`92f3342815d9701c80764b50e2c809c6801a7c6a`として`master`へ反映済みで、本報告書はその検証結果を記録する追加ファイルである。

## 実plan構造とsnapshot builder dry-run

ローカルの最新CSVから、出力先を`artifacts/phase0_forecast_snapshot_validation/`へ分離してplanを生成した。Sheetsのoccupancy schedule読込は権限不足でスキップされたが、plan生成とsnapshot evidence検証は完了した。認証情報、project/site ID、実家庭の発電値は報告書へ出力していない。

確認結果:

- top-level `generated_at`: UTC timezone-aware
- `forecast.hourly_weather`: 24時間
- `pv_array_forecast.hourly`: 24時間
- weather/PVのhour集合: 一致
- provider forecasts: 2系統
- snapshot rows: 24
- `issued_at_source`: `plan` 24件
- `lead_minutes`: 944〜2324分
- `quality_flags`: 0件
- physical-PV evidence coverage: 100%
- provider evidence coverage: 100%

各行について、`snapshot_id`、`forecast_run_id`、`issued_at`、`target_at`、`lead_minutes`、forecast PV、weather code、cloud cover、shortwave、temperature、humidity、dew point、wind、provider、PV model/source、physical PV、calibration factor、detail JSON、quality flagsのフィールドが生成されることを確認した。

既存の古いartifact planには`generated_at`がないものもあり、その場合は`issued_at_source=ingested_at_fallback`となる。これはfallback flagで識別されるため、古いplanをplan-issued vintageとして扱ってはいない。

## SQLiteコピー検証

対象は`artifacts/solar_monitor.db`のコピーのみであり、元DBには書き込んでいない。snapshot persistenceは検証用SQLiteコピーに対して実行した。

| 検証 | 結果 |
|---|---|
| 同一immutable plan 1回目 | 24行追加 |
| 同一immutable plan 2回目 | 0行追加 |
| `generated_at`を2時間変更したplan | 24行追加 |
| snapshot総数 | 48行 |
| distinct `forecast_run_id` | 2 |
| 同一target hourの最大vintage数 | 2 |
| `pv_provider` / `physical_pv_kwh` / `pv_forecast_detail_json` | 存在 |
| 既存`forecast_hourly`件数 | 1,984件で維持 |

旧Phase 0 v1 schemaからのmigration、追加列の再実行安全性、既存row保持は`tests/test_forecast_snapshot.py::test_sqlite_migrates_snapshot_table_created_by_phase0_v1`を含むfocused testで確認した。

さらに、CSV/settings identityを変えず、同じ`generated_at`のままPV forecast evidenceだけを変更したplanでも、24行+24行、distinct run 2件となった。pipeline run identifierだけに依存せず、forecast evidenceの変更を別vintageとして保持できる。

## cutoff leakage protection

同一target hourに以下の2 vintageを用意した。

- forecast A: 00:00 JST発行
- forecast B: 02:00 JST発行
- operational cutoff: 01:00 JST

`select_latest_snapshot_before(...)`の選択結果はA。cutoffを同値のUTC表現へ変更してもAであり、Bは除外された。

## latest-value contract

snapshotはappend-only evidence storeとして扱われ、既存のmutable `forecast_hourly`をlatest forecastとして維持する。

- `forecast_hourly`の既存件数はSQLiteコピー検証で維持。
- snapshot tableを既存consumerへ切り替えるコードは存在しない。
- dashboard、correction history、SOC optimizerの既存読み取り先を変更していない。
- snapshot保存失敗時に別のforecast algorithmへfallbackする変更は行っていない。

## Firestore / PostgreSQL

本検証ではproduction cloudへの書込みを禁止し、Firestore/PostgreSQLの実環境検証は実施していない。

- Firestore: NOT RUN（production接続なし）
- PostgreSQL: NOT RUN（ローカル検証環境なし）

Firestore adapterとPostgreSQL adapterのappend-only/idempotency処理はfocused testsのmock範囲で確認し、実環境での書込み結果とは区別した。

## 非機密の集計診断

- snapshot rows per forecast run: 24
- `issued_at_source`: `plan` 24件
- lead-time range: 944〜2324分
- quality flags: 0件
- physical-PV evidence coverage: 100%
- provider evidence coverage: 100%

## 残存リスクと次段階

- occupancy scheduleのSheets読込は認証scope不足でスキップされたため、occupancy依存のplan差分は別途確認が必要。
- ty/deptry/TypeScript系の診断は環境不足で未実行。
- Firestore/PostgreSQLの実環境での保存・再取込は未検証。
- 古いartifact planのingested-time fallbackは、prospective運用ではplan generated timeを必須にする運用確認が必要。

Phase 0のローカル検証範囲では、forecast issue/generated timeの保存、lead time、weather/shortwave、provider/physical-PV evidence、final PV forecast、idempotency、multi-vintage保持、cutoff leakage防止、latest contract互換を確認した。production adaptive correction modelの実装は行っていない。

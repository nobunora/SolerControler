# S08 forecast・parameters projection

依存: S07。関連gate: G05/G07。coverage: R03/R07/R09/R10/R11/R15、C02windows/C05trace/C08save-before-control/C09freshness/C12protected-vars/C14logs/C16gates。

## 入口・参照範囲

- S07の採用済みraw/PublishedPlan/archived_docを受け取る。control CAS成功確認がない候補のmutable更新は禁止。
- AIルーティング: generation binding/adapter raceはCodexレビュー、限定projection patchはLuna。新storage fieldや許可外symbolが必要ならLuna停止。
- [共通契約](../contracts.md)、[公開契約](../publication-contracts.md)、[gates](../gates.json)を正本にする。fulltrace/pack keyはデータ契約から参照し重複定義しない。
- CodebaseMemoryの `persist_forecast_only_plan → forecast_job.main`、`archive_plan_snapshot → _update_plan_read_models` をsource確認済みnavigationとして使う。

## 変更対象

| ファイル・完全修飾symbol | 限定差分 |
| --- | --- |
| [forecast_persistence.py](../../../../../app/operations/forecast_persistence.py) `app.operations.forecast_persistence.persist_forecast_only_plan` | 旧forecast-only互換wrapperの既定保存/control非更新を維持 |
| 同上 `app.operations.forecast_persistence.project_forecast_plan` **NEW** | 固定raw/planから24値とSOC/容量を投影、自身の世代CASでday一体更新。署名/例外は公開契約 |
| [firestore.py](../../../../../app/operations/firestore.py) `app.operations.firestore.upsert_model_parameters_from_plan` | 既存名/mean/variance/field解釈を保持する互換wrapper |
| 同上 `app.operations.firestore.project_model_parameters` **NEW** | 同rawからparameters、同projection版retryはsample加算0。署名/例外は公開契約 |
| [planner.py](../../../../../app/separated_runtime/planner.py) `app.separated_runtime.planner.main` | control成功後だけforecast/plan read model/parametersへ進みassetへ引継ぐ |
| [plan_snapshot.py](../../../../../app/backup/plan_snapshot.py) `app.backup.plan_snapshot.project_plan_read_models` **NEW** | S07の既存helper再利用境界を本段で結線 |

## 変更禁止

| symbol・実source anchor | 変数/保持内容 |
| --- | --- |
| `forecast_persistence._complete_hourly_rows`: `# HISTORICAL_FAILURE_LOCK: validate values before replacing any saved vintage.` | `rows/target_date/value`、`len(rows) == 24` と `set(range(24))`、非有限/負値拒否を維持 |
| `domain.extract_hourly_forecast_from_plan`: `# HISTORICAL_FAILURE_LOCK: missing forecasts must not become valid zero rows.` | `pv_by_hour/load_by_hour/pv_start/pv_kwh/load_kwh`、declared窓外night0と窓内欠損を区別。source編集対象外 |
| `persist_forecast_only_plan`: `planned_capacity_kwh = to_float(plan_result.get("effective_capacity_kwh"))` | `planned_target_soc_percent/planned_capacity_kwh/planned_hourly_soc_percent/hourly_rows`、同原本PV/load/capacityで既存SOC再計算 |
| `firestore.upsert_model_parameters_from_plan`: `variance = abs(mean) * 0.05` | `coeff/name/mean/variance/sample_count`、既存parameter名/分散意味保持。`sample_count = int(prev.get("sample_count") or 0) + 1` を同版retryへ無条件転用しない |
| `forecast_snapshot.persist_forecast_snapshots`: `raw = json.loads(night_plan_path.read_text(encoding="utf-8"))` | `night_plan_path/raw/rows`。現入口は再読する。固定run-private fileだけを渡し、共有mutable pathを再読させない。新rows API/署名変更は対象外 |
| `plan_snapshot._update_plan_read_models`: `update(firestore.transaction(max_attempts=1))` | `refs/previous/doc/raw`、既存単調helper/少数読書き維持。control pointerへのtransaction読取追加禁止 |

[保護文書](../../../agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md)の10/6予想欠落・SQLite由来脱落・同vintage契約を守る。`sync.TABLE_SPECS/sqlite_repository.load_sqlite_query_snapshot/forecast_recovery`のlockを変更しない。

## クラス・変数差分

| 所在 | 変数差分 | 型・意味 |
| --- | --- | --- |
| NEW `project_forecast_plan` | `raw/plan/private_plan_path/data/hourly_rows/snapshot_rows/forecast_run_id/forecast_issued_at` | 同bytes由来、run IDとcontrol versionは別物。必須Pathはrun-private単一所有。新class不要 |
| 同上 | 既存 `planned_target_soc_percent/planned_capacity_kwh/planned_hourly_soc_percent` | 目標/容量と07起点SOC17を同rawから取り出す。金融/容量式を変えない |
| NEW `project_model_parameters` | `raw/plan/coeff/ref/prev/sample_count` | 承認済み内部4field `target_date/generated_at/control_version_id/raw_plan_sha256`を既存docへtransaction保存。source-map/legacy移行は公開契約 |
| `planner.main` | S07の `raw/plan/archived_doc`を再利用 | projectionごとにpath再読しない。遅い旧runがglobal latest/parametersを後退させない |

## 実装手順・分岐

1. G05のprotected限定承認とG07のadapter binding定義を確認する。binding未確定なら対象patch停止。確定後の限定ローカル実装/fixture準備は可能で、実装後adapter実証未了中はG07 OPENのまま採用/handoff PASS停止。現行forecast batchはdelete/setで自身の世代preconditionがない。
2. candidateのraw/hash/target/generated_atを検証し24値を抽出。snapshotは当時生成時刻を保持し、後日の再計算へ置換しない。
3. snapshotは既存path型入口を維持。生成後不変/単一所有の `private_plan_path` を渡す前後にraw bytes/hash一致を検証する。別runが触れる共有pathしかない場合はG07停止。前後hash検証を排他の代用にせず、private単一所有のfixtureも試験する。
4. projection自身のtarget/生成時刻/hash/版をtransaction/CAS内で読む。同時刻同版は冪等、異版/古候補は更新0。REST currentDocumentを別doc pointer検証へ使わない。
5. forecast hourly/day/SOC/capacityを一世代で投影。night_charge_plansには既存monotonic helperを使う。別storage間を一transactionと呼ばない。
6. parametersは同版sample加算0、別採用版だけ既存解釈の更新。対象日が古い候補はglobal parametersへ書かない。PV thetaを既存coeffへ混ぜない。
7. 障害は `plan_projection_failed` に採用版/stageを記録、controlを取消/再生成/再適用せず、同raw/versionで非制御再試行。S09へ整合した投影証拠だけ渡す。

## テスト・PASS条件

実行前にcode-quality-audit/必須gate。NEW試験追加済み・ローカル実行済み。実SDK protobuf/request検証をserver競合証明と分ける。

| ID・状態 | fixture/条件 | command・厳密PASS |
| --- | --- | --- |
| PROJECTION-01 EXISTING | 欠損/NaN/日付違い/完全日、原本容量とSOC、古日recovery | `python -m pytest tests/test_forecast_history_contract.py tests/test_dashboard_history_contract.py -q`。無効入力write0、24値/vintage/parity保持、31日/recovery順序維持 |
| PROJECTION-02 EXISTING | latest/day古retry、transaction abort、同raw | `python -m pytest tests/test_plan_snapshot_recovery.py -q`。既存helper単調・hash・初回由来・旧wrapper互換PASS |
| PROJECTION-03 NEW LOCAL `test_forecast_projection_generation.py` | old/new同日、同時刻異版、拒否候補、採用後故障、private改竄/shared path | `python -m pytest tests/test_forecast_projection_generation.py -q`。候補mutable0、後退0、同版冪等、24値/SOC/capacity一致、shared path拒否。実crash recoveryは未実証 |
| PROJECTION-04 NEW LOCAL 同ファイル | parameters同版/古日、legacy初回、standalone→採用→wrapper retry | 同上。count5→6→6、同raw採用sample増分0、古版0、mean/variance同値、control再publish0 |
| PROJECTION-05 NEW PARTIAL 同ファイル | 実Firestore SDK local RPC transport、transaction protobuf/ID | 同上。26writeを一transactionへ束ねcontrol pointer読取0。emulator未用意、server同時writer/SDK全transaction deadlineはINCONCLUSIVE |

## 中断条件

- 原本/identityを束ねられない、snapshot private raw保存不一致。実adapter競合試験が未実施ならG07 OPENで導入PASSを停止し、承認済みローカル試験は進める。
- 新collection/manifest/marker field外部契約を未承認追加する必要、許可外backend/SOC/過去readerの変更、control pointerロックが必要。
- retryでsample_count増加、候補mutable書込、PV/SOC異世代、古target上書き、旧wrapper回帰失敗。

## 出口・引継ぎ

採用版とforecast run/原本hash/target/生成時刻、実adapter競合/再試行証拠をS09へ渡す。最小handoffはproducerが検証可能な投影識別の確認。未確定field bindingはG07 INCONCLUSIVEとして必要symbolだけ返す。

# KP-NET 実績データ差分同期 詳細実装書

## 1. この文書の役割

本書は [KP-NET 実績データ差分同期 仕様書](./KPNET_INCREMENTAL_MONITORING_SYNC_SPEC_JA.md) を実装へ落とすための詳細設計である。

Codex は本書を実装指示として扱う。

本PRは仕様確定用であり、仕様PR作成時点では production code を変更しない。
CodexがこのPR branchへ実装を追加する場合は、以下の順序・境界・停止条件を守ること。

## 2. 実装前の必須手順

### 2.1 AGENTS.md

最初に `AGENTS.md` を読み、特に以下を守る。

- CodebaseMemory first
- Performance-Sensitive Read Paths
- Historical Failure Protected Regions
- Human approval gate
- code-quality-audit before tests

### 2.2 CodebaseMemory

実装を考える前に、以下のsymbolsと1 hopのcaller/callee/testを確認する。

- `app.kpnet.csv_visualization._default_csv_target_months`
- `app.kpnet.csv_visualization._resolve_months`
- `app.kpnet.workflow._run_csv_phase`
- `app.operations.monitoring_csv.iter_monitoring_points`
- `app.operations.domain.iter_monitoring_rows`
- `app.operations.firestore.ingest_monitoring_csvs`
- `app.operations.firestore.recalc_dashboard_daily_metrics`
- `app.operations.firestore.recalc_battery_pv_charge_end_soc`
- `app.operations.firestore.recalc_cost_daily`
- `app.operations.sqlite.ingest_monitoring_csvs`
- `app.operations.sqlite.recalc_battery_pv_charge_end_soc`
- `app.operations.sqlite.recalc_cost_daily`
- `app.operations.postgres.ingest_monitoring_csvs`
- `app.operations.postgres.recalc_battery_pv_charge_end_soc`
- `app.operations.postgres.recalc_cost_daily`
- `app.operations.workflow._ingest_firestore`
- `app.operations.workflow._ingest_sqlite`
- `app.operations.workflow._ingest_postgres`
- `app.operations.workflow._refresh_dashboard_snapshots`

CodebaseMemory が利用不能なら、その事実をPRコメントへ記録し、上記symbolを `rg` とfocused readsで確認してから実装する。

## 3. 設計方針

### 3.1 共通ロジックとadapterを分離

以下はDB非依存の共通ロジックにする。

- JST直近4日の計算
- 4日が属する年月集合
- CSV行のdedupe
- payloadの意味的一致判定
- INSERT / UPDATE / UNCHANGED判定用データ構造
- changed timestamp → affected dates変換
- stats集計

以下はbackend adapterに残す。

- recent existing rowsのquery
- INSERT / UPDATE write
- daily aggregate source row query
- cost baseline query
- aggregate document write

### 3.2 既存のbackend境界を潰さない

SQLite / PostgreSQL / Firestore を、似ているという理由だけで1つの巨大adapterへ統合しない。

driver-specific SQL / Firestore SDKは各backend moduleに残す。

### 3.3 通常経路とfull rebuildを明確に分離

通常CSV同期から全履歴scanを除去する。

もし既存のfull-scan関数を互換性のため残す場合は:

- 通常 `_ingest_*` から呼ばない。
- 名前またはdocstringで full rebuild / compatibility path と明確化する。
- 回帰テストで通常pipelineが呼ばないことを固定する。

## 4. 推奨ファイル構成

### 4.1 新規: `app/operations/monitoring_sync.py`

DB非依存の同期domain helperを置く。

推奨データ型:

```python
@dataclass(frozen=True)
class MonitoringSyncWindow:
    start_date: date
    end_date: date
    months: tuple[str, ...]

@dataclass(frozen=True)
class MonitoringSyncStats:
    rows_seen: int
    rows_in_window: int
    inserted: int
    updated: int
    unchanged: int
    csv_duplicate_same: int
    csv_duplicate_conflict: int
    changed_timestamps: tuple[str, ...]
    calendar_dates: tuple[str, ...]
    dashboard_affected_dates: tuple[str, ...]
```

必要なら `MonitoringDedupResult` / `MonitoringChange` を追加してよい。

名前は既存命名へ合わせて多少変更してよいが、責務は分ける。

### 4.2 既存: `app/operations/monitoring_csv.py`

CSVのlow-level parse責務を維持する。

ここへDB照合ロジックを入れない。

### 4.3 既存: `app/domain/monitoring.py`

`MonitoringPoint` とstorage row表現を維持する。

一意性比較のために共通payload helperが必要なら、domain dependency方向を壊さない範囲で置く。

Import Linter契約に反するなら `operations/monitoring_sync.py` 側へ置く。

## 5. KP-NET対象月の変更

### 5.1 現行

`_default_csv_target_months()` は常に前月＋当月を返す。

### 5.2 新仕様

通常実行では、JST直近4暦日が属する月だけを返す。`KP_CSV_TARGET_MONTHS` が明示されている場合は取得月指定として維持するが、DB同期の4日window解除は `DATA_MONITORING_FULL_BACKFILL=true` のときだけ行う。

例:

```text
2026-09-20
=> ["2026-09"]

2026-10-01
=> ["2026-09", "2026-10"]

2026-10-03
=> ["2026-09", "2026-10"]

2026-10-04
=> ["2026-10"]
```

### 5.3 実装箇所

第一候補:

- `app/kpnet/csv_visualization.py::_default_csv_target_months`
- `tests/test_kpnet_workflow.py`

現在のhelper名を維持して中身を4日windowへ変えてよい。

別helperを作る場合は、互換wrapperを残す必要性をCodebaseMemory/テストから判断する。

### 5.4 `include_latest`

`_resolve_months()` の `include_latest` が、4日window外の不要月を追加しないよう注意する。

期待する最終target monthsは:

```text
explicit requested months
または
4日window months
```

を基準とする。

単に `available` の最新月を追加して、4日window外の月が増える設計は禁止。

明示月指定とfull backfillを混同しない。full backfillは `DATA_MONITORING_FULL_BACKFILL=true` を必要とし、既定値はfalseとする。

## 6. source rowsの準備

### 6.1 複数月CSV

必要月が2つある場合は、両CSVのrowsを同一logical streamとして扱う。

### 6.2 window filter

parse後、通常syncでは:

```text
today-3 00:00 <= ts < tomorrow 00:00
```

のみ残す。

timezone基準は Asia/Tokyo。

CSVのtimestampがnaive local datetimeである現行契約を壊さない。

### 6.3 sort

dedupe後のsource rowsはtimestamp降順にできる。

ただし4日window内で「既存が見つかったから停止」はしない。

## 7. CSV dedupe実装

### 7.1 canonical comparison tuple

以下を比較tupleとする。

```text
ts
pv_kwh
load_kwh
sell_kwh
buy_kwh
charge_kwh
discharge_kwh
soc_percent
```

floatの勝手な丸めは追加しない。

parserから得られる既存値をそのまま意味比較する。

### 7.2 same duplicate

同じ `ts` に同じcomparison tuple:

- 1 rowにcollapse
- `csv_duplicate_same += duplicate_count`

### 7.3 conflict duplicate

同じ `ts` に異なるcomparison tuple:

- canonical rowへ自動決定しない
- conflicted timestamp setへ追加
- DB write対象から除外
- stats/logへ記録

## 8. existing rowsのbounded load

### 8.1 Firestore

4日windowだけを取得する。

禁止:

```python
client.collection("monitoring_samples").stream()
```

第一候補は `ts` range query。

必要なら document-id based query / batched gets を選択してよいが:

- 全collection scan禁止
- query boundsがテストで観測可能
- total history sizeに依存しない

こと。

取得後は:

```python
existing_by_ts: dict[str, dict]
```

にする。

### 8.2 SQLite

```sql
SELECT ...
FROM monitoring_samples
WHERE ts >= ? AND ts < ?
```

### 8.3 PostgreSQL

```sql
SELECT ...
FROM monitoring_samples
WHERE ts >= %s AND ts < %s
```

## 9. change classification

共通ロジックで:

```text
source ts not in existing
=> INSERT

source ts in existing and monitoring payload differs
=> UPDATE

source ts in existing and monitoring payload same
=> UNCHANGED
```

metadataは比較しない。

## 10. write実装

### 10.1 Firestore

INSERT/UPDATEだけbatch writeする。

unchanged rowへ `set()` しない。

payloadには変更があった場合のみ:

- source monitoring values
- `source_csv`
- `ingested_at`

を保存する。

### 10.2 SQLite / PostgreSQL

現在の全row UPSERT loopを、changesだけのUPSERTへ変更する。

### 10.3 return value

従来 `ingest_monitoring_csvs()` がintを返している箇所を確認する。

推奨:

- internal新APIはstructured resultを返す。
- compatibilityが必要ならwrapperでintを返す。
- `pipeline_runs.csv_rows_upserted` には **実際にINSERT/UPDATEした行数** を保存する。

`unchanged` をupsertedとして数えない。

DB schema追加は、ログだけで要件を満たせるなら避ける。

## 11. affected dates算出

INSERT / UPDATEされた各timestamp Tについて:

```text
calendar date(T)
```

を追加する。

さらに local time が:

```text
23:00 <= time < 24:00
```

なら翌日を `dashboard_affected_dates` に追加する。

理由: `review_night_charge_kwh` が翌日所属になるため。

## 12. bounded daily materialization

### 12.1 推奨新API

Firestoreについて、例えば:

```python
recalc_monitoring_daily_metrics(
    client,
    *,
    calendar_dates: set[str],
    dashboard_affected_dates: set[str],
    updated_at: str,
) -> MonitoringDailyMaterializeResult
```

のような bounded APIを追加する。

正確な名前は既存styleに合わせてよい。

### 12.2 query window

日 D の dashboard metricsに必要なのは:

```text
D-1 23:00
～
D+1 00:00
```

この中から:

- Dの日次actual
- D-1 23:00～D 07:00 のreview night charge

を計算する。

複数affected datesが連続する場合、query rangeをcoalesceしてもよい。

### 12.3 同一readの再利用

同一bounded rowsから可能な限り:

- dashboard_daily_metrics
- battery_daily_metrics.pv_charge_end_*

を計算する。

同じ日のsource rowsを2回Firestoreから取らない。

### 12.4 missing valueの扱い

現行metrics semanticsを変更しない。

特に:

- morning_soc_percent
- soc_min/max
- day_soc_max
- first_sample_at
- latest_sample_at
- review_night_charge_kwh
- day/night buy
- sample_count

は既存テスト結果と一致させる。

## 13. cost_daily bounded recompute

### 13.1 start

```text
earliest_changed_date
→ その月の1日
```

を `cost_recalc_start` とする。

### 13.2 end

```text
tomorrow 00:00 JST
```

までのmonitoring rowsをbounded queryする。

future rowsは対象にしない。

### 13.3 baseline

`cost_recalc_start` より前の直近日次 `cost_daily` から:

- cumulative_kwh
- cumulative_yen

を取得する。

存在しなければ0。

### 13.4 calculate_daily_costs

`calculate_daily_costs()` の月内tier計算はそのまま利用する。

必要なら以下のどちらかでglobal cumulative offsetを適用する。

A. optional initial cumulative引数を追加する。
B. 結果生成後にinternal helperでbaselineを加算する。

既存callersを壊しにくい方を選ぶ。

### 13.5 tiered料金

月初からrowsを渡すことで:

- actual day cumulative tier
- counterfactual day cumulative tier

を正しく再構築する。

変更日だけを単独でtier計算してはいけない。

### 13.6 writes

再計算範囲の `cost_daily` だけを更新する。

全history `cost_daily` を再writeしない。

## 14. workflow統合

対象:

- `_ingest_sqlite`
- `_ingest_postgres`
- `_ingest_firestore`

### 14.1 CSVあり

推奨フロー:

```text
collect csv paths
  ↓
incremental monitoring sync
  ↓
sync_result
  ↓
if changed:
    bounded daily materialization
    bounded cost recalculation
else:
    monitoring-derived recalculation skip
```

### 14.2 CSVなし

settings / night-plan only runの既存挙動を変えない。

### 14.3 actual-only import

`DATA_PIPELINE_INCLUDE_NIGHT_PLAN=false` の契約を維持する。

monitoring syncのためにnight planを読み書きしない。

### 14.4 hit rate

`recalc_model_hit_rates()` は monitoring_samples 由来ではない。

本タスクでアルゴリズムを変更しない。

ただし actual-only no-change pathで呼ぶ必要がないことを証明できる場合に限り、明確な既存dependency確認とテストを伴ってskipしてよい。

根拠がなければスコープを広げない。

## 15. snapshot更新

### 15.1 Firestore actual-only

monitoring INSERT/UPDATE > 0:

- bootstrap snapshot更新
- historyは更新しない

monitoring change = 0 かつ他入力change = 0:

- snapshot再生成をskip

### 15.2 scheduled 23/03/07

PR #47で確立した以下を維持する。

- 23: bootstrap + full history
- 03/07: bootstrap only
- forecast: bootstrap only

このタスクのためにhistory scheduleを変えない。

## 16. pipeline_run semantics

### 16.1 already ingested same run_key

現行のidempotencyを維持する。

同じrun directoryの再処理で監視writeを再発生させない。

### 16.2 new run, same data

run_keyが新しくても、実績payloadが同じなら:

```text
monitoring_samples writes = 0
```

にする。

これが今回の主要契約。

## 17. 重複監査ツール

### 17.1 新規候補

```text
scripts/audit_monitoring_duplicates.py
```

または既存script namingに合わせた名前。

### 17.2 default

defaultはdry-run。

### 17.3 scan

このツールだけは明示的なfull auditなので全履歴scan可。

通常pipelineから呼ばない。

### 17.4 report

最低限:

- total docs
- canonical
- duplicate_same groups/docs
- duplicate_conflict groups/docs
- id_payload_mismatch
- invalid timestamps

### 17.5 apply

`--apply` は exact duplicate かつ canonical documentを一意に決められるものだけ削除。

conflictは削除しない。

### 17.6 production protection

deploy時、自動で `--apply` を実行しない。

Scheduler/Cloud Run Jobへ自動登録しない。

本番applyはユーザーの明示承認を別途必要とする。

## 18. Firestore legacy timestamp注意

現行テストにはoffset付きtimestampとnaive timestampの双方が存在する。

実装前にproduction保存形式をCodebaseMemory/source/runtime evidenceで確認する。

仕様PRだけを根拠に、全既存document IDを一括renameしない。

新しいCSV由来rowは既存parser `MonitoringPoint.as_storage_row()` のtimestamp形式を維持する。

legacy normalizationは重複監査ツールの責務とし、通常syncの暗黙migrationにしない。

## 19. ログ

workflow log例:

```text
[monitoring_sync] reconcile_days=4 source_months=2026-09
[monitoring_sync] csv_rows=960 window_rows=192 duplicate_same=0 duplicate_conflict=0
[monitoring_sync] inserted=4 updated=1 unchanged=187
[monitoring_sync] calendar_dates=2026-09-18,2026-09-20 dashboard_affected_dates=2026-09-18,2026-09-20
[monitoring_sync] cost_recalc_start=2026-09-01
```

絶対ローカルパスやcredentialを出さない。

## 20. 実装順序

Codexは以下の小さい単位で進める。

1. tests: 4-day month selection
2. code: month selection
3. tests: dedupe/equality/change classification
4. code: shared monitoring_sync domain
5. tests: backend bounded existing-row reads / unchanged no-write
6. code: backend incremental writes
7. tests: affected dates / previous-day 23:00 night review
8. code: bounded daily materialization
9. tests: bounded cost recompute / cumulative baseline / tiered month
10. code: bounded cost recompute
11. tests: workflow no-change/change integration
12. code: workflow integration
13. tests: duplicate audit dry-run/apply safety
14. code: audit tool
15. focused regression tests
16. full code-quality-audit
17. full pytest/pre-release checks required by AGENTS

各段階で失敗原因が直前変更と無関係なら、勝手なcleanupへ広げない。

## 21. 禁止事項

- Schedulerを追加しない。
- hourly refreshを実装しない。
- dashboard refresh buttonを実装しない。
- device control codeを変更しない。
- protected historical failure regionを変更しない。
- `monitoring_samples` 全履歴scanを通常syncのfallbackとして残さない。
- sourceに無いrowを削除しない。
- conflict duplicateを勝手に最後のrowで解決しない。
- production duplicate cleanupを自動実行しない。
- production deploy / mergeをユーザー承認なしで行わない。

## 22. 変更が予想されるファイル

実装前調査後に最小化すること。

主候補:

- `app/kpnet/csv_visualization.py`
- `app/kpnet/config.py`（必要な場合のみ）
- `app/operations/monitoring_sync.py`（新規候補）
- `app/operations/firestore.py`
- `app/operations/sqlite.py`
- `app/operations/postgres.py`
- `app/operations/workflow.py`
- `app/operations/cost_daily.py`
- `scripts/audit_monitoring_duplicates.py`（新規候補）
- relevant tests

不要なdashboard JS/CSS、runtime control、deployment scriptは変更しない。

## 23. Codexの完了報告

PRコメントに以下を1回でまとめる。

- exact HEAD
- CodebaseMemory queries
- changed files
- before/after data path
- normal syncのFirestore query bounds
- monitoring writesのbefore/after
- no-change pathの確認
- maintenance delayed-fill testcase
- month-boundary testcase
- duplicate testcase
- cost tier testcase
- focused tests
- full quality gate
- residual risks
- productionで未実測の項目

production値を推測で「実測」と書かない。

runtimeでしか確認できない項目だけを残し、Web側レビューで直せる細かい事項をCodexへ何度も往復させない。

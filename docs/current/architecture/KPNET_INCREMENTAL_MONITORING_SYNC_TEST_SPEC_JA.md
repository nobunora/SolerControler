# KP-NET 実績データ差分同期 詳細テスト仕様書

## 1. 目的

本書は「KP-NET 実績データ差分同期 仕様書」と「詳細実装書」の受入条件を、再現可能なテストケースとして固定する。

このタスクでは、集計結果だけでなく次も correctness として検査する。

- read 範囲
- write 件数
- full scan 不在
- KP-NET 対象月
- delayed fill
- duplicate handling
- no-change idempotency
- aggregate 再計算範囲
- cost cumulative correctness
- control 非影響

## 2. テスト原則

### 2.1 外部アクセス禁止

通常 pytest では KP-NET 実サイト、Firestore 実環境、GCS、Secret Manager、実機設定へアクセスしない。
fixture、fake adapter、保存済み入力を使う。

### 2.2 実装詳細ではなく契約を検査

特に次を固定する。

- query は必ず有界
- 通常 path で monitoring_samples の unbounded stream を呼ばない
- unchanged row へ write しない
- source 欠損で DELETE しない

### 2.3 backend parity

CSV の意味的一致判定は SQLite、PostgreSQL、Firestore で共通にする。
DB 固有 query/write だけ backend ごとにテストする。

## 3. テストファイル候補

Codex は既存配置に合わせて最小化してよい。

- tests/test_kpnet_workflow.py
- tests/test_firestore_operations.py
- tests/test_firestore_dashboard_metrics.py
- tests/test_db_pipeline_main.py
- tests/test_operations_cost_daily.py
- tests/test_monitoring_sync.py 新規候補
- tests/test_monitoring_duplicate_audit.py 新規候補
- SQLite/PostgreSQL backend 関連テスト
- tests/test_night_soc_protected_contract.py

## 4. Window / month selection

### T-WIN-001 月途中は当月だけ

Given: now = 2026-09-20 JST

Expected:
- reconcile dates = 09/17, 09/18, 09/19, 09/20
- target months = 2026-09
- 2026-08 を取得しない

### T-WIN-002 月初は前月＋当月

Given: now = 2026-10-01 JST

Expected:
- dates = 09/28, 09/29, 09/30, 10/01
- target months = 2026-09, 2026-10

### T-WIN-003 月3日も跨ぐ

Given: now = 2026-10-03 JST

Expected:
- dates = 09/30, 10/01, 10/02, 10/03
- target months = 2026-09, 2026-10

### T-WIN-004 月4日以降は当月のみ

Given: now = 2026-10-04 JST

Expected:
- dates = 10/01, 10/02, 10/03, 10/04
- target months = 2026-10

### T-WIN-005 年跨ぎ

Given: now = 2027-01-02 JST

Expected:
- target months = 2026-12, 2027-01

### T-WIN-006 explicit target months を維持

KP_CSV_TARGET_MONTHS を明示した場合、通常4日windowの resolver が勝手に削らない。
明示指定は backfill として処理可能であること。

### T-WIN-007 include_latest が window 外 month を追加しない

available months に不要な月が存在しても、通常syncで4日window外のmonthを追加しない。

## 5. CSV filter / dedupe

### T-CSV-001 window 外 row をDB照合しない

1か月分CSVでも、9/20実行時は9/16以前を change classification へ渡さない。

### T-CSV-002 timestamp 降順

dedupe 後の処理対象が最新から過去へ安定した順序になる。

### T-CSV-003 exact duplicate collapse

Input:
- 14:30 payload A
- 14:30 payload A

Expected:
- canonical rows = 1
- csv_duplicate_same = 1
- csv_duplicate_conflict = 0

### T-CSV-004 三重複

同一rowが3回なら canonical 1件、duplicate_same は余剰2件。

### T-CSV-005 conflict duplicate

Input:
- 14:30 pv=0.50
- 14:30 pv=0.55

Expected:
- timestamp はwrite候補から除外
- conflict count > 0
- conflict timestamp をdiagnosticに残す
- last-row-wins にしない

### T-CSV-006 conflict が他rowを止めない

14:30 が conflict でも 15:00 の正常rowは処理する。

### T-CSV-007 metadata はmeaningful equalityから除外

DBの source_csv / ingested_at だけが古く、monitoring実績値が同一なら UNCHANGED / write=0。

## 6. Change classification

### T-CHG-001 missing DB row は INSERT

sourceあり、DBなし。
Expected: inserted=1。

### T-CHG-002 same row は UNCHANGED

monitoring fieldsがすべて同一。
Expected: unchanged=1、write=0。

### T-CHG-003 one metric changed は UPDATE

例: soc_percent 62 -> 64。
Expected: updated=1、write=1。

### T-CHG-004 null to value は UPDATE

DB soc_percent=None、source=55。
Expected: UPDATE。

### T-CHG-005 value to null は UPDATE

有効なsource parse結果がnullなら意味的変更として扱う。

### T-CHG-006 source に無いDB row は保持

DBに14:00あり、maintenance中sourceに14:00なし。
Expected:
- delete=0
- existing row保持
- 0/nullで上書きしない

## 7. KP-NET delayed fill

### T-MNT-001 半日欠損を翌々日に補完

Initial DB:
- 9/18 09:30 exists
- 9/18 10:00～20:00 missing
- 9/18 20:30 exists

9/20 source に 10:00～20:00 が現れる。

Expected:
- missing rows だけ INSERT
- 09:30 / 20:30 の同一rowはwriteしない
- 9/18をaffected dateへ追加
- 9/18日次集計を再計算

### T-MNT-002 欠損中の同期で既存を削除しない

sourceに一部timestampが無くてもDB既存値を保持。

### T-MNT-003 4日目まで補完可能

today-3 の missing row が復活した場合 INSERT。

### T-MNT-004 5日以上前は通常sync対象外

today-4 以前は通常4日syncでDB照合しない。
明示backfillのみ。

### T-MNT-005 required source month unavailable

4日windowが前月を含むがKP-NET available monthsに前月がない。

Expected:
- 当月の取得可能分は処理可能
- DB deleteなし
- unavailable month diagnosticあり
- 0件実績としてaggregateを消さない

## 8. Firestore read/write bounds

### T-FS-001 recent rows は bounded query

fake Firestore で unbounded collection stream を呼ぶと AssertionError。
通常syncがrange queryだけで完了すること。

### T-FS-002 history size に依存しない

1年、2年の古い履歴をfakeに持たせても query bounds は直近4日のまま。

### T-FS-003 unchanged only は monitoring write 0

4日分すべて同一。
Expected: monitoring batch.set count = 0。

### T-FS-004 inserted + updated だけwrite

inserted=2、updated=1、unchanged=20 のとき monitoring writes=3。

### T-FS-005 metadata 差だけではwrite 0

source_csv / ingested_at 更新目的のwrite禁止。

### T-FS-006 batch limit 維持

明示backfillで450件超でもFirestore batch上限を超えない。

## 9. SQLite / PostgreSQL write semantics

### T-DB-001 SQLite same row no UPDATE

既存とsourceが同一ならUPSERTを発行しない、または実変更件数に数えない。

### T-DB-002 SQLite changed row only UPSERT

INSERT/UPDATE対象だけwrite。

### T-DB-003 PostgreSQL same row no UPDATE

SQLiteと同じmeaningful equality。

### T-DB-004 PostgreSQL changed row only UPSERT

driver差以外のsync resultがFirestoreと一致。

### T-DB-005 csv_rows_upserted

pipeline_runs.csv_rows_upserted は実際のINSERT+UPDATE件数。
unchangedは含めない。

## 10. Affected date calculation

### T-DATE-001 日中row

2026-09-20T14:30

Expected:
- calendar_dates = 09/20
- dashboard_affected_dates = 09/20

### T-DATE-002 23:00 row

2026-09-20T23:00

Expected:
- calendar_dates = 09/20
- dashboard_affected_dates = 09/20, 09/21

### T-DATE-003 23:30 も翌日影響

Expected: 09/20 と 09/21。

### T-DATE-004 22:30 は翌日影響なし

Expected: 09/20のみ。

## 11. dashboard_daily_metrics bounded recompute

### T-DASH-001 現行metrics一致

既存 test_dashboard_metrics_materialize_review_night_window_and_morning_soc と同fixtureで部分再計算後も次が一致する。

- review_night_charge_kwh
- day_buy_kwh
- night_buy_kwh
- morning_soc_percent
- day_soc_max_percent
- first_sample_at
- latest_sample_at
- sample_count

### T-DASH-002 previous day 23:00 を含む

D-1 23:00 charge=1、D 00:00 charge=2。
Expected: D の review_night_charge_kwh=3。

Dの00:00以降だけを読む誤実装では失敗するfixtureにする。

### T-DASH-003 D 23:00変更でD+1更新

D 23:00 charge訂正時:
- Dの日次doc
- D+1のreview-night
の両方を更新。

### T-DASH-004 unrelated day をwriteしない

9/20変更で9/10のdashboard_daily_metricsをwriteしない。

### T-DASH-005 no-change でmaterialization callなし

監視row変更0件ならmonitoring由来daily materializationを呼ばない。

## 12. PV charge end SOC

### T-PV-001 latest qualifying sample

同日:
- 12:00 pv>0 charge>0 soc=60
- 12:30 pv>0 charge>0 soc=63
- 13:00 pv=0 charge>0 soc=65

Expected:
- pv_charge_end_soc=63
- pv_charge_end_at=12:30

### T-PV-002 affected dayだけ

9/20変更で9/10のbattery_daily_metricsを更新しない。

### T-PV-003 same bounded read reuse

dashboard metricsとPV endのために同一日rangeを重複取得しないことをfake query call countで確認する。

別queryが必要なら、PRにread budgetと理由を記録し仕様更新を先に行う。

## 13. cost_daily

### T-COST-001 flat same result

既存 calculate_daily_costs のflat fixtureとbounded recompute結果が一致。

### T-COST-002 earliest changed month startからquery

earliest changed = 2026-09-18。

Expected:
- cost source read start = 2026-09-01
- それ以前の monitoring row を読まない

### T-COST-003 cumulative baseline

8/31 cost_daily:
- cumulative_kwh=100
- cumulative_yen=2000

9/1以降の再計算へbaselineが正しく加算される。

### T-COST-004 no baseline

過去cost_dailyが無い場合 baseline=0。

### T-COST-005 tiered month cumulative

9/18訂正がtier boundary前後へ影響するfixtureを作る。
9/18だけ単独計算した値ではなく、9/1からの月内累積で正しく計算されること。

### T-COST-006 month boundary

10/01実行で9/30が変更。
Expected: cost_recalc_start = 2026-09-01。
9月と10月の月内tier resetも正しい。

### T-COST-007 unrelated history scanなし

1年前のmonitoring dataへqueryを発行しない。

### T-COST-008 no-change はcost recalcなし

monitoring change=0なら bounded cost pathも呼ばない。

## 14. Workflow integration

### T-WF-001 actual-only insert

DATA_PIPELINE_INCLUDE_NIGHT_PLAN=false、sourceに新規rowあり。

Expected:
- monitoring INSERT
- bounded aggregates
- bootstrap update
- night plan ingestなし
- forecast persistなし

### T-WF-002 actual-only no-change

source全て同一。

Expected:
- monitoring write=0
- daily materialization=0
- cost recalc=0
- monitoring由来bootstrap rewrite=0
- night plan ingest=0

### T-WF-003 actual-only delayed fill

maintenance gapが埋まり inserted>0。
Expected: bootstrap更新。

### T-WF-004 same pipeline run_key

同じrunの再実行は従来idempotency維持。

### T-WF-005 new run_key / same payload

run directoryは新しいがsource payload同一。
Expected: monitoring write=0。
今回の主要回帰テスト。

### T-WF-006 settings-only

csv_run_dir=None、settingsあり。
monitoring sync追加によりsettings経路を壊さない。

### T-WF-007 slot23 snapshot contract

23時:
- bootstrap
- history full rebuild
を維持。

### T-WF-008 slot03/07 snapshot contract

03/07:
- bootstrap only
- history更新なし
を維持。

### T-WF-009 forecast snapshot contract

forecast:
- bootstrap only
を維持。

## 15. Duplicate audit tool

### T-AUD-001 default dry-run

引数なしではdelete/writeを一切しない。

### T-AUD-002 canonical only

正常docだけならduplicate_same=0、duplicate_conflict=0。

### T-AUD-003 id/payload mismatch

doc.id != payload.ts を検出。

### T-AUD-004 exact legacy duplicate

同一logical timestamp、同一monitoring payloadの2docs。

dry-run:
- duplicate_same分類
- delete=0

### T-AUD-005 apply exact duplicate

明示applyのfake backendでcanonical 1件を残し、余分なexact duplicateだけdelete。

### T-AUD-006 conflictはapplyでも削除しない

同一logical timestamp、異なるmetrics。
Expected:
- duplicate_conflict
- delete=0

### T-AUD-007 invalid timestamp

reportするが自動削除しない。

### T-AUD-008 production自動実行禁止

deploy script、scheduler config、normal workflowからaudit applyが呼ばれていない。

## 16. Performance regression contracts

### T-PERF-001 no unbounded Firestore monitoring stream

通常workflowで monitoring_samples のunbounded streamを呼んだらfail。

### T-PERF-002 unchanged 4-day sync write budget

全row同一:
- monitoring writes=0
- daily metric writes=0
- cost writes=0

### T-PERF-003 history-size independence

同じ直近4日fixtureに対して history 30日、365日、730日を模しても query bounds/call countが増えない。

### T-PERF-004 month fetch count

通常月途中:
- KP-NET CSV downloads = 1 month

月初4日window跨ぎ:
- downloads = 2 months max

明示backfillは除外。

### T-PERF-005 source absentで過去scanしない

source row不足を補うため5日目、1か月前、1年前へDB scanを広げない。

## 17. Safety / protected regression

### T-SAFE-001 night SOC protected contract

tests/test_night_soc_protected_contract.py を必ず実行し、skip/warning化しない。

### T-SAFE-002 device control files untouched

以下に意図しない変更なし:
- app/runtime/night_soc_time_contract.py
- protected 23/03/07 symbols
- forced/economy profile logic

### T-SAFE-003 no scheduler addition

hourly monitoring Schedulerを追加しない。

### T-SAFE-004 no IAM/secret expansion

dashboard/runtime service account、Secret権限を増やさない。

## 18. Backend parity

### T-PAR-001 identical source/DB fixture

SQLite/PostgreSQL/Firestoreで inserted、updated、unchanged、conflict、changed dates が一致。

### T-PAR-002 null semantics

Noneを含むmetricsでbackend間判定一致。

### T-PAR-003 timestamp identity

新規source rowの ts が各backendの一意キーとして保存される。

## 19. Existing regression tests

最低限:

- tests/test_kpnet_workflow.py
- tests/test_firestore_operations.py
- tests/test_firestore_dashboard_metrics.py
- tests/test_db_pipeline_main.py
- tests/test_operations_cost_daily.py
- tests/test_dashboard_snapshots.py
- tests/test_night_soc_protected_contract.py

CodebaseMemoryで直接関連が判明した既存testも追加する。

## 20. code-quality-audit

pytest前にAGENTS.mdどおり code-quality-audit Skillを実行する。

適用対象:
- Ruff lint
- repo skill指定のtype check
- deptry
- Oxlint
- tsc

baseline advisoryと新規failureを区別する。
Ruff formatterは実行しない。

## 21. Full verification

実装完了時:

1. focused tests
2. code-quality-audit
3. mandatory import contracts
4. full pytest
5. type check
6. security_check
7. repository pre-release checks

本番deployは行わない。

## 22. Production validation specification

利用者が別途production deployを明示承認した後だけ実施する。

### P-VAL-001 no-change import

同一実績再取得runで:
- monitoring writesが0または設計どおり最小
- full collection scanなし
- bootstrap不要再uploadなし

### P-VAL-002 normal new data

新規intervalがあるrunで:
- inserted件数
- updated件数
- affected dates
- bounded read scope
を記録。

### P-VAL-003 maintenance delayed fill

自然発生する欠損補完を待つ。
確認目的で本番データを削除・捏造しない。
補完時に過去4日内のmissing timestampがinsertされることを確認。

### P-VAL-004 Firestore reads/writes before/after

実測可能な:
- monitoring reads
- monitoring writes
- job duration
を比較。
推測値禁止。

### P-VAL-005 duplicate audit

最初はdry-runのみ。
production applyは別途明示承認まで実行しない。

## 23. 受入判定

### PASS

すべて満たす:
- window/month必須ケースpass
- CSV dedupe/change classification pass
- delayed fill pass
- backend no-write pass
- full-scan禁止pass
- affected date/night review pass
- bounded cost pass
- workflow contract pass
- duplicate audit safety pass
- protected control tests pass
- full quality gate pass

### FAIL

いずれか:
- unchanged rowを再write
- source欠損でDB delete
- 通常syncが4日より古いhistoryへscan拡大
- 月途中で不要な前月CSVを毎回取得
- cost tier semantics破壊
- review-night 23:00境界破壊
- control behavior変更
- conflict duplicateを無警告上書き
- production cleanup自動実行

### INCONCLUSIVE

production runtimeでしか証明できない事項だけを残す。
source/testで確認可能な問題をINCONCLUSIVEとして丸投げしない。

## 24. Codex報告フォーマット

PRコメントを1つにまとめる。

- Result: PASS / FAIL / INCONCLUSIVE
- exact HEAD
- changed files
- CodebaseMemory queries / relevant edges
- implemented: 4-day window / month selection / dedupe / incremental writes / bounded aggregates / bounded cost / audit tool
- focused tests
- full tests
- lint/type/security
- full monitoring scan present: yes/no
- unchanged monitoring writes
- normal target months
- query bounds
- control files changed: yes/no
- scheduler changed: yes/no
- IAM/secrets changed: yes/no
- production-only residual checks

何度も小分けで依頼を返さず、runtimeでしか確認できない事項だけを残す。

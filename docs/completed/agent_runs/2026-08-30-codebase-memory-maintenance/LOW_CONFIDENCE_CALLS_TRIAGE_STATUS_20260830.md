# Low-confidence CALLS triage status — 2026-08-30

> このファイルは `CODEX_RETRY_LOW_CONFIDENCE_CALLS_TRIAGE_JA.md` の完了判定用tracked statusである。
> Codexは再実行時に必ず更新する。PR #19の一部作業が終わっただけではCOMPLETEにしない。

## Status

```text
status: COMPLETE
```

Allowed values:

```text
NOT_STARTED
IN_PROGRESS
COMPLETE
PARTIAL
BLOCKED
```

---

## Baseline

```text
baseline_head: 6d04d49c0f42e7fdd23806b076d303963638122e
artifact_source_commit: 6d04d49c0f42e7fdd23806b076d303963638122e
artifact_nodes: 5901
artifact_edges: 18194
codebase_memory_version: 0.10.8
index_status: ready
parse_partial_count: 9
skipped_count: 0
index_generation: 2026-08-30T13:16:55Z
index_mode: full
coverage_note: best-effort; app/tests have no source parse gaps, scripts has 9 flagged files outside cited edge lines
```

---

## Current CALLS inventory

```text
current_calls_total: 4443
current_low_confidence_calls_total: 692
confidence_lt_0_25: 183
confidence_0_25_to_lt_0_5: 509
classified_count: 692
unprocessed_count: 0
```

### Resolution strategy counts

```text
suffix_match: 355
unique_name: 337
```

現行graphに存在するstrategyだけを書く。

---

## Classification counts

```text
source_fix_count: 0
graph_false_positive_count: 237
dynamic_external_api_count: 79
test_fake_count: 136
builtins_runtime_count: 240
intentional_dynamic_dispatch_count: 0
needs_more_evidence_count: 0
```

### Required checksum

```text
source_fix_count
+ graph_false_positive_count
+ dynamic_external_api_count
+ test_fake_count
+ builtins_runtime_count
+ intentional_dynamic_dispatch_count
+ needs_more_evidence_count
+ unprocessed_count
== current_low_confidence_calls_total
```

```text
checksum_status: PASS
```

COMPLETEには`checksum_status: PASS`かつ`unprocessed_count: 0`が必要。

---

## Source-fix candidates

全件を記録する。0件なら`none`と明記する。

### Candidate template

```text
candidate_id:
caller:
callee:
caller_file_line:
callee_file_line_or_external:
confidence:
resolution_strategy:
actual_source_expression:
source_evidence:
history_evidence:
test_evidence:
architecture_evidence:
recommended_minimal_patch:
recommended_tests:
risk:
recommended_next_pr:
```

```text
none. Source review found no verified production defect. Low-confidence edges are kept as follow-up leads only; no source-fix was implemented in this phase.
```

---

## Needs-more-evidence

全件を記録する。0件なら`none`。

```text
none. All 692 edges have a classification and source-line verification. No cited edge landed on a flagged parse-partial line.
```

各項目に、何が不足しているかと次に実行すべきexact actionを書く。

---

## Representative graph-noise evidence

最低でも各該当カテゴリから代表例を残す。

### graph-false-positive

```text
edge count: 237
subgroups: datetime.now -> app/runtime/cloud_job.py::_SystemMonitorClock.now = 44; local cloud-job lambda now = 2; parser.parse_args -> diagnose_hourly_pv_adaptive_gate.parse_args = 13; data/static/config identifier collisions = 15; direct local symbol calls with low resolver confidence = 163.
representative: ordinal 1, app/backup/artifacts.py:113, datetime.now -> app/runtime/cloud_job.py::_SystemMonitorClock.now, confidence 0.28, suffix_match.
representative: ordinal 343, app/runtime/cloud_job.py:168, _run_night_23 -> app/runtime/slot_orchestration.py::_run_night_23, confidence 0.38, unique_name; source is a readable direct local call and is not a source defect.
```

### dynamic-external-api

```text
edge count: 79
representative: ordinal 7, app/backup/drive.py:187, firestore_client.collection -> tests/test_drive_backup.py::_EmptyFirestoreClient.collection, confidence 0.14, suffix_match.
representative: ordinal 9, app/backup/drive.py:241, service.files()...execute -> tests/test_drive_backup.py::_FakeRequest.execute, confidence 0.28, suffix_match.
reason: production external SDK/DB fluent calls are resolved to SDK-shaped test doubles; source is retained.
```

### test-fake

```text
edge count: 136
representative: ordinal 29, app/backup/weekly.py:28, cursor.fetchall -> tests/test_postgres_operations.py::_Cursor.fetchall, confidence 0.38, unique_name.
representative: ordinal 56, app/dashboard/firestore_repository.py:517, rows -> tests/test_dashboard_calculations.js::rows, confidence 0.38, unique_name.
reason: pytest fakes, monkeypatch-shaped helpers, and test callers account for this resolution noise.
```

### builtins-runtime

```text
edge count: 240
representative: ordinal 2, app/backup/artifacts.py:128, str -> builtins.str, confidence 0.38, unique_name.
representative: ordinal 5, app/backup/drive.py:92, path.suffix.lower -> builtins.str.lower, confidence 0.38, unique_name.
reason: builtin/stdlib calls are valid runtime operations, not unused edges.
```

### intentional-dynamic-dispatch

```text
none observed in the low-confidence set. String dispatch and registry paths remain protected by the triage rules and were not rewritten.
```

---

## Export / query evidence

全edgeをどのように取得したか、再現可能な形で記録する。

機密情報・個人パス・credentialは書かない。

```text
schema/query discovery:
low-confidence filter:
pagination or partition strategy:
raw export location (local/non-tracked if applicable):
raw edge count:
```

```text
schema/query discovery: CodebaseMemory MCP get_graph_schema, index_status, check_index_coverage, get_architecture; CALLS properties confirmed as confidence, strategy, line, callee plus source/target qualified_name and file_path/start_line.
low-confidence filter: MATCH (s)-[e:CALLS]->(t) WHERE e.confidence < 0.5.
inventory queries: CALLS total; low-confidence total; confidence < 0.25; 0.25 <= confidence < 0.5; strategy grouping.
raw export method: stable source-file/line/callee/edge-id ordering from the shared graph artifact after MCP schema/count verification; each row includes the required fields plus source_line, classification, reason, verification_status.
pagination or partition strategy: single MCP query returned total=692 with max_rows=1000; local artifact export independently reproduced 692 unique rows.
raw export location (local/non-tracked if applicable): artifacts/codebase_memory/low_confidence_calls_20260830_classified.jsonl
raw edge count: 692
raw_export_record_count: 692
raw_export_dedupe_count: 692
raw_export_dedupe_key: caller_symbol + caller_file + caller_line + callee_symbol + callee_file_or_external + confidence + resolution_strategy
raw_export_sha256: 3bfb38328697f47d873fda1963b1ebc9d60012843c8564ebc59ed643d483f366
```

---

## Verification

```text
source_changed: NO
changed_files: docs/current/agent/LOW_CONFIDENCE_CALLS_TRIAGE_STATUS_20260830.md; generated .codebase-memory artifact refresh
code_quality_audit: PASS (applicable checks reviewed; no source remediation)
ruff: not applicable (no source change; repository rule requires lint-only when run)
ty: not applicable (no source change)
deptry: not applicable (no source/dependency change)
oxlint: not applicable (no JS source change)
tsc: not applicable (no TS source change)
focused_pytest: not applicable (classification-only; no behavior change)
import_linter: not applicable (no import-boundary change)
git_diff_check: PASS
```

`not applicable`の場合は理由を書く。

---

## Shared graph refresh

```text
refresh_needed: YES (active docs changed after the previous source snapshot)
source_bearing_commit: 6d04d49c0f42e7fdd23806b076d303963638122e
refresh_count: 1 (automatic current-state refresh; no artifact-only follow-up)
artifact_commit: 8dfb210428b656ee0db4f545140af033c3efa166
artifact_self_followup_refresh: NO
```

期待値:

```text
artifact_self_followup_refresh: NO
```

---

## Completion decision

### COMPLETE

以下をすべて満たす場合のみ:

- current total取得済み
- 全edge取得済み
- classified_countが全対象を覆う
- unprocessed_count=0
- checksum PASS
- source-fix全件evidenceあり
- needs-more-evidence全件evidenceあり
- source changeがあればquality/tests成功
- protected runtime誤変更なし
- graph confidence改善目的だけのsource rewriteなし

### PARTIAL

```text
unprocessed_count:
reason:
next_exact_action:
```

```text
unprocessed_count: 0; not applicable.
```

### BLOCKED

```text
blocker:
attempted_queries_or_commands:
obtained_data:
missing_capability:
next_exact_action:
```

```text
blocker: none
attempted_queries_or_commands: none; MCP recovered and returned ready/schema/count/export data.
obtained_data: complete
missing_capability: none
next_exact_action: none
```

---

## Final summary

```text
result: COMPLETE
low_confidence_total: 692
classified: 692
unprocessed: 0
source_fix: 0
next_prs: none; retain raw evidence for any future root-cause-specific PR
```

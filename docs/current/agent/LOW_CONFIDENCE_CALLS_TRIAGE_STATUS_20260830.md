# Low-confidence CALLS triage status — 2026-08-30

> このファイルは `CODEX_RETRY_LOW_CONFIDENCE_CALLS_TRIAGE_JA.md` の完了判定用tracked statusである。
> Codexは再実行時に必ず更新する。PR #19の一部作業が終わっただけではCOMPLETEにしない。

## Status

```text
status: NOT_STARTED
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
baseline_head: 52701f7766ba6dbdb5ed1cd1821e61b303c77635
artifact_source_commit: b3041bf264b2e5346350e292689e535ed906f78e
artifact_nodes: 5800
artifact_edges: 18093
codebase_memory_version: TO_FILL
index_status: TO_FILL
parse_partial_count: TO_FILL
skipped_count: TO_FILL
```

---

## Current CALLS inventory

```text
current_calls_total: TO_FILL
current_low_confidence_calls_total: TO_FILL
confidence_lt_0_25: TO_FILL
confidence_0_25_to_lt_0_5: TO_FILL
classified_count: TO_FILL
unprocessed_count: TO_FILL
```

### Resolution strategy counts

```text
TO_FILL
```

現行graphに存在するstrategyだけを書く。

---

## Classification counts

```text
source_fix_count: TO_FILL
graph_false_positive_count: TO_FILL
dynamic_external_api_count: TO_FILL
test_fake_count: TO_FILL
builtins_runtime_count: TO_FILL
intentional_dynamic_dispatch_count: TO_FILL
needs_more_evidence_count: TO_FILL
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
checksum_status: TO_FILL
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
TO_FILL
```

---

## Needs-more-evidence

全件を記録する。0件なら`none`。

```text
TO_FILL
```

各項目に、何が不足しているかと次に実行すべきexact actionを書く。

---

## Representative graph-noise evidence

最低でも各該当カテゴリから代表例を残す。

### graph-false-positive

```text
TO_FILL
```

### dynamic-external-api

```text
TO_FILL
```

### test-fake

```text
TO_FILL
```

### builtins-runtime

```text
TO_FILL
```

### intentional-dynamic-dispatch

```text
TO_FILL
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
TO_FILL
```

---

## Verification

```text
source_changed: TO_FILL
changed_files: TO_FILL
code_quality_audit: TO_FILL
ruff: TO_FILL
ty: TO_FILL
deptry: TO_FILL
oxlint: TO_FILL
tsc: TO_FILL
focused_pytest: TO_FILL
import_linter: TO_FILL
git_diff_check: TO_FILL
```

`not applicable`の場合は理由を書く。

---

## Shared graph refresh

```text
refresh_needed: TO_FILL
source_bearing_commit: TO_FILL
refresh_count: TO_FILL
artifact_commit: TO_FILL
artifact_self_followup_refresh: TO_FILL
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
TO_FILL
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
TO_FILL
```

---

## Final summary

```text
result: NOT_STARTED
low_confidence_total: TO_FILL
classified: TO_FILL
unprocessed: TO_FILL
source_fix: TO_FILL
next_prs: TO_FILL
```

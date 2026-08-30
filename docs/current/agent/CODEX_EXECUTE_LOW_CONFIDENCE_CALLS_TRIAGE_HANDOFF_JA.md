# Codex実行ハンドオフ: low-confidence CALLS 全件トリアージ

## 0. この文書の目的

この文書は、PR #20で追加された

- `CODEX_RETRY_LOW_CONFIDENCE_CALLS_TRIAGE_JA.md`
- `LOW_CONFIDENCE_CALLS_TRIAGE_STATUS_20260830.md`

を**実際に最後まで実行するためのハンドオフ**である。

PR #20は完了条件を定義したが、2026-08-30時点のmasterでは、その後にlow-confidence CALLS全件トリアージの実行コミットは存在しない。

現在確認済みのmasterは:

```text
HEAD = a49a28b20d969ac8e81123d424a55c6016a70c6c
Merge pull request #20 ...
```

tracked statusは現在:

```text
status: NOT_STARTED
```

であり、件数・分類・source-fix evidenceはすべて未記入である。

したがって、このフェーズは**未着手**として扱う。

---

## 1. 現在のshared graphはstaleとして扱う

現在の`.codebase-memory/artifact.json`は:

```text
commit = b3041bf264b2e5346350e292689e535ed906f78e
nodes = 5800
edges = 18093
```

を表す。

その後にPR #20で`docs/current/**`が変更されている。

`codebase_memory_shared_graph_usage_ja.md`の鮮度規則では、artifact source commit以降に`docs/current/**`のactive ruleが変わっている場合はstaleである。

よって、**`b3041bf...`を表すshared graphをそのままcurrent graphとして件数集計してはいけない。**

旧件数:

```text
692 / 240 / 215 / 237
```

もcurrent値として使用禁止である。

---

## 2. 重要な実行順の補足

PR #20の指示には「staleなら分類開始前にreindex」「artifact refreshは必要な場合だけ1回」とある。

今回、次の2種類を明確に区別する。

### A. pre-analysis local index sync

分類前にcurrent working stateをCodebaseMemoryへ反映するためのローカルindex更新。

- 分析の正確性のために必要
- この段階では`.codebase-memory/**`をcommitしない
- generated artifactを共有状態として確定しない

### B. final shared artifact refresh

分類結果/statusをcommitした**最終active-doc state**を表すための共有artifact生成。

- tracked status commit後に行う
- `.codebase-memory/**`の共有commitはこの1回だけ
- artifact commit自身を追う再refreshは禁止

つまり、必要ならCodebaseMemoryのindex処理自体は「分析前」と「最終共有化前」に実行され得るが、**共有artifact commitは最後の1回だけ**にする。

分析前のgenerated `.codebase-memory/**`を途中commitしてはいけない。

---

## 3. 作業開始前チェック

PowerShell 7を使用する。

最低限:

```powershell
git status --short
git rev-parse HEAD
git log -5 --oneline
Get-Content .codebase-memory/artifact.json
Get-Content docs/current/agent/LOW_CONFIDENCE_CALLS_TRIAGE_STATUS_20260830.md
```

確認条件:

```text
working tree: clean、または今回作業だけ
master/作業branch baseline: PR #20 merge以降の最新HEAD
status: NOT_STARTED
artifact project: C-VSC-SolerControler
```

HEADがこの文書記載の`a49a28b...`より進んでいる場合は、**実際のcurrent HEADをbaselineとして記録する**。

ハードコードされたSHAを優先しない。

---

## 4. 必読順

実行前に:

1. `AGENTS.md`
2. `docs/current/agent/codebase_memory_triage_and_maintenance_ja.md`
3. `docs/current/agent/codebase_memory_shared_graph_usage_ja.md`
4. `docs/current/agent/CODEX_RETRY_LOW_CONFIDENCE_CALLS_TRIAGE_JA.md`
5. `docs/current/agent/LOW_CONFIDENCE_CALLS_TRIAGE_STATUS_20260830.md`
6. この文書

を読む。

protected runtimeのsource-fix候補が出た場合のみ:

7. `docs/current/agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md`

を読む。

---

## 5. statusを開始状態へ更新する

作業開始時、tracked status fileのworking-tree内容を:

```text
status: IN_PROGRESS
```

へ更新する。

ただし、この段階ではstatusだけを独立commitしなくてよい。

目的は作業中断時に状態を失わないこと。

status fileには少なくともcurrent baseline HEADを先に書く。

```text
baseline_head: <actual current HEAD>
```

---

## 6. 分析前local index sync

shared artifactはPR #20の`docs/current`変更を含んでいないため、分類開始前にcurrent working stateをCodebaseMemoryへ反映する。

手順:

1. current projectを確認
2. index statusを確認
3. incremental/index refreshを実行
4. `status=ready`を確認
5. node/edge/version/partial/skippedを取得

必須記録:

```text
analysis_index_head_or_state:
codebase_memory_version:
index_status: ready
analysis_nodes:
analysis_edges:
parse_partial_count:
skipped_count:
```

この段階で生成された:

```text
.codebase-memory/artifact.json
.codebase-memory/graph.db.zst
```

は**まだcommitしない**。

status commitと共有artifact commitを混ぜない。

---

## 7. schema discoveryを先に行う

過去のproperty名を決め打ちしない。

current graphの`CALLS` edge sample/schemaから、最低限次を確定する。

```text
caller qualified name property
caller file property
caller line property
callee qualified name property
callee file/external property
callee line property if available
confidence property
resolution strategy property
edge type property
```

statusに:

```text
schema_discovery: PASS
schema_notes: ...
```

を記録する。

必要propertyが取得できない場合は、その時点で作業を止めず、取得可能fieldと欠損fieldを記録し、分類継続可否を判断する。

全edge同定が不可能なら最終statusは`BLOCKED`。

---

## 8. current inventoryを確定する

current graphから実測する。

必須:

```text
current_calls_total
current_low_confidence_calls_total
confidence_lt_0_25
confidence_0_25_to_lt_0_5
resolution strategyごとの件数
```

primary target:

```text
edge_type = CALLS
confidence < 0.5
```

現行schemaでconfidence表現が異なる場合は、等価条件を採用し、その根拠を書く。

ここで取得した`current_low_confidence_calls_total`を以後の唯一の母数にする。

過去reportの数字を足したり引いたりして現在値を推定しない。

---

## 9. 全edgeを漏れなくexportする

サンプル調査は禁止。

各edgeの最低record:

```text
ordinal_or_edge_id
caller_symbol
caller_file
caller_line
callee_symbol
callee_file_or_external
callee_line_if_available
confidence
resolution_strategy
```

取得順が不安定な場合は、caller file/line/callee/confidence等でstable sortしてordinalを付ける。

一括取得できない場合は:

1. confidence band
2. resolution strategy
3. caller package
4. pagination

の順に分割してよい。

重複edgeはdedupe keyを明示する。

推奨dedupe key:

```text
caller_symbol + caller_file + caller_line
+ callee_symbol + callee_file/external
+ confidence + resolution_strategy
```

raw edge listはGit管理外の一時artifactでよい。

ただしstatusに:

```text
raw_export_method:
raw_export_record_count:
raw_export_dedupe_count:
raw_export_checksum_or_hash_if_available:
```

を残す。

必須検算:

```text
raw_export_dedupe_count == current_low_confidence_calls_total
```

一致しない限り分類へ進んでもCOMPLETEにはできない。

---

## 10. 全edge分類

全edgeを必ず次の1カテゴリへ入れる。

```text
source-fix
graph-false-positive
dynamic-external-api
test-fake
builtins-runtime
intentional-dynamic-dispatch
needs-more-evidence
```

カテゴリを重複付与しない。

各edgeについて少なくとも:

```text
source_expression
classification
reason
verification_status
```

を付ける。

### source検証

必要に応じてfocused readと:

```powershell
rg -n "<symbol-or-expression>" <focused-path>
```

を使う。

確認対象:

- direct call
- method call
- import alias/re-export
- SDK fluent chain
- builtin/runtime API
- callback
- registry
- getattr
- string dispatch
- DI/protocol
- monkeypatch/fake

CodebaseMemoryのcallee表示に合わせるためだけにsourceを書き換えない。

---

## 11. known noiseを再確認する

少なくとも次は既知のnoise候補としてsourceを確認してから分類する。

### `datetime.now()`

明示的builtin/runtime callが別クラスの`.now`へ解決されている場合:

```text
graph-false-positive
```

を基本とする。

### Firestore fluent API

`order_by()/limit()/stream()/create()/update()`等がtestsのfake methodへ解決されている場合:

```text
dynamic-external-api
```

またはcallerがtest側なら:

```text
test-fake
```

を検討する。

### dynamic dispatch

`getattr`、string command、callback registry、DI等で意図的に静的解決が弱い場合:

```text
intentional-dynamic-dispatch
```

を検討する。

**noiseをsource-fixへ変換しない。**

---

## 12. source-fix候補の二次審査

`source-fix`へ入れる前に必ず:

1. sourceを読む
2. `rg`で参照を確認
3. testsを確認
4. `git log -S`または関連historyを確認
5. architecture boundaryを確認
6. protected regionか確認

する。

status fileにはsource-fix全件について:

```text
candidate_id
caller
callee
file/line
confidence
resolution_strategy
actual_source_expression
source_evidence
history_evidence
test_evidence
architecture_evidence
recommended_minimal_patch
recommended_tests
risk
recommended_next_pr
```

を記録する。

### このフェーズでsource-fixを実装しない

今回の主目的は**分類と抽出**である。

source-fixが見つかっても原則としてproduction sourceは変更しない。

root causeごとの後続PRに分離する。

例外的に変更が必要なら、1 logical unitだけに限定し、PR #20の非対象/保護条件を再確認する。

---

## 13. needs-more-evidenceの扱い

`needs-more-evidence`は逃げカテゴリではない。

全件について:

```text
edge_id
missing evidence
attempted verification
why not classifiable
exact next action
```

を記録する。

COMPLETEは`needs-more-evidence`が0件である必要まではないが、**その全件が記録済み**である必要がある。

`unprocessed`とは区別する。

---

## 14. 分類checksum

最終的に:

```text
source_fix_count
+ graph_false_positive_count
+ dynamic_external_api_count
+ test_fake_count
+ builtins_runtime_count
+ intentional_dynamic_dispatch_count
+ needs_more_evidence_count
== classified_count
```

かつ:

```text
classified_count + unprocessed_count
== current_low_confidence_calls_total
```

を満たす。

COMPLETEの場合:

```text
unprocessed_count = 0
checksum_status = PASS
```

必須。

---

## 15. status fileを最終化する

`LOW_CONFIDENCE_CALLS_TRIAGE_STATUS_20260830.md`を最終結果へ更新する。

### COMPLETE

全edge取得・分類済み、unprocessed=0、checksum PASS。

### PARTIAL

一部分類済みだが未処理edgeが残る。

必ず:

```text
unprocessed_count
未処理subset
exact next action
```

を残す。

### BLOCKED

MCP/schema/query/permission等により全件同定または分類が不可能。

必ず:

```text
blocker
attempted queries/commands
obtainable data
missing capability
exact next action
```

を残す。

「件数が多い」「時間が足りない」はBLOCKED理由にしない。

---

## 16. status/evidence commitを先に作る

分類が終わったら、まずtracked status/evidenceだけをsource-bearing active-doc commitとして作る。

想定changed file:

```text
docs/current/agent/LOW_CONFIDENCE_CALLS_TRIAGE_STATUS_20260830.md
```

必要なら分類結果を説明する小さいcurrent docを追加してよいが、completed reportはユーザーの明示依頼なしに作らない。

このcommitでは:

```text
.codebase-memory/artifact.json
.codebase-memory/graph.db.zst
```

をstage/commitしない。

production sourceを変更していなければ、その旨を明記する。

---

## 17. 最終shared graph refresh

status/evidence commit後、working treeを確認する。

そのcommitを基準にCodebaseMemoryを最終同期する。

確認:

```text
status = ready
artifact source commit = status/evidence commit
```

生成された:

```text
.codebase-memory/artifact.json
.codebase-memory/graph.db.zst
```

だけを最後のgenerated commitとして追加する。

PR descriptionまたはstatusには:

```text
final_artifact_source_commit
final_artifact_nodes
final_artifact_edges
final_index_status
final_refresh_committed: yes
```

を記録する。

このartifact commitでHEADが進んでも、**artifact commit自身を取り込む再refreshをしない。**

---

## 18. quality / tests

分類だけでproduction sourceを変更しない場合でも、tracked docsとartifact生成に対するrepository quality workflowは確認する。

production sourceを変更した場合のみ、そのsourceに対応するfocused regressionを追加で実行する。

ただし今回の推奨は:

```text
production source changes = 0
```

である。

source-fixは次PRへ分離する。

---

## 19. subagentを使う場合

全edge数が多ければsubagentを使ってよい。

推奨:

```text
A: runtime / operations / scripts
B: forecasting / energy_plan / domain
C: dashboard / persistence / external SDK / tests
```

各agentへ渡すもの:

```text
stable edge subset
classification definitions
required output schema
focused paths
stop condition
```

parentは全edgeを再調査せず、重複・未分類・矛盾だけを統合する。

最後にordinal集合の欠番/重複を検査する。

---

## 20. 今回やらないこと

- `_archive_weather_rows`を戻さない
- `_archive_weather_history`を削除しない
- similarity cleanupへ拡張しない
- weather canonicalizationへ戻らない
- Phase 1 frozen policyを変更しない
- protected Cloud Job/SOC pathをgraph confidenceだけで変更しない
- production deploymentをしない
- low-confidence edgeをゼロにするためのrename/refactorをしない
- source-fix候補をまとめて巨大PRにしない

---

## 21. 完了判定

`Result: COMPLETE`と報告できるのは、最低限すべて成立した場合だけ。

```text
[ ] current active-doc stateでanalysis index ready
[ ] current CALLS total取得
[ ] current low-confidence CALLS total取得
[ ] raw deduped edge count == low-confidence total
[ ] 全edgeにclassification付与
[ ] classified + unprocessed == total
[ ] COMPLETEならunprocessed=0
[ ] checksum PASS
[ ] source-fix全件evidence記録
[ ] needs-more-evidence全件evidence記録
[ ] status file最終更新
[ ] production rewrite for graph confidence = 0
[ ] protected runtime accidental change = 0
[ ] status/evidence commit作成
[ ] final shared artifactをそのcommitに対して1回だけ生成・commit
[ ] artifact-only self-follow refresh = 0
[ ] quality確認
```

1つでも満たせない場合は`PARTIAL`または`BLOCKED`。

---

## 22. 最終報告フォーマット

```text
Result: COMPLETE / PARTIAL / BLOCKED
Baseline HEAD:
Analysis indexed state:
CodebaseMemory version:
Index status:
Current CALLS total:
Current low-confidence CALLS total:
Raw deduped edges:
Classified:
Unprocessed:
Checksum: PASS / FAIL

Classification:
- source-fix:
- graph-false-positive:
- dynamic-external-api:
- test-fake:
- builtins-runtime:
- intentional-dynamic-dispatch:
- needs-more-evidence:

Source-fix candidates:
- ...

Needs-more-evidence:
- ...

Status/evidence commit:
Final artifact source commit:
Final artifact commit:
Quality/tests:
Production source changes:
Next recommended PR(s):
```

最重要なのは、**graphをきれいにすることではなく、全edgeを現在のsource evidenceで分類し、本当に修正価値があるsource-fixだけを次の独立PRへ送ること**である。

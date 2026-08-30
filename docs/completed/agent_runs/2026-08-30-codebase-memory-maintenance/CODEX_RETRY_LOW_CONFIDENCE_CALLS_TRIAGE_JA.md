# Codex再実行指示: low-confidence CALLS 全edge再取得・分類

## 0. この作業の位置づけ

PR #19 のうち、`_archive_weather_rows` の削除と shared CodebaseMemory artifact の再生成までは完了している。

現在の確認済み baseline:

```text
master HEAD:
52701f7766ba6dbdb5ed1cd1821e61b303c77635
chore: refresh CodebaseMemory archive graph

source-bearing commit:
b3041bf264b2e5346350e292689e535ed906f78e
chore: remove stale archive weather rows helper

artifact project:
C-VSC-SolerControler

artifact nodes:
5800

artifact edges:
18093
```

`_archive_weather_rows` は削除済みであり、この作業で再度触らない。

未完了なのは、PR #19 が要求した次の部分である。

1. 最新 graph から low-confidence `CALLS` を全edge再取得する。
2. edgeごとに証拠を保存する。
3. 全edgeを指定カテゴリへ分類する。
4. `source-fix` だけを後続の小さい修正候補として抽出する。
5. 現在値・未処理数・分類結果を tracked status file に記録する。

**この5項目が揃うまで、このフェーズを COMPLETE と報告してはならない。**

---

## 1. 最初に必ず読むもの

次を順に読む。

1. `AGENTS.md`
2. `docs/current/agent/codebase_memory_triage_and_maintenance_ja.md`
3. `docs/current/agent/codebase_memory_shared_graph_usage_ja.md`
4. `docs/completed/agent_runs/2026-08-30-codebase-memory-maintenance/CODEX_NEXT_ARCHIVE_WEATHER_AND_LOW_CONFIDENCE_CALLS_JA.md`
5. `docs/completed/agent_runs/2026-08-30-codebase-memory-maintenance/LOW_CONFIDENCE_CALLS_TRIAGE_STATUS_20260830.md`

protected runtime に触れる可能性が出た場合のみ:

6. `docs/current/agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md`

広い探索またはsubagentを使う場合のみ:

7. `docs/current/agent/codex_token_usage_rules.md`

---

## 2. 作業開始時の repository / artifact 確認

PowerShell 7 を使う。

最低限:

```powershell
git status --short
git rev-parse HEAD
Get-Content .codebase-memory/artifact.json
```

次を確認する。

- working tree が意図しない変更を持っていない
- `artifact.json.project == C-VSC-SolerControler`
- artifact の source commit が `b3041bf...` か、それ以降の正当な source-bearing commit
- artifact commit から HEAD までの差分が generated `.codebase-memory/**` だけなら fresh と扱う
- source/tests/current docs が artifact commit 後に変わっていれば stale と扱う

artifact が stale の場合は、**分類開始前に1回だけ** CodebaseMemory の incremental/reindex を行う。

artifact commit 自身を追うためだけの二度目の refresh は禁止。

---

## 3. CodebaseMemory index 状態の確認

CodebaseMemory MCP を使い、まず index status を確認する。

必要条件:

```text
project = C-VSC-SolerControler
status = ready
```

さらに可能なら次を記録する。

```text
CodebaseMemory version
node count
edge count
parse partial files
skipped files
indexed source commit / artifact commit
```

`status != ready` のまま分類を開始しない。

parse partial / skipped に今回の caller/callee が含まれる場合は、そのedgeを自動で `source-fix` にせず `needs-more-evidence` とする。

---

## 4. graph schema を先に確認する

過去の件数やproperty名を決め打ちしない。

CodebaseMemory の schema / sample edge query を使って、現行 `CALLS` edge に存在するproperty名を確認する。

最低限必要な情報:

```text
caller qualified name
caller file
caller line
callee qualified name
callee file or external target
callee line if available
confidence
resolution strategy
edge type
```

property名が過去と違う場合は、現行schemaに合わせる。

**旧レポートの692件、240件、215件、237件を current count として再利用しない。**

---

## 5. low-confidence CALLS の対象条件

今回の primary target は:

```text
edge type = CALLS
confidence < 0.5
```

である。

ただし現行CodebaseMemoryで confidence の表現方法が変わっている場合は、同等の low-confidence condition を schema evidence 付きで採用する。

対象件数を最初に確定し、status fileへ記録する。

最低限:

```text
current total CALLS
current low-confidence CALLS
confidence < 0.25
0.25 <= confidence < 0.5
resolution strategy counts
```

strategy例:

```text
suffix_match
unique_name
その他現行strategy
```

存在しないstrategy名を推測で作らない。

---

## 6. 全edgeを必ず列挙する

low-confidence edgeをサンプル調査だけで終わらせない。

全edgeについて最低限次のrecordを持つ。

```text
edge_id or stable ordinal
caller_symbol
caller_file
caller_line
callee_symbol
callee_file_or_external
callee_line_if_available
confidence
resolution_strategy
source_expression
classification
reason
verification_status
```

CodebaseMemory MCP が一括exportできない場合:

1. confidence bandで分割
2. resolution strategyで分割
3. caller packageで分割
4. pagination / limitを使う

のいずれかで**最終的に全件を覆う**。

最終的に:

```text
classified_count + unprocessed_count == current_low_confidence_total
```

を成立させる。

成立しない場合は COMPLETE にしない。

---

## 7. 1 edgeごとのsource検証

CodebaseMemory edgeをそのまま正しいcallとして扱わない。

各edgeについて必要に応じて:

```powershell
rg -n "<callee-or-expression>" <focused-file-or-dir>
```

を使い、caller source の実式を読む。

確認するもの:

- `foo()` の直接callか
- `obj.foo()` のmethod callか
- SDK fluent chainか
- builtin/runtime objectか
- alias importか
- re-exportか
- string dispatchか
- getattr / callback / dependency injectionか
- test monkeypatch / fake / protocol implementationか

sourceが明確なら、graphの誤解消をsourceへ逆輸入しない。

---

## 8. 分類カテゴリ

各edgeを必ず次のどれかへ分類する。

### A. `source-fix`

source自体に実際の保守問題がある。

例:

- stale alias
- obsolete wrapperをまだ呼んでいる
- 間違ったowner moduleへ依存
- 同名関数の曖昧なimportが実際に保守事故を起こしている
- contract mismatch
- dead pathへの誤参照

条件:

- graphだけでなくsourceで問題が確認できる
- history / tests / architectureで修正方向を裏付けできる

### B. `graph-false-positive`

sourceは明確で、CodebaseMemory resolverだけが誤結合している。

既知例:

```text
app/backup/artifacts.py の datetime.now()
```

をproject内の別 `now` methodへ寄せるような解決。

**production sourceをgraph confidence向上目的で変更しない。**

### C. `dynamic-external-api`

Firestore等、外部SDKのfluent/dynamic method。

例:

```text
collection.order_by(...).limit(...).stream()
query.stream()
external_sdk.execute()
```

project内fake methodへ誤結合していてもsource rewriteしない。

### D. `test-fake`

pytest fake / monkeypatch / SDK-shaped fake / test helper が原因。

production SDK callとfake methodの同名性によるresolverノイズを含む。

### E. `builtins-runtime`

Python builtin、stdlib、runtime-bound method等。

例:

```text
str.strip
list.append
dict.get
datetime.now
Path.exists
```

ただしproject wrapperを実際に誤callしている場合はsource evidenceにより再分類する。

### F. `intentional-dynamic-dispatch`

設計上意図したdynamic dispatch。

例:

- `_cloud_call("symbol")`
- `getattr`
- callback registry
- protocol/DI runtime binding
- plugin hook

### G. `needs-more-evidence`

sourceだけでは確定できない。

例:

- parser partial
- generated/runtime metaprogramming
- historyを読まないと互換契約が不明
- external API shapeが手元sourceだけで確認不能

このカテゴリをゼロにするために無理な推測をしない。

---

## 9. 既知の誤修正禁止ケース

次のケースは再確認してよいが、graph confidence改善だけでsourceを書き換えない。

### `datetime.now()`

同名project methodへresolverが寄ったとしても、sourceが標準`datetime.now()`なら `graph-false-positive` / `builtins-runtime`。

### Firestore fluent API

production codeの `.stream()`, `.create()`, `.update()`, `.execute()` 等が tests の fake methodsへ結び付いても、外部SDK呼出しなら `dynamic-external-api`。

### test fake / protocol method

framework/fixture/DI呼出しは inbound CALLS=0 や低confidenceになり得る。

### dynamic helper

string dispatchやregistryから呼ばれるsymbolをdead code扱いしない。

---

## 10. `source-fix` 判定時の追加確認

`source-fix`に分類する前に、必ず次を行う。

1. focused source read
2. `rg` で全current reference確認
3. `git log -S "<symbol>" -- <file-or-path>`
4. 必要なら `git blame`
5. linked tests確認
6. architecture/import-linter boundary確認
7. protected region該当確認

最低限 evidence:

```text
caller
callee
file/line
confidence
resolution_strategy
actual source expression
why source itself is wrong
history evidence
test evidence
recommended minimal patch
recommended focused tests
risk
```

このevidenceが不足したものは `needs-more-evidence`。

---

## 11. source修正の扱い

今回の主目的は分類であり、複数のsource-fixを一括修正しない。

原則:

- まず全edge分類を完了
- source-fixを一覧化
- root causeごとに分離
- 1 logical unit = 1 patch / PR

ただし明白かつ単一の極小cleanupが1件だけ見つかり、AGENTS.mdの全条件を満たす場合のみ、その場で別commitへ切ることは可。

複数 subsystem を同時変更しない。

SOC / Cloud Job 23:00 / 03:00 / 07:00 / production deployment / shadow gate の protected behaviorへ自動修正を広げない。

---

## 12. quality / tests

### 分類だけでsource変更なし

source testを無意味に全実行しない。

ただし tracked status/evidence docsを変更する場合、repository標準の必要なquality checksを実行する。

### source変更あり

AGENTS.mdどおり、tests前に `code-quality-audit` Skillを実行する。

その後:

- Ruff lint
- applicable ty / deptry / Oxlint / tsc
- focused pytest
- import-linter if dependency boundary touched
- `git diff --check`

を行う。

失敗時は既存failureか今回change起因かを分ける。

---

## 13. status file 更新を必須化

この作業では:

```text
docs/completed/agent_runs/2026-08-30-codebase-memory-maintenance/LOW_CONFIDENCE_CALLS_TRIAGE_STATUS_20260830.md
```

を必ず更新する。

開始時:

```text
status: IN_PROGRESS
```

完了時は次のどれか:

```text
status: COMPLETE
status: PARTIAL
status: BLOCKED
```

### COMPLETE にしてよい条件

すべて満たすこと。

- current low-confidence CALLS総数が取得済み
- 全edge列挙済み
- 全edge分類済み、またはunprocessed=0
- 7カテゴリ件数が記録済み
- `source-fix`全候補のevidenceが記録済み
- known graph noiseをsource rewriteしていない
- protected regionを不用意に変更していない
- source changeがある場合はfocused tests/quality成功
- artifact refreshが必要ならsource-bearing commitに対して1回だけ実行

### PARTIAL

edge exportはできたが、一部classification未完了等。

必ず:

```text
unprocessed_count
why
next exact action
```

を書く。

### BLOCKED

MCP/query/schema/permission等で全edge取得できない場合。

必ず:

```text
blocker
attempted commands/queries
what data was obtainable
what exact capability is missing
```

を書く。

**「時間が足りない」「多いのでサンプルだけ」はBLOCKED理由にしない。pagination / 分割 query で継続する。**

---

## 14. status file に必ず記録する数値

```text
baseline_head
artifact_source_commit
artifact_nodes
artifact_edges
codebase_memory_version
index_status
parse_partial_count
skipped_count
current_calls_total
current_low_confidence_calls_total
confidence_lt_0_25
confidence_0_25_to_lt_0_5
classified_count
unprocessed_count
source_fix_count
graph_false_positive_count
dynamic_external_api_count
test_fake_count
builtins_runtime_count
intentional_dynamic_dispatch_count
needs_more_evidence_count
```

さらに resolution strategy counts を全種類記録する。

合計検算:

```text
source_fix
+ graph_false_positive
+ dynamic_external_api
+ test_fake
+ builtins_runtime
+ intentional_dynamic_dispatch
+ needs_more_evidence
+ unprocessed
== current_low_confidence_calls_total
```

一致しなければ COMPLETE 禁止。

---

## 15. edge evidence の保存方法

status fileを数千行にしない。

全edgeのraw listingはローカル作業artifactとして保持してよい。

tracked status fileには:

- aggregate counts
- strategy counts
- classification counts
- `source-fix`全件
- `needs-more-evidence`全件
- 各noise categoryの代表例
- raw exportの生成方法 / command / query shape

を記録する。

機密情報、`.env`内容、project ID、credential、user-specific pathはtracked fileへ入れない。

raw exportをGit追跡する必要はない。

---

## 16. subagentを使う場合

全edgeが多い場合はsubagent分割可。

推奨分割:

```text
Agent A: app/runtime + app/operations + scripts
Agent B: app/forecasting + app/energy_plan
Agent C: dashboard/persistence/external SDK + tests
```

または resolution strategy単位。

各subagentへ必ず渡す:

- edge subset
- classification rules
- exact source files
- expected output schema
- stop condition

parentは同じedgeを再調査せず、重複/矛盾だけ統合する。

---

## 17. 完了時のCodex最終報告

必ず以下の形式で報告する。

```text
Result: COMPLETE / PARTIAL / BLOCKED
Baseline HEAD:
Artifact source commit:
Index status:
Current low-confidence CALLS total:
Classified:
Unprocessed:

Classification:
- source-fix:
- graph-false-positive:
- dynamic-external-api:
- test-fake:
- builtins-runtime:
- intentional-dynamic-dispatch:
- needs-more-evidence:

Source-fix candidates:
1. ...
2. ...

Changed files:
Tests/quality:
Artifact refresh:
Status file commit:
Next recommended PR(s):
```

`COMPLETE`と書く場合、status fileも`status: COMPLETE`でなければならない。

---

## 18. この再実行でやらないこと

- `_archive_weather_rows` を再調査して戻さない
- `_archive_weather_history` compatibility seamを削除しない
- old 692 / 237件をcurrent値として流用しない
- graph confidence向上だけのrename/refactorをしない
- similarity cleanupへ脱線しない
- weather classificationへ戻らない
- Phase 1 frozen policyを変更しない
- production deploymentを行わない
- source-fix候補を一括で巨大refactorしない

---

## 19. 最終完了条件

このPR #19未完了フェーズは、次をすべて満たして初めて終了する。

1. shared graphの鮮度を確認した
2. CodebaseMemory `ready`を確認した
3. current low-confidence CALLS総数を取得した
4. 全edgeを取得した
5. 全edgeを7カテゴリへ分類した
6. 合計検算が一致した
7. `source-fix`だけを修正候補として抽出した
8. `needs-more-evidence`を明示した
9. tracked status fileを更新した
10. statusがCOMPLETE/PARTIAL/BLOCKEDのどれか明示された
11. COMPLETEならunprocessed=0
12. graph noiseを理由にproduction sourceを書き換えていない
13. source changeがあればquality/focused testsが成功した
14. artifact refreshは必要な場合だけ1回行った

**最重要:** `_archive_weather_rows`削除やartifact更新が終わったことを、このフェーズ全体の完了と誤認しないこと。今回の主作業は low-confidence CALLS の全件再取得・分類・source-fix抽出である。

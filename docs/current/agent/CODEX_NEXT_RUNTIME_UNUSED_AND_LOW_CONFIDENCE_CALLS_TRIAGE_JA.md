# 次作業: Runtime stale helper確定と低信頼CALLS再調査

## 0. この文書の位置づけ

この文書は、2026-08-30 の CodebaseMemory cleanup / shared graph 導入 / operational weather-class canonicalization の次に実施する作業を固定する実装指示である。

設計提案ではなく、次の調査・cleanupの判断条件として扱う。

必ず先に以下を読むこと。

- `AGENTS.md`
- `docs/current/agent/codebase_memory_triage_and_maintenance_ja.md`
- `docs/current/agent/codebase_memory_shared_graph_usage_ja.md`
- `docs/current/agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md`

CodebaseMemory の結果だけで production source を変更してはならない。

---

## 1. 現在の確定状態

### 1.1 Weather canonicalization は完了扱い

source-bearing commit:

```text
41e749568d3a4df61ef9dbd0c87706159f4b3a33
refactor: canonicalize operational weather classes
```

実装済み:

- `app/domain/weather.py::open_meteo_weather_class` を operational canonical owner とした。
- `app.energy_plan.weather_history.weather_class` は compatibility wrapper として維持した。
- `app.forecasting.pv_array_calibration._weather_class_from_code` も wrapper として維持した。
- `app.operations.shadow_gate.weather_class` は Phase 1 frozen diagnostic classification として分離したまま維持した。
- `code 1` と `code 80` で operational / shadow semantics が異なることを negative regression で固定した。

この作業で weather wrapper の追加cleanupを行わない。

### 1.2 Shared graph artifact は正しい更新順序

artifact commit:

```text
2a151c398342b3751921072097d67855d5afd83d
chore: refresh CodebaseMemory weather graph
```

artifact は source-bearing commit `41e749...` を記録している。

```text
nodes=5687
edges=17999
```

現在の HEAD が artifact-only commit `2a151c...` であっても、それだけを理由に artifact を再refreshしてはならない。

### 1.3 CI baseline

HEAD `2a151c...` の repository quality workflow run 131 は成功済み。

次作業で新しい失敗が出た場合、この成功状態との差分で原因を切り分ける。

---

## 2. 今回の目的

次の2つを混同しない。

1. 実ソースと履歴から stale と確認できる private helper の cleanup
2. CodebaseMemory の低信頼 `CALLS` を最新graphで edge単位に再取得し、source修正対象とgraph上の誤解決を分離する調査

今回の優先対象は次の順序とする。

```text
A. app.runtime.command_adapter._run_optional
B. app.energy_plan.workflow._archive_weather_rows
C. low-confidence CALLS 全体の最新再分類
```

ただし A と B を同じ source patch で削除してはならない。

---

# Phase A: `_run_optional` の最終確認と最小cleanup

## 3. 現在確認できている事実

対象:

```text
app/runtime/command_adapter.py::_run_optional
```

現在の実装は、`_run()` を実行し、全例外を捕捉して optional step failure を表示して終了する。

```python
def _run_optional(command, env_updates=None, *, label):
    try:
        _run(command, env_updates)
    except Exception as exc:
        print(...)
```

これは単なるaliasではなく、失敗を non-fatal にする意味を持つ。

したがって「定義しか見えない」だけで削除してはならない。

## 4. 履歴から分かっていること

2026-08-02 の commit:

```text
60bc52f250c979d46e73527fe3d0a6c14e4ba7a3
refactor: complete cloud job orchestration split
```

で `_run_optional` は `cloud_job.py` のローカル実装から `app/runtime/command_adapter.py` へ移動された。

当時は orchestration split の一環として adapter に残す合理性があった。

その後 2026-08-29 の時間所有権分離により、現在の `cloud_job.py` の command adapter import は:

```python
from app.runtime.command_adapter import _env_float, _env_int, _run, _run_operation_with_retry
```

となっており、`_run_optional` をimportしない。

現在の `slot_orchestration.py` も 23/03/07 の専用経路だけを呼び、generic optional command pathを持たない。

このため、現時点では `_run_optional` は「過去のorchestration構造から残ったstale helper」の可能性が高い。

## 5. 削除前に必ず行う最終確認

ローカル checkout で以下を実行する。

### 5.1 repository reference search

```powershell
rg -n --hidden --glob '!\.git/**' --glob '!\.codebase-memory/**' "_run_optional" .
```

判定:

- definitionのみ → 次へ
- test/import/monkeypatch/string dispatchが存在 → 削除停止、用途を読む

### 5.2 Git history search

```powershell
git log --oneline -S "_run_optional" --all -- app/runtime app scripts tests docs/current
```

必要なcommitは `git show <sha> -- <relevant-file>` で読む。

確認事項:

- compatibility seam として意図的に残す説明がないか
- operator script / old scheduler の契約として残されたものではないか
- optional failure semantics が別関数へ移行した結果として不要になったのか

### 5.3 CodebaseMemory

最新indexで `_run_optional` の:

- inbound callers
- CALL_REFERENCE
- semantic references
- tests
- file/module ownership

を確認する。

CodebaseMemory が inbound=0 でも、それだけを削除根拠にしない。

## 6. 削除条件

以下を全て満たす場合のみ削除する。

- repo全体の直接参照がdefinition以外にない
- string dispatch / getattr / dynamic importがない
- testsがprivate seamとして利用していない
- docs/current / operator contractで約束していない
- Git historyにcompatibility維持の意図がない
- 現行23/03/07 ownership pathから不要

## 7. 削除する場合の変更範囲

変更してよいのは原則:

```text
app/runtime/command_adapter.py
```

だけ。

削除するのは `_run_optional` 定義のみ。

同時に以下を変更してはならない。

- `_run`
- process-group kill
- timeout / deadline semantics
- `_run_operation_with_retry`
- retry count / delay
- secret masking
- `cloud_job.py`
- `slot_orchestration.py`
- 23/03/07 HISTORICAL_FAILURE_LOCK regions

不要importが生じた場合のみ、そのimportを同じpatchで削除してよい。

## 8. `_run_optional` cleanup の検証

最低限:

```powershell
python -m ruff check app/runtime/command_adapter.py
python -m pytest tests/test_cloud_job_runner.py tests/test_historical_failure_protection.py -q
```

command adapter専用testが存在する場合は必ず追加実行する。

その後 repository rule に従い full validation を最後に実行する。

重要:

- historical failure test の失敗を「unused cleanupだから無関係」と無視しない
- timeout/retry/23/03/07 behaviorに差分が出た場合はcleanupをrevertして原因を調べる

## 9. 削除条件を満たさない場合

削除しない。

その場合は `_run_optional` に短い説明を追加することを検討する。

例:

```text
codebase-memory: keep-separate / compatibility — <具体的理由>
```

ただし、理由が実証できないのに「将来使うかもしれない」だけでコメントを作らない。

---

# Phase B: `_archive_weather_rows` は別判定

## 10. 対象

```text
app/energy_plan/workflow.py::_archive_weather_rows
```

既存調査では definition-only 候補として挙がっている。

しかし `_run_optional` と同時削除してはならない。

## 11. なぜ慎重に扱うか

現在の `workflow.py` には別の明示的compatibility seamが存在する。

```python
def _weather_class(...):
    """Compatibility seam for tests importing the former workflow helper."""
```

つまり、このfileではworkflow split後も、private helper名をtests/compatibilityのため残す設計が実際に使われている。

したがって `_archive_weather_rows` も同じ種類のseamである可能性を、inbound=0だけでは排除できない。

## 12. 必須履歴調査

```powershell
rg -n --hidden --glob '!\.git/**' --glob '!\.codebase-memory/**' "_archive_weather_rows" .
git log --oneline -S "_archive_weather_rows" --all -- app/energy_plan tests scripts docs/current
git log --oneline -S "_archive_weather_history" --all -- app/energy_plan tests scripts docs/current
```

導入・移行commitを実際に読む。

確認する問い:

1. `WeatherHistoryFetchResult` 導入以前の list-return API を保持するためのwrapperか
2. testsが過去にこの名前を直接importしていたか
3. migration期間だけ残したものか
4. current docsでprivate seamを利用する例があるか
5. local/operator scriptsからの利用を想定した記述があるか

## 13. 判定

### DELETE

互換契約が存在しないことを履歴まで確認できた場合:

- `_archive_weather_rows`だけを別patchで削除
- `_archive_weather_history`
- `archive_weather_history`
- cache/chunk/partial diagnostics

には触れない。

### KEEP

互換目的が確認できた場合:

- 削除しない
- 理由が現在コードから読み取れない場合だけcompatibility commentを追加
- 必要ならdirect compatibility regressionを追加

### BLOCKED

履歴が曖昧で判断できない場合:

- 削除しない
- 「inbound 0」のみで判断しない

## 14. `_archive_weather_rows`を削除する場合の検証

```powershell
python -m ruff check app/energy_plan/workflow.py app/energy_plan/weather_history.py
python -m pytest tests/test_energy_model.py -q
```

archive weatherの以下を含むtestを確認する。

- cache hit
- partial chunk
- HTTP failure
- JSON failure
- timeout
- missing dates

---

# Phase C: 最新 low-confidence CALLS の再分類

## 15. 古い692件を現在値として使わない

2026-08-30 初回調査では:

```text
CALLS total               4445
confidence < 0.5           692
suffix_match               355
unique_name                337
builtins target            240
tests target               215
other/app-local            237
```

だった。

しかし、その後に:

- completed reports exclusion
- 4 unused helper cleanup
- dotenv canonicalization
- shared graph rules/current docs追加
- weather canonicalization

が入っている。

したがって 692 / 237 を現在値として報告してはならない。

## 16. 最新graphを基準に再取得

開始時に:

```text
index_status = ready
artifact source-bearing commit = 41e749568d3a4df61ef9dbd0c87706159f4b3a33
```

を確認する。

artifact-only HEAD差分だけなら再index不要。

ローカルworking treeにsource変更がある場合は、調査前にその状態を明示し、必要ならincremental indexを更新する。

## 17. 必ず保存するedge fields

低信頼 `CALLS` について最低限:

```text
caller_symbol
caller_file
caller_line
callee_symbol
callee_file / external target
confidence
resolution_strategy
edge_type
```

を取得する。

可能なら以下も追加する。

```text
caller_module
callee_module
is_test_source
is_test_target
is_builtin_target
is_external_target
```

集計値だけを残して、個別edgeを捨ててはならない。

## 18. 分類カテゴリ

全edgeを次のいずれかに分類する。

### A. `source-fix`

実ソースにも独立した問題がある。

例:

- stale aliasが誤った実装を指している
- 実際に曖昧な動的dispatchが保守上のバグを生んでいる
- import/rename後の参照残り
- current contractと実装が不一致

**CodebaseMemory confidenceが低いこと自体は source-fix 理由にならない。**

### B. `graph-false-positive`

sourceは明確だがgraph resolverが別symbolへ結んでいる。

既知例:

```text
collect_cleanup_candidates() の datetime.now()
  -> _SystemMonitorClock.now と誤解決
```

production codeは変更しない。

必要なら再現可能な最小evidenceをまとめ、CodebaseMemory upstream issue候補にする。

### C. `dynamic-external-api`

外部SDK / fluent API / dynamically typed library。

例:

```text
Firestore order_by(...).stream()
```

sourceをgraphに合わせて書き換えない。

### D. `test-fake`

SDK-shaped fake、protocol fake、fixture helper等。

production codeを変更しない。

### E. `builtins-runtime`

`dict.get`, `list.append`, `datetime`, builtin等のresolver ambiguity。

通常はsource修正対象外。

### F. `intentional-dynamic-dispatch`

`getattr`, registry, DI, protocol, plugin的dispatchなど、repository設計上意図的なもの。

CALL_REFERENCE等と合わせて確認し、inbound=0だけでdead code扱いしない。

### G. `needs-more-evidence`

source/history/testだけでは確定できないもの。

無理にAへ分類しない。

## 19. 優先順位

全低信頼edgeを同じ深さで直さない。

調査優先順位:

1. `app/runtime` / battery / device control
2. `app/operations` のproduction-critical path
3. `app/energy_plan` / forecasting
4. persistence / adapters
5. scripts
6. tests / builtins / external SDK

ただし優先度が高いことと「source修正すべき」は別判断。

## 20. source verification

各 `source-fix` 候補について最低限:

```powershell
rg -n "<caller|callee exact names>" app scripts tests
git log --oneline -S "<relevant symbol>" -- <relevant paths>
```

を行い、関連sourceを読む。

CodebaseMemory edge表だけでpatchを作らない。

## 21. 修正patchの単位

`source-fix` が見つかっても、一括修正しない。

1つの論理原因ごとに別patchとする。

禁止:

```text
fix 20 low-confidence calls
```

推奨:

```text
fix stale X alias after Y migration
fix wrong registry target for Z
```

のように実コード上の原因で命名する。

## 22. graph false positiveへの対応

`graph-false-positive` を見つけた場合:

- production source変更なし
- 必要なら調査evidenceへ記録
- 同じresolver patternが多数ある場合のみupstream issue候補としてまとめる
- commentsを大量追加してresolverを誘導しない

CodebaseMemoryの解析精度を上げる目的だけのproduction refactorは禁止。

---

# 23. この次作業での推奨実行順

```text
1. git status --short
2. shared graph freshness確認
3. _run_optional graph/query + rg + git log -S
4. DELETE条件を満たすなら _run_optional だけcleanup
5. focused quality/tests
6. detect_changes / blast radius確認
7. source-bearing commit
8. _archive_weather_rows は別途 read-only history調査
9. DELETE/KEEP/BLOCKED verdictを出す
10. low-confidence CALLSを最新graphからedge単位でexport
11. categoriesへ分類
12. source-fix候補だけsource/history/testで再検証
13. source-fixがあれば後続の個別PR候補を列挙
14. explicit index_repositoryを必要なsource-bearing状態に対して1回実行
15. shared artifact更新を最後のcommitにする
16. artifact commit自身を取り込む二度目refreshはしない
```

重要:

- Phase AのcleanupとPhase Cのsource-fixを同じpatchにしない。
- `_archive_weather_rows`の削除をPhase Aに混ぜない。
- current weather wrappersを今回削除しない。

---

# 24. テスト/品質基準

repository rulesに従い、test前にcode-quality-auditを実施する。

最低限、変更対象に応じて:

```powershell
python -m ruff check <changed-python-files>
```

Runtime cleanup時:

```powershell
python -m pytest tests/test_cloud_job_runner.py tests/test_historical_failure_protection.py -q
```

Energy Plan cleanupを後続patchで行う場合:

```powershell
python -m pytest tests/test_energy_model.py -q
```

最後にrepository-required full validationを実施する。

新しいdiagnosticと既存debtを混同しない。

---

# 25. Stop conditions

以下の場合はsource変更せず停止して報告する。

- `_run_optional` に現在の互換利用が見つかった
- `_archive_weather_rows` のhistorical intentが確定できない
- low-confidence edgeがsource defectかresolver defectか判別不能
- protected historical failure regionに変更が必要になる
- external contract変更が必要になる
- graph artifactが対象source状態を表しておらず、freshnessを確定できない

---

# 26. 完了条件

このフェーズの完了は「低信頼edgeをゼロにすること」ではない。

以下を満たせば完了。

- `_run_optional` が DELETE / KEEP のどちらかでevidence付き確定
- 削除する場合は最小patchと回帰testが成功
- `_archive_weather_rows` が DELETE / KEEP / BLOCKED でevidence付き確定
- 最新graphの低信頼CALLS総数とresolution strategy別内訳を再取得
- app-local edgeを個別evidence付きで分類
- `source-fix` と source変更禁止対象を分離
- source-fix候補を後続の小さな個別作業に分割
- shared graph artifactをsource-bearing状態に対して一度だけ更新

成功指標は graph confidence の数値改善ではなく、**誤修正を避けながら、実際のsource defectだけを小さく特定できたこと**とする。

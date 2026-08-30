# 次作業: `_archive_weather_rows` の最終判定と low-confidence CALLS の最新再分類

## 目的

この文書は、2026-08-30 時点の `master`（CodebaseMemory artifact commit `199419f14bdbab533c8c4950b1540e48d7ac2ddb`）から次に行う調査・cleanup の実装手順を固定する。

今回の目的は二つだけである。

1. `app/energy_plan/workflow.py::_archive_weather_rows` が本当に削除可能かを、現在ソース・Git履歴・CodebaseMemory・テスト境界の全てで最終確認し、条件を満たす場合だけ単独で削除する。
2. 最新 shared graph から low-confidence `CALLS` を edge 単位で再取得し、実際にソース修正が必要な edge と、解析器・動的API・test fake 等によるノイズを分離する。

low-confidence edge を減らすこと自体を目標にしてはならない。完了条件は、**source defect だけを根拠付きで抽出し、誤修正を避けること**である。

---

## 0. 現在の確定状態

### 0.1 `_run_optional` cleanup は完了

PR #18 で strong delete candidate とした `app/runtime/command_adapter.py::_run_optional` は、source-bearing commit:

```text
13f165099e46473142927d0513ab8cab6722fb76
chore: remove stale optional runtime helper
```

で定義だけが削除された。

変更は `app/runtime/command_adapter.py` の `_run_optional` 7行のみで、`_run`、timeout、deadline、process kill、retry、23/03/07 runtime path は変更されていない。

このcleanupを再度触らないこと。

### 0.2 CodebaseMemory artifact は source-bearing commit に対して更新済み

artifact commit:

```text
199419f14bdbab533c8c4950b1540e48d7ac2ddb
chore: refresh CodebaseMemory runtime graph
```

`artifact.json` は artifact commit 自身ではなく、source-bearing commit `13f165099e...` を指している。

```text
project = C-VSC-SolerControler
nodes   = 5743
edges   = 18040
```

この状態は shared graph 運用ルールどおりである。

artifact commit 自身を取り込む目的の再indexは禁止する。

### 0.3 CI

current HEAD `199419f...` の quality workflow run 141 は SUCCESS。

node/edge 数が weather artifact 時点の `5687 / 17999` から `5743 / 18040` に増えたことを異常扱いしないこと。PR #18 の `docs/current` 追加は意図的に index 対象であり、node/edge 数は「少ないほど良い」指標ではない。

---

## 1. `_archive_weather_rows` の追加履歴調査で分かったこと

対象:

```text
app/energy_plan/workflow.py::_archive_weather_rows
```

現在の実装は概ね次の形である。

```python
def _archive_weather_rows(... ) -> list[dict[str, object]]:
    return archive_weather_history(
        ...
    ).rows
```

一方、現行 production path は `WeatherHistoryFetchResult` を返す `_archive_weather_history` / `WeatherHistoryPort` を使用している。

### 1.1 現在の production path

`_DefaultWeatherHistoryPort.load_history()` は `_archive_weather_history(...)` を返す。

`_build_consumption_forecasts()` は `weather_source.load_history(...)` で `WeatherHistoryFetchResult` を受け、

```python
weather_history.rows
weather_history.received_dates
weather_history.errors
...
```

の診断情報も利用する。

つまり list-only wrapper である `_archive_weather_rows` は現在の計画実行経路に不要である。

### 1.2 `WeatherHistoryFetchResult` 導入前から実行経路は `_archive_weather_history`

2026-07-19 の result model 抽出直前の commit `42b40aa3...` を確認すると、既に:

```python
def _archive_weather_rows(...):
    return _archive_weather_history(...).rows
```

は存在していた。

しかし同時点の `_build_consumption_forecasts()` は `_archive_weather_rows` ではなく、直接:

```python
weather_history = _archive_weather_history(...)
```

を使用していた。

したがって `_archive_weather_rows` は、少なくとも result model 抽出直前には production execution path の owner ではなかった。

### 1.3 当時の tests も `_archive_weather_history` を使用

同時点の `tests/test_energy_model.py` は `energy_model_main` から `_archive_weather_history` を import しており、`_archive_weather_rows` を import していなかった。

このため「list-only helper が既存 test contract のため残っていた」という根拠も現在の履歴調査では確認できていない。

### 1.4 2026-07-19 に `WeatherHistoryFetchResult` と `WeatherHistoryPort` が正式境界になった

commit:

```text
897dfdfa2db7bcc3649f10185eb5f6efee927d05
Extract weather history result model
```

で `WeatherHistoryFetchResult` が `app.energy_plan.weather` に抽出された。

続く:

```text
90ae2150b26427af4067f67c6ecc99a54699229f
Inject weather history port
```

で `WeatherHistoryPort.load_history(...) -> WeatherHistoryFetchResult` が導入され、`_build_consumption_forecasts()` は injected port 経由へ移行した。

ここでも list-only helper を正式 boundary にする変更はない。

### 1.5 2026-08-02 の archive ownership 移行

commit:

```text
8c285bb0358a89a6fa369dd9ce4fb8d776424c2d
refactor: extract energy weather archive retrieval
```

で canonical archive retrieval が `app.energy_plan.weather_history.archive_weather_history` に移された。

この変更では `_archive_weather_rows` も canonical function を直接呼ぶ形に更新されたが、production caller 追加は確認できない。

さらに直後の:

```text
3c62b7b52d8225403dfc92b9b9ac73f9d5652152
refactor: remove duplicate energy weather archive
```

では、workflow に残す private seam について明確に:

- 既存 runtime tests
- dependency-injection patch boundary

を保つために **同名の `_archive_weather_history` を薄い委譲として残す** と記録されている。

ここで保持理由が明文化された対象は `_archive_weather_history` であり、`_archive_weather_rows` ではない。

### 1.6 現行 tests

現在の `tests/test_energy_model.py` は archive retrieval を:

```python
from app.energy_plan.weather_history import archive_weather_history as _archive_weather_history
```

で canonical module から import する。

`_archive_weather_rows` の現行 test import は今回の GitHub source review では確認できなかった。

---

## 2. 現時点の verdict

### `_archive_weather_rows`: DELETE CANDIDATE（最終 local evidence 確認待ち）

履歴調査により、前回の `HOLD` から一段進めてよい。

理由:

1. 現行 production path は `WeatherHistoryFetchResult` を使用する。
2. result model 抽出前から `_build_consumption_forecasts()` は `_archive_weather_history` を直接使用していた。
3. 当時の tests も `_archive_weather_history` を import していた。
4. `WeatherHistoryPort` 導入後は result object が正式 contract。
5. 8/2 の archive extraction で明示的に残すと記録された compatibility/injection seam は `_archive_weather_history`。
6. `_archive_weather_rows` は現在 canonical archive function をもう一度呼んで `.rows` だけ返すだけであり、別 semantics を持たない。

ただし GitHub connector から shared binary graph の edge query はできないため、**削除前に local CodebaseMemory と `rg` / `git log -S` の最終確認が必須**である。

---

## 3. `_archive_weather_rows` を削除する前の必須確認

実装開始前に repository rules に従い CodebaseMemory を query する。

### 3.1 artifact / index freshness

確認:

```text
artifact source commit = 13f165099e46473142927d0513ab8cab6722fb76
```

ローカル checkout に source/tests/current docs の未index差分がある場合だけ incremental refresh する。

artifact-only HEAD `199419f...` を理由に再indexしない。

### 3.2 CodebaseMemory symbol query

`_archive_weather_rows` について最低限確認:

- inbound `CALLS`
- inbound `CALL_REFERENCE`
- `TESTS`
- `USAGE`
- symbol/file identity
- low-confidence inbound edge の有無

`in_degree=0` だけで削除しない。動的参照、string dispatch、monkeypatch target がないことを確認する。

### 3.3 text search

最低限:

```powershell
rg -n --hidden --glob '!\.git/**' "_archive_weather_rows" .
```

期待される状態は definition のみ。

特に確認:

- `from app.energy_plan.workflow import _archive_weather_rows`
- `workflow._archive_weather_rows`
- `monkeypatch.setattr(... "_archive_weather_rows" ...)`
- `getattr(..., "_archive_weather_rows")`
- `_cloud_call("_archive_weather_rows")` のような string dispatch
- docs/current に「互換のため残す」とする current rule がないか

### 3.4 Git history

最低限:

```powershell
git log --all -S"_archive_weather_rows" -- app/energy_plan/workflow.py energy_model_main.py tests
```

必要なら各 commit を `git show` で読む。

確認したいこと:

- helper 導入時の目的
- caller が消えた commit
- public/private compatibility 意図
- monkeypatch target として維持した履歴

今回の GitHub 調査で得た履歴を再確認し、矛盾する証拠があれば削除を止める。

---

## 4. DELETE 条件

以下を全て満たす場合だけ `_archive_weather_rows` を削除する。

- current direct caller なし
- current dynamic/string caller なし
- test import/monkeypatch target なし
- CodebaseMemory inbound evidence なし、または inbound が明確な graph false positive のみ
- current docs/ADR に compatibility contract なし
- history に「この名前を保持する」根拠なし
- external documented API ではない

一つでも不明なら verdict を `BLOCKED` に戻し、ソースは変更しない。

---

## 5. 削除 patch の範囲

削除条件成立時の変更は一つだけ。

```text
app/energy_plan/workflow.py::_archive_weather_rows
```

の定義全体を削除する。

### 変更禁止

この patch では以下を変更しない。

- `_archive_weather_history`
- `_DefaultWeatherHistoryPort`
- `WeatherHistoryPort`
- `WeatherHistoryFetchResult`
- `app.energy_plan.weather_history.archive_weather_history`
- archive cache semantics
- chunk size / timeout fallback
- Open-Meteo request behavior
- forecast behavior
- consumption model inputs
- unrelated private wrappers

特に `_archive_weather_history` は 8/2 の履歴で dependency-injection / runtime-test seam として残す理由が明文化されているため、同時削除禁止。

---

## 6. `_archive_weather_rows` cleanup の検証

最低限:

```powershell
python -m ruff check app/energy_plan/workflow.py
python -m pytest tests/test_energy_model.py tests/test_energy_model_runtime.py tests/test_energy_plan_historical.py
```

repository の standard quality workflow がある場合はそれも実行する。

テスト後、`rg` で helper 名が消えていることを確認する。

CodebaseMemory `detect_changes` または相当する impact query で、変更が helper deletion の範囲に留まることを確認する。

---

## 7. low-confidence CALLS は最新 graph から全件取り直す

旧調査の数値:

```text
confidence < 0.5 = 692
builtins target = 240
tests target = 215
app/other = 237
```

は historical baseline であり、**現在値ではない**。

その後に source cleanup、dotenv canonicalization、weather canonicalization、runtime cleanup、current docs 追加が行われている。

したがって今回必ず current graph から再取得する。

### 7.1 取得条件

対象 edge:

```text
edge_type = CALLS
confidence < 0.5
```

可能なら threshold 別にも集計:

```text
< 0.25
0.25 <= confidence < 0.5
```

### 7.2 edge 単位で保存する field

最低限:

```text
caller_symbol
caller_kind
caller_file
caller_line
callee_symbol
callee_kind
callee_file_or_external_target
callee_line_if_known
confidence
resolution_strategy
edge_type
```

追加で取得できるなら:

```text
edge_id
raw_call_text
source_snippet
is_test_source
is_test_target
```

### 7.3 aggregate だけで終わらない

「app-local 200件」のような集計だけを保存してはならない。

次のレビューで個々の edge をソースへ戻れるよう、edge identity を残すこと。

raw evidence が大きい場合は ignored `.log` を force-add しない。ユーザーが追跡を要求した場合のみ sanitised `.txt` / `.md` とし、秘密情報を含めない。

---

## 8. low-confidence CALLS の分類

各 edge を次のいずれか一つへ分類する。

### A. `source-fix`

ソース側に独立した問題がある。

例:

- stale alias/import により call owner が曖昧
- 不要な indirection が残っている
- string dispatch が不要なのに残存
- wrapper が完全に obsolete で compatibility 根拠なし
- actual call と intended call の不一致をソース review で確認

**CodebaseMemory が低信頼だから**ではなく、source/tests/history を読んでも問題と判断できる場合だけここに入れる。

### B. `graph-false-positive`

ソースは明確だが graph resolver が誤解している。

既知例:

```python
current = now or datetime.now()
```

を別の `.now()` method へ解決する等。

この分類は production source を変更しない。

### C. `dynamic-external-api`

Firestore等の external SDK の fluent/dynamic method。

例:

```python
query.stream()
files().create(...).execute()
```

receiver type が `Any` / SDK dynamic object のため local method と誤結合するケース。

sourceをgraph向けに書き換えない。

### D. `test-fake`

SDK-shaped fake / stub の method 名へ resolver が誤結合するケース。

production implementation と fake の双方を graph confidence 向上目的で変更しない。

### E. `builtins-runtime`

`datetime.now`、`Path`、標準ライブラリ、builtin等。

resolver ambiguity だけなら変更しない。

### F. `intentional-dynamic-dispatch`

`getattr`、plugin boundary、compatibility shim、explicit string routing等で動的呼出しが設計上必要。

必要なら comment/test で intent を守るが、静的graphのために設計を変えない。

### G. `needs-more-evidence`

ソース・history・testだけで確定できない。

推測で `source-fix` に昇格しない。

---

## 9. low-confidence edge を深掘りする順序

全edgeを同じ重さで調べない。

優先順位:

1. production caller -> production callee に見える app-local edge
2. historical-failure protected runtime に接触する edge
3. persistence / state mutation / device control edge
4. external SDK / test fake と疑われる edge
5. builtin/runtime edge

ただし protected runtime は「優先的に直す」のではなく「誤修正の損害が大きいので優先的に正しく分類する」という意味である。

---

## 10. `source-fix` 候補の昇格条件

edge を `source-fix` とする前に必ず:

1. caller sourceを読む
2. callee sourceを読む
3. direct `rg` / exact symbol search
4. git history確認
5. relevant tests確認
6. architecture/ADR boundary確認
7. CodebaseMemoryの関連caller/callee確認

を実施する。

「名前が似ている」「confidenceが0.3」「inboundが0」だけでは source-fix にしない。

---

## 11. `source-fix` は分類PRと同時実装しない

low-confidence classification 中に source defect を見つけても、原則その場で大量修正しない。

各 source-fix は原因ごとに別 logical patch とする。

例:

```text
PR A: stale private wrapper deletion
PR B: obsolete alias cleanup
PR C: dynamic dispatch simplification
```

無関係な edge を一つの「CodebaseMemory cleanup PR」にまとめない。

---

## 12. keep-separate / no-change の扱い

既知の意図的境界を再統合しない。

特に:

- Phase 1 shadow weather classification
- domain-specific JSON readers
- backend adapter parity
- standalone operational scripts
- SDK-shaped test fakes
- compatibility seams with explicit history

は similarity / low-confidence CALLS を理由に統合しない。

既存の `codebase-memory: keep-separate` comment は、その意図がまだ有効なら維持する。

---

## 13. runtime protected path

今回の primary scope は Energy Plan archive と CALLS classification である。

23:00 / 03:00 / 07:00 runtime の `HISTORICAL_FAILURE_LOCK` を変更しない。

low-confidence edge が protected runtime を指していても、まず分類だけ行う。

source defect が本当に見つかった場合は、historical protection tests と failure history を読み、別作業として提案する。

---

## 14. CodebaseMemory artifact 更新

### source change が `_archive_weather_rows` deletion のみの場合

1. source patch
2. focused tests / quality
3. source-bearing commit 作成
4. explicit CodebaseMemory reindex を1回
5. generated artifact を最後のartifact commitとして追加

artifact commit で HEAD が進んでも二度目のrefreshをしない。

### classificationだけでsource changeがない場合

active `docs/current` を変更したなら、merge後に shared graph freshness policy に従って必要な時だけ refresh する。

artifact metadataをHEAD一致させること自体を目的にしない。

---

## 15. 次回報告に必ず含める内容

### `_archive_weather_rows`

```text
verdict: DELETE / KEEP / BLOCKED
current refs:
history refs:
CodebaseMemory inbound:
dynamic/string refs:
test refs:
implemented change:
tests:
```

### low-confidence CALLS

```text
current total low-confidence CALLS:
confidence bands:
resolution strategy counts:
classification counts:
source-fix count:
graph-false-positive count:
dynamic-external-api count:
test-fake count:
builtins-runtime count:
intentional-dynamic-dispatch count:
needs-more-evidence count:
```

さらに `source-fix` 各候補について:

```text
caller
callee
file/line
confidence
resolution_strategy
source evidence
history evidence
test evidence
recommended patch
recommended tests
risk
```

を記録する。

---

## 16. 完了条件

このフェーズは次を全て満たした時に完了とする。

- `_archive_weather_rows` を DELETE / KEEP / BLOCKED のいずれかへ根拠付きで確定
- DELETEの場合は定義だけの最小patch + focused regressionが成功
- `_archive_weather_history` compatibility/injection seamを誤って削除していない
- current graphからlow-confidence CALLSをedge単位で再取得
- 全edgeを7カテゴリのいずれかへ分類、または未処理数を明示
- source-fixだけを別patch候補として抽出
- graph confidence改善だけを目的とするproduction rewriteをしていない
- protected runtimeを不用意に変更していない
- source-bearing artifact refreshは必要時に1回だけ

最優先は「静的graphを美しくすること」ではなく、**実際の保守リスクを減らしながら、解析ノイズをソース変更へ誤変換しないこと**である。

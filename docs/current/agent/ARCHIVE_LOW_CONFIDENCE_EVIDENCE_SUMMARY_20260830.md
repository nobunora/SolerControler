# Archive / low-confidence CALLS evidence summary — 2026-08-30

## Current master

```text
HEAD: 199419f14bdbab533c8c4950b1540e48d7ac2ddb
artifact source-bearing commit: 13f165099e46473142927d0513ab8cab6722fb76
CodebaseMemory: nodes=5743 edges=18040
quality run 141: SUCCESS
```

## `_run_optional`

PR #18後の `13f165099e...` で `app/runtime/command_adapter.py::_run_optional` の定義7行だけが削除された。

評価: **完了・妥当**。

- `_run` 非変更
- timeout/deadline 非変更
- process kill 非変更
- retry 非変更
- 23/03/07 protected runtime 非変更
- current quality SUCCESS

このhelperを再導入しない。

## `_archive_weather_rows`

現行:

```python
def _archive_weather_rows(...):
    return archive_weather_history(...).rows
```

現行 production path は `WeatherHistoryPort.load_history() -> WeatherHistoryFetchResult` を使用し、`_archive_weather_rows`を使用しない。

履歴:

- `42b40aa3...`（2026-07-19 result model抽出直前）でも `_archive_weather_rows` は存在したが、`_build_consumption_forecasts()` は `_archive_weather_history()` を直接使用。
- 同時点の `tests/test_energy_model.py` も `_archive_weather_history` をimport。
- `897dfdfa...` で `WeatherHistoryFetchResult` を正式model化。
- `90ae2150...` で `WeatherHistoryPort` を導入し、execution pathをresult object boundaryへ移行。
- `8c285bb0...` でcanonical archive retrievalを `app.energy_plan.weather_history.archive_weather_history` へ抽出。
- `3c62b7b5...` では dependency-injection / runtime-test patch boundaryとして残すprivate seamを `_archive_weather_history` と明記。`_archive_weather_rows`の保持理由は記録されていない。
- current `tests/test_energy_model.py` はcanonical `archive_weather_history as _archive_weather_history` をimport。

現時点 verdict:

```text
_archive_weather_rows = DELETE CANDIDATE
```

ただし GitHub connector から shared binary graph の inbound edge を直接queryできないため、削除前に local CodebaseMemory + `rg` + `git log -S` を必須とする。

`_archive_weather_history` は別物であり削除禁止。

## low-confidence CALLS

旧値:

```text
confidence < 0.5 = 692
builtins target = 240
tests target = 215
app/other = 237
```

は historical baseline のみ。現在値として使わない。

最新 artifact source commit `13f165099e...` の graph から edge 単位で再取得する。

分類:

```text
source-fix
graph-false-positive
dynamic-external-api
test-fake
builtins-runtime
intentional-dynamic-dispatch
needs-more-evidence
```

`source-fix`だけを後続source patchへ進める。

既知の false-positive / no-change pattern:

- explicit `datetime.now()` の誤解決
- Firestore等fluent SDK methodのlocal fakeへの誤結合
- SDK-shaped test fake
- readable direct callのinbound miss

CodebaseMemory confidence向上だけを目的にproduction sourceを変更しない。

## Recommended order

```text
1. local CBM / rg / history で `_archive_weather_rows` 最終確認
2. 条件成立時のみ helper定義単体を削除
3. Energy Plan focused tests + quality
4. latest low-confidence CALLSをedge単位でexport
5. 7分類へ振り分け
6. source-fixだけを個別patch候補化
7. source-bearing状態を必要時に1回だけreindex
```

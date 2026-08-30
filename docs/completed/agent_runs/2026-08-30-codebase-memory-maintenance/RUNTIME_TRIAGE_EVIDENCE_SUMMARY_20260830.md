# Runtime / CodebaseMemory 次フェーズ判断サマリ

## 現在の評価

- operational weather-class canonicalization は仕様どおり完了している。
- Phase 1 shadow weather classification は intentional separate contract のまま保護されている。
- shared graph artifact は source-bearing commit `41e749568d3a4df61ef9dbd0c87706159f4b3a33` を一度だけindexした状態で、artifact-only HEADを再追従していない。
- current HEAD quality workflow run 131 は成功している。

## 次候補

### `_run_optional`

現在は `app/runtime/command_adapter.py` に定義されるが、現行 `cloud_job.py` はimportせず、23/03/07 `slot_orchestration.py`にもgeneric optional command経路はない。

履歴上は 2026-08-02 の orchestration split で `cloud_job.py` から command adapter へ移された。2026-08-29 の time-ownership isolation 後に利用経路が消えた可能性が高い。

判定: **strong delete candidate, final local evidence required**。

最終削除条件と検証手順は `CODEX_NEXT_RUNTIME_UNUSED_AND_LOW_CONFIDENCE_CALLS_TRIAGE_JA.md`（同一アーカイブディレクトリ）に従う。

### `_archive_weather_rows`

過去調査では definition-only 候補だが、`app/energy_plan/workflow.py` には `_weather_class` のように tests compatibility のため意図的に残されたprivate seamが実在する。

判定: **HOLD pending history/compatibility evidence**。

`_run_optional` と同じpatchで削除してはならない。

## Low-confidence CALLS

初回調査の `692 / app-other 237` は現在値として再利用しない。

weather canonicalization後の shared graph から edge fields を再取得し、次に分類する。

- source-fix
- graph-false-positive
- dynamic-external-api
- test-fake
- builtins-runtime
- intentional-dynamic-dispatch
- needs-more-evidence

production sourceを CodebaseMemory confidence 改善だけのために変更しない。

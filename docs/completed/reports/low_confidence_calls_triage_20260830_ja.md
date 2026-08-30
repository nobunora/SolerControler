# low-confidence CALLS 全件トリアージ報告（2026-08-30）

## 結論

`C-VSC-SolerControler` の現行CodebaseMemory graphを再確認し、confidence `< 0.5` の `CALLS` 692件を全件取得・重複検査・分類した。source-fixに昇格できる実欠陥は0件であり、production sourceは変更していない。

## 実行基準

- status/evidence基準: `07a01fee12a0e7540402561e8e4fe88827ebeb92`
- 最終共有artifact commit: `8dfb210428b656ee0db4f545140af033c3efa166`
- CodebaseMemory: 0.10.8
- index status: `ready`
- graph: 5,901 nodes / 18,194 edges
- coverage: skipped 0、parse_partial 9（scriptsの指定行を含むが、今回の692 edgeのcaller/callee行とは重ならない）

## CALLS inventory

| 項目 | 件数 |
|---|---:|
| current CALLS | 4,443 |
| confidence `< 0.25` | 183 |
| `0.25 <= confidence < 0.5` | 509 |
| low-confidence合計 | 692 |
| raw export | 692 |
| dedupe後 | 692 |
| 未処理 | 0 |

resolution strategyは `suffix_match=355`、`unique_name=337` だった。

## 分類結果

| 分類 | 件数 | 判断 |
|---|---:|---|
| `source-fix` | 0 | source・history・testsで実欠陥を確認できるものなし |
| `graph-false-positive` | 237 | resolverの同名衝突または低信頼な候補表示。sourceは維持 |
| `dynamic-external-api` | 79 | Firestore/Drive/DB等の外部fluent callがtest fakeへ解決 |
| `test-fake` | 136 | pytest fake、monkeypatch、test helperへの解決 |
| `builtins-runtime` | 240 | `dict.get`、`str.lower`、`list.append`等のbuiltin/stdlib |
| `intentional-dynamic-dispatch` | 0 | 今回の低信頼集合では該当なし |
| `needs-more-evidence` | 0 | 全件でcaller source lineを確認済み |

checksum:

```text
0 + 237 + 79 + 136 + 240 + 0 + 0 + 0 = 692
classified + unprocessed = 692 + 0 = 692
```

## 代表的な根拠

- `datetime.now` が `app/runtime/cloud_job.py::_SystemMonitorClock.now` に寄る44件、および同ファイル内のclock lambda 2件は、suffix resolver由来であり、時刻処理の書き換え理由にならない。
- 各CLIの `parser.parse_args` が `diagnose_hourly_pv_adaptive_gate.parse_args` に寄る13件は、CLI parserの同名衝突である。
- Firestore/Drive fluent callが `tests/test_drive_backup.py` や `tests/test_firestore_dashboard_metrics.py` のfakeへ寄る79件は、外部API境界を維持する。
- DB cursor/fetch、pytest helper、test callerからtest doubleへ寄る136件は、テスト境界のノイズである。
- builtin/stdlib target 240件は未使用edgeではなく、通常のruntime呼び出しである。
- その他の直接ローカル呼び出しはsource expressionとcallee basenameが一致しており、confidence改善だけを目的としたrename/refactorは行わない。

## 保存した証拠

全edgeのraw/classified evidenceは作業用の非追跡ファイルに保存した。

`artifacts/codebase_memory/low_confidence_calls_20260830_classified.jsonl`

- 692 records
- dedupe key: caller symbol/file/line + callee symbol/file + confidence + strategy
- SHA-256: `3bfb38328697f47d873fda1963b1ebc9d60012843c8564ebc59ed643d483f366`
- 各recordにsource line、classification、reason、verification statusを含む

 tracked statusは [`LOW_CONFIDENCE_CALLS_TRIAGE_STATUS_20260830.md`](../agent_runs/2026-08-30-codebase-memory-maintenance/LOW_CONFIDENCE_CALLS_TRIAGE_STATUS_20260830.md) に保存した。

## 変更・検証

- 変更したsource: なし
- 変更したtracked docs: status file、本文書
- Ruff: `0.16.1`, `python -m ruff check .` 成功
- Import Linter / ty / deptry / Oxlint / tsc: 未導入または今回のsource無変更では非該当。未導入を設定済みとは扱わない
- `git diff --check`: 成功
- production deployment: 実施していない

## commit / push

1. `07a01fe` — `docs: complete low-confidence CALLS triage status`（status/evidence）
2. `8dfb210` — `chore: refresh CodebaseMemory graph after CALLS triage`（shared artifact）

両commitを `origin/master` へpush済みで、最終確認時の local/remote SHAは `8dfb210428b656ee0db4f545140af033c3efa166` で一致している。

CodebaseMemory watcherがartifact-only HEADを追従して一時的に生成物を更新したが、自己追従refreshは禁止のため、その差分は破棄し、push済みartifact commitの状態へ戻した。

## 次の対応

source-fix候補は0件のため、追加PRは作成しない。将来source欠陥が確認された場合は、今回の分類結果を根拠として、原因別の小さいPRに分離する。

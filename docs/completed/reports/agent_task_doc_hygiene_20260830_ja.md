# CodebaseMemory agent task文書衛生化 実施報告（2026-08-30）

## 結果

完了。リモート更新（PR #22）をfast-forwardで取り込み、完了済みの一回限りのagent task文書を履歴領域へ移動した。terminal metadataを正規化し、参照切れを検査した後、CodebaseMemoryを再インデックスしてartifactを共有状態へ更新し、コミット・プッシュした。

## Git / リモート

| 項目 | 値 |
| --- | --- |
| fetch/pull前のローカルHEAD | `9abf6aa` |
| 取り込み後の基準HEAD | `52e10f52043c76eab64cd2edb7a634a9c06762fd` |
| 取り込んだ更新 | `fb946fe`（terminal metadata正規化要求）、`76d3d42`（文書衛生タスク定義）、PR #22 merge |
| 文書移行コミット | `7ce2303c81b420f725fb1f08461705f724216fd3` |
| CodebaseMemory artifactコミット | `5881528` |
| 作業ブランチ | `master` |

## 監査した候補

次の9件について、ファイル内容、Git履歴、実装着地、status/result、参照関係を確認した。いずれも完了済みの一回限りの指示・status・evidenceで、現行の耐久ルールではないため移動対象とした。

- `ARCHIVE_LOW_CONFIDENCE_EVIDENCE_SUMMARY_20260830.md`
- `CODEX_EXECUTE_LOW_CONFIDENCE_CALLS_TRIAGE_HANDOFF_JA.md`
- `CODEX_NEXT_ARCHIVE_WEATHER_AND_LOW_CONFIDENCE_CALLS_JA.md`
- `CODEX_NEXT_RUNTIME_UNUSED_AND_LOW_CONFIDENCE_CALLS_TRIAGE_JA.md`
- `CODEX_NEXT_WEATHER_CLASS_CANONICALIZATION_AND_CBM_USE_JA.md`
- `CODEX_RETRY_LOW_CONFIDENCE_CALLS_TRIAGE_JA.md`
- `LOW_CONFIDENCE_CALLS_TRIAGE_STATUS_20260830.md`
- `RUNTIME_TRIAGE_EVIDENCE_SUMMARY_20260830.md`
- `CODEX_NEXT_AGENT_TASK_DOC_HYGIENE_JA.md`（今回の指示ファイル自身）

## 移動先

全候補を履歴保持のため`git mv`で次へ移動した。

`docs/completed/agent_runs/2026-08-30-codebase-memory-maintenance/`

現行に残したのはdurable guideのみで、`README.md`、CodebaseMemory共有/triage規則、作業・レビュー・安全境界・報告・Skill規則など12件は変更していない。これらは今後の作業方法を定義するcurrent contractであるためである。

## terminal metadata正規化

`LOW_CONFIDENCE_CALLS_TRIAGE_STATUS_20260830.md`の次の未確定値を、完了レポートとGit履歴で確認できた値に置換した。

```text
artifact_commit: 8dfb210428b656ee0db4f545140af033c3efa166
```

候補文書全体を`pending`、`TO_FILL`、古いパス、terminal recordと矛盾する値について検索した。残る`pending`は履歴文書中のHOLD判定または今回の指示文書が示す旧値の説明例であり、terminal metadataの未確定値ではない。推測による補完は行っていない。

## 参照・リンク検証

- 移動後の候補相互参照を`docs/completed/agent_runs/2026-08-30-codebase-memory-maintenance/`へ更新した。
- 既存の完了レポートからstatusへのリンクを同じ履歴領域へ更新した。
- Markdown相対リンク検査対象（移動9件、既存完了レポート）: `BROKEN_LINKS=0`。
- `docs/current/agent`から移動済み候補への古いMarkdownリンクは残っていない。
- statusの`changed_files`にある旧パスは、過去の実コミット時点の変更ファイルを記録する証跡であり、リンクではないため事実として保持した。

## `.cbmignore`

既存の`docs/completed/reports/`除外は維持し、次の1行だけを追加した。

```text
docs/completed/agent_runs/
```

`docs/completed/**`全体は除外していない。現行ガイド、source、testsは引き続きインデックス対象である。

## CodebaseMemory

### 更新前（作業開始時のMCP status）

- project: `C-VSC-SolerControler`
- status: `ready`
- nodes: `5924`
- edges: `18217`
- parse_partial: 9 files
- skipped: 0 files

### 更新後

- project: `SolerControler`
- status: `ready`
- nodes: `5660`
- edges: `17929`
- indexed_at: `2026-08-30T14:00:15Z`（status記録、artifact JSONは`2026-08-30T14:00:17Z`）
- parse_partial: 9 files（既存のPowerShell/config範囲、内容は未変更）
- skipped: 0 files
- artifact source commit: `7ce2303c81b420f725fb1f08461705f724216fd3`
- artifact log: `C:/Users/nobun/.cache/codebase-memory-mcp/logs/SolerControler-1788098417.log`

ノード/エッジ減少は、完了済みagent run文書をインデックスから意図的に除外した結果であり、sourceの削除やグラフ品質向上を目的とした変更ではない。`check_index_coverage`では`docs/current/agent`に記録上の問題なし、`app`/`scripts`/`tests`は既存のキャッシュ除外・PowerShell parse_partialのみ、completed agent runは意図どおり`not_indexed_dir`となった。

artifact-only commit自身を含めるための二度目のrefreshは行っていない。

## 品質確認

- `git diff --check`: PASS
- Markdown相対リンク検査: PASS（`BROKEN_LINKS=0`）
- `python -m ruff check .`: PASS（`All checks passed!`）
- source変更: なし
- runtime behavior変更: なし
- tests/focused pytest: docs-onlyのため未実施
- ty / deptry / Oxlint / tsc / import-linter: source・依存・JS/TS・import boundary変更なしのため未実施

## 次の推奨作業

以後、実装検討前は現行のCodebaseMemory共有グラフを`index_status`で`ready`確認し、対象シンボルのgraph検索・coverage確認を先に行う。完了済みagent run文書は`docs/completed/agent_runs/`で参照し、currentには耐久ルールだけを置く。

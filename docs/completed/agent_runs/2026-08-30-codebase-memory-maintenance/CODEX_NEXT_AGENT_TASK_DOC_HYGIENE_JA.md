# Codex次作業指示: 完了済みagent task文書を`docs/current`から分離する

## 0. 目的

2026-08-30のCodebaseMemory low-confidence `CALLS` 全件トリアージは完了した。

確認済み成果:

```text
master baseline at task creation:
9abf6aaeb4ff3f8d0c58ecc777c35e3a6a3d7b73

status/evidence commit:
07a01fee12a0e7540402561e8e4fe88827ebeb92

shared artifact commit:
8dfb210428b656ee0db4f545140af033c3efa166

CodebaseMemory: 0.10.8
index: ready
nodes: 5901
edges: 18194
current CALLS: 4443
low-confidence CALLS: 692
classified: 692
unprocessed: 0
checksum: PASS
source-fix: 0
```

したがってlow-confidence `CALLS`を理由にproduction sourceを修正する次PRは不要である。

一方、`docs/current/agent/`には、一回限りの実行指示・完了status・evidence summaryが複数残っている。`docs/current/agent/README.md`がこの場所を「現行ガイド」と位置付けているため、完了済みone-shot task文書を残し続けると、agentの誤読、CodebaseMemory探索ノイズ、不要なartifact stale判定、durable ruleとhistorical evidenceの境界不明瞭化を招く。

この作業ではproduction sourceには触れず、agent文書のcurrent/completed境界だけを整理する。

## 1. 最初に読むもの

1. `AGENTS.md`
2. `docs/current/agent/README.md`
3. `docs/current/agent/agent_working_rules.md`
4. `docs/current/agent/codebase_memory_shared_graph_usage_ja.md`
5. `docs/current/agent/codebase_memory_triage_and_maintenance_ja.md`
6. `.cbmignore`
7. `docs/completed/reports/low_confidence_calls_triage_20260830_ja.md`

候補リストをsource of truthと決め打ちせず、実ファイル・Git history・参照元を確認する。

## 2. 作業開始時の確認

PowerShell 7で最低限:

```powershell
git status --short
git rev-parse HEAD
Get-Content .codebase-memory/artifact.json
Get-Content .cbmignore
Get-ChildItem docs/current/agent -File | Select-Object Name
```

working tree、current master、artifact project/source commit、artifact以降の変更を確認する。このPR merge後は`docs/current/**`が変わるため、現行ルールに従ってCodebaseMemoryの鮮度を判定する。

## 3. completed監査の第一候補

```text
docs/completed/agent_runs/2026-08-30-codebase-memory-maintenance/ARCHIVE_LOW_CONFIDENCE_EVIDENCE_SUMMARY_20260830.md
docs/completed/agent_runs/2026-08-30-codebase-memory-maintenance/CODEX_EXECUTE_LOW_CONFIDENCE_CALLS_TRIAGE_HANDOFF_JA.md
docs/completed/agent_runs/2026-08-30-codebase-memory-maintenance/CODEX_NEXT_ARCHIVE_WEATHER_AND_LOW_CONFIDENCE_CALLS_JA.md
docs/completed/agent_runs/2026-08-30-codebase-memory-maintenance/CODEX_NEXT_RUNTIME_UNUSED_AND_LOW_CONFIDENCE_CALLS_TRIAGE_JA.md
docs/completed/agent_runs/2026-08-30-codebase-memory-maintenance/CODEX_NEXT_WEATHER_CLASS_CANONICALIZATION_AND_CBM_USE_JA.md
docs/completed/agent_runs/2026-08-30-codebase-memory-maintenance/CODEX_RETRY_LOW_CONFIDENCE_CALLS_TRIAGE_JA.md
docs/completed/agent_runs/2026-08-30-codebase-memory-maintenance/LOW_CONFIDENCE_CALLS_TRIAGE_STATUS_20260830.md
docs/completed/agent_runs/2026-08-30-codebase-memory-maintenance/RUNTIME_TRIAGE_EVIDENCE_SUMMARY_20260830.md
```

名前だけで移動しない。各ファイルについてpurpose、creation PR/commit、要求された実装が着地済みか、status/resultがterminalか、current文書がactive contractとして参照しているか、将来もdurable ruleとして必要かを確認する。不明なら移動せず理由を記録する。

### 3.1 archive前のterminal metadata整合

`LOW_CONFIDENCE_CALLS_TRIAGE_STATUS_20260830.md`は`status: COMPLETE`、`checksum_status: PASS`、`unprocessed_count: 0`だが、`Shared graph refresh`節の次の値だけがpre-artifact snapshotのまま残っている。

```text
artifact_commit: pending status/evidence commit
```

実際の最終共有artifact commitは完了レポートとGit historyで確認済みの:

```text
8dfb210428b656ee0db4f545140af033c3efa166
```

である。

historical destinationへ移す前に、Git historyと`.codebase-memory/artifact.json`を再確認し、statusの`artifact_commit`をこの確定値へbackfillする。これは調査結果の変更ではなく、terminal recordのmetadata正規化である。

他にも`pending`、`TO_FILL`、古いpath、実際のfinal commitと矛盾するterminal metadataが候補文書に残っていないか検索する。ただし推測で値を埋めず、Git/成果物で確定できるものだけ修正する。

## 4. currentに残すdurable guide

少なくとも次はcompleted候補に含めない。

```text
docs/current/agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md
docs/current/agent/README.md
docs/current/agent/agent_working_rules.md
docs/current/agent/ai_pwsh_bridge_usage_rules.md
docs/current/agent/bad_patterns.md
docs/current/agent/code_review.md
docs/current/agent/codebase_memory_shared_graph_usage_ja.md
docs/current/agent/codebase_memory_triage_and_maintenance_ja.md
docs/current/agent/codex_token_usage_rules.md
docs/current/agent/design_intent_rules.md
docs/current/agent/report_template.md
docs/current/agent/skill_creation_rules.md
```

これらは将来の作業方法・安全境界・review規則を定義するdurable contractであり、内容変更・移動とも今回のscope外。

## 5. product / Phase 1文書には触れない

`docs/current/product/**`等の`CODEX_*`文書を名前だけでhistoricalと判断しない。今回のscopeは`docs/current/agent/`のone-shot agent task文書だけ。

対象外:

```text
docs/current/product/**
docs/current/architecture/**
docs/current/ops/**
docs/current/prompts/**
Phase 1 selector / shadow / actual contract
SOC protected paths
```

## 6. historical destination

completedと確認できたagent task文書は次へ移動する。

```text
docs/completed/agent_runs/2026-08-30-codebase-memory-maintenance/
```

単純削除せず、原則`git mv`でGit history上の成果物を保持する。

## 7. `.cbmignore`更新

移動と同時に次を追加する。

```text
docs/completed/agent_runs/
```

source/tests/current docsはindexし、completed one-shot agent runだけを通常探索から外す。`docs/completed/**`全体をignoreしない。

## 8. reference audit

移動前に全候補への参照を`rg`で検索する。参照を`active-current-reference`、`historical-reference`、`self-reference`、`completed-report-reference`へ分類する。

current guideから参照される場合はactive dependencyか確認する。historical evidenceへのリンクなら移動後pathへ更新し、active contractそのものなら候補を移動しない。broken relative linkを残さない。

## 9. READMEの役割

`docs/current/agent/README.md`はcurrent guideの索引であり、one-shot task履歴の索引にしない。durable guide一覧が実態と一致しているか確認し、必要な最小限のpath/index修正だけを行う。

## 10. この指示ファイル自身

このファイルもone-shot task instructionである。実作業COMPLETE時はcurrentに残さず、次へ移動する。

```text
docs/completed/agent_runs/2026-08-30-codebase-memory-maintenance/CODEX_NEXT_AGENT_TASK_DOC_HYGIENE_JA.md
```

## 11. CodebaseMemory refresh順序

この作業は`docs/current/**`と`.cbmignore`を変更するためshared graphの意味が変わる。

推奨順序:

```text
1. candidate audit
2. terminal metadata整合
3. git mv / reference path修正
4. .cbmignore更新
5. READMEの必要最小限修正
6. git diff --check
7. docs migration commit
8. CodebaseMemory refresh / status=ready確認
9. source/tests/current durable docsのcoverage確認
10. completed/agent_runs除外確認
11. .codebase-memory artifact-only commit
```

artifact-only commit自身を追う二度目refreshは禁止。

## 12. refresh後に記録するもの

```text
before_nodes
before_edges
after_nodes
after_edges
index_status
parse_partial_count
skipped_count
excluded historical agent-run files
```

node/edge減少自体を成功条件にしない。current durable guide・source・testsがgraphに残り、completed agent runだけが意図どおり除外されることを確認する。

## 13. source / runtime変更禁止

変更可能:

```text
docs/current/agent/**
docs/completed/agent_runs/**
.cbmignore
.codebase-memory/** generated artifact
```

変更禁止:

```text
app/**
scripts/** implementation
tests/**
pyproject.toml
requirements*
static/**
templates/**
operational config behavior
```

production deploymentもしない。

## 14. raw 692-edge evidenceの監査性は別課題

今回のCALLS triageでは、全692 edgeのclassified raw evidenceは非追跡local artifactに保存され、tracked docsにはSHA-256、aggregate counts、query方法だけが残っている。これはCOMPLETE判定を否定しないが、GitHubだけで第三者が692行すべてを再inspectionできる状態ではない。

ただしこのdoc hygiene作業でraw JSONLを無条件にGit追跡してはならない。機密・local path・generated evidence policyを含め、raw evidence retention policyは別のlogical PRで検討する。

## 15. 完了条件

1. `docs/current/agent/`のone-shot候補を実ファイル/history/referenceで監査した。
2. historical archive前にterminal metadataの`pending`/`TO_FILL`/確定commit不整合を監査し、Gitで確定できる値だけ正規化した。
3. completedと確認できたものだけをhistorical destinationへ移動した。
4. active/uncertainなものを誤移動していない。
5. durable guideを移動していない。
6. product等へscope拡張していない。
7. `.cbmignore`へ`docs/completed/agent_runs/`だけを限定追加した。
8. repo内参照を監査し必要なpath更新を行った。
9. broken relative linkを残していない。
10. この指示ファイル自身も完了時にhistoricalへ移動した。
11. `git diff --check`成功。
12. production source/tests/runtime behavior変更なし。
13. docs migration後にCodebaseMemoryを1回refreshした。
14. CodebaseMemory `status=ready`確認。
15. current durable docs/source/testsがcoverageに残る。
16. completed agent runsが意図どおり除外される。
17. artifact-only commitを追う二度目refreshなし。
18. repository quality workflow成功。

## 16. STOP条件

次の場合は移動せず`retain-current`として理由を報告する。

- task完了を確認できない
- current architecture/product contractがactive sourceとして参照している
- historical destination移動で意味が変わる
- reference graphが複雑で安全なpath更新を確定できない

「名前がCODEX_NEXTだから」だけを理由に移動しない。

## 17. 最終報告フォーマット

```text
Result: COMPLETE / PARTIAL / BLOCKED
Baseline HEAD:
Migration commit:
Artifact commit:
Audited candidates:
Moved to completed:
Retained current:
Reason for retained files:
Terminal metadata normalized:
References updated:
Broken-link check:
.cbmignore change:
CodebaseMemory before nodes/edges:
CodebaseMemory after nodes/edges/status/parse_partial/skipped:
Coverage verification:
Source changes: NO
Runtime behavior changes: NO
Quality workflow:
Next recommended task:
```

## 18. 最重要ルール

この作業はsource cleanupではない。low-confidence CALLS triageは`source-fix=0`で完了している。

次に行うのは、完了済みone-shot agent task文書をcurrent contractから分離し、CodebaseMemoryとagent navigationの信号対雑音比を改善する文書衛生作業である。node/edge数を減らすこと自体を目的化せず、`current = 現行ルール`、`completed = 履歴`という意味境界を正しくする。

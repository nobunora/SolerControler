# Luna軽向け: 2026-08-31 SOC 100%未達の原因切り分け・最小修正・本番反映手順

## 0. この文書の目的

この文書は、2026-08-31朝に「目標SOCは100%の想定なのに、実績SOCが100%手前で強制充電を終了した」事象について、Luna軽が途中で設計判断を行わず、次の順番を固定して完了するための実行指示である。

1. 既存の2026-08-31調査報告を読む。
2. 本番Cloud Run Jobの非機密設定をread-onlyで確認する。
3. CodebaseMemory接続を限定手順で確認する。
4. 100%目標なら99%以下で停止しない契約を回帰テストで固定する。
5. `app/runtime/cloud_job.py`を最小変更する。
6. 03監視のtarget / SOC source / observed value / stop reasonをCloud Loggingへ必ず残す。
7. focused tests、quality gate、full testsを通す。
8. commit / push / CI成功を確認する。
9. production runbookどおりにデプロイする。
10. reversible settings round-tripとCloud Run Job設定を確認する。
11. 次回03定時実行で原因を確定できるログ契約が入った状態にする。

この文書でLunaが独自に仕様を変更してはいけない。

---

## 1. 現在の固定事実

基準調査commit:

- `336e9a1c6ec62aaa029a27de7256ccd25277f5e3`
- `docs: record 2026-08-31 SOC gap investigation`

調査報告:

- `docs/completed/reports/soc_gap_investigation_2026-08-31.md`
- `docs/completed/reports/soc_gap_evidence_2026-08-31.csv`
- `docs/completed/reports/soc_gap_log_summary_2026-08-31.md`

確認済み実績:

- 00:00 SOC 0%
- 03:00 SOC 14%
- 04:00 SOC 50%
- 05:00 SOC 85%
- 05:30 SOC 93%、充電 0.486 kWh
- 06:00 SOC 93%、充電 0 kWh
- 06:30 SOC 93%、充電 0 kWh
- 07:00 SOC 86%、放電 0.698 kWh

03 Cloud Run execution:

- 開始: 2026-08-31 03:00:17 JST
- 完了: 2026-08-31 05:38:54 JST
- 成功数1
- WARNING / ERROR severity 0
- 通常のforced monitor cutoff 06:45 JSTより約66分早く完了

したがって「Cloud Runが06:45 cutoffまで動き続けたが機器だけ93%で充電停止した」とは扱わない。

現行`app/runtime/cloud_job.py::_monitor_partial_forced_and_stop`には、次の停止条件が存在する。

```python
latest >= target - settings.stop_soc_margin_percent
```

現行`app/settings/forced_charge.py`のdefaultは次である。

```text
ADJUST03_FORCE_STOP_SOC_MARGIN_PERCENT default = 1.0
```

よってsource defaultだけならtarget=100でstop thresholdは99であり、93%停止を直接説明しない。

93%付近停止を説明できる候補は、以下の3つに限定して調査する。

A. 本番Cloud Run Jobで`ADJUST03_FORCE_STOP_SOC_MARGIN_PERCENT`が約7以上へoverrideされていた。
B. 2026-08-31の実行時plan targetが100%ではなかった。
C. monitorが参照したrealtime SOCが30分CSVの93%より高い値を返し、target到達と判定した。

これ以外の原因を先に増やしてはいけない。

---

## 2. 今回固定するユーザー要件

ユーザー要件は次のとおり固定する。

> plan targetが100%なら、03 monitorは99%以下のSOCをtarget reachedとして扱ってはいけない。

一般化すると次である。

```text
stop_by_target := latest_soc is not None AND latest_soc >= target_soc
```

次は禁止する。

```text
latest_soc >= target_soc - margin
latest_soc + margin >= target_soc
round(latest_soc) >= target_soc - margin
SocChargeMode candidateをtarget stop thresholdとして使う
```

`SocChargeMode`最大50%候補は、強制充電モードを起動するためのdevice candidateであり、連続値plan targetの停止閾値ではない。

今回`ADJUST03_FORCE_STOP_SOC_MARGIN_PERCENT`設定自体を削除しない。既存env互換性を壊さないため残す。ただし03 target reached判定には使用しない。

---

## 3. 絶対禁止事項

このタスクで以下を行ってはいけない。

- plannerのtarget計算ロジック変更
- 23:00 ownership変更
- 06:45 forced cutoff変更
- 06:50 final standby開始境界変更
- 06:55 03 external I/O hard cutoff変更
- 07:00 green ownership変更
- forced reapply復活
- Firestore persistence / fallback再導入
- lease再導入
- `SocChargeMode`候補ルール変更
- KP-NET candidate parser変更
- realtime SOC HTML parser変更
- retry回数変更
- polling間隔変更
- static forced profile復活
- production secretの表示またはcommit
- `.env`のcommit
- raw Cloud Run全envのログ保存
- 通常03 Jobを現在時刻に手動実行
- CodebaseMemoryが壊れたことを理由にreinstall / package update / MCP設定書換え
- 原因未確定部分を「確定原因」と報告

source変更が上記のいずれかを必要とするように見えた場合は、変更せずSTOPする。

---

## 4. 作業開始時のGit固定手順

PowerShell 7で実行する。

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
git log -5 --oneline
```

条件:

- `master`から開始する。
- この指示PR merge後の最新`master`をpullする。
- user作業由来の未commit変更が存在する場合は上書きしない。
- user変更がある場合は、そのファイル名だけ報告してSTOPする。
- 自分が作った一時成果物だけなら削除してcleanへ戻してよい。

次に必ず読む。

```text
AGENTS.md
docs/current/ops/PRODUCTION_DEPLOYMENT_RUNBOOK_JA.md
docs/current/agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md
docs/completed/reports/soc_gap_investigation_2026-08-31.md
docs/completed/reports/soc_gap_log_summary_2026-08-31.md
app/runtime/cloud_job.py
app/runtime/soc_reading.py
app/settings/forced_charge.py
tests/test_cloud_job_runner.py
```

---

## 5. CodebaseMemory接続確認: 今回の限定手順

今回、CodebaseMemory MCPは複数回`transport closed`で接続不能になった実績がある。

### 5.1 最初の1回

既存repo手順に従い、canonical project `C-VSC-SolerControler`のstatusを1回だけ取得する。

成功して`ready`なら、次のsymbolだけqueryする。

```text
app.runtime.cloud_job::_monitor_partial_forced_and_stop
app.runtime.soc_reading::read_soc_with_fallback
app.settings.forced_charge::ForcedChargeSettings.from_env
app.kpnet.client::KpNetClient.read_realtime_soc_percent
```

query結果は必ずsource / `rg`と照合する。

### 5.2 `transport closed`の場合

次の順番以外を実行しない。

1. exact error文字列を作業報告へ保存する。
2. `index_repository`を呼ばない。
3. duplicate projectを作らない。
4. package reinstallしない。
5. MCP設定を書き換えない。
6. CodebaseMemory version updateをしない。
7. 既存CodebaseMemory processの有無をread-onlyで確認する。
8. MCP statusをもう1回だけ試す。

2回目も`transport closed`なら、次の固定文字列を報告へ入れる。

```text
CBM_TRANSPORT_BLOCKED_USER_AUTHORIZED_SOURCE_FALLBACK
```

このSOC incidentについては、ユーザーがCodebaseMemory接続不能時の再開を明示的に許可している。したがってこの文字列を記録した後は、`rg` + direct source inspectionへ切り替えて続行する。

ただし、CodebaseMemoryを利用できたと偽ってはいけない。

### 5.3 source fallback時の必須`rg`

```powershell
rg -n "stop_soc_margin_percent|ADJUST03_FORCE_STOP_SOC_MARGIN_PERCENT|target_soc_7_percent|03-target-reached-standby|03-immediate-standby" app tests docs/current
rg -n "read_realtime_soc_percent|read_soc_with_fallback|latest_csv_soc_reading" app tests
```

結果を変更対象の絞り込みだけに使用する。

---

## 6. source edit前に本番Cloud Run設定をread-only確認

productionへmutationする前に、現在の`solar-battery-03` Job設定をread-onlyで確認する。

既存runbook / wrapperからproject、region、job nameを解決する。推測でproject IDを作らない。

`gcloud run jobs describe`でJSONを取得してよいが、報告に出してよいenvは以下だけ。

```text
CLOUD_JOB_SLOT
TIMEZONE
ADJUST03_FORCE_STOP_SOC_MARGIN_PERCENT
ADJUST03_FORCE_MONITOR_POLL_SECONDS
ADJUST03_COMPLETION_CONFIRM_BEFORE_MINUTES
ADJUST03_REGENERATE_PLAN
```

認証情報、secret ref、全env一覧を表示してはいけない。

記録する項目:

```text
03_JOB_IMAGE
03_JOB_TASK_TIMEOUT
03_JOB_CLOUD_JOB_SLOT
03_JOB_TIMEZONE
03_JOB_STOP_MARGIN_RAW
03_JOB_POLL_SECONDS_RAW
03_JOB_CONFIRM_BEFORE_RAW
03_JOB_REGENERATE_PLAN_RAW
```

### 6.1 判定ルール

`ADJUST03_FORCE_STOP_SOC_MARGIN_PERCENT`が明示されており、値が0より大きい場合:

```text
CAUSE_SIGNAL_STOP_MARGIN_OVERRIDE=<value>
```

と記録する。

未設定の場合:

```text
CAUSE_SIGNAL_STOP_MARGIN_OVERRIDE=absent(default source value applies)
```

0の場合:

```text
CAUSE_SIGNAL_STOP_MARGIN_OVERRIDE=0
```

この結果にかかわらず、今回のexact-target修正は実施する。

---

## 7. 2026-08-31 target証跡の扱い

過去execution container filesystemを推測で復元しない。

次のどれかに既存証跡がある場合だけtargetを確定してよい。

- 当該executionに紐づくCloud Logging
- 既に保存済みplan artifact
- digest / executionと対応が明確な既存成果物

見つからない場合は次の固定値で報告する。

```text
2026-08-31_EFFECTIVE_TARGET=NOT_RETROACTIVELY_PROVEN
```

ユーザー申告「設定SOCは100%」は重要な運用要件だが、保存ログにtarget値がなければ過去executionのtarget証跡と混同しない。

---

## 8. 今回変更してよいファイル

原則として変更可能なのは以下だけ。

```text
app/runtime/cloud_job.py
tests/test_cloud_job_runner.py
```

必要なら、exact-target契約を説明するコメント更新のみ次を許可する。

```text
docs/current/agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md
```

`app/settings/forced_charge.py`はこのタスクでは変更しない。

他source file変更が必要に見えたらSTOPする。

---

## 9. 実装内容: 判断禁止、以下をそのまま実施

対象:

```text
app/runtime/cloud_job.py::_monitor_partial_forced_and_stop
```

現行の2か所のtarget reached判定から`settings.stop_soc_margin_percent`を外す。

### 9.1 immediate判定

現行概念:

```python
latest is None or latest >= target - settings.stop_soc_margin_percent
```

変更後の契約:

```python
latest is None or latest >= target
```

### 9.2 monitor loop判定

現行概念:

```python
latest is None or latest >= target - settings.stop_soc_margin_percent
```

変更後の契約:

```python
latest is None or latest >= target
```

`None`時にstandbyへ遷移する現行fail-safeは変更しない。

### 9.3 `stop_soc_margin_percent`設定

- dataclass fieldを削除しない。
- env parserを削除しない。
- defaultを変更しない。
- 03 target stop判定では参照しない。

理由: 今回は互換性を壊さず、target reached意味だけを正す最小変更とする。

---

## 10. 03 monitorログを必ず追加する

今回の調査では当日target、SOC source、停止理由が保存されず、過去executionから直接原因を確定できなかった。

この問題を再発させない。

### 10.1 plan読込直後

次の情報を1行でstdoutへ出す。

```text
[cloud_job_runner] 03-monitor contract target=<xx.xx>% exact_target_stop=true configured_stop_margin=<xx.xx>% applied_stop_margin=0.00%
```

値は実際の`target`と`settings.stop_soc_margin_percent`を使用する。

secretを含めない。

### 10.2 SOC readごと

initial readとmonitor loop readの両方で次を出す。

```text
[cloud_job_runner] 03-monitor soc value=<value|none> source=<source> observed_at=<iso|none> target=<xx.xx>% action=<continue|target_reached|soc_unavailable>
```

`SocReading.error`がある場合は、既存の安全なerror文字列だけ追加してよい。

HTML、cookie、URL query、credentialを出してはいけない。

### 10.3 target停止直前

standby write前に必ず次を出す。

```text
[cloud_job_runner] 03-monitor stop reason=target_reached latest=<xx.xx>% target=<xx.xx>%
```

### 10.4 SOC unavailable停止

```text
[cloud_job_runner] 03-monitor stop reason=soc_unavailable target=<xx.xx>%
```

### 10.5 cutoff停止

既存の`03-monitor-cutoff-standby`直前に次を出す。

```text
[cloud_job_runner] 03-monitor stop reason=monitor_cutoff target=<xx.xx>%
```

ログ追加のためだけに新しいlogging frameworkを導入しない。既存`print(..., flush=True)`を使う。

---

## 11. 必須回帰テスト

対象:

```text
tests/test_cloud_job_runner.py
```

既存test helper `_Device`へ、read回数を検証できる最小fieldを追加してよい。

### 11.1 100% exact target

テスト名を固定する。

```text
test_03_target_100_does_not_stop_at_93_or_99_even_with_legacy_margin
```

設定:

```text
ADJUST03_FORCE_STOP_SOC_MARGIN_PERCENT=7
plan target=100
SOC sequence=[93, 99, 100]
03 start=03:00 JST
```

必須assert:

```text
forcedは1回
standbyは1回
SOC readは100を見るまで続く
93ではstandbyしない
99ではstandbyしない
100でstandbyする
```

### 11.2 80% exact target

テスト名:

```text
test_03_target_80_does_not_stop_at_79_with_legacy_margin
```

設定:

```text
ADJUST03_FORCE_STOP_SOC_MARGIN_PERCENT=7
plan target=80
SOC sequence=[79, 80]
```

79では停止せず80で停止すること。

### 11.3 ログ契約

テスト名:

```text
test_03_target_stop_log_records_target_source_and_reason
```

`capsys`を使用し、少なくとも次がstdoutに存在すること。

```text
exact_target_stop=true
target=100.00%
source=fake
reason=target_reached
latest=100.00%
```

### 11.4 既存テストを壊さない

最低限以下を再実行する。

```text
test_03_targets_are_continuous
test_03_mismatch_is_not_reapplied_and_does_not_gate_07
test_03_hard_fence_controls_all_device_io
```

0/30/50/80/100 target contractを維持する。

---

## 12. realtime SOC parserは今回変更禁止

今回の93% CSVとmonitor内部SOCが一致していた証拠はない。

しかしparser faultの直接証拠もない。

よって今回は以下を行わない。

- `_extract_simple_visualization_soc_percent`変更
- `read_realtime_soc_percent`変更
- realtimeよりCSVを優先する変更
- 2-source voting導入

代わりにSection 10のログで次回の値を保存する。

次回同様の未達が起き、ログで`realtime=100`相当なのにCSVが大幅に低いことが確認された場合だけ、別タスクとしてparser / source arbitrationを調査する。

---

## 13. focused validation

source edit後、順番固定で実行する。

### 13.1 diff確認

```powershell
git diff -- app/runtime/cloud_job.py tests/test_cloud_job_runner.py
git diff --check
```

変更対象が許可範囲外ならSTOPする。

### 13.2 focused pytest

```powershell
python -m pytest tests/test_cloud_job_runner.py -q
```

失敗したらproductionへ進まない。

### 13.3 related tests

```powershell
python -m pytest tests/test_soc_monitor_time_fence.py tests/test_kpnet_mode_only_time_fence.py tests/test_kpnet_workflow.py -q
```

### 13.4 quality

repoの現行code-quality-audit手順を実行する。

その後Ruff lintを実行する。formatterは実行しない。

### 13.5 full applicable tests

repo runbook / CIと同等のfull applicable test suiteとimport-linterを実行する。

1件でも失敗したらpush/deployしない。

---

## 14. commit前の原因分類

ここまでのread-only証拠を次の形式で記録する。

```text
STOP_MARGIN_PRODUCTION_VALUE=<value|absent|unknown>
2026-08-31_EFFECTIVE_TARGET=<value|NOT_RETROACTIVELY_PROVEN>
2026-08-31_MONITOR_SOC_AT_STOP=<value|NOT_RETROACTIVELY_PROVEN>
CBM_STATUS=<READY|TRANSPORT_BLOCKED_USER_AUTHORIZED_SOURCE_FALLBACK>
```

原因分類は次から1つだけ選ぶ。

### Case A

本番marginが6%以上など、93%停止を数値上説明できる場合:

```text
CAUSE_CLASS=PRODUCTION_STOP_MARGIN_CAN_EXPLAIN_EARLY_STOP
```

### Case B

当日targetが93〜94%付近と証明できた場合:

```text
CAUSE_CLASS=EXECUTION_TARGET_BELOW_USER_EXPECTATION
```

この場合でもexact-target runtime修正は実施済みとするが、planner targetの追加調査が必要であることを報告する。plannerはこのタスクで直さない。

### Case C

marginが説明せず、target=100が証明でき、monitor realtime SOCが100相当だった場合:

```text
CAUSE_CLASS=REALTIME_VS_CSV_SOC_DISAGREEMENT
```

parserはこのタスクで直さない。追加調査を報告する。

### Case D

証拠不足の場合:

```text
CAUSE_CLASS=HISTORICAL_STOP_REASON_NOT_PROVABLE
```

この場合も「原因不明」とだけ書かず、exact-target契約修正とobservability追加まで完了させる。

---

## 15. source commit

全local validation成功後のみcommitする。

推奨commit messageを固定する。

```text
fix: require exact SOC target before 03 standby
```

commit後:

```powershell
git status --short
git show --stat --oneline HEAD
```

source commit SHAを記録する。

---

## 16. CodebaseMemory shared artifact

### 16.1 MCPがREADYの場合

repoのshared graph freshnessルールに従い、source-bearing final commitを対象にindexを1回だけ更新する。

その後shared artifactを生成し、artifact-only commitにする。

自分自身のartifact commitを理由に再indexしない。

### 16.2 MCPがtransport blockedの場合

artifactを捏造しない。

既存`.codebase-memory/artifact.json`を書き換えない。

次を報告する。

```text
SHARED_CBM_REFRESH=SKIPPED_TRANSPORT_BLOCKED_USER_AUTHORIZED
```

このSOC incidentではユーザーがsource fallback継続を承認しているため、tests / CIが成功していれば次工程へ進む。

---

## 17. pushとGitHub CI

push直前:

```powershell
git status --short
git fetch origin
git rev-parse HEAD
git rev-parse origin/master
```

remoteが自分のbaseから別source commitで進んでいた場合はpushしない。STOPしてSHAを報告する。

fast-forward可能ならpushする。

```powershell
git push origin master
```

push後、最新HEADのGitHub Actions quality workflowがSUCCESSになるまで確認する。

CI failure / cancelled / timed_outならproduction deploy禁止。

---

## 18. production deploy前固定条件

次のすべてがtrueであること。

```text
local focused tests PASS
related tests PASS
full applicable tests PASS
Ruff PASS
git diff --check PASS
GitHub CI PASS
HEAD == origin/master
worktree clean
```

さらに必ず再読する。

```text
docs/current/ops/PRODUCTION_DEPLOYMENT_RUNBOOK_JA.md
```

production wrapper以外の独自deploy commandを作らない。

---

## 19. production gate

runbookに定義されたproduction preflight / security check / ValidateOnlyを順番どおり実行する。

どれか1つでも失敗したらdeployしない。

secret内容を報告に含めない。

---

## 20. production deploy

既存公式wrapper:

```text
scripts/deploy_production_from_env.ps1
```

を使用する。

新しいStatePathを1個だけ作り、そのdeployment中は同じStatePathを使い続ける。

中断時:

1. 新しいdeploymentを開始しない。
2. 同じStatePathを読む。
3. Cloud側terminal stateをread-only確認する。
4. 成功stageだけ保持する。
5. runbookどおり`-Resume`する。

state JSONを手修正しない。

---

## 21. post-deploy settings round-trip

既存runbookどおり、Schedulerを持たない専用settings-roundtrip Jobで実施する。

確認内容:

1. initial settings snapshot取得
2. forced mode適用
3. read-back match
4. 60秒保持
5. 完全なinitial snapshotへrestore
6. restore read-back match

今回のexact target stop修正を確認するために通常03 Jobを現在時刻に手動実行してはいけない。

---

## 22. deployed source / Job確認

デプロイ後、read-onlyで次を確認する。

```text
03 Job image digestが今回deployment imageと一致
03 Job task timeoutが既存契約どおり
03 Schedulerが既存時刻のまま
07 Schedulerが既存時刻のまま
23/03/07 ownership境界に変更なし
```

また、03 Jobのenv `ADJUST03_FORCE_STOP_SOC_MARGIN_PERCENT`が残っていても、今回sourceではtarget reachedへ適用されないことを報告する。

このタスクでenv cleanup mutationはしない。

---

## 23. 次回定時03実行で期待するログ

次回03 Jobでは少なくとも次がCloud Loggingに出る必要がある。

```text
03-monitor contract target=... exact_target_stop=true configured_stop_margin=... applied_stop_margin=0.00%
03-monitor soc value=... source=... observed_at=... target=... action=continue
...
03-monitor soc value=100.00 source=... target=100.00 action=target_reached
03-monitor stop reason=target_reached latest=100.00% target=100.00%
```

100%目標時に99以下のログの直後に`reason=target_reached`が出た場合は回帰失敗である。

次回検証は別スケジュール実行であり、この作業セッションで待機してはいけない。

---

## 24. COMPLETE条件

次をすべて満たした場合のみ、このタスクを`DEPLOYED`と報告してよい。

- [ ] 2026-08-31報告を読んだ
- [ ] production stop margin実値をread-only確認した
- [ ] 過去target証跡の有無を判定した
- [ ] CodebaseMemory statusまたはtransport fallbackを正しく記録した
- [ ] target reached判定からmarginを除外した
- [ ] target=100は93/99で止まらず100で止まるtestがPASS
- [ ] target=80は79で止まらず80で止まるtestがPASS
- [ ] 03 target/source/reasonログ契約testがPASS
- [ ] 既存03 ownership testsがPASS
- [ ] focused tests PASS
- [ ] related tests PASS
- [ ] quality/Ruff PASS
- [ ] full applicable tests PASS
- [ ] source commit作成済み
- [ ] CodebaseMemory READYならfinal shared artifact更新済み
- [ ] CodebaseMemory blockedならskip理由を明記済み
- [ ] origin/masterへpush済み
- [ ] GitHub CI SUCCESS
- [ ] production preflight PASS
- [ ] production deploy PASS
- [ ] settings round-trip PASS
- [ ] 60秒hold PASS
- [ ] full restore read-back PASS
- [ ] deployed image digest確認済み
- [ ] 23/03/07 scheduler ownership変更なし
- [ ] worktree clean
- [ ] HEAD == origin/master

次回朝の実機100%到達は未来の定時executionなので、今回の`DEPLOYED`条件には含めない。

---

## 25. 最終報告テンプレート

必ず以下の順番で短く報告する。

```text
Result: DEPLOYED | BLOCKED

Incident evidence:
- 2026-08-31 03 execution: 03:00:17 -> 05:38:54 JST
- CSV: 05:30=93%, 06:00=93%, 06:30=93%, 07:00=86%

Cause classification:
- CAUSE_CLASS=...
- STOP_MARGIN_PRODUCTION_VALUE=...
- 2026-08-31_EFFECTIVE_TARGET=...
- 2026-08-31_MONITOR_SOC_AT_STOP=...

CodebaseMemory:
- CBM_STATUS=...
- SHARED_CBM_REFRESH=...

Implemented contract:
- target=100 requires SOC >=100 before target standby
- legacy stop margin is not applied to target-reached decision
- target/source/observed_at/stop_reason are logged

Validation:
- focused tests: PASS/FAIL
- related tests: PASS/FAIL
- full tests: PASS/FAIL
- Ruff: PASS/FAIL
- GitHub CI: PASS/FAIL

Git:
- source commit=<sha>
- artifact commit=<sha|none>
- origin/master=<sha>

Production:
- deployment state=<path>
- deployed image=<digest or safe identifier>
- round-trip=PASS/FAIL
- restore=PASS/FAIL

Next scheduled verification:
- confirm target=100 does not emit target_reached below 100
- compare realtime SOC log with official CSV if another discrepancy occurs
```

`Result: COMPLETE`という語は使わない。未来の定時03実行による実機確認が残るため、成功時は`Result: DEPLOYED`とする。

---

## 26. Luna軽への最終拘束

迷った場合に代替案を実装してはいけない。

この文書に明記されていないsource変更が必要になったらSTOPする。

特に次の判断をLuna自身で行わない。

- marginを何%にすべきか
- targetをplanner側で100へ固定すべきか
- realtimeとCSVのどちらを正とすべきか
- 03 Jobを臨時実行すべきか
- CodebaseMemoryをreinstallすべきか

今回実装する答えは1つだけである。

```text
03 target reached = observed SOC >= plan target
```

そして、次回問題が起きたときに必ずtarget / observed SOC / source / stop reasonがCloud Loggingから復元できる状態にする。

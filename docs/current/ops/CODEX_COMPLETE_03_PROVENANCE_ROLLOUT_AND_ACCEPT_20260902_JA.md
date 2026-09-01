# Codex実行指示: 03 plan provenance loggerを本番反映し、2026-09-02 scheduled 03で受入確認する

## 0. 目的

この作業は、すでにmasterへ入った03 plan provenance logging実装を**再設計せず**本番runnerへ反映し、次の通常Scheduler実行である2026-09-02 03:00 JSTの03 Jobから、実際に使用したnight planのbase/final target・optimizer・constraint・candidate情報をCloud Loggingで確定するための完了手順である。

現在の既知状態:

```text
source implementation commit = 6eee4d9c861c03d52ee8e93a003e9c531c36f37c
message = fix: log exact 03 plan SOC provenance
CodebaseMemory artifact commit = 638336c33fb082760bfb394025b4a6bdc1164821
artifact source commit = 6eee4d9c861c03d52ee8e93a003e9c531c36f37c
CodebaseMemory project = C-VSC-SolerControler
nodes = 6145
edges = 18608
quality run on 638336c3 = completed / success
```

既知のhistorical issue:

```text
2026-09-01 runtime contract target = 91%
2026-09-01 stop SOC = 96%
stop reason = target_reached
historical plan base/final provenance = unavailable
```

そのため、今回は過去の91%を推測修正しない。

次回から必ず次のログを残す実装がmasterへ入っている:

```text
[cloud_job_runner] 03-plan-provenance {json}
```

このJSONを2026-09-02 scheduled 03で受入確認し、base→final targetの変化理由を分類する。

---

# 1. 最重要ルール

今回のsource実装は完成済みとして扱う。

以下のproduction sourceを変更してはいけない:

```text
app/energy_plan/night_plan.py
app/runtime/cloud_job.py
app/runtime/slot_orchestration.py
app/runtime/night_soc_time_contract.py
app/energy_plan/optimization.py
app/energy_plan/soc_constraints.py
app/energy_plan/soc_cost.py
app/kpnet/**
scripts/deploy_production_from_env.ps1
scripts/run_cloud_job_from_env.ps1
scripts/production_deployment_gate.ps1
```

特に以下は禁止:

```text
03 exact-target判定の変更
100%固定化
optimizer無効化
headroom guard無効化
candidate選択ロジック変更
03へFirestore/GCS/DB persistence追加
03へ新network I/O追加
03へ追加device write追加
forced reapply復活
06:45 / 06:50 / 06:55変更
23 / 03 / 07 ownership変更
Scheduler時刻変更
Cloud Run retry変更
```

source変更が必要に見えた場合はその場で変更せず、最終Resultを`BLOCKED_SOURCE_CHANGE_REQUIRED`として停止する。

---

# 2. 今回の完了状態

最終Resultは次のいずれか1つだけとする。

```text
DEPLOYED_AWAITING_SCHEDULED_03
COMPLETE_SCHEDULED_03_PROVENANCE_ACCEPTED
BLOCKED_PRODUCTION_PREFLIGHT
BLOCKED_DEPLOYMENT
BLOCKED_SCOPE_MISMATCH
BLOCKED_SETTINGS_ROUNDTRIP
BLOCKED_DRYRUN
BLOCKED_SCHEDULED_03_EXECUTION
BLOCKED_PROVENANCE_LOG_MISSING
BLOCKED_PROVENANCE_CONFLICT
BLOCKED_SOURCE_CHANGE_REQUIRED
```

2026-09-02 03:00 JSTのscheduled 03がまだ発生していない時点で`COMPLETE_SCHEDULED_03_PROVENANCE_ACCEPTED`を名乗ってはいけない。

その場合は本番反映まで正常なら:

```text
DEPLOYED_AWAITING_SCHEDULED_03
```

で終了する。

通常03 Jobを時間外に手動実行してCOMPLETEへ進めることは禁止する。

---

# 3. Git preflight

PowerShell 7のみ使用する。

最初に:

```powershell
git status --short
git branch --show-current
git fetch origin
git rev-parse HEAD
git rev-parse origin/master
git merge-base --is-ancestor 6eee4d9c861c03d52ee8e93a003e9c531c36f37c HEAD
```

必要条件:

```text
tracked worktree = clean
branch = master
HEAD = origin/master
6eee4d9c source commit is ancestor of HEAD
```

origin/masterがfast-forwardで進んでいる場合のみ:

```powershell
git pull --ff-only
```

を1回実行してよい。

禁止:

```text
git stash
git reset --hard
git rebase
force push
tracked user changesの破棄
```

tracked changeがある場合は`BLOCKED_PRODUCTION_PREFLIGHT`で停止する。

---

# 4. source実装のread-only再確認

次をread-onlyで確認する。

```text
app/energy_plan/night_plan.py::build_night_plan_provenance
app/runtime/cloud_job.py::_read_plan_meta
app/runtime/cloud_job.py::_monitor_partial_forced_and_stop
```

最低限、次の契約がsource上で確認できなければ停止する。

```text
1. plan raw bytesのSHA-256を計算する
2. build_night_plan_provenanceへplan_sha256を渡す
3. 03 monitor開始時に03-plan-provenanceをexactly once出力する
4. final targetは既存result.target_soc_7_percentを使う
5. target reachedはobserved SOC >= final targetのまま
6. provenance log追加によるnetwork/device writeはない
```

`rg`例:

```powershell
rg -n '03-plan-provenance|build_night_plan_provenance|plan_sha256|target_reached' app/energy_plan/night_plan.py app/runtime/cloud_job.py tests/test_cloud_job_runner.py
```

sourceがこの契約と異なる場合はsourceを直さず`BLOCKED_SOURCE_CHANGE_REQUIRED`で停止する。

---

# 5. CodebaseMemory

`.codebase-memory/artifact.json`をread-only確認する。

必要条件:

```text
schema_version = 2
project = C-VSC-SolerControler
commit = 6eee4d9c861c03d52ee8e93a003e9c531c36f37c
nodes > 0
edges > 0
```

この作業ではproduction deploymentとscheduled log受入だけを行うため、CodebaseMemory artifactを再生成しない。

理由:

- production sourceは変更しない
- one-shot execution instruction自身は完了時にhistorical locationへarchiveする
- deployment state / Cloud logsはgraph sourceではない

CodebaseMemory transport障害を理由にdeploymentを止めない。artifact metadataとdirect sourceをauthorityとして使用する。

MCP再install、duplicate project、reindex連打は禁止する。

---

# 6. CI gate

GitHubのmaster HEADに対する最新`quality` workflowを確認する。

必要条件:

```text
status = completed
conclusion = success
```

少なくともsource commit`6eee4d9c`を含むHEADでquality successが必要。

既知の基準:

```text
638336c33fb082760bfb394025b4a6bdc1164821
quality run #212
completed / success
```

この指示PR merge後にmaster HEADが進んだ場合は、新HEADのqualityがterminal successになるまでproduction gateへ進まない。

CI failure/cancelled/timed_outの場合は`BLOCKED_PRODUCTION_PREFLIGHT`。

---

# 7. production runbook再読

本番操作直前に必ず全文を再読する。

```text
docs/current/ops/PRODUCTION_DEPLOYMENT_RUNBOOK_JA.md
```

repositoryの`solar-production-deployment` Skillが利用可能なら必ず使用する。

Skill discoveryは1回まで。

Skillが見つからない場合でもrunbookとofficial wrapperが明確に存在するなら、独自低レベルcommandへ逃げずrunbookのofficial wrapperだけを使う。

---

# 8. production preflight

最初に:

```powershell
git status --short
pwsh -NoProfile -File scripts/production_deployment_gate.ps1 -RunPreRelease
```

必須:

```text
worktree clean
local source backup success
local DB backup success
security check success
relevant/full tests success
.env ignored and unstaged
ValidateOnly success
No deployment was performed
preflight state success
```

禁止:

```text
-SkipLocalBackup
preflight failureの無視
ValidateOnly省略
```

失敗時は本番wrapperを起動せず`BLOCKED_PRODUCTION_PREFLIGHT`。

---

# 9. deployment state pathを1個だけ作る

開始時に1個だけ作成する。

```powershell
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$statePath = "artifacts/deployment_state/production-$stamp.json"
```

この作業では最後まで同じ`$statePath`だけを使う。

新しいstate pathでやり直してはいけない。

---

# 10. official production deploy

preflight success後のみ:

```powershell
pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 `
  -SkipPreRelease `
  -SettingsRoundTripTargetSoc 50 `
  -DeploymentScope auto `
  -StatePath $statePath
```

低レベルの`gcloud run jobs update`等へ置き換えない。

credential-bearing commandを手組みしない。

---

# 11. DeploymentScope auto判定

今回runner sourceに未反映差分が存在するなら、通常はrunnerを含むscopeになるはずだが、Lunaがscopeを推測で上書きしてはいけない。

wrapperのauto結果を使う。

判定:

### A. runner

正常。継続する。

### B. full

wrapperが最後の成功production commitとの差分からfullを選んだ証拠がある場合のみ継続してよい。

runnerを強制指定して既存未反映dashboard差分を飛ばしてはいけない。

### C. dashboard only

runner source`6eee4d9c`がproductionへ未反映なのにdashboard-onlyなら矛盾。

`BLOCKED_SCOPE_MISMATCH`で停止する。

### D. none

`none`だから即成功扱いしない。

read-onlyでofficial deployment evidenceから、production runnerがすでに`6eee4d9c`を含むbuild/imageであることを確認する。

確認できた場合だけbuild/deployをスキップしてpost-deploy verificationへ進む。

確認できない、またはproduction runnerが`6eee4d9c`以前なら:

```text
BLOCKED_SCOPE_MISMATCH
```

とする。

scope logic自体をsource変更してはいけない。

---

# 12. 中断時のresume

外側shell/sessionが切れても新deploymentを開始しない。

同じ`$statePath`を読む。

成功扱い可能なのはexplicit `success` stageのみ。

次は成功扱い禁止:

```text
running
failed
skipped_manual
unknown
```

Cloud Build / Cloud Runのterminal stateをread-only確認してから:

```powershell
pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 `
  -Resume `
  -StatePath $statePath
```

で続行する。

state JSONを手編集してsuccessへ変えてはいけない。

---

# 13. settings round-trip

runner/full deploymentを実施した場合、settings round-trip成功が必須。

wrapper state/logで次を確認する。

```text
forced mode apply success
SocChargeMode max candidate 50 apply success
read-back success
hold exactly 60 sec
restore initial controlled settings
restore read-back success
terminal success
```

wrapperがこれらを明示的に完了していれば重複実行しない。

明示証拠が無い場合のみ1回:

```powershell
pwsh -NoProfile -File scripts/run_cloud_job_from_env.ps1 `
  -Slot settings-roundtrip `
  -SettingsRoundTripTargetSoc 50 `
  -TestExecution
```

復元失敗時は同じテストを自動retryせず`BLOCKED_SETTINGS_ROUNDTRIP`。

---

# 14. 07 DryRun

post-deploy smokeとして必ず:

```powershell
pwsh -NoProfile -File scripts/run_cloud_job_from_env.ps1 -Slot 07 -DryRun
```

合格条件:

```text
Completed=True
ResourcesAvailable=True
Started=True
ContainerReady=True
failedCount=0 またはfield omitted
```

受付だけで成功扱いしない。

不合格は`BLOCKED_DRYRUN`。

---

# 15. 23/03/07 read-only contract確認

本番の3 JobとSchedulerをread-only確認する。

必須:

```text
23 job exists
03 job exists
07 job exists
intended runner image generation/digest is current for all required runner jobs
Scheduler timezone = Asia/Tokyo
23 schedule = 0 23 * * *
03 schedule = 0 3 * * *
07 schedule = 0 7 * * *
03 retry max remains 0
```

project ID、region、resource ID、execution ID、image digest実値、Secret値はtracked reportやチャットへ書かない。

必要なら「一致した/不一致」だけ記録する。

---

# 16. 03を手動実行してはいけない

今回の最重要禁止事項。

次の通常実行は:

```text
2026-09-02 03:00 JST
```

これより前に、確認目的でproduction 03 Jobをnormal mode手動実行してはいけない。

禁止:

```text
run_cloud_job_from_env.ps1 -Slot 03
manual gcloud execute 03
Scheduler force-run
03 normal test execution
```

03の実機動作はscheduled executionだけで確認する。

---

# 17. scheduled 03前に作業が終わった場合

2026-09-02 03:00 JSTより前にdeployment verificationまで完了した場合、その時点で無理にsessionを維持しない。

最終Result:

```text
DEPLOYED_AWAITING_SCHEDULED_03
```

として報告してよい。

報告には:

```text
production rollout = complete
manual 03 execution = NO
next acceptance source = scheduled 2026-09-02 03:00 JST
```

を含める。

scheduled runが発生していないのにprovenance結果を推測してはいけない。

---

# 18. 2026-09-02 scheduled 03 execution確認

scheduled 03後、read-onlyで最新executionを確認する。

まずScheduler-originの通常03 executionであることを確認する。

確認事項:

```text
scheduled time corresponds to 2026-09-02 03:00 JST
manual test executionではない
container started
terminal state available
```

Job成功/失敗は記録するが、Job successだけでSOC target provenance acceptance成功とはしない。

execution自体が存在しない、起動していない、terminal evidenceが取れない場合:

```text
BLOCKED_SCHEDULED_03_EXECUTION
```

---

# 19. provenance logをexactly one確認

該当scheduled 03 executionのCloud Loggingから次prefixを検索する。

```text
[cloud_job_runner] 03-plan-provenance 
```

該当execution内でexactly 1行であること。

0行:

```text
BLOCKED_PROVENANCE_LOG_MISSING
```

2行以上:

```text
BLOCKED_PROVENANCE_CONFLICT
```

同じexecutionの別コンテナ/再試行等で複数行になった場合は勝手に最新だけ選ばず、retry/attempt evidenceを確認する。

03 retry=0契約と矛盾する場合はBLOCKED扱い。

---

# 20. provenance JSON schema acceptance

JSONをparseし、最低限次を確認する。

```text
schema_version = 1
forecast_date = 2026-09-02
plan_sha256 = 64文字lower/upper hexのSHA-256
final_target_soc_7_percent = numeric 0..100
optimizer_kind in {cost, legacy, none, unknown}
provenance_status in {complete, partial, conflict}
```

さらに同executionの:

```text
03-monitor contract target=XX.XX%
```

と:

```text
final_target_soc_7_percent
```

が数値的に一致すること。

不一致なら`BLOCKED_PROVENANCE_CONFLICT`。

`provenance_status=conflict`も`BLOCKED_PROVENANCE_CONFLICT`。

`partial`はログ自体の実装受入は成功しているが、原因分類は不完全なのでCOMPLETE禁止。

---

# 21. base→final target分類

`provenance_status=complete`の場合だけ次へ進む。

分類を以下の1つへ固定する。

## A. COST_OPTIMIZER_SELECTED_FINAL_TARGET

条件例:

```text
optimizer_kind = cost
base target is present
final target is present
selected_candidate.target_soc_percent = final target
max_target_soc_percent_after_guards >= final target
```

さらに`candidate_100_percent`が存在する場合、selected candidateと100 candidateの:

```text
total_expected_cost_yen
required_night_charge_kwh
expected_day_buy_kwh
expected_sell_kwh
expected_peak_unmet_kwh
expected_monthly_tier_landing_penalty_yen
decision_prior_cost_yen
```

を比較する。

差額は数値のみ報告してよいが、candidate全件をtracked reportへ貼らない。

100候補の方が高コストで91等が選択されているなら、まず`optimizer choice by design`として記録し、その場で100固定へ変更しない。

## B. TARGET_CAPPED_BY_ACTIVE_CONSTRAINT

条件例:

```text
base target > final target
max_target_soc_percent_after_guards < base target
final target <= max target after guards
candidate_100_percent absent
```

`active_constraints`と`constraint_details`からcap sourceを特定する。

候補:

```text
morning_pv_headroom_guard
daytime_net_surplus_headroom_guard
historical_daytime_soc_gain_guard
```

constraint sourceを証拠として記録するが、その場で無効化しない。

## C. LEGACY_OPTIMIZER_SELECTED_FINAL_TARGET

```text
optimizer_kind = legacy
```

base/finalとobjectiveを記録する。

legacyをその場でcostへ切り替えたり無効化しない。

## D. NO_OPTIMIZER_FINAL_TARGET

```text
optimizer_kind = none
```

baseが無い場合も含む。

final target生成元をsource contractとplan payloadから説明する。

## E. PROVENANCE_PARTIAL

```text
provenance_status = partial
または optimizer_kind = unknown
```

COMPLETE禁止。

必要な欠損fieldだけ列挙する。

source変更は別logical unitとする。

---

# 22. 100%という値との比較方法

過去の2026-08-29 dashboard 100%を2026-09-02 planと混同しない。

比較対象にしてよい100%は、2026-09-02 provenance JSON自身の:

```text
base_target_soc_7_percent = 100
または candidate_100_percent
```

だけ。

別日dashboard値を「base=100の証拠」として使用禁止。

もし2026-09-02 provenanceでbase=100 / final<100が実際に確認できた場合、初めて同一plan内の100→final変換として証明する。

---

# 23. final SOC stop reasonも同じexecutionから確認

provenanceだけでなく同execution内の03 monitor logsを確認する。

必要な最終分類:

```text
target_reached
monitor_cutoff
soc_unavailable
runtime/readback error
```

`target_reached`の場合:

```text
latest SOC >= final target
```

を確認する。

Job successだけでtarget reachedとしない。

---

# 24. 今回source修正を行わない理由

この作業の目的は観測結果を確定することであり、結果を見た同じ実行内でoptimizer/constraintを変更しない。

たとえば:

```text
base=100
final=91
optimizer_kind=cost
candidate100 exists
91のexpected costが低い
```

が確認できても、直ちに100固定へ変更禁止。

次のsource修正には、少なくとも:

```text
利用者が「100%」をhard minimum targetとして要求しているのか
optimizerの経済最適化targetとして100%を表示しただけなのか
UI/metric semanticsが誤っているのか
```

の仕様決定が必要。

今回はevidence reportまでで停止する。

---

# 25. tracked report

scheduled acceptanceまで完了した場合のみ、次を作成する。

```text
docs/completed/reports/03_plan_provenance_scheduled_acceptance_2026-09-02.md
```

含める内容:

```text
Result
source commit
production rollout status
manual 03 execution = NO
scheduled 03 execution verified = YES/NO
provenance line count
forecast_date
plan_sha256 = PRESENT / format valid（hash実値は記載不要）
base target
final target
optimizer kind
optimizer objective
max target after guards
active constraints
selected candidate target
100 candidate present/absent
selected vs 100 cost delta（取得できる場合）
final stop reason
final SOC relative to target
classification
source change = NO
secrets exposed = NO
```

project ID、resource ID、execution ID、image digest実値、Secret値は記載しない。

scheduled 03前に終わる場合は、このacceptance reportを空のまま作らない。

---

# 26. one-shot instructionのarchive

`COMPLETE_SCHEDULED_03_PROVENANCE_ACCEPTED`まで到達した場合、このone-shot instruction自身をcurrentからhistoricalへ移す。

```text
docs/current/ops/CODEX_COMPLETE_03_PROVENANCE_ROLLOUT_AND_ACCEPT_20260902_JA.md
```

を例として:

```text
docs/completed/agent_runs/2026-09-02-03-plan-provenance-rollout/
```

へ`git mv`する。

`DEPLOYED_AWAITING_SCHEDULED_03`の段階ではまだcurrentに残してよい。

source/testを変更しない。

---

# 27. CodebaseMemory refresh規則

production deployment、Cloud Logging確認、completed report追加だけを理由にCodebaseMemoryを再refreshしない。

このone-shot current instructionをCOMPLETE時にhistoricalへ移した後、source/tests/current durable docsがartifact source`6eee4d9c`と意味的に一致していれば、artifact refresh不要。

もし実行中に別のcurrent durable rule/source/testがmasterへ追加された場合のみshared graph freshness ruleに従い別途判断する。

二重refresh禁止。

---

# 28. commit / push規則

production acceptance reportやinstruction archiveをcommitする場合:

```powershell
python scripts/security_check.py
git diff --check
git status --short
```

を確認する。

source/tests変更が無いことを確認する。

stage対象を限定する。

例:

```text
docs/completed/reports/03_plan_provenance_scheduled_acceptance_2026-09-02.md
docs/completed/agent_runs/2026-09-02-03-plan-provenance-rollout/...
```

push後:

```text
HEAD == origin/master
tracked tree clean
```

を確認する。

---

# 29. STOP条件

以下は即STOP:

```text
tracked user changeあり
non-fast-forward drift
CI failure
production preflight failure
backup failure
ValidateOnly failure
deployment state corruption
scope=dashboard only while runner source not deployed
scope=none but 6eee production inclusion not proven
settings restore failure
07 DryRun failure
23/03/07 scheduler contract mismatch
03 retry != 0
manual 03 normal executionが必要になる
scheduled 03 execution不在
03-plan-provenance 0行/複数行
forecast_date mismatch
plan SHA format invalid
final target != 03 monitor contract target
provenance_status conflict
source変更が必要
```

STOP時に回避source fixを作らない。

---

# 30. 最終報告フォーマット

必ず次の順番で報告する。

```text
1. Result
2. final repo HEAD
3. source implementation commit preserved = 6eee4d9c...
4. additional source change = NO
5. latest quality = status / conclusion
6. CodebaseMemory artifact = project / source commit / nodes / edges
7. production preflight = PASS/FAIL
8. deployment scope = runner/full/none/blocked
9. deployment state = success/blocked and resume used YES/NO
10. settings round-trip = forced/readback/60sec/restore/readback
11. 07 DryRun = Completed/ResourcesAvailable/Started/ContainerReady/failedCount
12. 23/03/07 jobs + Scheduler contract = PASS/FAIL
13. manual 03 execution = NO
14. scheduled 2026-09-02 03 execution = VERIFIED / NOT_YET / BLOCKED
15. 03-plan-provenance line count
16. forecast date
17. base target
18. final target
19. optimizer kind/objective
20. max target after guards
21. active constraints
22. selected target
23. 100 candidate present/absent
24. final stop reason / SOC
25. classification
26. tracked acceptance report path if created
27. secrets exposed = NO
```

Secret、project ID、region、resource ID、execution ID、image digest実値は出力しない。

---

# 31. 実行順まとめ

順番を変更しない。

```text
1. git clean/master/origin確認
2. source read-only contract確認
3. CBM artifact metadata確認
4. latest GitHub quality SUCCESS確認
5. production runbook全文再読
6. production gate -RunPreRelease
7. deployment state pathを1個作成
8. official deploy wrapper -DeploymentScope auto
9. scope整合確認
10. settings round-trip確認
11. 07 DryRun
12. 23/03/07 jobs + Scheduler read-only確認
13. 通常03を手動実行しない
14. scheduled 2026-09-02 03:00 JSTがまだなら DEPLOYED_AWAITING_SCHEDULED_03
15. scheduled execution後、該当executionをread-only確認
16. 03-plan-provenance exactly one確認
17. provenance schema/forecast date/hash/final target一致確認
18. base→final分類
19. final SOC stop reason確認
20. acceptance report作成
21. one-shot instruction archive
22. security_check / diff check
23. docsだけcommit/push
24. HEAD==origin/master / clean確認
25. final report
```

この作業での最重要点は、**03を手動実行せず、次の自然な03実行で、実際に読まれたplanのbase→final target理由を初めて確定すること**である。

# Codex実行指示: 03 plan provenance logging を今夜のscheduled 03前に安全に本番反映する

## 0. 目的

この指示は、既に実装・テスト・CI済みの03 plan provenance loggingを本番runnerへ反映し、2026-09-02 03:00 JSTの**通常Scheduler実行だけ**で受入確認するためのone-shot handoffである。

新しいoptimizer修正、SOC target修正、03 runtime再設計は行わない。

現在の基準:

```text
master = 2b448f28b83244ff76fa86fcbeff8e23f87d659b
source implementation = 6eee4d9c861c03d52ee8e93a003e9c531c36f37c
implementation message = fix: log exact 03 plan SOC provenance
CodebaseMemory source = 6eee4d9c861c03d52ee8e93a003e9c531c36f37c
CodebaseMemory = 6145 nodes / 18608 edges
master quality = run #219 / completed / success
```

この指示作成時刻は2026-09-01 23:51 JST前後である。

次のscheduled 03は:

```text
2026-09-02 03:00 JST
```

---

# 1. 最終Result

この作業のResultは次のいずれか1つだけ。

```text
DEPLOYED_WAITING_SCHEDULED_03
ACCEPTED_COST_OPTIMIZER_SELECTED_FINAL_TARGET
ACCEPTED_GUARD_CAPPED_FINAL_TARGET
ACCEPTED_BASE_TARGET_ALREADY_BELOW_USER_VALUE
ACCEPTED_NO_OPTIMIZER_FINAL_TARGET
ACCEPTED_OTHER_PROVEN_PLAN_TRANSFORM
ACCEPTED_PROVENANCE_PARTIAL
NOT_DEPLOYED_BEFORE_CUTOFF
BLOCKED_DEPLOYMENT
BLOCKED_RUNTIME_EVIDENCE
```

`COMPLETE`という曖昧な語は禁止する。

---

# 2. 絶対禁止

今回変更禁止:

```text
app/energy_plan/optimization.py
app/energy_plan/soc_constraints.py
app/energy_plan/soc_cost.py
app/runtime/night_soc_time_contract.py
app/runtime/slot_orchestration.py
03 target reached条件
configured/applied SOC margin契約
06:45 / 06:50 / 06:55 fence
23/03/07 ownership
Scheduler cron
Cloud Run retry count
SocChargeMode activation candidate契約
```

さらに禁止:

```text
100%へ固定
cost optimizer無効化
headroom guard無効化
03 normal Jobの手動execute
03へFirestore/GCS/DB persistence追加
03へnetwork I/O追加
device write追加
Scheduler一時停止
Scheduler時刻変更
低レベルgcloud deployの手組み
production state JSON手編集
force push
reset --hard
stash
```

---

# 3. 時刻ゲート

作業開始時にAsia/Tokyoの現在時刻を取得して記録する。

## 3.1 新規deployment開始可

```text
now < 2026-09-02 02:30:00 JST
```

の場合のみ、新しいproduction deployment workflowを開始してよい。

## 3.2 02:30以降

```text
now >= 2026-09-02 02:30:00 JST
```

かつproduction runner反映がまだ開始されていない場合:

```text
新規deployment開始禁止
Result = NOT_DEPLOYED_BEFORE_CUTOFF
```

03 Schedulerを止めたり遅らせたりして帳尻を合わせてはいけない。

## 3.3 既にCloud Build/Run更新が進行中の場合

02:30を越えたからという理由だけで外側からcancelしない。

次だけ行う。

1. 既存StatePathを読む。
2. Cloud Build/Cloud Runの終端状態をread-only確認する。
3. `running`をsuccess扱いしない。
4. 新しい別StatePathを作らない。
5. 同じcommitを再buildしない。
6. 追加mutation stageを推測で開始しない。

03:00までにrunner更新成功が証明できなければ、そのscheduled 03を新provenance loggingの受入結果として扱わない。

---

# 4. Git preflight

PowerShell 7を使用。

```powershell
git status --short
git branch --show-current
git fetch origin
git rev-parse HEAD
git rev-parse origin/master
```

開始条件:

```text
tracked user change = none
HEAD == origin/master
origin/master contains 6eee4d9c861c03d52ee8e93a003e9c531c36f37c
```

tracked user changeがあればSTOP。

未追跡user fileは削除しない。

---

# 5. 必読

順番固定:

```text
AGENTS.md
docs/current/agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md
docs/current/ops/PRODUCTION_DEPLOYMENT_RUNBOOK_JA.md
docs/current/ops/CODEX_ADD_03_PLAN_PROVENANCE_LOGGING_20260901_JA.md
docs/current/ops/CODEX_COMPLETE_03_PROVENANCE_ROLLOUT_AND_ACCEPT_20260902_JA.md
app/energy_plan/night_plan.py
app/runtime/cloud_job.py
app/runtime/slot_orchestration.py
app/runtime/night_soc_time_contract.py
```

source authorityは現在checkoutしているsourceとする。

---

# 6. 実装再確認

source変更せず、次をread-only確認する。

```text
build_night_plan_provenance() が存在
_read_plan_meta() がraw bytes SHA-256を計算
03 monitor開始時に [cloud_job_runner] 03-plan-provenance を exactly 1回print
03-monitor contractログがその後に出る
exact target reached判定は observed SOC >= target
```

次のprovenance fieldsが存在することを確認:

```text
schema_version
forecast_date
plan_sha256
base_target_soc_7_percent
final_target_soc_7_percent
required_night_charge_kwh
optimizer_kind
optimizer_objective
max_target_soc_percent_after_guards
active_constraints
selected_candidate
candidate_100_percent
nearest_lower_candidate
nearest_higher_candidate
provenance_status
constraint_details (存在時)
```

不足があれば今回その場で設計修正せずSTOPし、`BLOCKED_DEPLOYMENT`として報告する。

---

# 7. テスト・CI確認

GitHub側で次を確認。

```text
master quality run #219
status = completed
conclusion = success
```

さらにlocal pre-release gate内でsource/tests/securityが再確認されるため、ここで独自のテスト省略判断をしない。

---

# 8. 既に本番反映済みか先に判定

重複deploymentを避ける。

production runnerの現在image/revisionをread-only確認し、`6eee4d9c`を含むbuildであることを既存deployment state・image metadata・build provenanceのいずれかで明示証明できる場合:

```text
duplicate deployment = 禁止
```

そのままSection 12へ進む。

証明できない場合だけSection 9へ進む。

commit文字列がimage tagに直接含まれない構成では、既存production stateのsource commit/build provenanceを使う。推測は禁止。

---

# 9. Production preflight

開始時刻が02:30 JSTより前であることを再確認。

```powershell
pwsh -NoProfile -File scripts/production_deployment_gate.ps1 -RunPreRelease
```

成功条件:

```text
worktree clean
local source backup success
DB backup success when applicable
security check success
relevant/full tests success
.env ignored/not staged
ValidateOnly success
No deployment was performed
preflight state success
```

`-SkipLocalBackup`禁止。

preflight失敗時:

```text
production wrapper開始禁止
Result = BLOCKED_DEPLOYMENT
```

---

# 10. Production deployment

StatePathは1つだけ作る。

例:

```text
artifacts/deployment_state/production-20260902-<HHMMSS>.json
```

公式wrapperだけを使用:

```powershell
pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 `
  -SkipPreRelease `
  -SettingsRoundTripTargetSoc 50 `
  -DeploymentScope auto `
  -StatePath $statePath
```

## 10.1 scope判定

今回source changeはrunner pathである。

`DeploymentScope auto`結果が:

```text
runnerを含む
```

ことを確認する。

もし`none`なら、既に同一sourceがproduction扱いになっているかをstate/provenanceで再確認する。

dashboard-only等、今回のsource差分と一致しないscopeならSTOP。

## 10.2 外側のsession/PowerShellが切れた場合

新しいdeploymentを開始しない。

同じStatePathを使う。

Cloud側終端をread-only確認後、必要な場合のみ:

```powershell
pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 `
  -Resume `
  -StatePath $statePath
```

`running` / `failed` / `skipped_manual`をsuccess扱いしない。

---

# 11. Settings round-trip

wrapperが実際にdeploymentを行った場合、次が成功済みであることをstate/logで確認する。

```text
forced candidate applied
SocChargeMode device candidate = 50
read-back success
hold = exactly 60 sec
pre-test controlled settings restored
restoration read-back success
```

wrapper内で明示成功済みなら追加実行禁止。

未実行/不明の場合だけ公式専用Jobを1回:

```powershell
pwsh -NoProfile -File scripts/run_cloud_job_from_env.ps1 `
  -Slot settings-roundtrip `
  -SettingsRoundTripTargetSoc 50 `
  -TestExecution
```

復元失敗時は自動retry禁止。

---

# 12. 07 DryRun

production runner更新を行った場合のみ公式DryRun:

```powershell
pwsh -NoProfile -File scripts/run_cloud_job_from_env.ps1 -Slot 07 -DryRun
```

終端条件:

```text
Completed=True
ResourcesAvailable=True
Started=True
ContainerReady=True
failedCount=0 または omitted
```

受付だけで成功扱いしない。

---

# 13. Scheduler / Job read-only contract

次をread-only確認。

```text
timezone = Asia/Tokyo
23 schedule = 0 23 * * *
03 schedule = 0 3 * * *
07 schedule = 0 7 * * *
03 retry = 0
23/03/07 intended runner image = rollout対象image
```

変更は禁止。

---

# 14. Pre-03 report

本番反映が成功し、まだ03:00前ならtracked reportを作成してよい。

```text
docs/completed/reports/03_plan_provenance_rollout_2026-09-02.md
```

この段階では最低限:

```text
rollout_status = DEPLOYED_WAITING_SCHEDULED_03
source_commit = 6eee4d9c861c03d52ee8e93a003e9c531c36f37c
quality = SUCCESS
production_gate = SUCCESS
deployment = SUCCESS
settings_roundtrip = SUCCESS
07_dryrun = SUCCESS
scheduler_contract = PASS
scheduled_acceptance = PENDING
secrets_exposed = NO
```

を書く。

識別子・project番号・resource ID・secret値は書かない。

このreportだけを先にcommit/pushしてもよい。

commit message:

```text
docs: record 03 provenance rollout status
```

---

# 15. 03:00までの待機方針

03 Jobを手動executeしない。

03:00より前にタスク実行環境が終了する場合:

```text
Result = DEPLOYED_WAITING_SCHEDULED_03
```

として終了してよい。

03受入が未実施なのに`ACCEPTED_*`を名乗らない。

---

# 16. Scheduled 03受入

対象は**2026-09-02 03:00 JSTにSchedulerから起動された通常03 execution**だけ。

手動executionは受入対象外。

まずexecution origin/timeを確認してScheduler起動であることを証明する。

次にCloud Loggingをread-onlyで取得。

必須ログ順:

```text
03-plan-provenance
03-monitor contract
03-monitor soc ...
最終 stop reason
```

`03-plan-provenance`は対象executionにつきexactly 1行でなければならない。

0行:

```text
BLOCKED_RUNTIME_EVIDENCE
```

2行以上:

```text
BLOCKED_RUNTIME_EVIDENCE
```

として、duplicate controller/duplicate monitor仮説を記録するがsource変更しない。

---

# 17. Provenance consistency checks

provenance JSONについて全件確認。

```text
forecast_date == 2026-09-02
plan_sha256 is non-empty 64 hex chars
final_target_soc_7_percent == 03-monitor contract target
provenance_status in {complete, partial, conflict}
```

`forecast_date`不一致なら:

```text
PLAN_DATE_MISMATCH
```

として`ACCEPTED_OTHER_PROVEN_PLAN_TRANSFORM`または`BLOCKED_RUNTIME_EVIDENCE`を選ぶ。optimizer修正は禁止。

base conflictがあれば`provenance_status=conflict`を尊重し、値を推測で統合しない。

---

# 18. 原因分類

## 18.1 Cost optimizerがbaseより低いfinalを選択

条件:

```text
optimizer_kind = cost
base target > final target
max_target_soc_percent_after_guards >= base target または >= 100 when base=100
selected_candidate.target == final target
candidate_100_percent exists when base=100
```

さらにcandidate 100とselectedのcost fieldsを比較できる場合、差分を記録する。

Result:

```text
ACCEPTED_COST_OPTIMIZER_SELECTED_FINAL_TARGET
```

この作業内でcost optimizerを変更しない。

## 18.2 Guard cap

条件例:

```text
base target > final target
max_target_soc_percent_after_guards < base target
selected candidate target <= max_target
100 candidate absent when max_target < 100
constraint_details/active_constraintsにcap根拠あり
```

Result:

```text
ACCEPTED_GUARD_CAPPED_FINAL_TARGET
```

この作業内でguardを変更しない。

## 18.3 Base target自体が100ではない

条件:

```text
base_target_soc_7_percent != 100
```

で、ユーザー/dashboard側の別日100%とは直接変換関係がない。

Result:

```text
ACCEPTED_BASE_TARGET_ALREADY_BELOW_USER_VALUE
```

## 18.4 Optimizerなし

```text
optimizer_kind = none
```

かつfinal targetがplan raw targetとして直接使用されている。

Result:

```text
ACCEPTED_NO_OPTIMIZER_FINAL_TARGET
```

## 18.5 上記以外だが証拠はcomplete

Result:

```text
ACCEPTED_OTHER_PROVEN_PLAN_TRANSFORM
```

## 18.6 partial

```text
provenance_status = partial
```

Result:

```text
ACCEPTED_PROVENANCE_PARTIAL
```

sourceをその場で補完修正しない。欠落fieldだけ列挙する。

---

# 19. SOC停止結果も別軸で記録

provenance原因分類とSOC停止結果を混ぜない。

別欄に:

```text
runtime_target
initial_soc
final_relevant_soc
soc_source
stop_reason
monitor_cutoff 여부
```

を記録。

`stop_reason=target_reached`の場合はexact-target contract確認として扱う。

`monitor_cutoff`や`soc_unavailable`なら、target生成理由のprovenanceは有効でもSOC到達成功とは別判定にする。

---

# 20. Final report update

Section 14で作ったreportを更新するか、未作成なら作る。

必須フォーマット:

```text
Result = <one fixed result>
Source commit = 6eee4d9c861c03d52ee8e93a003e9c531c36f37c
Scheduled execution = CONFIRMED / NOT_CONFIRMED
Forecast date = ...
Plan SHA-256 present = YES/NO
Base target = ...
Final target = ...
Optimizer kind = ...
Optimizer objective = ...
Max target after guards = ...
Active constraints = ...
Selected candidate target = ...
100% candidate present = YES/NO/NA
Nearest lower target = ...
Nearest higher target = ...
Provenance status = ...
Runtime target = ...
Final relevant SOC = ...
SOC source = ...
Stop reason = ...
Source change during acceptance = NO
Optimizer/guard change = NO
03 manual execution = NO
Secrets exposed = NO
```

candidate cost値を記録する場合もsecretやresource IDを含めない。

---

# 21. この作業でsource fixを作らない

たとえ原因が明確でも、このacceptance作業ではproduction sourceを直さない。

理由:

```text
1. provenance instrumentationの受入とoptimizer policy変更を同じlogical unitに混ぜない
2. 1回のscheduled evidenceをまず固定する
3. target policy変更は別PRで回帰条件・料金影響・PV headroom影響を独立評価する
```

必要な次PR候補だけreportに1行で記録する。

---

# 22. CodebaseMemory

このactive instruction追加によりshared artifactはcurrent docsに対してstrictにはstaleになる。

ただしproduction sourceは6eee4d9c時点から変わっていない。

このone-shotが`DEPLOYED_WAITING_SCHEDULED_03`で一旦終了する場合、artifact refreshはまだ行わない。

最終`ACCEPTED_*`またはterminal `BLOCKED_*` reportをcommitし、このinstructionをcompletedへarchiveする段階で**1回だけ**shared artifact refreshを行う。

transport failure時:

```text
status 1回
exact error記録
status 2回目
```

2回ともtransport failureならreinstall/reindex連打禁止。source/rg fallbackを使用し、CodebaseMemoryを使用できたと偽らない。

---

# 23. Git commit/push

tracked変更を限定する。

acceptance完了時に許可:

```text
docs/completed/reports/03_plan_provenance_rollout_2026-09-02.md
このone-shot instructionのcompleted archive移動
.codebase-memory/artifact.json
.codebase-memory/graph.db.zst
```

production sourceを変更してはいけない。

commitはlogical unitごと。

例:

```text
docs: record 03 provenance rollout status
docs: record scheduled 03 provenance acceptance
chore: refresh CodebaseMemory 03 acceptance graph
```

push前:

```powershell
git diff --check
python scripts/security_check.py
git status --short
git rev-parse HEAD
```

push後:

```text
HEAD == origin/master
worktree clean
```

を確認。

---

# 24. STOP条件

次のいずれかでSTOPし、回避実装を作らない。

```text
tracked user changes
HEAD/origin drift
quality failure
production preflight failure
02:30以降でdeployment未開始
deployment state ambiguous
settings restore failure
runner provenance不明
03 executionがmanual起動
03-plan-provenance 0件/複数
forecast date mismatchで証拠が結べない
provenance conflict
secret exposure risk
protected sourceを触る必要が出た
```

---

# 25. 最終チェックリスト

```text
[ ] source implementation 6eee4d9cを確認
[ ] master quality #219 SUCCESS確認
[ ] current time gate確認
[ ] duplicate deployment判定
[ ] preflight success
[ ] official wrapperのみ使用
[ ] runner scope確認
[ ] settings round-trip success
[ ] restore read-back success
[ ] 07 DryRun success
[ ] Scheduler変更なし
[ ] 03 manual executeなし
[ ] scheduled 03 origin確認
[ ] 03-plan-provenance exactly one
[ ] forecast_date整合
[ ] SHA-256 present
[ ] base/final target記録
[ ] optimizer kind記録
[ ] guard/candidate記録
[ ] stop reason記録
[ ] 原因分類を1つへ固定
[ ] acceptance中source changeなし
[ ] final report commit/push
[ ] 最終段階だけCodebaseMemory refresh 1回
[ ] secrets exposed = NO
```

1つでも満たさなければ`ACCEPTED_*`を名乗らない。

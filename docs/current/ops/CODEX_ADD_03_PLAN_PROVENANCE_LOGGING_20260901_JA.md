# Codex実行指示: 03が実際に使用したSOC plan provenanceをCloud Loggingへ固定し、次回から100→91の理由を必ず復元可能にする

## 0. 目的

2026-09-01の調査では、03 runtimeが`target=91%`を使用し、realtime SOC 96%で`target_reached`停止したことは証明できた。一方、利用者が見ていた100%は2026-08-29の`battery_daily_metrics.setting_soc_target_percent`であり、2026-09-01 03が実際に使用したhistorical `night_charge_plan.json`は回収できなかった。

直近の確定結果:

```text
PRIMARY_CAUSE = PROVENANCE_BLOCKED
SECONDARY_CAUSE = DIFFERENT_DATE_VALUE
USER/DASHBOARD_VALUE = 100% / date=2026-08-29
BASE_PLAN_TARGET = NOT_PROVEN
FINAL_PLAN_TARGET = NOT_PROVEN
03_RUNTIME_TARGET = 91%
03_STOP_SOC = 96%
SOURCE_CHANGE = NO
```

今回の作業は、次回以降の03実行で同じ証拠欠落を起こさないため、**03が実際に読み込んだplanそのものから、既存のoptimizer/constraint/candidate情報を抽出し、Cloud Loggingへ1行JSONで必ず残す**。

この作業はSOC最適化アルゴリズムを変更しない。100%固定もしない。03にFirestore/GCS/DB persistenceを追加しない。

---

## 1. 最重要契約

変更後も次を絶対に維持する。

```text
03 target reached condition = observed SOC >= result.target_soc_7_percent
23 = unconditional standby candidate/read-back
03 = standalone
07 = unconditional green candidate/read-back
03 forced reapply = 禁止
03 Firestore persistence = 禁止
03 DB persistence = 禁止
03 GCS upload = 禁止
03 lease/owner/day-gate = 禁止
06:45 monitor cutoff = 維持
06:50 final standby start cutoff = 維持
06:55 hard I/O cutoff = 維持
SocChargeMode=50 = activation candidateでありplanning targetではない
NIGHT_SOC_READBACK_REQUIRED = strictのまま
```

今回追加してよいものは**local plan read後のCPU内処理 + stdout logging**だけ。

新しいnetwork I/O、cloud API call、Firestore/GCS write、plan archive upload、Scheduler変更、device write追加は禁止。

---

## 2. baseline

開始時の期待master:

```text
a8db587bdf279f8d403b54614b5994ff99250679
docs: record SOC target 100 to 91 provenance block (#29)
```

開始時に必ず:

```powershell
git status --short
git branch --show-current
git fetch origin
git rev-parse HEAD
git rev-parse origin/master
```

条件:

```text
tracked user changesあり -> STOP
origin/masterが進んでいる -> git pull --ff-onlyを1回だけ
non-fast-forward/rebase必要 -> STOP
```

禁止:

```text
git reset --hard
git stash
git clean
force push
user file削除
```

---

## 3. 必読ファイル

順番固定:

```text
AGENTS.md
docs/current/agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md
docs/current/agent/codebase_memory_shared_graph_usage_ja.md
docs/current/ops/PRODUCTION_DEPLOYMENT_RUNBOOK_JA.md
docs/completed/reports/soc_target_100_to_91_provenance_2026-09-01.md
app/energy_plan/night_plan.py
app/energy_plan/result_builder.py
app/energy_plan/optimization.py
app/energy_plan/soc_constraints.py
app/energy_plan/soc_cost.py
app/runtime/cloud_job.py
app/runtime/slot_orchestration.py
tests/test_cloud_job_runner.py
tests/test_night_soc_controller.py
tests/test_night_soc_protected_contract.py
tests/test_night_soc_time_ownership.py
```

sourceがこの指示と不一致なら、sourceを正としてSTOPし、差分を報告する。推測で指示を読み替えない。

---

## 4. CodebaseMemory

canonical projectのみ:

```text
C-VSC-SolerControler
```

statusを1回確認。

readyなら次だけquery:

```text
app.energy_plan.night_plan.parse_night_plan
app.energy_plan.optimization.run_current_optimizer
app.energy_plan.result_builder._build_energy_model_output
app.runtime.cloud_job._read_plan_meta
app.runtime.cloud_job._monitor_partial_forced_and_stop
app.runtime.slot_orchestration._run_adjust_03
```

transport closed/connection failureならexact errorを保存し、statusをもう1回だけ実行。

2回目もtransport failureなら:

```text
CBM_TRANSPORT_BLOCKED_USER_AUTHORIZED_SOURCE_FALLBACK
```

として`rg` + direct source inspectionで続行する。

禁止:

```text
reinstall
package update
MCP config変更
duplicate project作成
接続失敗だけを理由にreindex
```

sourceが最終authority。

---

## 5. 今回のsource変更許可範囲

原則として次だけ:

```text
app/energy_plan/night_plan.py
app/runtime/cloud_job.py
tests/test_cloud_job_runner.py
```

必要な場合のみ、pure helper test用に次の既存test fileを1つ追加許可:

```text
tests/test_energy_plan_night_plan.py
```

上記以外の`app/**`変更が必要になったらSTOP。

特に変更禁止:

```text
app/energy_plan/optimization.py
app/energy_plan/soc_cost.py
app/energy_plan/soc_constraints.py
app/runtime/slot_orchestration.py
app/runtime/night_soc_time_contract.py
app/kpnet/**
app/operations/firestore.py
app/backup/night_plan_archive.py
scripts/deploy_*.ps1
Scheduler定義
```

---

## 6. 実装するprovenanceの意味

目的は「03が実際に読んだファイルを後日説明できること」。

次の事実を1つのprovenance payloadへ固定する。

### 必須field

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
```

### schema_version

固定:

```text
1
```

### forecast_date

```text
plan.forecast.date
```

無ければ空文字ではなく`null`。

### plan_sha256

03が実際に読んだ`night_charge_plan.json`の**raw file bytes**に対するSHA-256 lowercase hex。

JSON再serialize後のhashは禁止。

### base_target_soc_7_percent

優先順位固定:

1. `result.target_soc_7_percent_base`
2. 無ければ`decision_rationale.raw_target_soc_7_percent`
3. どちらも無ければ`null`

両方存在して値が異なる場合、勝手に一方を正としない。

その場合:

```text
provenance_status = "conflict"
```

とし、loggingは行う。ただし03 device controlを停止しない。

### final_target_soc_7_percent

唯一のauthority:

```text
result.target_soc_7_percent
```

これは既存03 targetと完全一致しなければならない。

### required_night_charge_kwh

```text
result.required_night_charge_kwh
```

無ければ`0.0`ではなく`null`をlogging用payloadに入れる。

既存control用`_read_plan_meta`の互換性を壊さないこと。

---

## 7. optimizer_kind判定

新しいoptimizer判断ロジックを作らない。plan内の既存markerだけを見る。

順序固定:

### cost

次のどちらか成立:

```text
result.target_soc_7_percent_cost_optimized がnumeric
OR
daytime_soc_optimization.objective == "minimize_night_charge_cost_plus_expected_day_buy_cost_plus_expected_sell_opportunity_loss"
```

=>

```text
optimizer_kind = "cost"
```

### legacy

costでなく、次が成立:

```text
result.target_soc_7_percent_base が存在
AND
daytime_soc_optimization.objective == "avoid_daytime_buy_and_sell_then_peak_soc_near_target"
```

または`daytime_soc_optimization.legacy_peak_objective.objective`が同値。

=>

```text
optimizer_kind = "legacy"
```

### none

```text
result.target_soc_7_percent_base が無い
AND
final targetは存在
AND
daytime_soc_optimizationが空/None
```

=>

```text
optimizer_kind = "none"
```

それ以外:

```text
optimizer_kind = "unknown"
```

`unknown`でも03を停止しない。

---

## 8. optimizer_objective

優先順位:

1. `daytime_soc_optimization.objective`
2. `decision_rationale.objective`
3. `null`

文字列以外は`null`。

---

## 9. max_target_soc_percent_after_guards

```text
daytime_soc_optimization.max_target_soc_percent_after_guards
```

numericかつ0..100だけ採用。

無ければ`null`。

`null`を100とみなしてloggingしてはいけない。

---

## 10. active_constraints

第一authority:

```text
decision_rationale.active_constraints
```

list[str]だけ採用。

無い場合は空list。

さらにpayloadへconstraint detailを追加してよいが、追加するなら固定keyだけ:

```text
constraint_details.morning_pv_headroom_guard
constraint_details.daytime_net_surplus_headroom_guard
constraint_details.historical_daytime_soc_gain_guard
```

各guardからloggingしてよいfield:

```text
applied
reason
cap_target_soc_percent
guard_headroom_kwh
usable_headroom_kwh
guard_gain_percent
expected_net_surplus_kwh
```

存在するfieldだけ。全payload丸ごとloggingは禁止。

---

## 11. selected_candidate

`daytime_soc_optimization.selected_candidate`がdictなら、次だけ抽出:

```text
target_soc_percent
total_expected_cost_yen
required_night_charge_kwh
expected_day_buy_kwh
expected_sell_kwh
expected_peak_unmet_kwh
expected_monthly_tier_landing_penalty_yen
decision_prior_cost_yen
```

無ければ`null`。

scenario_replaysはlogging禁止。

---

## 12. candidate digest

`daytime_soc_optimization.candidate_summaries`がlistの場合だけ使用。

candidate summaryから許可するfield:

```text
target_soc_percent
total_expected_cost_yen
required_night_charge_kwh
expected_day_buy_kwh
expected_sell_kwh
expected_peak_unmet_kwh
expected_monthly_tier_landing_penalty_yen
decision_prior_cost_yen
rejection_reason
```

全candidateをlogしてはいけない。

必ず次の最大4件だけ:

```text
selected candidate
100% candidate（存在時）
selectedより最も近いlower candidate
selectedより最も近いhigher candidate
```

重複targetは1件へdedupe。

payload field:

```text
candidate_100_percent
nearest_lower_candidate
nearest_higher_candidate
```

selected candidateはSection 11の別field。

100%候補が無ければ`null`。勝手に生成しない。

これにより:

```text
91選択 / 100候補あり -> cost差を比較可能
91選択 / max guard=91 / 100候補なし -> capを疑える
```

という後日判定が可能になる。

---

## 13. provenance_status

固定分類:

```text
complete
partial
conflict
```

### complete

最低限:

```text
forecast_dateあり
final targetあり
plan_sha256あり
optimizer_kind != unknown
```

### partial

上記のどれかoptional provenanceが不足。ただしfinal targetは既存control pathでvalid。

### conflict

base targetの複数authorityが同時存在し、numeric値が一致しない等、plan自身に矛盾がある。

重要:

```text
partial/conflictはdiagnostic status
03 control failureへ変換しない
```

既存final target parse failureだけは従来どおりfailureでよい。

---

## 14. pure helper

`app/energy_plan/night_plan.py`へpure helperを追加する。

推奨固定名:

```python
def build_night_plan_provenance(raw: dict[str, Any], *, plan_sha256: str) -> dict[str, Any]:
```

このhelperは禁止:

```text
file I/O
network I/O
env read
logging
Firestore/GCS access
current time参照
```

入力rawだけから決定的にpayloadを返す。

既存`parse_night_plan()`は意味変更しない。

---

## 15. cloud_job側変更

`app/runtime/cloud_job.py`のみ。

### 15.1 raw bytes

`_read_plan_meta()`またはその直前helperでplan fileを1回readし、同じbytesから:

```text
SHA-256
JSON parse
final target
provenance
```

を生成する。

同一monitor開始でファイルを複数回readしない。

### 15.2 return contract

既存testへの影響を最小化するため、`_read_plan_meta()`の既存key:

```text
target_soc_7_percent
required_night_charge_kwh
effective_capacity_kwh
```

は保持する。

追加keyとして:

```text
provenance
```

を許可する。

### 15.3 log位置

`_monitor_partial_forced_and_stop()`でplanをreadした直後、既存:

```text
[cloud_job_runner] 03-monitor contract ...
```

より**前**に1回だけ出す。

prefix固定:

```text
[cloud_job_runner] 03-plan-provenance 
```

後ろはcompact JSON:

```python
json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
```

例:

```text
[cloud_job_runner] 03-plan-provenance {"active_constraints":["reserve_soc"],"base_target_soc_7_percent":100.0,"candidate_100_percent":{"target_soc_percent":100.0,"total_expected_cost_yen":...},"final_target_soc_7_percent":91.0,"forecast_date":"2026-09-02","max_target_soc_percent_after_guards":100.0,"optimizer_kind":"cost","plan_sha256":"...","provenance_status":"complete","schema_version":1,"selected_candidate":{"target_soc_percent":91.0,"total_expected_cost_yen":...}}
```

### 15.4 log回数

1 executionにつき1回だけ。

poll loop内へ入れない。

### 15.5 failure

logging printが通常Python stdoutで失敗する特殊ケースを除き、provenance optional field不足でdevice controlを止めない。

---

## 16. 絶対に追加してはいけないもの

```text
03からFirestore night_charge_plans write
03からGCS upload
03からDrive upload
03からsettings_events write
03からpipeline_runs write
03 plan history database
追加Cloud Run Job
追加Scheduler
03 retry増加
forced reapply
新しいsleep
新しいnetwork request
```

`app/backup/night_plan_archive.py`の既存archive pathを03へ呼び込むのは禁止。

理由: 03 standalone/time ownership契約を破壊するため。

---

## 17. regression tests

### Test A: cost optimizer 100 -> 91

固定test名:

```python
def test_03_plan_provenance_logs_cost_base_100_final_91(...):
```

fixture plan:

```text
forecast.date = 2026-09-02
result.target_soc_7_percent_base = 100
result.target_soc_7_percent = 91
result.target_soc_7_percent_cost_optimized = 91
result.required_night_charge_kwh = distinct value
daytime_soc_optimization.objective = cost objective
daytime_soc_optimization.max_target_soc_percent_after_guards = 100
daytime_soc_optimization.selected_candidate.target_soc_percent = 91
candidate_summaries contains 90, 91, 92, 100
```

assert:

```text
log prefix exactly once
base=100
final=91
optimizer_kind=cost
selected=91
candidate_100=100
nearest_lower=90
nearest_higher=92
plan_sha256 == raw fixture bytes sha256
03-monitor contract target=91.00%
```

### Test B: guard cap 91

```python
def test_03_plan_provenance_logs_guard_cap_when_100_candidate_is_absent(...):
```

fixture:

```text
base=100
final=91
max_target_soc_percent_after_guards=91
active_constraints includes daytime_net_surplus_headroom_guard
candidate summaries max=91
```

assert:

```text
candidate_100_percent is null
max guard=91
active constraint retained
control target remains91
```

### Test C: optional provenance missing

```python
def test_03_plan_provenance_partial_does_not_block_forced_control(...):
```

fixture minimal valid plan:

```text
forecast.dateあり
result.target_soc_7_percent=80
optimizer markersなし
```

assert:

```text
provenance_status in {partial, complete according to fixed rules}
final=80
03 forced path continues
no new failure
```

### Test D: base conflict

```python
def test_night_plan_provenance_marks_conflicting_base_targets_without_changing_final(...):
```

fixture:

```text
result.target_soc_7_percent_base=100
decision_rationale.raw_target_soc_7_percent=95
result.target_soc_7_percent=91
```

assert:

```text
provenance_status=conflict
final=91 unchanged
```

### Test E: no external persistence

既存03 test fixtureで次をmonkeypatchして、呼ばれたらfail:

```text
Firestore client/open function
upload_night_plan_to_gcs
settings event writer
```

今回のsourceから新規import自体を作らないことが望ましい。

### Existing exact-target tests

既存:

```text
target=100 / SOC=99 -> continue
target=100 / SOC=100 -> target_reached
```

を必ず再実行し、挙動変更0を確認。

---

## 18. focused quality

repoの`code-quality-audit` Skillを先に使用。

利用不能なら再discoverを1回だけ。それでも不能ならSTOP。

その後:

```powershell
python -m ruff check app/energy_plan/night_plan.py app/runtime/cloud_job.py tests/test_cloud_job_runner.py
python -m pytest -q tests/test_cloud_job_runner.py tests/test_night_soc_controller.py tests/test_night_soc_protected_contract.py tests/test_night_soc_time_ownership.py
python scripts/security_check.py
git diff --check
```

pure helper test fileを追加した場合はfocused pytestへそのfileも追加。

---

## 19. full quality

focused PASS後:

```powershell
python -m pytest
python scripts/security_check.py
git diff --check
```

repo AGENTS/quality ruleが追加checkを要求するなら、それも実行。

失敗時は原因を直せる範囲がSection 5内か判定。

Section 5外source変更が必要ならSTOP。

---

## 20. source commit

stageするのは今回変更したsource/testだけ。

固定message:

```text
fix: log exact 03 plan SOC provenance
```

commit後:

```powershell
git status --short
git show --stat --oneline HEAD
```

想定外fileが入っていたらpush禁止。

---

## 21. CodebaseMemory final refresh

source/test commit後、同じcanonical projectをcurrent HEADへ同期。

ready確認後、最低限:

```text
app.energy_plan.night_plan.build_night_plan_provenance
app.runtime.cloud_job._read_plan_meta
app.runtime.cloud_job._monitor_partial_forced_and_stop
```

をquery。

shared artifactを**1回だけ**生成。

artifact条件:

```text
schema_version=2
project=C-VSC-SolerControler
commit=artifact生成直前のsource-bearing HEAD
nodes>0
edges>0
```

stage:

```text
.codebase-memory/artifact.json
.codebase-memory/graph.db.zst
```

固定message:

```text
chore: refresh CodebaseMemory 03 provenance graph
```

artifact commit後に二度目refreshは禁止。

CBM transportが最後までblockedならartifactを偽造しない。その場合はsource/test commitだけで進み、final reportを`PARTIAL_CBM_BLOCKED`にする。

---

## 22. push / CI

push前:

```powershell
git status --short
git fetch origin
git rev-parse HEAD
git rev-parse origin/master
```

remote driftがあれば`git pull --ff-only`のみ。

push:

```powershell
git push origin master
```

GitHub quality workflowを同じHEADについてterminalまで確認。

必須:

```text
status=completed
conclusion=success
```

failure/cancelled/timed_outならproduction deploy禁止。

---

## 23. production deployment

今回source変更はrunner behaviorのloggingのみだが、productionへ反映しないと次回03で証拠が取れない。

CI SUCCESS後に`PRODUCTION_DEPLOYMENT_RUNBOOK_JA.md`を全文再読し、repoの`solar-production-deployment` Skillを使用。

preflight:

```powershell
pwsh -NoProfile -File scripts/production_deployment_gate.ps1 -RunPreRelease
```

PASS後、state pathを1つだけ作る。

```powershell
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$statePath = "artifacts/deployment_state/production-$stamp.json"
```

official wrapper:

```powershell
pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 `
  -SkipPreRelease `
  -SettingsRoundTripTargetSoc 50 `
  -DeploymentScope auto `
  -StatePath $statePath
```

`DeploymentScope auto`の判定を上書きしない。

低レベルad-hoc deployは禁止。

中断時は同じstate fileで`-Resume`。

state JSON手編集禁止。

---

## 24. live verification

今回のsourceは03 runner loggingなので、**通常03 Jobを時間外に手動実行してはいけない**。

production deploy後に確認してよいもの:

```text
23/03/07 jobs intended image
Scheduler 03 = 0 3 * * * / Asia/Tokyo / ENABLED
07 DryRun（runbookが要求する場合）
settings round-trip（wrapper/runbook契約どおり）
```

禁止:

```text
03 normal manual execute
23 normal manual execute
07 non-DryRun manual execute
```

---

## 25. 次回03での受入条件

次のscheduled 03 executionでCloud Loggingから次のprefixをread-only取得する。

```text
[cloud_job_runner] 03-plan-provenance
```

その1行から最低限:

```text
forecast_date
plan_sha256
base target
final target
optimizer kind
max target after guards
active constraints
selected candidate
100% candidate有無/コスト
```

が復元可能であること。

同じexecutionの:

```text
03-monitor contract
03-monitor soc
03-monitor stop reason
```

と突き合わせる。

これにより次回は以下を証拠で分類できる。

```text
BASE100_FINAL91_COST_SELECTED
BASE100_FINAL91_GUARD_CAPPED
BASE_EQUALS_FINAL
LEGACY_OPTIMIZER_SELECTED
NO_OPTIMIZER
PLAN_PROVENANCE_CONFLICT
PLAN_PROVENANCE_PARTIAL
```

---

## 26. 今回の完了ステータス

source/test/CI/deployまで完了しても、scheduled 03がまだ来ていなければ:

```text
Result: DEPLOYED_WAITING_SCHEDULED_03
```

とする。

次のscheduled 03ログまで取得し、`03-plan-provenance`が存在して内容がcontract lineと一致した場合のみ:

```text
Result: COMPLETE_PROVENANCE_OBSERVABLE
```

とする。

---

## 27. STOP条件

即STOP:

```text
tracked user changesあり
protected 23/03/07 ownership変更が必要
optimizer source変更が必要
Firestore/GCS persistenceを03へ追加しないと実現できない
exact-target判定変更が必要
追加device writeが必要
06:45/06:50/06:55変更が必要
focused/full testsの修正にSection 5外source変更が必要
production preflight失敗
settings round-trip restore failure
CI non-success
```

回避実装を作らない。

---

## 28. 最終報告フォーマット

必ず次の順で報告:

```text
1. Result
2. final repo HEAD
3. files changed
4. 03 protected contract changed = NO
5. provenance schema_version
6. logged fields
7. plan SHA-256 source = raw file bytes
8. candidate digest rule
9. focused tests
10. full tests
11. security
12. CodebaseMemory status/project/nodes/edges/artifact source commit
13. GitHub CI result
14. production preflight
15. DeploymentScope/result/state resume有無
16. settings round-trip result
17. 03 manual execution = NO
18. next scheduled 03 provenance observed = YES/NO
19. secrets exposed = NO
```

project ID、region、execution ID、image digest全文、secret値は報告へ書かない。

---

## 29. 最重要の意図

今回解決するのは「91%が正しいか」ではない。

今回解決するのは:

> **03が使用した91%が、どのbase target・optimizer・constraint・candidate比較から生成されたかを、次回以降確実に証明できるようにすること。**

最適化ロジック自体には触れない。

次回ログで`base=100 / final=91`が実証された後に初めて、その日のcandidate cost/constraint evidenceを使って「91が仕様か不具合か」を判断する。

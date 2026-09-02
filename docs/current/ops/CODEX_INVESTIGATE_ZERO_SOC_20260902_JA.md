# Codex実行指示: 2026-09-02朝 SOC 0% を証拠優先で調査する

## 0. 目的

2026-09-02朝、利用者確認で蓄電池SOCが再び **0%** であった。

この障害は、直前まで追跡していた「100%表示に対して03 runtime targetが91%だった」問題より優先する。

今回の作業は **原因調査のみ** とする。原因がsource defectとして確定しても、この作業内でproduction sourceを修正・deployしてはいけない。

最終成果物は、2026-09-02 03:00 JSTのscheduled 03 execution、07:00 JST execution、公式監視CSV、Cloud Logging、plan provenanceを同一時系列で突き合わせたtracked investigation reportである。

---

# 1. 既知事実

開始時点でGitに保存済みの事実:

```text
03 provenance source implementation:
6eee4d9c861c03d52ee8e93a003e9c531c36f37c
fix: log exact 03 plan SOC provenance

pre-incident rollout report:
docs/completed/reports/03_plan_provenance_rollout_2026-09-02.md

rollout_status = DEPLOYED_WAITING_SCHEDULED_03
quality = SUCCESS
production_gate = SUCCESS
deployment = SUCCESS
settings_roundtrip = SUCCESS
07_dryrun = SUCCESS
scheduler_contract = PASS
scheduled_acceptance = PENDING
manual_03_execution = NO
```

利用者の新規事実:

```text
2026-09-02 morning SOC = 0%
```

この0%を「03が失敗した」とまだ断定しない。

最初に次の2分岐を確定する。

```text
A. 03 plan final target 自体が 0% だった
B. 03 plan final target は 0% より大きかったが、充電できなかった / 監視が誤判定した
```

---

# 2. 現行source契約の固定理解

`app/runtime/cloud_job.py::_monitor_partial_forced_and_stop`の現行契約:

1. planを読む。
2. `03-plan-provenance`を1回stdoutへ出す。
3. targetを`result.target_soc_7_percent`から得る。
4. initial SOCを読む。
5. forced mode-only profileを1回適用する。
6. `latest >= target`ならstandbyへ戻す。
7. target未達なら06:45まで監視する。
8. SOC unavailable、target reached、monitor cutoffのいずれかでstandbyへ戻す。

重要:

```text
target = 0
initial SOC = 0
```

なら、forced apply後に即`target_reached`となりstandbyへ戻り得る。

逆に:

```text
target > 0
initial SOC = 0
```

なら、正常系ではforcedを維持してSOC監視を継続する。

`app/kpnet/workflow.py::run_kpnet_mode_only_profile(profile="forced")`は:

- 現在設定をread-only取得
- required current fieldsが欠ければfail closed
- current snapshotをbaseにする
- `batteryOperatingMode`だけforced候補へ置換
- `socChargeMode`だけ最大supported候補へ置換
- write
- read-back
- controlled field mismatchならfailure

である。

この契約は今回の調査中に変更禁止。

---

# 3. 絶対禁止事項

今回のinvestigation taskでは以下を禁止する。

```text
production source edit
production deployment
Cloud Run Job manual normal 03 execution
Scheduler pause/disable/time change
03/07 retry policy change
forced reapply追加
lease/owner/Firestore handoff復活
03へのFirestore/GCS/DB persistence追加
optimizer無効化
100%固定
headroom guard無効化
stop condition変更
SOC parser変更
settings round-trip再実行
実機設定writeを伴う診断
低レベルgcloud update
tracked secret保存
full env dump
cookie/html/auth token保存
```

read-only Cloud Run / Logging / Scheduler / KP-NET CSV取得は許可する。

---

# 4. 完了ステータス

最終`Result`は以下の1つだけ。

```text
INVESTIGATION_COMPLETE_CAUSE_PROVEN
INVESTIGATION_COMPLETE_CAUSE_NARROWED
BLOCKED_CLOUD_EVIDENCE
BLOCKED_MONITORING_EVIDENCE
BLOCKED_GIT_STATE
```

`INVESTIGATION_COMPLETE_CAUSE_PROVEN`はSection 17の原因分類を1つだけ証拠で確定できた場合のみ。

推測で`CAUSE_PROVEN`を名乗らない。

---

# 5. Git preflight

PowerShell 7のみ使用。

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
git fetch origin
git rev-parse origin/master
```

条件:

```text
tracked user changesあり -> STOP
local master behind origin/master -> git pull --ff-only
rebase必要 -> STOP
non-fast-forward -> STOP
```

禁止:

```text
git reset --hard
git stash
force push
user untracked file削除
```

開始master基準:

```text
9519e3024667f8a6edc5cfe21d67dac6084af678
Merge pull request #33 ... record 03 provenance rollout status
```

origin/masterがそれより進んでいたら、最新masterをauthorityにして差分を先に読む。

---

# 6. 必読ファイル

以下を順番固定で読む。

```text
AGENTS.md
docs/current/agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md
docs/current/ops/PRODUCTION_DEPLOYMENT_RUNBOOK_JA.md
docs/completed/reports/03_plan_provenance_rollout_2026-09-02.md
docs/completed/reports/soc_exact_target_stop_remediation_2026-08-31.md
docs/completed/reports/soc_gap_investigation_2026-08-31.md
docs/completed/reports/soc_target_provenance_investigation_2026-09-01.md
app/runtime/cloud_job.py
app/runtime/slot_orchestration.py
app/runtime/soc_reading.py
app/runtime/night_soc_time_contract.py
app/kpnet/workflow.py
app/kpnet/client.py
app/kpnet/profile_builder.py
app/energy_plan/night_plan.py
app/energy_plan/optimization.py
```

存在しないreport名があれば、`docs/completed/reports`を列挙し最も対応するtracked reportを使う。似た名前を新規生成して存在したことにしない。

---

# 7. CodebaseMemory preflight

canonical projectのみ:

```text
C-VSC-SolerControler
```

statusを1回確認。

readyなら次のsymbolだけqueryする。

```text
app.runtime.slot_orchestration._run_adjust_03
app.runtime.cloud_job._ensure_night_plan_available
app.runtime.cloud_job._monitor_partial_forced_and_stop
app.runtime.cloud_job._RunnerMonitorDevicePort.read_soc
app.runtime.soc_reading.read_soc_with_fallback
app.kpnet.workflow.run_kpnet_mode_only_profile
app.kpnet.workflow._mode_only_profile_from_current_settings
app.energy_plan.night_plan.build_night_plan_provenance
```

transport failureなら:

1. exact errorをメモ。
2. statusをもう1回だけ実行。
3. 2回目も失敗なら:

```text
CBM_TRANSPORT_BLOCKED_USER_AUTHORIZED_SOURCE_FALLBACK
```

として`rg`+直接source inspectionへ進む。

禁止:

```text
reinstall
package update
MCP config edit
duplicate project
別project名
reindex連打
```

今回の調査はCBM failureだけを理由に停止しない。

---

# 8. mandatory rg

```powershell
rg -n "03-plan-provenance|03-monitor contract|03-monitor soc|03-monitor stop reason|03-forced-start|03-immediate-standby|03-target-reached-standby|03-monitor-cutoff-standby" app tests docs
rg -n "run_kpnet_mode_only_profile|_mode_only_profile_from_current_settings|read_soc_with_fallback|latest_realtime_soc_percent|latest_csv_soc_reading" app tests
rg -n "target_soc_7_percent|target_soc_7_percent_base|max_target_soc_percent_after_guards|candidate_summaries|active_constraints" app tests docs
```

sourceとtracked docsの契約差があればsourceをauthorityとし、reportに差を記録する。

---

# 9. incident time window

対象日はJSTで固定:

```text
incident date = 2026-09-02
scheduled 03 = 2026-09-02 03:00 JST
scheduled 07 = 2026-09-02 07:00 JST
```

Cloud Logging取得window:

```text
2026-09-02 02:45 JST ～ 2026-09-02 07:30 JST
UTC equivalent:
2026-09-01T17:45:00Z ～ 2026-09-01T22:30:00Z
```

監視CSV確認window:

```text
2026-09-01 23:00 JST ～ 2026-09-02 08:00 JST
```

日付を1日ずらさない。

---

# 10. 03 Scheduler / execution existence確認

read-onlyで確認する。

最低限記録:

```text
scheduler_enabled
scheduler_timezone
scheduler_schedule
scheduler_last_attempt_or_equivalent
03_execution_exists
execution_created_at
execution_started_at
execution_completed_at
succeeded_count
failed_count
Completed condition
Started condition
ContainerReady condition
ResourcesAvailable condition
image digest or immutable image identity
```

期待schedule:

```text
0 3 * * *
timezone = Asia/Tokyo
```

識別子/project番号/resource IDはreportに書かない。

executionが存在しなければSection 17の:

```text
SCHEDULER_OR_EXECUTION_NOT_RUN
```

候補になる。

ただしScheduler側attempt証拠まで確認する。

---

# 11. 03 Cloud Logging mandatory marker inventory

2026-09-02 03 executionだけにscopeを限定し、以下を数える。

```text
03-plan-provenance
03-monitor contract
03-monitor soc
03-monitor stop reason
03 standby failure
KP-NET mode-only workflow failed
KP-NET settings read-back mismatch
03 ownership ended
Traceback
ERROR
WARNING
```

reportに必ず:

```text
provenance_line_count = N
contract_line_count = N
soc_line_count = N
stop_reason_line_count = N
```

を記録する。

正常な新runner受入では:

```text
provenance_line_count = 1
contract_line_count = 1
stop_reason_line_count = 1
```

を期待する。

0件ならimage/source rollout mismatchを疑う。

2件以上なら同じexecution内でcontroller重複実行を疑う。

どちらも推測で補完しない。

---

# 12. 03-plan-provenanceを完全に解析

ログの1行JSONから次を転記する。

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
selected_candidate.target_soc_percent
selected_candidate.total_expected_cost_yen
selected_candidate.required_night_charge_kwh
candidate_100_percent.target_soc_percent
candidate_100_percent.total_expected_cost_yen
nearest_lower_candidate
nearest_higher_candidate
provenance_status
constraint_details
```

nullはnullのまま記録。

存在しないfieldを0に変換しない。

### 12.1 最初の強制分岐

```text
final_target_soc_7_percent <= 0
```

なら`TARGET_ZERO_BRANCH`。

```text
final_target_soc_7_percent > 0
```

なら`TARGET_POSITIVE_BRANCH`。

この分岐確定前にforced mode failureを原因と断定しない。

### 12.2 TARGET_ZERO_BRANCH

必ず次を判定する。

```text
base targetも0か
base >0 から final=0へoptimizerが変更したか
max target after guardsが0か
active constraintのcapが0か
selected candidateが0か
candidate 100が存在したか
nearest higher candidateのcostはいくらか
```

分類例:

```text
base=0, final=0 -> upstream/base target zero
base>0, max_guard=0, final=0 -> constraint cap zero
base>0, max_guard>0, selected=0 -> optimizer selected zero
```

ただし今回のinvestigationではoptimizer sourceを修正しない。

---

# 13. 03-monitor SOC timeline解析

すべての`03-monitor soc`を時刻順に表にする。

列:

```text
log_timestamp_jst
value_percent
source
observed_at
runtime_target_percent
action
```

必ず確認:

```text
first SOC value
first SOC source
last SOC value
last SOC source
minimum SOC
maximum SOC
number of realtime readings
number of csv readings
number of unavailable readings
```

stop lineから:

```text
stop_reason
target
latest if present
stop_timestamp_jst
```

を記録する。

### 13.1 target>0かつfirst SOC=0の場合

正常ならforced apply後、次回以降SOCが上昇するか、少なくとも監視が継続する。

以下を分類する。

```text
forced apply前にruntime error
forced apply/readback failure
forced apply gate passed but SOC stayed 0
SOC became unavailable and fail-safe standby
monitor cutoffまで未達
realtime readingが不自然に高くtarget_reached
```

---

# 14. forced mode-only結果の確認

Cloud Loggingから次を探す。

```text
KP-NET mode-only workflow failed
KP-NET settings read-back mismatch
setting confirmation failed
TimeoutError
login failure
write failure
read current settings failure
candidate map failure
```

現行`run_kpnet_mode_only_profile`はexception時にreturn 1となり、03側は`RuntimeError`へ変換する。

したがって:

```text
03 execution error + mode-only failure log
```

があれば`FORCED_WRITE_OR_READBACK_FAILED`候補。

一方、エラー無しでcontrollerがSOC monitoringへ進んでいる場合は:

```text
READBACK_GATE_PASSED
```

と表現する。

**requested/observed historical settings値がログに無い場合、それらを成功ログから捏造しない。**

`READBACK_GATE_PASSED`と`historical exact setting values proven`は別。

---

# 15. 公式監視CSVで実電力を確認

read-only KP-NET CSV取得のみ許可。

2026-09-01 23:00 ～ 2026-09-02 08:00 JSTについて最低30分粒度で次を抽出する。

```text
timestamp
soc_percent
charge_kwh
discharge_kwh
pv_kwh
buy_kwh
load_kwh
```

repo parserの正式field名に合わせる。

tracked evidence file:

```text
docs/completed/reports/zero_soc_evidence_2026-09-02.csv
```

列は必要最小限。元CSV全体をtracked fileへコピーしない。

最低集計:

```text
23:00 SOC
00:00 SOC
03:00 SOC
03:30 SOC
04:00 SOC
04:30 SOC
05:00 SOC
05:30 SOC
06:00 SOC
06:30 SOC
07:00 SOC
07:30 SOC
08:00 SOC
03:00-07:00 total charge_kwh
03:00-07:00 total discharge_kwh
```

「朝SOC 0%」を、可能なら07:00/07:30 official CSVで再確認する。

利用者報告とCSVが異なる場合は両方を残し、どちらかを消さない。

---

# 16. runtime SOCとofficial CSVを時系列比較

各`03-monitor soc`について、同時刻に最も近いofficial monitoring sampleを併記する。

report table:

```text
runtime timestamp
runtime SOC
runtime source
official CSV timestamp
official CSV SOC
delta percentage points
runtime action
```

重要な判定:

### 16.1 runtime target_reachedなのにofficial CSVが低SOC

もし:

```text
stop_reason = target_reached
runtime source = realtime
runtime latest >= target
```

で、近接official CSVが明らかに低SOCのままなら、

```text
REALTIME_VS_OFFICIAL_SOC_DISAGREEMENT
```

候補とする。

ただしparser bugをこの作業内で修正しない。

### 16.2 runtimeもofficial CSVも0のまま

final target >0で、03:00-07:00 charge_kwhもほぼ/完全に0なら:

```text
FORCED_MODE_DID_NOT_PRODUCE_CHARGE
```

候補。

この場合も、write failureログの有無で:

```text
write/readback failed
vs
readback gate passed but physical charging absent
```

を分離する。

---

# 17. 原因分類

最終classificationは以下から **1つ** 選ぶ。

```text
SCHEDULER_OR_EXECUTION_NOT_RUN
ROLLOUT_IMAGE_MISMATCH_NO_PROVENANCE_LOG
PLAN_FINAL_TARGET_ZERO_BASE_ZERO
PLAN_FINAL_TARGET_ZERO_CONSTRAINT_CAP
PLAN_FINAL_TARGET_ZERO_COST_OPTIMIZER
PLAN_GENERATION_OR_PREP_FAILURE
FORCED_WRITE_OR_READBACK_FAILED
FORCED_MODE_GATE_PASSED_BUT_NO_CHARGE
SOC_UNAVAILABLE_FAILSAFE_STANDBY
MONITOR_CUTOFF_WITHOUT_CHARGE
REALTIME_VS_OFFICIAL_SOC_DISAGREEMENT
UNEXPECTED_TARGET_REACHED_LOGIC_INPUT
HISTORICAL_CAUSE_NOT_PROVABLE
```

### 17.1 classification rule

`PLAN_FINAL_TARGET_ZERO_*`はprovenance JSONでfinal=0を証明できた場合のみ。

`FORCED_WRITE_OR_READBACK_FAILED`は具体的error/readback mismatch/return failure証拠が必要。

`FORCED_MODE_GATE_PASSED_BUT_NO_CHARGE`は:

```text
final target > 0
forced pathがfailure無しでmonitoringへ進んだ
runtime/official SOCが上昇しない
official charge energyが0または実質的に充電不成立
```

を全て満たすこと。

`SOC_UNAVAILABLE_FAILSAFE_STANDBY`はstop reason=soc_unavailableを必須とする。

`MONITOR_CUTOFF_WITHOUT_CHARGE`はstop reason=monitor_cutoffを必須とする。

`REALTIME_VS_OFFICIAL_SOC_DISAGREEMENT`は停止判断に使われたruntime SOCとofficial CSVの矛盾を表で示す。

証明できなければ`HISTORICAL_CAUSE_NOT_PROVABLE`。

---

# 18. 07:00 execution確認

03原因調査と独立して07 jobを確認する。

read-onlyで:

```text
07 execution exists
07 created/started/completed
succeeded/failed count
Completed/Started/ContainerReady/ResourcesAvailable
mode-only workflow failure有無
read-back mismatch有無
```

07は03結果に関係なくgreenを1回applyする契約。

今回SOC 0の原因を07 failureへ誤帰属しない。

ただし07 failureが同時発生していたらsecondary incidentとしてreportへ記録。

また06:55 JST以降に03由来device writeが存在しないことをログ時刻で確認する。

---

# 19. 23:00 previous-night確認

2026-09-01 23:00 JST slot23 executionをread-only確認。

最低限:

```text
execution success/failure
standby mode-only failure有無
read-back mismatch有無
```

23 failureがあっても、それだけで03が充電しなかった直接原因とは断定しない。

03 forcedはcurrent snapshotからforced modeへ切り替える独立契約だからである。

secondary evidenceとして残す。

---

# 20. plan preparation path確認

`_run_adjust_03`は:

```text
03-initial-csv
-> _ensure_night_plan_available
-> monitor
```

の順。

try blockでexceptionが起きると、`plan_path.exists()`がtrueなら既存planで継続し得る。

今回のexecution logsで必ず確認:

```text
initial CSV succeeded?
energy_model_main.py started?
energy model completed?
FORECAST_DATE_OVERRIDE corresponded to 2026-09-02?
plan generation traceback/error?
provenance forecast_date = 2026-09-02?
```

もし:

```text
plan generation failed
+ old/existing plan was used
+ provenance forecast_date != 2026-09-02
```

なら`PLAN_GENERATION_OR_PREP_FAILURE`を強く支持する。

container filesystemのhistorical状態を推測しない。ログ/provenanceだけを使う。

---

# 21. image provenance確認

03 execution imageがprovenance logging sourceを含むことをread-only確認する。

最低条件:

```text
production deployment stateがsource_commit=6eee4d9c系を指す
03 execution immutable image identityがそのdeploy後revision/imageと一致
03-plan-provenance markerが1回存在
```

markerが無ければ、sourceがdeploy済みだったというreportだけで新runner実行済みと断定しない。

`ROLLOUT_IMAGE_MISMATCH_NO_PROVENANCE_LOG`候補にする。

---

# 22. 現在設定のread-only確認の扱い

2026-09-02 22時以降の現在KP-NET設定をread-onlyで取得してもよいが、用途を限定する。

許可用途:

```text
current candidate mapsの確認
current BatteryOperatingMode candidate set
current SocChargeMode candidate set
parserが現在のformを読めるか
```

禁止用途:

```text
現在設定を03:00 historical settings値だったと断定
現在greenを03 forced失敗の証拠にする
現在SOCを朝SOCの代用にする
```

歴史証拠と現況診断を分離する。

---

# 23. report出力

tracked report:

```text
docs/completed/reports/zero_soc_investigation_2026-09-02.md
```

必須章:

```text
1. Incident statement
2. Repository/source baseline
3. Production rollout provenance
4. Scheduler and execution timeline
5. 03 provenance payload
6. Runtime SOC timeline
7. Official monitoring timeline
8. Runtime-vs-official SOC comparison
9. Forced mode/read-back evidence
10. Plan generation evidence
11. 23/07 secondary evidence
12. Root-cause classification
13. What is proven
14. What is not proven
15. Next fix scope
16. Safety/non-mutation statement
```

report冒頭に必ず:

```text
Result: <Section 4 status>
Primary classification: <Section 17 one value>
Incident date: 2026-09-02
Production mutation performed: NO
Normal 03 manual execution performed: NO
```

を書く。

---

# 24. evidence CSV

必要なofficial monitoring rowsが取得できた場合:

```text
docs/completed/reports/zero_soc_evidence_2026-09-02.csv
```

を追加。

推奨列:

```text
timestamp_jst,soc_percent,charge_kwh,discharge_kwh,pv_kwh,buy_kwh,load_kwh
```

credential、URL query token、raw HTML、cookieは入れない。

---

# 25. source defect候補が見つかった場合

このtask内で修正しない。

reportの`Next fix scope`へ以下の形式で書く。

```text
NEXT_FIX_REQUIRED = YES
PROVEN_DEFECT_BOUNDARY = <exact symbol/file>
MINIMUM_ALLOWED_FILES = <files>
PROTECTED_FILES_NOT_TO_CHANGE = <files/contracts>
REQUIRED_REGRESSION_TEST = <test concept>
PRODUCTION_DEPLOYMENT_REQUIRED = YES/NO/UNKNOWN
```

例:

```text
PLAN target=0 がoptimizer意図なら source defectと断定しない
realtime誤読が証明されたら soc_reading/client parser境界をnext fix候補
forced read-back成功なのに充電0なら mode-only device contractをnext fix候補
plan date mismatchなら plan preparation fallback境界をnext fix候補
```

---

# 26. 調査中のSTOP条件

以下の場合は回避策を作らずSTOP/reportする。

```text
Cloud credentials unavailable
03 execution logs retention切れ
official monitoring CSV unavailable
Git tracked user changes
production resource read permission denied
incident executionを一意に特定できない
```

ただし一部証拠だけ取得できるなら`INVESTIGATION_COMPLETE_CAUSE_NARROWED`を使用し、取得済み情報を失わない。

---

# 27. validation

report/evidenceのみのcommit前に:

```powershell
git diff --check
python scripts/security_check.py
```

repo policyでdocs-onlyにもqualityが必要なら該当commandを実行。

source/testを変更していないことを:

```powershell
git diff --name-only <baseline>...HEAD
```

で確認。

許可tracked changesは原則:

```text
docs/completed/reports/zero_soc_investigation_2026-09-02.md
docs/completed/reports/zero_soc_evidence_2026-09-02.csv  # evidence取得時のみ
```

このone-shot instruction自身を作業完了時にarchiveするrepo運用が既存contractにある場合のみ、既定completed agent_runs destinationへ`git mv`してよい。

---

# 28. commit / push

調査完了commit message:

```text
docs: record 2026-09-02 zero SOC investigation
```

source fix commitを混ぜない。

push前:

```powershell
git status --short
git diff --check
git log -1 --oneline
```

push後:

```text
local HEAD == origin branch HEAD
worktree clean
```

GitHub CIが起動する場合はcompleted/successまで確認。

CI failureなら原因をreportし、production mutationで回避しない。

---

# 29. 最終報告フォーマット

Codexの最終回答はこの順番。

```text
Result: ...
Primary classification: ...
03 execution: ...
03 final target: ...%
03 base target: ...%
03 stop reason: ...
03 first/last SOC: ... / ...
03:00-07:00 charge total: ... kWh
07 execution: ...
Runtime-vs-official SOC disagreement: YES/NO/NOT_PROVABLE
Forced write/readback failure: YES/NO/NOT_PROVABLE
Plan date mismatch: YES/NO/NOT_PROVABLE
Report commit: <sha>
CI: SUCCESS/FAILURE/NOT_RUN
Production mutation: NO
Next fix required: YES/NO
```

秘密値/resource IDを貼らない。

---

# 30. 最重要判断順序

Luna軽は必ず次の順番だけで進む。

```text
1. 03 executionが存在したか
2. 新runner/provenance markerが存在したか
3. final targetが0か >0か
4. 03 runtime SOCがどう推移したか
5. stop reasonは何か
6. forced mode-only failureがあったか
7. official charge energyが実際に流れたか
8. runtime SOCとofficial SOCは一致したか
9. plan date/generationは正しかったか
10. 23/07 secondary evidenceを確認
11. Section 17から原因を1つ分類
12. report/evidenceだけcommit
```

この順番を飛ばして「optimizerが悪い」「KP-NETが悪い」「SOC parserが悪い」と決めない。

今回の目的は、2026-09-02朝SOC 0%の原因を、既に追加済みのprovenanceログと実電力データを使って初めて再現可能な形で固定することである。

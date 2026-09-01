# Codex実行指示: 2026-09-01 SOC target 100% → 91% の生成経路を確定し、必要な場合のみ修正する

## 0. 目的

2026-09-01 03:00 JST の本番03実行について、exact-target停止そのものは正常に動作した。

既存の追跡済み報告で確定している値:

```text
classification = EXACT_TARGET_CONFIRMED
03 runtime contract target = 91.00%
final relevant SOC = 96.00%
SOC source = realtime
stop reason = target_reached
```

一方、利用者が確認していた設定/表示SOCは100%であった。

今回の作業は、次の値がどこで分岐したかを証拠で確定する。

```text
user/dashboard setting = 100%
        ↓
base target ?
        ↓
optimizer/constraint target ?
        ↓
night_charge_plan.json result.target_soc_7_percent = ?
        ↓
03 runtime contract target = 91%
```

**最重要:** 91%を見ただけで100%へ固定する修正は禁止する。

現行Energy Planには、base targetをcost optimizerまたはlegacy optimizerのfinal targetで置換する設計が存在する。またPV headroom/history系constraintもtarget ceilingを持つ。したがって、100%と91%の差が仕様上正しい最適化なのか、入力/日付/表示/意味の不整合なのかを先に確定する。

---

# 1. 完了条件

この作業の最終Resultは次のいずれか1つだけ。

```text
COMPLETE_NO_SOURCE_CHANGE
COMPLETE_SOURCE_FIXED_AND_DEPLOYED
BLOCKED_PROVENANCE
BLOCKED_UNSAFE_TO_CHANGE
PARTIAL_CBM_BLOCKED
```

`COMPLETE_*`を名乗れるのは、最低限次を満たした場合だけ。

1. 2026-09-01 03 runtime target=91%を既存報告/Cloud Loggingで再確認。
2. 91%を生成したplanのforecast dateを確定。
3. `target_soc_7_percent_base`を確定、または保存証跡がないことを明示。
4. final `target_soc_7_percent`を確定。
5. optimizer modeを確定（cost / legacy / no optimizer）。
6. active constraintと各capを確定。
7. dashboard/user側100%のsource fieldを確定。
8. 100と91の意味が同一か別指標かを分類。
9. 原因分類をSection 15の1つへ固定。
10. source変更が必要な分類の場合だけ限定修正。
11. focused/full test + quality + securityを成功。
12. source変更時はGitHub CI成功後のみproductionへ反映。
13. source変更時は公式deployment wrapper以外を使用しない。
14. 最終reportをtracked fileへ保存。
15. CodebaseMemoryが使用可能なら最後にshared artifactを1回だけ更新。

---

# 2. baseline

開始基準master:

```text
3f8ce9b22b40f088b1744ca42b49f915c6a88ed4
chore: refresh CodebaseMemory dashboard warning graph
```

直前の重要commit:

```text
5de2cf7f1372458e861d1b1f613504d053838a70  fix: require exact SOC target before 03 standby
14e4e0bd6d7b75bc986b10dbc6332839738060a4  fix: align dashboard SOC and settings warnings
ccf9c83cfdb394459afc4513abbd93168fd4c4d0  docs: confirm 2026-09-01 03 stop reason
3f8ce9b22b40f088b1744ca42b49f915c6a88ed4  chore: refresh CodebaseMemory dashboard warning graph
```

既知事実:

```text
03 exact-target condition = observed SOC >= plan target
2026-09-01 runtime target = 91%
2026-09-01 final observed SOC = 96%
stop reason = target_reached
```

**03 runtime exact-target実装は今回の調査対象外。**

以下は変更禁止:

```text
app/runtime/cloud_job.py のtarget reached条件
app/runtime/night_soc_time_contract.py
23/03/07 ownership
06:45 / 06:50 / 06:55 fences
KP-NET mode-only current snapshot契約
SocChargeMode=50 activation candidate契約
```

---

# 3. Git preflight

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
git rebase必要 -> STOP
non-fast-forward -> STOP
```

禁止:

```text
git reset --hard
git stash
force push
利用者の未追跡ファイル削除
```

開始時はmasterへ移動し、`git pull --ff-only`でorigin/masterへ合わせる。

---

# 4. 必読ファイル

以下を**順番固定**で読む。

```text
AGENTS.md
docs/current/agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md
docs/current/agent/codebase_memory_shared_graph_usage_ja.md
docs/current/ops/PRODUCTION_DEPLOYMENT_RUNBOOK_JA.md
docs/completed/reports/soc_exact_target_stop_remediation_2026-08-31.md
docs/completed/reports/dashboard_warning_investigation_2026-09-01.md
app/energy_plan/night_plan.py
app/energy_plan/result_builder.py
app/energy_plan/optimization.py
app/energy_plan/soc_constraints.py
app/energy_plan/soc_cost.py
app/energy_plan/workflow.py
app/energy_plan/plan_quality.py
app/energy_plan/forecast_inputs.py
app/runtime/cloud_job.py
app/dashboard/warnings.py
app/dashboard/service.py
app/dashboard/firestore_repository.py
app/operations/firestore.py
```

さらに`rg`で次を全件列挙する。

```powershell
rg -n --hidden --glob '!docs/completed/**' `
  'target_soc_7_percent|target_soc_7_percent_base|setting_soc_target_percent|final_target_soc_7_percent|raw_target_soc_7_percent|max_target_soc_percent|cap_target_soc_percent' `
  app tests docs/current scripts
```

出力は調査メモへ要約し、生ログをtracked fileへ丸ごと貼らない。

---

# 5. CodebaseMemory preflight

既存canonical projectのみ使用:

```text
C-VSC-SolerControler
```

statusを1回確認。

readyなら次だけqueryする。

```text
app.energy_plan.optimization.run_current_optimizer
app.energy_plan.optimization.run_legacy_optimizer
app.energy_plan.optimization.cost_max_target_soc
app.energy_plan.result_builder._build_energy_model_output
app.energy_plan.soc_constraints.assemble_constraint_set
app.energy_plan.soc_constraints.morning_pv_headroom_guard
app.energy_plan.soc_constraints.daytime_net_surplus_headroom_guard
app.energy_plan.soc_constraints.historical_daytime_soc_gain_guard
app.energy_plan.night_plan.parse_night_plan
app.runtime.cloud_job._monitor_partial_forced_and_stop
app.dashboard.warnings.build_dashboard_warnings
```

source/`rg`を最終authorityとする。

transport closed/connection failureなら:

```text
exact error保存
statusをもう1回だけ実行
```

2回目もtransport failureなら:

```text
CBM_TRANSPORT_BLOCKED_USER_AUTHORIZED_SOURCE_FALLBACK
```

と記録し、`rg` + direct source inspectionで作業継続。

禁止:

```text
CodebaseMemory reinstall
upgrade/downgrade
MCP設定編集
別project作成
duplicate project作成
index_repository連打
transport failureをreindexで直そうとする
```

CBMが使えなかった場合、使えたと報告しない。

---

# Part A: 2026-09-01 03 runtime target=91%のprovenanceを固定

## 6. 既存03実行の再確認

通常03 Jobを新規実行しない。

既存報告:

```text
docs/completed/reports/dashboard_warning_investigation_2026-09-01.md
```

から次を確認。

```text
classification = EXACT_TARGET_CONFIRMED
contract target = 91.00%
final relevant SOC = 96.00%
SOC source = realtime
stop reason = target_reached
```

必要な場合だけ既存Cloud Run executionログをread-onlyで再確認する。

新規03実行禁止。

---

## 7. 03が実際に読んだplan contract

`app/runtime/cloud_job.py`でtargetがどのfieldから取得されるかsourceで確認する。

期待:

```text
night_charge_plan.json
 -> result.target_soc_7_percent
 -> _read_plan_meta()
 -> _monitor_partial_forced_and_stop()
 -> contract target
```

このchainが異なる場合は、その時点で記録し、勝手に修正しない。

確認項目:

```text
field name
date validation
plan_quality gate
minimum target overrideの有無
runtime env overrideの有無
```

**runtimeで91を作っていると推測しない。**

---

## 8. historical plan artifact探索

2026-09-01 03実行が使用した`night_charge_plan.json`のhistorical copyをread-onlyで探す。

探索順序を固定:

1. repository tracked report/evidence
2. deployment/runtime artifactで既存保存済みのもの
3. configured night-plan archive destination（read-only list/download）
4. Cloud Logging内のsanitized plan summary
5. Firestoreに既存文書がある場合のみread-only

禁止:

```text
現在のnight_charge_plan.jsonを当時のplanとして扱う
現在planを上書き
historical再生成結果をhistorical実物と断定
Firestoreへplanを書き戻す
```

historical planが取得できた場合、以下だけをsanitized evidenceへ保存。

```text
forecast.date
generated_at_utc / plan id / hash（機密でなければhashのみ）
inputs.soc_now_percent
result.target_soc_7_percent_base
result.target_soc_7_percent
result.required_night_charge_kwh_base
result.required_night_charge_kwh
result.target_soc_7_percent_cost_optimized
plan_quality.status / should_apply
decision_rationale.objective
decision_rationale.active_constraints
decision_rationale.raw_target_soc_7_percent
decision_rationale.final_target_soc_7_percent
decision_rationale.cost_breakdown_yen
decision_rationale.morning_pv_headroom_guard
decision_rationale.daytime_net_surplus_headroom_guard
decision_rationale.historical_daytime_soc_gain_guard
final predicted PV/load values必要部分
```

secret、project id、account id、execution idはtracked reportへ保存しない。

---

## 9. historical planが見つからない場合

historical plan不在なら、次を別々に扱う。

```text
PROVEN_FROM_HISTORICAL_ARTIFACT
PROVEN_FROM_RUNTIME_LOG
RECONSTRUCTED_FROM_SAME_INPUTS
CURRENT_SOURCE_ONLY
UNKNOWN
```

再計算は`RECONSTRUCTED_FROM_SAME_INPUTS`であり、historical planの証明ではない。

historical artifactがないだけでsource変更しない。

---

# Part B: 100%側のsourceを固定

## 10. `setting_soc_target_percent`の意味を追跡

`rg`で`setting_soc_target_percent`のwriterとreaderを全件確認する。

最低限次を表にする。

| field | writer | source input | date semantics | meaning |
|---|---|---|---|---|
| `setting_soc_target_percent` | ? | ? | ? | ? |
| `target_soc_7_percent_base` | optimizer前 | ? | forecast date | ? |
| `target_soc_7_percent` | optimizer後 | ? | forecast date | final runtime target |

100%側のsourceを以下のどれかへ固定。

```text
DEVICE_SETTING
BASE_PLANNER_TARGET
FINAL_PLANNER_TARGET
DAILY_METRIC_COPY
STALE_HISTORICAL_VALUE
DIFFERENT_DATE_VALUE
UNKNOWN
```

**100%と91%が同じ概念かを確認する前に「不一致」と断定しない。**

---

## 11. date alignmentを必ず確認

次の日付をすべてJSTで並べる。

```text
03 execution date/time
night plan forecast.date
plan generation timestamp
dashboard battery_daily_metrics.date
setting_soc_target_percent対象日
forecast_hourly対象日
```

日付が異なる場合:

```text
DATE_MISMATCH
```

として最優先で原因候補にする。

UTC/JST混同禁止。

---

# Part C: optimizerが100→91を作ったか検証

## 12. base/final targetのsource契約

現行sourceでは`run_current_optimizer()`が、optimizer成功時に:

```text
target_soc_7_percent_base = optimizer前のtarget
target_soc_7_percent = optimized.target_soc_7_percent
```

へ置換する。

`result_builder.py`はdecision rationaleへ:

```text
raw_target_soc_7_percent
final_target_soc_7_percent
final_required_night_charge_kwh
```

を記録する。

この契約をsourceで再確認する。

historical planで:

```text
base = 100
final = 91
```

なら、100→91は**03 runtimeではなくplanner optimizer境界**で起きたと分類する。

---

## 13. optimizer mode判定

次のどれか1つへ固定。

```text
COST_OPTIMIZER
LEGACY_OPTIMIZER
NO_OPTIMIZER
UNKNOWN
```

判定証拠:

```text
config.cost_optimization_enabled
plan decision_rationale.objective
cost_optimization payload
legacy_peak_objective payload
```

production Cloud Run job metadataからenv値を読む場合はread-only。

secret値は表示禁止。

---

## 14. 91%を決定した具体的根拠

### 14.1 constraint ceiling

最低限確認:

```text
SocConstraintSet.max_target_soc_percent
morning_pv_headroom_guard.cap_target_soc_percent
daytime_net_surplus_headroom_guard.cap_target_soc_percent
historical_daytime_soc_gain_guard.cap_target_soc_percent
apply_pv_headroom_caps
respect_morning_headroom_guard
```

91%がconstraint ceiling由来なら、どのguardが何%へcapしたか1つずつ記録。

### 14.2 cost optimizer candidate

91%がcapではなくcost optimumなら、最低限:

```text
selected target=91
selected total expected cost
selected expected day buy
selected expected sell/export
selected peak unmet
candidate 90/91/92/99/100の比較
```

を取得可能な既存optimizer diagnosticから比較する。

候補一覧が保存されていない場合はsame-input replayで作ってよいが、必ず`RECONSTRUCTED`とラベルする。

### 14.3 forecast inputs

次を確認。

```text
final predicted PV kWh
hourly PV 7-17
hourly load 7-17
PV uncertainty
weather class
forecast correction source
SOC now
capacity
reserve SOC
```

91%を選んだ理由がPV headroomなら、予測PVの過大評価がないかも確認する。

ただしforecast modelを同じ作業で修正しない。

### 14.4 historical SOC gain guard

historical guardが適用されている場合:

```text
sample_count
percentile
percentile_gain_percent
guard_gain_percent
cap_target_soc_percent
excluded_counts
```

を記録。

「過去に昼間SOCが上がるため朝SOCを下げる」設計が、今回の天候/実績に対して合理的かを数値で評価する。

---

# 15. 原因分類

必ず1つのPRIMARYと、必要ならSECONDARYを選ぶ。

## A. `OPTIMIZER_INTENDED_FINAL_TARGET_UI_SEMANTIC_MISMATCH`

条件:

```text
base target = 100
optimizer final = 91
optimizer evidence is internally consistent
03 consumed final=91 correctly
dashboard/user 100 is base/device/別指標
```

修正方針:

- optimizerを100へ固定しない。
- dashboard/UI/reportで`base target`と`final optimized morning target`を混同している箇所だけ修正。
- user-visible labelを意味に合わせる。
- final target=91を朝目標として表示するか、base 100とfinal 91を別表示する。

## B. `OPTIMIZER_UNINTENDED_CONSTRAINT_CAP`

条件:

```text
base=100
final=91
91がconstraint cap由来
constraint input/date/forecast/historyが不正またはstaleであることを証明
```

修正方針:

- 不正なinput/provenanceだけ修正。
- guard全体disable禁止。
- 100固定禁止。
- 問題のない他guardは変更しない。

## C. `OPTIMIZER_COST_OBJECTIVE_SELECTS_91_BY_DESIGN`

条件:

```text
base=100
constraint ceiling >=100 or >91
cost objectiveで91が最小コスト
inputsは正しい
```

修正:

- 原則source変更なし。
- dashboardの100がhard requirementに見えるなら表示/説明のみ修正。
- 利用者の100が「絶対最低朝SOC」の設定であることを既存仕様が明示している場合だけDへ再分類。

## D. `USER_HARD_TARGET_NOT_ENFORCED`

条件:

既存docs/config/UI契約が、100を**optimizer前の希望値ではなくhard minimum/final target**として定義している証拠がある。

かつoptimizerが91へ下げている。

修正方針:

- user hard targetをoptimizerのfloorとして扱う最小修正。
- `final_target >= configured_hard_target`を不変条件にする。
- 既存headroom safety capとhard targetが衝突する場合は自動で100を優先せず`BLOCKED_UNSAFE_TO_CHANGE`。

## E. `PLAN_DATE_OR_ARTIFACT_MISMATCH`

条件:

03が意図した日とは異なるplan、古いplan、別forecast dateを使用。

修正方針:

- plan selection/date validationのみ修正。
- optimizerロジック変更禁止。

## F. `DASHBOARD_STALE_OR_DIFFERENT_DATE_100`

条件:

100は別日/古いdaily metricで、03の91とは比較対象でない。

修正方針:

- dashboard date bindingのみ修正。
- optimizer/03変更禁止。

## G. `PROVENANCE_BLOCKED`

historical planや同一inputがなく、91生成理由を証明できない。

修正禁止。

結果=`BLOCKED_PROVENANCE`。

---

# 16. source変更前のSTOP gate

Section 15の分類がA〜Fへ確定するまでsource変更禁止。

分類確定時に、変更予定を次の形式で先に記録。

```text
PRIMARY_CAUSE = ...
PROVEN_EVIDENCE = ...
FILES_TO_CHANGE = ...
FILES_FORBIDDEN = ...
EXPECTED_BEHAVIOR_CHANGE = ...
ROLLBACK = ...
```

証拠が足りない場合はGで終了。

---

# Part D: 分類別の実装範囲

## 17. A/C/F: semantic/dashboard-only fix

許可候補:

```text
app/dashboard/**
tests/test_dashboard_data.py
必要なdashboard docs/report
```

原則禁止:

```text
app/energy_plan/optimization.py
app/energy_plan/soc_constraints.py
app/runtime/cloud_job.py
app/kpnet/**
```

必須test:

```text
base target 100 / final target 91を同じ値として表示しない
final targetの対象日を正しく表示
別日100を当日final targetとして使わない
```

---

## 18. B: proven bad constraint input

修正は原因fieldを生成するownerだけ。

例:

```text
wrong forecast date -> forecast input/date ownerだけ
stale history selection -> history selection ownerだけ
invalid cap calculation ->該当guardだけ
```

禁止:

```text
全headroom guard disable
max_target=100固定
optimizer bypass
```

必須test:

- defect replay
- adjacent valid case
- rainy/low-radiation relaxation
- historical insufficient-data case
- unaffected guard negative regression

---

## 19. D: hard target enforcement

この分類は最も危険なので、既存契約に「hard minimum」の明示証拠がある場合のみ実装可。

実装候補はoptimizer request境界に限定。

期待不変条件:

```text
configured hard target = 100
safe max target >= 100
=> final target = 100
```

安全capが91しか許容しない場合:

```text
hard target 100 > safe cap 91
```

このtask内で勝手に100を優先しない。

`BLOCKED_UNSAFE_TO_CHANGE`として報告。

---

## 20. E: plan mismatch

最低限必要:

```text
03 target date
plan forecast.date
current JST date
plan generated_at
```

修正後はwrong-date planを03が使用できないtestを追加。

fail-safeはstandby。

ただし03時刻ownershipは変更しない。

---

# 21. 観測性改善

原因にかかわらず、既存plan documentにすでにある情報をCloud Loggingへ出すだけで十分か評価する。

次回、最低限次を相関できること:

```text
forecast date
base target
final target
optimizer mode
active constraints
selected constraint caps
plan hash/id
03 contract target
```

ログ追加が必要なら、secret-free・1実行数行に限定。

巨大なcandidate tableをCloud Loggingへ毎回出さない。

---

# 22. テスト

source変更ありの場合、変更分類にかかわらず次を実行。

最初にrepositoryのcode-quality-audit Skillを使用。

focused testは変更ownerに合わせる。

最低限:

```powershell
python -m pytest -q tests/test_cloud_job_runner.py
python -m pytest -q tests/test_dashboard_data.py
```

Energy Plan変更時はさらに該当Energy Plan testsを`rg`で特定し、必ず実行。

その後:

```powershell
python -m ruff check .
python -m mypy app scripts --show-error-codes
python scripts/security_check.py
python -m pytest
```

既知のtool未導入はreportするが、今回変更に直接関係する必須test/lint failureを無視しない。

---

# 23. commit分割

調査reportとsource fixを混ぜない。

推奨:

1. source fix + tests
2. investigation/final report
3. CodebaseMemory artifact（必要時・最後の1回）

source fix commit messageは原因に合わせる。

例:

```text
fix: align displayed SOC target with optimized plan
fix: reject wrong-date night SOC plan
fix: preserve configured hard SOC target in optimizer
```

原因と一致しないcommit message禁止。

---

# 24. GitHub CI

source-bearing commitをmasterへpush後、quality workflowをterminalまで確認。

必須:

```text
status = completed
conclusion = success
```

CI failureならproduction deployment禁止。

---

# 25. production deployment

source behavior変更がある場合のみ。

直前に:

```text
docs/current/ops/PRODUCTION_DEPLOYMENT_RUNBOOK_JA.md
```

を全文再読。

repositoryの`solar-production-deployment` Skillを使用。

preflight:

```powershell
pwsh -NoProfile -File scripts/production_deployment_gate.ps1 -RunPreRelease
```

成功後のみ:

```powershell
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$statePath = "artifacts/deployment_state/production-$stamp.json"

pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 `
  -SkipPreRelease `
  -DeploymentScope auto `
  -StatePath $statePath
```

battery runtime変更がないdashboard-only fixで、auto scopeがbattery jobsを変更対象とした場合はSTOP。

Energy Plan/runner変更なら公式wrapperの判断に従う。

低レベル`gcloud run jobs deploy`を直接実行しない。

---

# 26. live mutation制限

このtaskで通常03 Jobを時間外手動実行しない。

KP-NET設定を調査目的で直接変更しない。

settings round-tripがofficial deployment wrapperに含まれる場合だけ既存runbookどおり実施。

---

# 27. CodebaseMemory final refresh

source/tests/current docsのtracked変更が完了し、CBMが利用可能な場合のみ、canonical projectをcurrent HEADへ同期してshared artifactを**1回だけ**生成。

artifact requirements:

```text
schema_version = 2
project = C-VSC-SolerControler
commit = artifact生成直前のtracked HEAD
nodes > 0
edges > 0
```

stage対象:

```text
.codebase-memory/artifact.json
.codebase-memory/graph.db.zst
```

artifact commit後にHEADが進んでも二度目refresh禁止。

CBM transportが復旧しない場合:

```text
PARTIAL_CBM_BLOCKED
```

とし、artifactを偽造しない。

---

# 28. 最終report

新規:

```text
docs/completed/reports/soc_target_100_to_91_provenance_2026-09-01.md
```

必須構成:

## Conclusion

```text
PRIMARY_CAUSE = <Section 15 classification>
USER/DASHBOARD_VALUE = 100% / source=<...> / date=<...>
BASE_PLAN_TARGET = <value or NOT_PROVEN>
FINAL_PLAN_TARGET = <value or NOT_PROVEN>
03_RUNTIME_TARGET = 91%
03_STOP_SOC = 96%
OPTIMIZER_MODE = <...>
ACTIVE_CONSTRAINTS = <...>
SOURCE_CHANGE = YES/NO
DEPLOYED = YES/NO/NOT_REQUIRED
```

## Provenance table

| stage | field | value | source | evidence grade |
|---|---|---:|---|---|
| user/dashboard | ... | 100 | ... | ... |
| base planner | target_soc_7_percent_base | ... | ... | ... |
| optimizer | final target | ... | ... | ... |
| plan | result.target_soc_7_percent | ... | ... | ... |
| runtime | contract target | 91 | Cloud log | PROVEN |
| runtime | stop SOC | 96 | Cloud log | PROVEN |

## Optimizer rationale

```text
cost objective
constraint caps
forecast PV/load
SOC now
capacity/reserve
candidate comparison必要範囲
```

## Behavior decision

なぜsource変更した/しなかったかを明記。

## Tests / CI / deployment

実測結果のみ。

## Secrets

```text
secrets exposed = NO
```

---

# 29. 最終報告フォーマット

Codexのチャット最終報告は次の順番固定。

```text
1. Result
2. PRIMARY_CAUSE
3. 100%のsource/date/meaning
4. base target
5. final optimizer target
6. 03 runtime target=91
7. stop SOC=96 / target_reached
8. optimizer mode
9. active constraints/caps
10. source変更有無と変更files
11. tests
12. CI
13. deployment
14. CodebaseMemory status/artifact
15. final master HEAD
16. remaining risk
17. secrets exposed = NO
```

---

# 30. 絶対禁止事項

以下を行った場合はCOMPLETE禁止。

```text
91%を見ただけで100%固定
optimizer全無効化
PV headroom guards全無効化
historical guard全無効化
03 exact-target条件の再変更
SocChargeMode=50をplanning target扱い
別日の100%と9/1の91%を無条件比較
現在planをhistorical planとみなす
reconstructed planをhistorical factと断定
Firestoreへ調査用書込み
通常03 Jobの時間外手動実行
CodebaseMemory transport failure時のreindex連打
MCP設定変更
duplicate CodebaseMemory project作成
secret/project/account/execution identifierのtracked report保存
```

---

# 31. この指示の核心

今回確定していることは、03 controllerが91%目標を96%まで充電して`target_reached`で正常停止したことだけである。

未確定なのは、利用者が見ていた100%と03が受け取った91%の間で何が起きたかである。

現行sourceには、optimizerがbase targetをfinal optimized targetへ置換する明示的境界と、PV/headroom/historyによるtarget capが存在する。

したがって次の順序を崩さない。

```text
100%のsource確定
→ historical base target確定
→ final target確定
→ optimizer mode確定
→ constraint/cost根拠確定
→ date semantics確認
→ 原因分類
→ 必要な場合だけ最小修正
→ test/CI
→ 必要な場合だけproduction deploy
```

Luna軽に推測でownerを選ばせない。証拠がない場合は`BLOCKED_PROVENANCE`で停止する。

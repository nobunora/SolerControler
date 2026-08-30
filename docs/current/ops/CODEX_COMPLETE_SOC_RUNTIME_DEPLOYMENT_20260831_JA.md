# Luna軽 実行指示書: SOC runtime修正の検証・CodebaseMemory更新・本番デプロイ完了

## 0. この文書の役割

この作業では設計を考え直さないこと。

すでにproduction source修正は `41eef039e108ba9dfeea3b237e9eaf6d8c5bf142` へ入っている。

この文書の目的は、残っている作業だけを順番どおり完了することである。

残作業は次の6項目だけ。

```text
1. 現在の修正内容を再確認する
2. fail-closed回帰テストを1件だけ追加する
3. quality/security/production preflightを完了する
4. CodebaseMemoryを復旧・current stateへ同期しshared artifactを1回だけ更新する
5. commit/pushを固定して本番デプロイする
6. settings round-trip / 07 DryRun / deployment stateを確認して終了する
```

この6項目以外へ作業を広げない。

---

# 1. 現在の既知状態

この指示書作成時点のmaster基準は以下。

```text
master HEAD:
41eef039e108ba9dfeea3b237e9eaf6d8c5bf142

commit message:
fix: preserve current settings in 03 forced mode

quality workflow:
run #179 = SUCCESS
```

現在のshared CodebaseMemory artifactは古い。

```text
artifact source commit:
e4609d741ff384c9acdb5a4053862cf723345fcd

project:
C-VSC-SolerControler

nodes:
5660

edges:
17953
```

したがってshared artifactはcurrent source/tests/docsを表していない。

この文書merge後は `docs/current/**` も変わるため、最終tracked stateに対して1回だけrefreshする必要がある。

---

# 2. 絶対に変更してはいけないもの

この作業では次を変更禁止とする。

```text
app/kpnet/workflow.py
app/kpnet/profile_builder.py
app/kpnet/profiles.py
app/kpnet/settings_roundtrip.py
app/runtime/cloud_job.py
app/runtime/slot_orchestration.py
app/runtime/night_soc_time_contract.py
app/runtime/night_soc_controller.py
app/runtime/soc_reading.py
scripts/deploy_gcp_jobs.ps1
scripts/deploy_production_from_env.ps1
scripts/production_deployment_gate.ps1
scripts/run_cloud_job_from_env.ps1
```

production sourceを変更してはいけない。

既存sourceに追加修正が必要だと思った場合はSTOPする。

「今の実装を少し改善する」「同じ処理を共通化する」「命名を整理する」は禁止。

---

# 3. この作業で変更を許可するtracked file

原則として変更を許可するのは次だけ。

```text
tests/test_kpnet_mode_only_time_fence.py
.codebase-memory/artifact.json
.codebase-memory/graph.db.zst
```

この実行指示書自身は編集しない。

上記以外のtracked file変更が必要になった場合はSTOPする。

例外はない。

---

# 4. 最初のローカル確認

PowerShell 7を使用する。

repo rootで次を実行する。

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
git log -8 --oneline
git fetch origin
git rev-parse origin/master
```

## 4.1 branch条件

作業branchは `master` とする。

別branchにいる場合:

```text
tracked変更なし
```

を確認した後だけmasterへ切り替える。

tracked変更がある場合はcheckout/stash/reset禁止。

STOPする。

## 4.2 HEAD条件

この文書をmergeした後なので、実行時HEADが `41eef039...` より後なのは正常。

ただし次を確認する。

```text
git merge-base --is-ancestor 41eef039e108ba9dfeea3b237e9eaf6d8c5bf142 HEAD
```

exit code 0でなければSTOP。

`41eef039...` のsource修正を含まない状態で作業してはならない。

## 4.3 remote同期

次を満たすまで変更開始禁止。

```text
HEAD == origin/master
```

origin/masterが先なら:

```powershell
git pull --ff-only origin master
```

を1回だけ実行する。

fast-forwardできない場合はSTOP。

rebase/merge/forceは禁止。

---

# 5. source修正内容をread-onlyで再確認

このphaseではsource edit禁止。

次だけを確認する。

```text
app/kpnet/workflow.py::_mode_only_profile_from_current_settings
app/kpnet/workflow.py::run_kpnet_mode_only_profile
app/kpnet/workflow.py::_apply_settings_profile
```

確認条件は固定。

## 5.1 forced branch

次の構造であること。

```text
current snapshotをProfileOverridesへ変換
BatteryOperatingMode forced candidateを取得
SocChargeMode最大candidateを取得
replace()でbatteryOperatingModeとsocChargeModeだけ変更
```

static `FORCED_CHARGE_PROFILE` を03 forced branchのbaseに戻してはならない。

## 5.2 preserved fields

03 forcedで以下がcurrentから維持される設計であること。

```text
socSafetyMode
socEconomyMode
socContactInput
chargeStartTimeH
chargeStartTimeM
chargeEndTimeH
chargeEndTimeM
dischargeStartTimeH
dischargeStartTimeM
dischargeEndTimeH
dischargeEndTimeM
agreementAmpere
onPowerOutageMode
onPowerOutageChargePowerW
```

## 5.3 read-back

read-back mismatchがwarning化されていないこと。

次を維持。

```text
NIGHT_SOC_READBACK_REQUIRED default=true
mismatch -> RuntimeError
requested/observed値をcontrolled fieldだけ記録
```

## 5.4 historical lock

次を変更していないこと。

```text
23 standby one unconditional read-back write
07 green one unconditional read-back write
03 reapply disabled
06:45 monitor cutoff
06:50 final standby start cutoff
06:55 control hard cutoff
```

1つでも違う場合はsourceを直さずSTOP。

---

# 6. CodebaseMemory接続確認と復旧手順

このphaseを飛ばしてはならない。

以前この作業でCodebaseMemory接続失敗が発生しているため、単に「readyでなければ停止」ではなく次の順序で復旧を試す。

## 6.1 最初のstatus確認

CodebaseMemory MCPへstatus/index statusを1回問い合わせる。

必要な既存project identityは固定。

```text
C-VSC-SolerControler
```

別名projectを新規作成してはいけない。

## 6.2 接続成功かつreadyの場合

そのままSection 6.6へ進む。

## 6.3 MCP transport / connection errorの場合

例:

```text
tool unavailable
connection closed
transport error
MCP server unavailable
```

この場合source editやdeployへ進まない。

次の復旧だけを行う。

```text
1. 現在設定済みのCodebaseMemory MCP connectionを再接続する
2. MCP設定ファイルを書き換えない
3. package install/updateをしない
4. repository configを変更しない
5. 再接続後statusを1回だけ再取得する
```

再接続方法はCodex/実行環境に既に登録済みのCodebaseMemory connectionを再度有効化する方法を使う。

新しいMCP server定義を作らない。

## 6.4 接続はできるがproject/indexが未ロードの場合

既存project `C-VSC-SolerControler` を現在repo rootに対して開く。

既存projectを開けない場合のみ、同じproject identityでcurrent repoをindexする。

次は禁止。

```text
C-VSC-SolerControler-2
SolerControler-new
別pathを指すduplicate project
```

## 6.5 statusがindexing/busyの場合

別のindex処理を開始しない。

同じ処理のstatusだけを確認し、terminal statusまで追う。

二重index禁止。

terminalがerrorになった場合は1回だけ同じrepo/projectで再indexする。

2回目もerrorならSTOP。

## 6.6 ready後の必須query

次のsymbolだけをqueryする。

```text
app.kpnet.workflow.run_kpnet_mode_only_profile
app.kpnet.workflow._mode_only_profile_from_current_settings
app.kpnet.workflow._apply_settings_profile
app.runtime.slot_orchestration._run_adjust_03
app.runtime.night_soc_time_contract.FORCED_MONITOR_CUTOFF
app.runtime.night_soc_time_contract.CONTROL_HARD_CUTOFF
```

目的はcall pathとprotected boundary確認だけ。

CodebaseMemory結果を理由にsourceを変更しない。

source/rgと矛盾した場合はsourceを正とする。

## 6.7 CodebaseMemory STOP条件

以下ならSTOP。

```text
再接続後もMCP transport error
同一projectで2回indexしてもreadyにならない
project identityをC-VSC-SolerControlerへ揃えられない
current source symbolをqueryできない
```

STOP時にユーザーへ「復旧してください」だけを書かない。

必ず次を報告する。

```text
1. 最初のstatus/error
2. 実行した再接続
3. 実行したindex回数
4. 最終status/error
5. repository fileを変更していないこと
```

---

# 7. 追加する回帰テストは1件だけ

`tests/test_kpnet_mode_only_time_fence.py`へ1件だけ追加する。

production sourceは変更しない。

## 7.1 テスト名

固定。

```python
def test_03_forced_mode_only_rejects_missing_required_current_field_without_write(...):
```

## 7.2 テスト条件

既存のreal mode-only fixture/fake sessionを再利用する。

`_distinct_current_settings()`相当のcurrentを用意し、次だけ削除する。

```text
chargeEndTimeH
```

## 7.3 実行

```python
workflow.run_kpnet_mode_only_profile(
    profile="forced",
    deadline_monotonic=300.0,
)
```

を呼ぶ。

`run_kpnet_mode_only_profile()`は内部例外をcatchしてreturn code 1にするため、期待は固定。

```python
assert return_code == 1
```

さらに次を必須確認。

```text
/batterysetting POSTが0回
/write/requestが0回
/pcssettingcomplete/が0回
```

login/read/candidate取得は許容する。

目的は「必須current field欠落時にdefault/static profileへfallbackしてdevice writeしない」を固定すること。

## 7.4 禁止

このテストを通すためにproduction sourceを変更してはならない。

既存実装で通らない場合はSTOP。

source修正案を作らない。

---

# 8. code-quality-audit

pytestより前にrepositoryの`code-quality-audit` Skillを実行する。

Skillが一時的に見つからない場合:

```text
1. Skill/tool discoveryを1回だけ再実行
2. 同じSkillを再取得
```

それでも利用不能ならSTOP。

別の自己流quality手順で代替してCOMPLETE扱いしない。

---

# 9. 追加テスト後の検証

順番固定。

## 9.1 Ruff

```powershell
python -m ruff check `
  app/kpnet/workflow.py `
  tests/test_kpnet_mode_only_time_fence.py `
  tests/test_kpnet_workflow.py
```

formatterは禁止。

## 9.2 focused tests

```powershell
python -m pytest -q `
  tests/test_kpnet_mode_only_time_fence.py `
  tests/test_kpnet_workflow.py `
  tests/test_kpnet_settings_roundtrip.py `
  tests/test_cloud_job_runner.py `
  tests/test_night_soc_controller.py `
  tests/test_night_soc_protected_contract.py `
  tests/test_night_soc_time_ownership.py
```

1件でもfailならdeploy禁止。

テストをskip/delete/xfailed化して通さない。

## 9.3 security

```powershell
python scripts/security_check.py
```

## 9.4 production ValidateOnly

```powershell
pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 -ValidateOnly
```

必ず次の意味を確認。

```text
validation success
No deployment was performed
```

ここまでではproduction mutation禁止。

---

# 10. test commit

Section 9が全部成功した場合のみcommitする。

stageするのはテスト1ファイルだけ。

```powershell
git add -- tests/test_kpnet_mode_only_time_fence.py
git diff --cached --check
git diff --cached --name-only
```

cached nameが1ファイルでない場合はcommit禁止。

commit message固定。

```text
test: guard incomplete 03 forced current snapshot
```

commit後:

```powershell
git status --short
git rev-parse HEAD
```

tracked user changeが残っていたらSTOP。

---

# 11. final CodebaseMemory index / shared artifact更新

この時点で以下がcurrent tracked stateに入っている。

```text
41eef039 source fix
この実行指示書
missing-field regression test
```

このtracked stateに対してCodebaseMemoryを同期する。

## 11.1 local index

現在HEADを同一project `C-VSC-SolerControler` へindexする。

既にreadyでcurrent HEADを反映しているなら不要な二重indexをしない。

status=readyを確認する。

## 11.2 final query

次を再確認。

```text
run_kpnet_mode_only_profile caller/callee
_mode_only_profile_from_current_settings callers
_run_adjust_03 -> mode-only forced path
23/07 ownership symbols
```

新しいcross-slot dependencyがないこと。

## 11.3 shared artifact生成

repositoryで既に使用しているCodebaseMemory shared-artifact生成方法を使用する。

手作業でgraph binaryを圧縮/編集しない。

生成後:

```powershell
Get-Content .codebase-memory/artifact.json
```

を確認する。

必須条件:

```text
schema_version = 2
project = C-VSC-SolerControler
commit = artifact生成直前のtracked HEAD
nodes > 0
edges > 0
```

`commit`が旧 `e4609d...` のままならartifact commit禁止。

## 11.4 artifact commit

stage対象固定。

```text
.codebase-memory/artifact.json
.codebase-memory/graph.db.zst
```

それ以外をstageしない。

commit message固定。

```text
chore: refresh CodebaseMemory SOC runtime graph
```

artifact commit後にHEADが進んでも再indexしない。

artifact commit自身をgraphへ含める2回目refreshは禁止。

---

# 12. push前の最終ローカル確認

次を実行する。

```powershell
python -m pytest
python scripts/security_check.py
git diff --check
git check-ignore .env
git status --short
git log -5 --oneline
```

条件:

```text
pytest PASS
security_check PASS
.env is ignored
tracked worktree clean
```

1つでも未達ならpush/deploy禁止。

---

# 13. origin/masterへpush

このrepositoryではsource fix `41eef039...` が既にmasterへ直接commit済みである。

過去を作り直すためのretroactive implementation PRを作ってはいけない。

今回の追加test/artifact commitはfast-forwardでmasterへpushする。

push前:

```powershell
git fetch origin
git rev-parse HEAD
git rev-parse origin/master
```

origin/masterがローカル作業開始後に進んでいたらpush禁止。

`git pull --ff-only`で取り込める場合だけ取り込み、Section 9以降を必要範囲で再確認する。

force push禁止。

push:

```powershell
git push origin master
```

push後:

```powershell
git rev-parse HEAD
git rev-parse origin/master
git status --short
```

必須:

```text
HEAD == origin/master
worktree clean
```

---

# 14. GitHub quality workflow確認

pushした最終HEADに対するquality workflowを確認する。

statusがqueued/in_progressの場合、同じrunのterminal statusを確認する。

別runを重複起動しない。

必須:

```text
status = completed
conclusion = success
```

failure/cancelled/timed_outならproduction deploy禁止。

CI failureを無視してローカル結果だけでdeployしない。

---

# 15. production runbook再読

本番操作の直前に必ず全文を読む。

```text
docs/current/ops/PRODUCTION_DEPLOYMENT_RUNBOOK_JA.md
```

過去に読んだ記憶で実行してはいけない。

さらにproduction deployment/recoveryではrepository指定の`solar-production-deployment` Skillを使用する。

Skillを呼べない場合はSTOP。

---

# 16. production preflight gate

最新HEADがorigin/masterへpush済みでCI SUCCESSを確認した後だけ実行する。

```powershell
pwsh -NoProfile -File scripts/production_deployment_gate.ps1 -RunPreRelease
```

次を全部成功させる。

```text
clean worktree
local source/DB backup success
.env not included
security check success
related/full tests success
ValidateOnly success
pre-release success
```

`-SkipLocalBackup`禁止。

preflight stateのstatusがsuccessでない場合はdeploy wrapperを起動しない。

runningをsuccess扱いしない。

---

# 17. production StatePathを1つだけ作る

preflight成功後にdeployment StatePathを1つ作る。

```powershell
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$statePath = "artifacts/deployment_state/production-$stamp.json"
```

この`$statePath`をそのdeploymentの最後まで使う。

途中で別state fileを作らない。

---

# 18. 通常production deploy

公式高レベルwrapperだけを使用する。

```powershell
pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 `
  -SkipPreRelease `
  -SettingsRoundTripTargetSoc 50 `
  -DeploymentScope auto `
  -StatePath $statePath
```

低レベル`gcloud run jobs deploy`を手組みしない。

credential-bearing commandを作らない。

## 18.1 DeploymentScope auto = runner/full/dashboardの場合

wrapperに任せて必要なbuild/deployだけ実行する。

同一commitでrunner buildを外から重複起動しない。

## 18.2 DeploymentScope auto = noneの場合

強制的にfull/runnerへ変更しない。

まずstate/historyから「最新成功production deployment commitが現在HEADと同一」であることを確認する。

同一なら再build/redeployしない。

Section 20のexplicit live verificationへ進む。

同一でないのにscope=noneならSTOP。

scope判定ロジックをその場で修正しない。

---

# 19. deployment途中で対話/PowerShellが切れた場合

新しいdeploymentを開始しない。

必ず同じ `$statePath` を使う。

状態ファイルを読む。

```powershell
Get-Content -LiteralPath $statePath -Raw -Encoding UTF8
```

成功済みstageだけをsuccess扱いする。

```text
running
failed
skipped_manual
```

はsuccessではない。

Cloud Build/Cloud Runが裏で続いていた可能性がある場合はread-onlyでterminal状態を確認する。

状態不明のまま再buildしない。

再開条件を満たしたら:

```powershell
pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 `
  -Resume `
  -StatePath $statePath
```

を使用する。

state JSONを手編集してsuccessへ変えない。

---

# 20. explicit live settings round-trip

production deploy wrapperがsettings round-tripをsuccessとして完了した場合も、今回のSOC device contract確認として最終的に1回だけexplicit verificationを行う。

ただし同じexecutionがwrapper内で直前に実行され、その成功・60秒保持・完全復元・read-backまでstate/logで明示確認できる場合は重複実行しない。

重複を避ける判断規則は固定。

```text
state/logにsettings-roundtrip success + restore read-back successが明示される
-> 再実行しない

それが明示確認できない
-> 次のofficial wrapperを1回だけ実行
```

実行する場合:

```powershell
pwsh -NoProfile -File scripts/run_cloud_job_from_env.ps1 `
  -Slot settings-roundtrip `
  -SettingsRoundTripTargetSoc 50 `
  -TestExecution
```

必須条件:

```text
forced mode apply success
SocChargeMode 50 apply success
read-back success
60 second hold
initial controlled settings restoration success
restoration read-back success
Cloud Run Job terminal success
```

復元失敗ならCOMPLETE禁止。

同じテストを自動retryしない。

---

# 21. 07 DryRun

最終production smokeとして公式wrapperで実行する。

```powershell
pwsh -NoProfile -File scripts/run_cloud_job_from_env.ps1 -Slot 07 -DryRun
```

このscript自身が最新executionの次の4条件を確認する。

```text
Completed=True
ResourcesAvailable=True
Started=True
ContainerReady=True
failedCount=0 またはfield omitted
```

command受付だけで成功扱いしない。

1つでも満たさない場合はCOMPLETE禁止。

---

# 22. 23/03/07 deployment契約確認

read-onlyで次を確認する。

```text
23 job存在
03 job存在
07 job存在
3 jobが意図した最新runner image世代へ更新済み
Scheduler timezone = Asia/Tokyo
23 schedule = 0 23 * * *
03 schedule = 0 3 * * *
07 schedule = 0 7 * * *
```

project ID、resource ID、secret値、account IDを最終報告へ出さない。

確認にはrepository wrapper/stateを優先する。

低レベルgcloudを使う場合もread-only describe/listに限定する。

mutation command禁止。

---

# 23. 今回やってはいけないproduction操作

禁止:

```text
実時間外の03 production Jobを通常モードで手動execute
23 Jobを通常モードで手動execute
07 JobをDryRunなしで手動execute
Scheduler時刻変更
Cloud Run retry設定変更
環境変数の追加変更
KP-NET設定を独自script/gcloud/curlで変更
read-back mismatchを無視して続行
settings-roundtrip復元失敗後の再deployment
```

特に03 forced pathを確認したいという理由で03 Jobを現在時刻に通常実行しない。

03 pathの正しさはsource regression + CodebaseMemory/source path + live reversible round-tripで確認する。

---

# 24. deployment後のrepository確認

本番操作でtracked fileが変わっていないことを確認する。

```powershell
git status --short
git rev-parse HEAD
git rev-parse origin/master
```

必須:

```text
worktree clean
HEAD == origin/master
```

もしdeployment中にtracked script/sourceを修正したくなった場合、その場で修正禁止。

STOPして別fixとして扱う。

本番で動いた内容とGitにない内容を作らない。

---

# 25. shared CodebaseMemory freshnessの最終扱い

artifact commit後にproduction deploymentだけを行い、tracked source/tests/current docsが変わっていなければ再refresh不要。

deployment stateは`artifacts/**`でありshared graph sourceではない。

次を理由に2回目refreshしてはいけない。

```text
artifact commitでHEADが進んだ
production executionが増えた
deployment state JSONが増えた
Cloud Run execution logが増えた
```

---

# 26. 成功条件

以下を全部満たした場合のみ `Result: COMPLETE` とする。

```text
[ ] 41eef039 source fixが現在HEADのancestor
[ ] source修正内容をread-onlyで再確認
[ ] production source追加変更なし
[ ] CodebaseMemory接続復旧/ready確認
[ ] CodebaseMemory required symbols query成功
[ ] missing-current-field regression test 1件追加
[ ] missing field時device write 0回をtestで固定
[ ] code-quality-audit実行
[ ] Ruff PASS
[ ] focused pytest PASS
[ ] full pytest PASS
[ ] security_check PASS
[ ] ValidateOnly PASS
[ ] test commit作成
[ ] shared CodebaseMemory artifact current tracked stateへrefresh
[ ] artifact project=C-VSC-SolerControler
[ ] artifact source commitが旧e4609dではない
[ ] artifact commit 1回のみ
[ ] origin/masterへfast-forward push
[ ] final GitHub quality SUCCESS
[ ] production runbook再読
[ ] solar-production-deployment Skill使用
[ ] production preflight gate SUCCESS
[ ] deployment StatePathを1つに固定
[ ] deploy wrapper terminal success またはscope=noneの正当性確認
[ ] settings round-trip success
[ ] forced apply/read-back確認
[ ] 60秒hold確認
[ ] complete restoration/read-back確認
[ ] 07 DryRun success
[ ] Completed/ResourcesAvailable/Started/ContainerReady=True
[ ] 23/03/07 job/scheduler契約確認
[ ] secret/project/resource identifierを報告へ出していない
[ ] post-deploy worktree clean
[ ] HEAD == origin/master
```

1つでも未達ならCOMPLETE禁止。

---

# 27. STOP条件と行動

## 27.1 source/test

次ならSTOP。

```text
既存sourceを追加修正しないとテストが通らない
23/07 behavior変更が必要
time fence変更が必要
read-back緩和が必要
新dependencyが必要
```

行動:

```text
変更せず停止
失敗testと最小source箇所を報告
修正案は実装しない
```

## 27.2 CodebaseMemory

次ならSTOP。

```text
再接続後もMCP unavailable
2回indexしてもreadyにならない
canonical project identityを使えない
artifactがcurrent tracked commitを指さない
```

行動:

```text
source/deployへ進まない
接続/再index履歴を報告
```

## 27.3 git/CI

次ならSTOP。

```text
origin/master diverged
fast-forwardできない
force pushが必要
GitHub quality failure
tracked worktree dirty
```

## 27.4 production preflight

次ならSTOP。

```text
production gate failure
backup failure
ValidateOnly failure
security failure
pre-release failure
```

## 27.5 deployment

次なら同一StatePathでResume条件を確認する。

```text
外側のsession/PowerShellだけ終了
Cloud Build/Run terminal状態未確認
```

次ならSTOP。

```text
Cloud Build FAILED
Cloud Run deployment FAILED
settings round-trip FAILED
restore read-back FAILED
07 DryRun FAILED
state file corrupt/unreadable
current deployment commitとstate commit不一致
```

失敗を推測でsuccessにしない。

---

# 28. Luna軽が自分で判断してはいけない事項

次を自分で決めない。

```text
sourceの追加リファクタ
別helperへの共通化
retry回数変更
sleep追加
fallback追加
read-back normalization
03 cutoff変更
07開始条件変更
Scheduler変更
deployment scope強制変更
settings round-trip省略
CodebaseMemory project別名作成
artifact self-follow refresh
CI failure無視
```

この文書に分岐がない事項は「やらない」が既定値。

---

# 29. 最終報告フォーマット

最終報告はこの順番だけで出す。

```text
1. Result: COMPLETE / BLOCKED
2. Final repository HEAD
3. Source change: 41eef039 preserved / additional source changes NONE
4. Added regression test result
5. CodebaseMemory
   - connection recovery needed: YES/NO
   - final status
   - project
   - artifact source commit
   - nodes
   - edges
   - artifact commit
6. Quality
   - code-quality-audit
   - Ruff
   - focused pytest
   - full pytest
   - security_check
   - ValidateOnly
   - GitHub quality run conclusion
7. Git
   - push result
   - HEAD == origin/master
   - worktree clean
8. Production preflight
   - gate result
   - backup result
9. Deployment
   - scope
   - state status
   - resume used: YES/NO
10. Live settings round-trip
   - forced/read-back
   - 60s hold
   - restoration/read-back
11. 07 DryRun
   - Completed
   - ResourcesAvailable
   - Started
   - ContainerReady
   - failedCount
12. Scheduler/job contract
   - 23/03/07 verified
13. Production identifiers/secrets exposed: NO
14. Follow-up
   - planner SOC source/observed_at/age provenance remains separate work only
```

project ID、region ID、job execution ID、image digest、secret名/value等の具体値は最終報告へ書かない。

CodebaseMemoryのproject名 `C-VSC-SolerControler` はrepository内の非機密identityなので記載してよい。

---

# 30. 最重要まとめ

この作業でLuna軽が行う判断はほぼない。

```text
sourceは41eef039のまま
追加はfail-closed test 1件だけ
CodebaseMemoryはcanonical projectを復旧してcurrent stateへ1回refresh
全チェック成功後にfast-forward push
公式runbook/wrapperだけでdeploy
settings round-tripで強制設定→60秒→完全復元
07 DryRunの4条件を確認
23/03/07契約をread-only確認
clean + HEAD==origin/masterで終了
```

これ以外は行わない。

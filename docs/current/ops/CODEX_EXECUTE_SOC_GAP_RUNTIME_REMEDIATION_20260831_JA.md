# Luna軽 実装指示書: 2026-08-30 SOC不足の原因確定と03 forced経路修正

## 0. 実行者への最重要指示

この作業では、自分で設計判断を広げないこと。

以下の順番・対象・停止条件に従うこと。

「より良いと思う」「ついでに直す」「同じ処理なので共通化する」は禁止。

この文書に明記されていないproduction behaviorを変更してはならない。

判断が必要に見える場合でも、まずこの文書の `STOP条件` を確認し、該当すれば変更せず停止報告する。

---

# 1. 作業目的

2026-08-30朝に発生した:

```text
計画SOC 100%
07:30実績SOC 73%
差 -27pt
```

について、次の2点だけを行う。

1. incident execution/log/imageのprovenanceをread-onlyで再確認する。
2. 現行03 forced pathがstatic `FORCED_CHARGE_PROFILE`全体を送る問題を、current snapshot基準の最小payloadへ修正する。

plannerの最適化ロジック変更、時刻所有権変更、Firestore制御復活は本作業に含めない。

---

# 2. 作業開始前に必ず読むファイル

以下だけをこの順番で読む。

```text
AGENTS.md
docs/current/agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md
docs/current/ops/SOC_GAP_ROOT_CAUSE_EVIDENCE_20260831_JA.md
docs/completed/reports/soc_gap_investigation_2026-08-30.md
docs/completed/reports/soc_gap_evidence_2026-08-30.csv
app/runtime/cloud_job.py
app/runtime/slot_orchestration.py
app/runtime/night_soc_time_contract.py
app/runtime/soc_reading.py
app/kpnet/workflow.py
app/kpnet/profile_builder.py
app/kpnet/profiles.py
app/kpnet/settings_roundtrip.py
tests/test_kpnet_mode_only_time_fence.py
tests/test_kpnet_settings_roundtrip.py
```

他のファイルを開くのは、この文書で明示したテスト失敗の直接原因を確認するときだけ。

---

# 3. CodebaseMemory preflight

`AGENTS.md`に従い、source編集前にCodebaseMemoryを使う。

## 3.1 必須確認

indexが `ready` であることを確認する。

次のsymbolだけをqueryする。

```text
app.runtime.cloud_job._monitor_partial_forced_and_stop
app.runtime.cloud_job._RunnerMonitorDevicePort.apply_profile
app.kpnet.workflow.run_kpnet_mode_only_profile
app.kpnet.workflow._apply_settings_profile
app.kpnet.profile_builder._build_payload
app.kpnet.settings_roundtrip.profile_from_current_settings
app.kpnet.settings_roundtrip.make_reversible_probe_profile
app.runtime.night_soc_controller.compare_setting_readback
```

## 3.2 変更許可

CodebaseMemoryの結果は変更理由にしない。

この文書に書かれたsource事実を `rg` と対象ファイル読解で確認してから編集する。

## 3.3 STOP条件

indexがreadyにならない場合:

```text
SOURCE EDIT禁止
```

次だけを報告して停止。

```text
CodebaseMemory status
実行したrefresh/query
失敗内容
```

---

# 4. 最初に実行するローカル確認

PowerShell 7を使用。

```powershell
git status --short
git rev-parse HEAD
git log -8 --oneline
```

## 4.1 STOP条件

tracked sourceに未commit変更がある場合:

```text
SOURCE EDIT禁止
```

ユーザー変更をstash/reset/checkoutしてはならない。

未commit pathだけ報告して停止。

---

# 5. incident provenance再確認

このphaseはread-only。

production mutation、Job execute、deployは禁止。

## 5.1 環境読込

repo rootで:

```powershell
. .\scripts\production_env.ps1
Import-ProductionEnv
$projectId = Get-RequiredProductionEnv 'GCP_PROJECT_ID'
$region = Get-RequiredProductionEnv 'GCP_REGION'
$gcloud = Join-Path $PWD 'scripts\gcloud.ps1'
```

値をconsole/reportへ直接表示しない。

## 5.2 03 Job execution一覧

次を実行する。

```powershell
& $gcloud run jobs executions list `
  --job solar-battery-03 `
  --region $region `
  --project $projectId `
  --limit 20 `
  --sort-by '~createTime' `
  --format json
```

2026-08-30 03:00 JST前後に該当するexecutionを1件特定する。

JST 03:00はUTC前日18:00付近。

対象時間帯:

```text
2026-08-29 17:30Z ～ 2026-08-29 20:00Z
```

この範囲外のexecutionをincident executionとして採用してはならない。

## 5.3 execution describe

特定したexecution nameに対して:

```powershell
& $gcloud run jobs executions describe <EXECUTION_NAME> `
  --region $region `
  --project $projectId `
  --format json
```

次だけを記録する。

```text
execution name
create/start/completion timestamp
job generation/revision情報
container image reference
digestが取れるならdigest
status condition
```

secret/env valueは記録しない。

## 5.4 対象executionのログ抽出

execution名で絞る。

`gcloud logging read`はread-only診断として使用可。

抽出語は次だけ。

```text
03-monitor
03-forced-start
03-forced-reapply
read-back mismatch
batteryOperatingMode
03-target-reached-standby
03-monitor-cutoff-standby
```

時間範囲は対象execution開始前10分〜終了後10分だけ。

全projectログを広範囲scanしない。

## 5.5 provenance判定

次のどれか1つに固定する。

### PROVEN

以下が全部一致:

```text
8/30朝対象executionを一意に特定
ログ時刻がexecution内
ログのcontrol signatureがsource世代と一致
imageまたはdeployment情報でsource世代を追跡可能
```

### PARTIAL

executionは一意だが、git SHA/image digestまでsourceと結べない。

### BLOCKED

execution自体を一意に特定できない。

## 5.6 重要

レポート既存記載:

```text
2026-08-28T21:24Z ～ 21:29Z
```

はJSTでは2026-08-29 06:24～06:29。

今回8/30朝のexecutionとは別日。

この時刻が再取得でも同じなら:

```text
2026-08-30 incidentの直接ログではない
```

と明記する。

既存レポートを書き換えるのは、本作業の最後にsource修正結果と一緒に行わない。

provenance訂正が必要なら、別commitにする。

---

# 6. source修正対象

source変更は原則この2ファイルだけ。

```text
app/kpnet/workflow.py
tests/test_kpnet_mode_only_time_fence.py
```

read-back diagnostic追加のため、必要な場合だけ:

```text
tests/test_kpnet_workflow.py
```

を変更してよい。

他source fileは変更禁止。

特に以下は変更禁止。

```text
app/runtime/cloud_job.py
app/runtime/slot_orchestration.py
app/runtime/night_soc_time_contract.py
app/runtime/soc_reading.py
app/runtime/night_soc_controller.py
app/kpnet/profile_builder.py
app/kpnet/profiles.py
app/kpnet/settings_roundtrip.py
scripts/deploy_gcp_jobs.ps1
```

本指示では上記を参照するだけ。

---

# 7. 変更1: current snapshotから03 forced profileを作る

## 7.1 `app/kpnet/workflow.py` にhelperを追加

`run_kpnet_mode_only_profile()`の直前、または同一module内の近接位置に、次の責務だけを持つprivate helperを追加する。

関数名は固定:

```python
_mode_only_profile_from_current_settings
```

signatureは固定:

```python
def _mode_only_profile_from_current_settings(
    current: dict[str, Any],
    *,
    name: str,
) -> ProfileOverrides:
```

## 7.2 helperの必須実装

`current`から次をそのまま文字列化して`ProfileOverrides`へ入れる。

```text
batteryOperatingMode
socSafetyMode
socEconomyMode
socContactInput
socChargeMode
chargeStartTimeH
chargeStartTimeM
chargeEndTimeH
chargeEndTimeM
dischargeStartTimeH
dischargeStartTimeM
dischargeEndTimeH
dischargeEndTimeM
agreementAmpere
```

追加2field:

```text
onPowerOutageMode
onPowerOutageChargePowerW
```

はcurrentにあればcurrent値を使う。

欠けている場合のみ既存`ProfileOverrides` defaultと同じ:

```text
onPowerOutageMode = "0"
onPowerOutageChargePowerW = "65535"
```

を使う。

## 7.3 必須field欠落

上記14個の必須fieldのうち1つでも:

```text
key不存在
または空文字
```

なら:

```python
RuntimeError
```

にする。

message prefixは固定:

```text
KP-NET current settings missing mode-only fields:
```

欠落field名をcomma-separatedで続ける。

silent fallback禁止。

## 7.4 helperでしてはいけないこと

禁止:

```text
candidate map lookup
plan read
env read
clock read
network I/O
Firestore I/O
SOC計算
```

current dict → ProfileOverrides変換だけ。

---

# 8. 変更2: forced分岐をsnapshot基準にする

`app/kpnet/workflow.py::run_kpnet_mode_only_profile()`のforced分岐だけ変更する。

現在の:

```python
elif profile == "forced":
    selected = replace(
        FORCED_CHARGE_PROFILE,
        battery_operating_mode=_pick_battery_operating_mode_code(
            maps["BatteryOperatingMode"],
            prefer="forced",
        ),
    )
```

を廃止する。

## 8.1 exact behavior

以下の順番で実装する。

### Step A

current snapshot profileを作る。

```python
selected = _mode_only_profile_from_current_settings(
    current,
    name="03-forced-mode-only",
)
```

### Step B

forced operating mode candidateを既存helperで取得。

```python
forced_mode_code = _pick_battery_operating_mode_code(
    maps["BatteryOperatingMode"],
    prefer="forced",
)
```

### Step C

`SocChargeMode`はsupported numeric candidateの最大codeを使う。

既存:

```python
_pick_max_code
```

を `app.kpnet.profile_builder` からimportして使う。

新しいcandidate parserを作らない。

```python
forced_soc_code = _pick_max_code(maps["SocChargeMode"])
```

### Step D

`replace()`で2fieldだけ変更する。

```python
selected = replace(
    selected,
    battery_operating_mode=forced_mode_code,
    soc_charge_mode=forced_soc_code,
)
```

## 8.2 forcedで変更を許可する意味field

```text
batteryOperatingMode
socChargeMode
```

のみ。

`_build_payload`がprovider form都合で他fieldも送信するのは許容するが、その値はcurrent snapshotと一致しなければならない。

## 8.3 絶対禁止

forced branchで以下を設定してはならない。

```text
socSafetyMode=50
socContactInput=100
chargeStart=04:30
chargeEnd=06:30
chargeStart=03:00
chargeEnd=06:45
```

その他、currentと異なるSOC/window値を新規生成してはならない。

---

# 9. standby / green分岐は変更禁止

次のbranchはbyte-levelで同じである必要はないが、behavior変更禁止。

```text
profile == "standby"
profile == "green"
```

特に:

```text
standby candidate=5
SLOT23_PRESERVED_FIELDS契約
green candidate=1
23 unconditional standby
07 unconditional green
```

を変更しない。

---

# 10. 変更3: read-back mismatchにrequested/observedを残す

`app/kpnet/workflow.py::_apply_settings_profile()`を変更する。

strict failureは維持する。

## 10.1 mismatch dict

`compare_setting_readback()`後に、mismatch fieldだけで次を作る。

```python
readback_mismatch_values = {
    field: {
        "requested": str(payload.get(field, "")),
        "observed": str(readback.get(field, "")),
    }
    for field in mismatches
}
```

## 10.2 summary

既存`setting_results` recordに必ず:

```text
readback_mismatch_values
```

を追加。

一致時は:

```json
{}
```

不一致時例:

```json
{
  "batteryOperatingMode": {
    "requested": "3",
    "observed": "1"
  }
}
```

## 10.3 exception message

不一致時はfield名だけでなく、requested/observedを含める。

formatは固定:

```text
KP-NET settings read-back mismatch for profile=<name>: batteryOperatingMode(requested=3 observed=1)
```

複数fieldはcomma+space区切り。

## 10.4 secret禁止

exception/summaryへ次を入れない。

```text
_csrf
loginid
loginpassword
password
secret
token
authorization
```

controlled setting fieldだけ。

## 10.5 strictness禁止変更

以下禁止。

```text
NIGHT_SOC_READBACK_REQUIRED default変更
mismatchをwarning化
retry追加
sleep追加
observed値へexpected値を合わせる
provider normalization推測
```

---

# 11. 新規回帰テスト1: 03 forcedはsnapshotを維持

`tests/test_kpnet_mode_only_time_fence.py`へ追加。

テスト名は固定:

```python
def test_03_forced_mode_only_preserves_current_snapshot_except_mode_and_soc_candidate(...):
```

## 11.1 current fixture値

テスト内ではcurrentをstatic profileと意図的に異なる値にする。

最低限:

```text
batteryOperatingMode = 1
socSafetyMode = 20
socEconomyMode = 10
socContactInput = 30
socChargeMode = 0
chargeStartTimeH = 23
chargeStartTimeM = 0
chargeEndTimeH = 7
chargeEndTimeM = 0
dischargeStartTimeH = 8
dischargeStartTimeM = 0
dischargeEndTimeH = 22
dischargeEndTimeM = 0
agreementAmpere = 40
onPowerOutageMode = 1
onPowerOutageChargePowerW = 1234
```

## 11.2 candidate map

BatteryOperatingMode:

```text
0 economy
1 green
3 forced
5 standby
```

SocChargeModeは最低:

```text
0
30
50
```

を返すようfakeを調整する。

## 11.3 expected

forced実行後confirm payload:

```text
batteryOperatingMode == "3"
socChargeMode == "50"
```

それ以外のcontrolled/provider settingはcurrent値と一致。

特に:

```text
chargeStartTimeH/M == 23:00
chargeEndTimeH/M == 07:00
```

であり:

```text
04:30
06:30
```

にならないこと。

---

# 12. 新規回帰テスト2: changed_fieldsは2field以内

テスト名固定:

```python
def test_03_forced_mode_only_changes_only_operating_mode_and_soc_charge_candidate(...):
```

`_apply_settings_profile`を直接またはsummary経由で確認し、changed fieldsが:

```python
set(changed_fields) <= {"batteryOperatingMode", "socChargeMode"}
```

であること。

currentが両方違うfixtureではexactに:

```python
{"batteryOperatingMode", "socChargeMode"}
```

を要求。

---

# 13. 新規回帰テスト3: static profileの時刻漏れ防止

テスト名固定:

```python
def test_03_forced_mode_only_does_not_inject_static_forced_window(...):
```

current:

```text
23:00-07:00
```

static `FORCED_CHARGE_PROFILE`:

```text
04:30-06:30
```

という差を前提に、confirm payloadが必ず23:00-07:00を維持すること。

---

# 14. 新規回帰テスト4: read-back mismatch詳細

`tests/test_kpnet_workflow.py`または既存fakeを使いやすい対象test fileへ追加。

テスト名固定:

```python
def test_readback_mismatch_records_requested_and_observed_controlled_values(...):
```

fake:

```text
requested batteryOperatingMode = 3
observed batteryOperatingMode = 1
```

expected:

1. `RuntimeError`
2. exception messageに:

```text
batteryOperatingMode(requested=3 observed=1)
```

3. summary recordに:

```json
"readback_mismatch_values": {
  "batteryOperatingMode": {
    "requested": "3",
    "observed": "1"
  }
}
```

4. secret値が含まれない。

---

# 15. 既存テストで絶対維持するもの

少なくとも次を実行し成功させる。

```text
tests/test_kpnet_mode_only_time_fence.py
tests/test_kpnet_workflow.py
tests/test_kpnet_settings_roundtrip.py
tests/test_cloud_job_runner.py
tests/test_night_soc_controller.py
tests/test_night_soc_protected_contract.py
tests/test_night_soc_time_ownership.py
```

fileが存在しない場合だけ、その1件を報告して残りを実行する。

名称の似た別testに勝手に置換しない。

---

# 16. historical locks

この作業は `app/kpnet/workflow.py::run_kpnet_mode_only_profile` 近傍を触る。

PR説明へ必ず次を記載する。

```text
Affected historical lock:
2026-08-29 user-authorized time ownership

Preserved behavior:
23 standby remains one unconditional read-back write.
07 green remains one unconditional read-back write.
03 reapply remains disabled.
06:45/06:50/06:55 fences remain unchanged.
```

`HISTORICAL_FAILURE_LOCK` commentを削除・短縮・移動しない。

---

# 17. code-quality-audit と検証順序

`AGENTS.md`どおり、pytestより先にcode-quality-audit Skillを実行。

その後、以下の順番を変えない。

## 17.1 Ruff

```powershell
python -m ruff check app/kpnet/workflow.py tests/test_kpnet_mode_only_time_fence.py tests/test_kpnet_workflow.py
```

`tests/test_kpnet_workflow.py`未変更でも実行する。

formatterは禁止。

## 17.2 focused pytest

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

## 17.3 security check

```powershell
python scripts/security_check.py
```

## 17.4 production env validation

source patchがproduction runtimeへ影響するため:

```powershell
pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 -ValidateOnly
```

ここまで成功してもdeploymentはしない。

---

# 18. 実機live round-trip

このPR作業中は自動実行禁止。

理由:

```text
production mutationを伴うため
```

source/test commitとPRを先に作る。

PR本文には:

```text
Live device verification: NOT RUN in implementation PR
Required before production deploy by existing production runbook
```

と書く。

既存deploy wrapperが実機round-tripを必須化しているため、独自の実機commandを作らない。

---

# 19. planner SOC freshnessは変更禁止

今回:

```text
plan soc=94%
03 realtime soc=0%
```

が確認されているが、次を変更してはならない。

```text
app/energy_plan/workflow.py
SOC optimizer
forecast correction
NightChargeInputs
plan schema
Firestore plan schema
```

理由:

03 forced production pathのstatic profile問題と、planner SOC provenance問題は別logical unitだから。

follow-upとして報告だけする。

報告文は固定:

```text
Follow-up candidate: add explicit SOC source/observed_at/age provenance to generated night plans and define a stale-input policy without changing historical replay semantics.
```

---

# 20. incident report訂正の扱い

provenance phaseで、既存reportのCloud Run時刻が8/30 incidentと一致しないことがPROVENした場合だけ、別commitで:

```text
docs/completed/reports/soc_gap_investigation_2026-08-30.md
```

を訂正してよい。

訂正内容は次に限定。

```text
read-back mismatchを直接原因から外す
ログ日付の不一致を記載
確定原因を「夜間充電未完遂」までに留める
旧split-brainを構造要因として記載
現行source hardeningとは区別する
```

provenanceがPARTIAL/BLOCKEDならreport本文を変更しない。

新しいcompleted reportを作らない。

---

# 21. commit分割

commitは最大3つ。

順番固定。

## Commit 1

source + regression testsのみ。

message:

```text
fix: preserve current settings in 03 forced mode
```

## Commit 2

provenanceがPROVENで既存incident report訂正が必要な場合だけ。

message:

```text
docs: correct SOC incident log provenance
```

該当しない場合は作らない。

## Commit 3

CodebaseMemory shared graph refreshがAGENTSルール上必要な場合だけ。

message:

```text
chore: refresh CodebaseMemory SOC runtime graph
```

artifactはsource-bearing Commit 1を指すこと。

artifact commit自身を指すための再refreshは禁止。

---

# 22. CodebaseMemory refresh

Commit 1でsource変更したため、source-bearing commit後に1回だけrefreshする。

`.codebase-memory/artifact.json` のsource commit identityがCommit 1を指すことを確認する。

artifact commit後にHEADが進んだという理由だけで2回目refreshしない。

binary graphを手編集しない。

---

# 23. PR本文に必ず含める内容

以下の見出しをそのまま使う。

```markdown
## Incident
## Proven evidence
## Log provenance
## Root structural issue
## Source change
## Historical locks preserved
## Tests
## Production verification
## Out of scope
```

## 23.1 Incident

```text
2026-08-30 target SOC 100%, actual SOC 73% at 07:30.
```

## 23.2 Proven evidence

最低限:

```text
03:00 SOC 15%
05:00 SOC 76%
05:30-06:30 charge 0kWh and SOC 76%
```

## 23.3 Log provenance

PROVEN/PARTIAL/BLOCKEDのどれかを明記。

既存8/28T21Zログを8/30直接ログとして扱ったかどうかを書く。

## 23.4 Root structural issue

次を明記。

```text
Historical implementation had split authority between realtime monitor SOC and plan-derived actuator profile.
Current implementation removed that dynamic split, but its 03 forced "mode-only" path still rebuilt a full static forced profile instead of preserving the current device snapshot.
```

## 23.5 Source change

```text
03 forced now preserves current settings and changes only batteryOperatingMode plus SocChargeMode candidate.
```

## 23.6 Historical locks preserved

Section 16の固定文を入れる。

## 23.7 Tests

実際に実行したcommandとpass/failのみ。

## 23.8 Production verification

```text
No production deployment or Cloud Run Job execution was performed by this PR.
Existing deployment runbook/live round-trip remains mandatory before production rollout.
```

## 23.9 Out of scope

```text
planner SOC freshness/provenance
forecast logic
SOC optimizer
23/07 behavior
03 reapply
Firestore lease/day gate restoration
```

---

# 24. 完了条件

以下が全部満たされたときだけCOMPLETE。

```text
[ ] CodebaseMemory preflight ready
[ ] incident provenanceをPROVEN/PARTIAL/BLOCKEDで記録
[ ] 03 forcedがcurrent snapshot基準
[ ] forcedで意図変更するfieldはbatteryOperatingModeとsocChargeModeだけ
[ ] static 04:30-06:30が03 forced payloadへ漏れない
[ ] read-back mismatchにrequested/observedが残る
[ ] read-back strict failureは維持
[ ] standby branch behavior不変
[ ] green branch behavior不変
[ ] 03 reapplyなし
[ ] 06:45/06:50/06:55不変
[ ] focused tests成功
[ ] Ruff成功
[ ] security_check成功
[ ] ValidateOnly成功
[ ] production deploy未実行
[ ] live device mutation未実行
[ ] source-bearing stateでCodebaseMemory refresh 1回
[ ] PR作成
```

1つでも未達ならCOMPLETEと報告しない。

---

# 25. STOP条件

以下のどれかなら、その時点でsource変更を止める。

```text
CodebaseMemoryがreadyにならない
tracked user changeが存在する
current settingsに必須fieldがないことが実機/fixture契約上判明し、既存API契約変更が必要
SocChargeMode候補がnumeric codeとして取得できない
BatteryOperatingMode forced candidateが取得できない
変更にapp/runtime/cloud_job.pyのbehavior変更が必要になる
変更にtime fence変更が必要になる
変更に23/07 behavior変更が必要になる
read-back mismatchを緩めないとtestが通らない
新dependencyが必要
external contract変更が必要
```

STOP時は回避実装を作らない。

次だけ報告する。

```text
停止条件
確認した証拠
変更していないこと
次に人間が決める必要がある1点
```

---

# 26. 最終報告フォーマット

Luna軽は最終報告を以下の順番だけで出す。

```text
1. Result: COMPLETE / BLOCKED
2. Provenance: PROVEN / PARTIAL / BLOCKED
3. Root cause evidence
4. Exact files changed
5. Exact behavior changed
6. Historical behavior preserved
7. Tests and checks
8. CodebaseMemory refresh result
9. Production actions: NONE
10. Follow-up: planner SOC provenance only
```

長い一般論、追加提案、別リファクタ案は書かない。

以上。
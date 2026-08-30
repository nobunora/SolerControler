# Codex次作業指示: 2026-08-30 SOC未達の最小修正

## 0. 目的

2026-08-30朝の目標SOC未達について、追跡済み調査報告・Git履歴・現在masterの実装を突合した結果を基に、事故時の古い制御を丸ごと復元せず、現在の時間所有権設計を維持したまま最小変更で再発確率を下げる。

この文書は実装用のone-shot指示である。実装完了後は`docs/current/agent/`に残さず、既存ルールに従って`docs/completed/agent_runs/`へ移動する。

## 1. 基準

作業開始時に必ず最新`master`へ追従し、少なくとも次を確認する。

```text
investigation commit: aaf857d37225e635361aaae677bf82c9ad1b4b8a
evidence commit:      d856838e5b075098859ce1509e94472c069cf9e0
baseline at this instruction creation:
                      d856838e5b075098859ce1509e94472c069cf9e0
```

最初に読むもの:

```text
AGENTS.md
docs/completed/reports/soc_gap_investigation_2026-08-30.md
docs/completed/reports/soc_gap_evidence_2026-08-30.csv
docs/current/agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md
docs/current/agent/agent_working_rules.md
app/runtime/cloud_job.py
app/runtime/night_soc_time_contract.py
app/runtime/soc_reading.py
app/kpnet/workflow.py
app/kpnet/profile_builder.py
app/kpnet/profiles.py
app/kpnet/settings_roundtrip.py
app/runtime/night_soc_controller.py
tests/test_cloud_job_runner.py
tests/test_kpnet_workflow.py
tests/test_night_soc_protected_contract.py
```

## 2. 事故の確定事実

追跡済み証跡から、2026-08-30朝について次は事実として扱ってよい。

```text
target SOC: 100%
07:00 actual SOC: 75%
07:30 actual SOC: 73%
night charge actual: 7.183 kWh
night charge expected/required: 約9.997 kWh
shortfall: 2.814 kWh
05:00 SOC: 76%
05:30-06:30 SOC: 76%
05:30-06:30 charge: 0 kWh
```

また、計画生成時の`SOC=94%`と03時監視の実機読出し`SOC=0%`に大きな乖離があった。計画側必要量は0.601 kWhだった一方、03時側の実行必要量は9.864 kWhだった。

03時制御は目標100%を認識していたが、強制充電の再適用で`batteryOperatingMode` read-back mismatchが発生し、複数回の失敗後に安全側standbyへ遷移した。07:00時点で既に75%だったため、日中負荷・PV不足は朝の未達の主因ではない。

## 3. 重要な世代差

### 3.1 事故時の本番挙動

事故ログは、次の特徴を持つ。

- 03監視中にforced profile再適用を行う。
- read-back mismatch時に複数回の再試行がある。
- durable execution state / fail-safe standby / handoffを持つ。
- 動的な実行必要量・開始終了時刻を扱う。

これは現在masterの`app/runtime/cloud_job.py`の単純化された実装とは一致しない。

したがって、**事故時Cloud Run imageと現在masterを同一世代として扱ってはいけない。**

実装開始時、Cloud Run revision/image digest/commit labelを安全に確認できる場合は取得し、事故時imageのGit commitを特定する。特定できない場合も、調査報告のログ挙動を根拠に「事故時実装世代はcurrent masterと異なる」と記録し、推測でSHAを断定しない。

### 3.2 比較する履歴

次を必ず比較する。

```text
72697aba6a5567959e5d766b81d7ee378557709d
fix: force charge through 50 percent device candidate

3cdd48cf686dae47a6b788133927424c437c99a6
fix: restore automatic night SOC control

f2cfa5171bf7d0a860f2cb65ce6afa7ce124e527
fix: preserve night SOC handoff after device mismatch

badd209df2b7ba007191abcc28af9aed20c02f45
fix: isolate scheduled battery control by time

current master
```

### 3.3 `72697ab`から保持すべき実機契約

`72697ab`で確認された重要な実機契約は次である。

- installed inverterの`SocChargeMode`候補は最大50%。
- 計画SOCは100%まで連続値を取り得る。
- 51-100%の目標でも、device candidate 50を使ってforced chargeを開始する。
- device candidate 50は停止目標ではない。
- 実際の停止判定は03 monitorがraw planning targetで行う。
- post-deploy live probeは、forced operating mode + SocChargeMode 50を適用し、read-back確認後60秒保持し、完全な初期snapshotへ復元する。

この契約を変更してはいけない。

### 3.4 `3cdd48c`の扱い

`3cdd48c`は2026-08-28のSOC 0%事故後にautomatic night SOC controlを復元した直後の履歴であり、`f2cfa5`の親commitである。

このcommitは「比較用のlast-known-restored baseline」として使用する。ただし、追跡済みCSVだけでは`3cdd48c`が一晩の本番運転で完全に問題なかったことまでは証明できない。文書・PRで「完全なlast-known-good」と断定せず、**post-incident restored baseline**と表現する。

### 3.5 `f2cfa5`の意味

`f2cfa5`は`3cdd48c`から大きく拡張し、device mismatch時のfail-safe・durable handoff・terminal persistenceを強化した。

安全性向上の意図は正しい。一方、事故時ログに現れた「forced再適用/read-back mismatch/最終standby」という挙動はこの世代の設計と整合する。

**この世代を丸ごと戻してはいけない。**

### 3.6 `badd209`の意味

`badd209`は時間所有権を明確化し、23/03/07の経路を大幅に分離した。

現行`cloud_job.py`では03 controlが単純化され、forced開始→SOC監視→standbyという構造になっている。旧世代のFirestore lease/reapply/terminal persistenceロジックをそのまま戻すのは禁止。

今回の最小修正は、この現行構造を前提に行う。

## 4. 現在masterで確認された問題

### 4.1 `run_kpnet_mode_only_profile()`が厳密にはmode-onlyではない

現在の`app/kpnet/workflow.py::run_kpnet_mode_only_profile()`は、forced branchで次を行う。

```python
selected = replace(
    FORCED_CHARGE_PROFILE,
    battery_operating_mode=_pick_battery_operating_mode_code(... prefer="forced"),
)
```

`FORCED_CHARGE_PROFILE`には固定値が含まれる。

```text
socSafetyMode = 50
socEconomyMode = 0
socContactInput = 100
socChargeMode = 50
chargeStart = 04:30
chargeEnd = 06:30
dischargeStart = 07:00
dischargeEnd = 23:00
```

`_apply_settings_profile()`はprofile全体からpayloadを構成するため、現在値との差があれば`batteryOperatingMode`以外も書き込む。

したがって関数名・設計意図の「mode-only」と実際のmutation setが一致していない。

### 4.2 これが危険な理由

現行03 monitorは、planのraw targetを停止判定に使う。一方でforced開始時に固定`04:30-06:30`を再注入すると、plan/現在のnight windowと独立してdevice scheduleを変え得る。

2026-08-30事故の実績では、目標100%未達のまま05:30以降charge=0となった。事故時本番世代はcurrent masterと異なるため、この固定06:30が事故原因だったとは断定しない。

しかし、現在masterにこの固定値注入を残すと、今後別の形で「monitorは100%を待つがdevice scheduleは先に充電を止める」という同型事故を再発させる可能性がある。

これは事故レポートから独立してcurrent sourceだけで確認できる構造上の不整合である。

## 5. 最小変更方針

### 5.1 変更の中心

**03 forced activationだけを、現在snapshotを基準にした最小mutationへ変更する。**

現行の23/07時間所有権、03 monitor、06:45/06:50/06:55 cutoff、standby fail-safeは維持する。

### 5.2 forced activationで変更してよいfield

03 forced開始時は、原則次の2 fieldだけを変更する。

```text
batteryOperatingMode -> actual forced candidate
socChargeMode        -> deviceで利用可能な最大候補（installed deviceでは50）
```

その他はreadした現在値をそのまま保持する。

```text
socSafetyMode
socEconomyMode
socContactInput
chargeStartTimeH/M
chargeEndTimeH/M
dischargeStartTimeH/M
dischargeEndTimeH/M
agreementAmpere
onPowerOutageMode
onPowerOutageChargePowerW
```

理由:

- `72697ab`のlive probeで、forced mode + SocChargeMode 50という最小command pathが実機検証済み。
- raw target停止は03 monitorが所有している。
- 03 forced開始時に固定windowを再注入する必要はない。
- 23/07 ownerが設定したcurrent windowを03が勝手に変更しない方が時間所有権設計と整合する。

### 5.3 実装方法

`ProfileOverrides`に、現在設定から完全なsnapshot profileを作る共通APIを追加する。

推奨:

```python
@dataclass(frozen=True)
class ProfileOverrides:
    ...

    @classmethod
    def from_current_settings(
        cls,
        current: Mapping[str, Any],
        *,
        name: str,
    ) -> "ProfileOverrides":
        ...
```

実装内容は現在`app/kpnet/settings_roundtrip.py::profile_from_current_settings()`にあるmappingを移す。

`settings_roundtrip.py`はこのclassmethodを使うよう変更し、重複を残さない。

その上で`run_kpnet_mode_only_profile(profile="forced")`を次の意味にする。

擬似コード:

```python
base = ProfileOverrides.from_current_settings(
    current,
    name="03-forced-activation",
)
forced_mode = _pick_battery_operating_mode_code(
    maps["BatteryOperatingMode"],
    prefer="forced",
)
forced_soc_code = _pick_max_code(maps["SocChargeMode"])
selected = replace(
    base,
    name="03-forced-activation",
    battery_operating_mode=forced_mode,
    soc_charge_mode=forced_soc_code,
)
```

`_pick_max_code`がprivate boundaryとして不適切なら、`build_device_soc_guard(... raw_target_soc_percent=100, stop_margin_percent=0)`で最大候補を得てもよい。ただし候補50をstop targetとして扱ってはいけない。

### 5.4 standby / green

今回のPRでは必要以上に変更しない。

- `standby`: 現行の「12 SOC/window fieldsをcurrentから保持し、batteryOperatingModeだけstandby candidateへ変える」契約を維持する。
- `green`: 現行07 contractを維持する。今回の事故修正に便乗してgreen field集合を再設計しない。

forced branchだけを最小修正する。

## 6. read-backの扱い

### 6.1 fail-open禁止

`batteryOperatingMode` read-back mismatchを無視して成功扱いにしてはいけない。

次は禁止:

```text
NIGHT_SOC_READBACK_REQUIRED=falseで逃げる
batteryOperatingModeをCONTROLLED_SETTING_FIELDSから削除
mismatchをwarning化
3回失敗後もforced成功扱い
07 green gateのために状態を成功へ書き換える
```

### 6.2 observability改善

read-back mismatchが発生した場合、credential/device ID等を含めず、次だけをsummary/logへ残す。

```text
profile
changed_fields
requested batteryOperatingMode code
observed batteryOperatingMode code
BatteryOperatingMode candidate mapのcode->label
requested socChargeMode code
observed socChargeMode code
```

候補labelに機密情報が含まれないことを確認する。

### 6.3 semantic normalization

事故レポートだけでは、`batteryOperatingMode`のrequested値とobserved値の実値を特定できない。

したがって今回、推測で`3 == 強制充電`等のread-back normalizationを新規導入してはいけない。

実機fixtureまたは本番read-only証跡で「コードとlabelが同一意味なのに文字列表現だけ違う」と確認できた場合だけ、別の小さな変更として正規化する。

## 7. SOC 94% vs 0%の扱い

### 7.1 実行制御

現在03 monitorは実機SOCを読み、raw targetと比較して停止する。このため、計画JSON内の`required_night_charge_kwh=0.601`を実行の唯一の根拠に戻してはいけない。

03ではlive SOCが優先である。

### 7.2 mismatch検出

planの`soc_now_percent`と03 initial live SOCの両方がある場合、差を計算し、閾値超過を構造化ログに残す。

推奨env default:

```text
ADJUST03_PLAN_LIVE_SOC_WARN_DELTA_PERCENT=10
```

ただし今回の最小PRでは、env追加が不要なら定数10.0でもよい。

例:

```text
03-plan-live-soc-divergence plan=94 live=0 delta=94
```

これはwarning/diagnosticであり、forced chargeを止めるgateにしてはいけない。

理由: live SOCが低いほど充電が必要であり、plan SOCが古いことを理由にfail closedすると今回と同じ未達を悪化させる。

### 7.3 将来のplan生成修正

計画SOCの鮮度・source/timezoneの根本修正は別PR候補とする。

今回の最小修正では、03 executionを安全にし、差分を観測可能にするところまでとする。

## 8. SOC gap reportの日付問題

`scripts/run_kpnet_soc_gap_report.ps1`は現状、plan dateとactual dateを同一日として扱うため、前日計画→翌朝実績の通常night cycleで失敗する。

これは今回のruntime fixとは別責務である。

同一PRに混ぜない。

必要なら次PRで:

```text
-PlanDate 2026-08-29
-ActualDate 2026-08-30
```

のように明示分離する。

## 9. 必須テスト

### 9.1 current snapshot preservation

`tests/test_kpnet_workflow.py`へ、realistic current green settingsからforced activationするテストを追加する。

初期current例:

```text
batteryOperatingMode=1
socSafetyMode=0
socEconomyMode=0
socContactInput=0
socChargeMode=0
chargeStartTime=23:00
chargeEndTime=07:00
dischargeStartTime=07:00
dischargeEndTime=23:00
agreementAmpere=<current>
```

candidate map:

```text
BatteryOperatingMode: 0=economy, 1=green, 3=forced, 5=standby
SocChargeMode: 0..50
```

forced後に期待する変更:

```text
batteryOperatingMode: 1 -> 3
socChargeMode: 0 -> 50
```

次は**変化しないこと**をassertする。

```text
socSafetyMode
socEconomyMode
socContactInput
chargeStartTimeH/M
chargeEndTimeH/M
dischargeStartTimeH/M
dischargeEndTimeH/M
agreementAmpere
```

特に:

```text
chargeStartTime == 23:00
chargeEndTime == 07:00
```

を固定する。

### 9.2 static profile regression

forced activation後のpayloadに次が事故的に入り込まないことをassertする。

```text
04:30
06:30
```

currentが別値なら、FORCED_CHARGE_PROFILEの固定timeで上書きされないこと。

### 9.3 candidate ceiling

SocChargeMode candidateが0..50でtargetが100%でも:

```text
forced activation succeeds
socChargeMode=50
raw target remains 100 for monitor stop
```

を確認する。

### 9.4 read-back mismatch

`batteryOperatingMode`のread-backがrequestedと異なるfixtureで:

```text
run_kpnet_mode_only_profile returns failure
03 device port raises
standby fail-safe path remains active
```

を確認する。

mismatchを成功化しない。

### 9.5 23 -> 03 -> 07 contract

既存protected testsに加え、可能ならfake clientで次を固定する。

```text
07 green state
-> 23 standby: SOC/window fields preserved
-> 03 forced: only operating mode + SocChargeMode activation changes
-> target reached standby
-> 07 green
```

03 forcedで23-07 windowが壊れないことを確認する。

### 9.6 plan/live SOC divergence

plan SOC=94、live SOC=0でも:

```text
divergence is recorded
forced activation is not blocked
monitor target remains plan target=100
```

を確認する。

## 10. 変更可能範囲

第一候補:

```text
app/kpnet/profiles.py
app/kpnet/settings_roundtrip.py
app/kpnet/workflow.py
app/runtime/cloud_job.py              # divergence logが必要な最小変更のみ
tests/test_kpnet_workflow.py
tests/test_cloud_job_runner.py
tests/test_night_soc_protected_contract.py
docs/current/agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md
```

必要性が証明できないファイルは触らない。

## 11. 変更禁止・非目標

今回禁止:

```text
旧f2cfa5 cloud_jobを丸ごと復元
Firestore lease/reapply/terminal persistenceを復活
06:45/06:50/06:55境界変更
07 ownerの意味変更
23 ownerの意味変更
SOC optimizerの目的関数変更
料金/PV forecast変更
NIGHT_SOC_READBACK_REQUIREDの緩和
batteryOperatingMode mismatchのwarning化
本番credential変更
本番deviceへの無承認書込み
```

## 12. protected historical rule追加

`PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md`へ、今回の新しいlockを追加する。

意味:

```text
03 forced activationはcurrent device settings snapshotを基準とし、
forced operating modeとdevice forced-charge candidate以外の
SOC/window fieldを固定profile値で上書きしない。
raw targetの停止所有者は03 monitor。
```

過去根拠:

```text
72697ab
2026-08-30 SOC gap evidence
badd209 current time ownership design
```

## 13. 実機確認の順序

ユーザー承認なしに本番書込みを行わない。

実装PRではまずoffline/fake testsまで。

実機検証を行う場合は既存reversible settings-roundtrip contractを使用し、次を満たす。

```text
initial snapshot read
forced + SocChargeMode50 apply
read-back match
hold exactly 60s
full initial snapshot restore
restore read-back match
```

実機確認で03 schedulerを手動起動する必要はない。

## 14. quality gate

最低限:

```text
python -m ruff check .
pytest focused tests
pytest tests/test_night_soc_protected_contract.py
git diff --check
```

repoルール上必要なら追加で:

```text
ty
mypy/import-linter相当
security_check
```

既存CIと同じquality workflowを成功させる。

## 15. CodebaseMemory

この実装でsource/current docsが変わるため、logical source commit後に既存共有graphルールに従い1回だけrefreshする。

artifact-only commit自身を追う二度目refreshは禁止。

## 16. 完了報告に必須の比較表

最終報告には次を入れる。

|観点|3cdd48c restored baseline|f2cfa5 incident-era lineage|badd209/current|修正後|
|---|---|---|---|---|
|03 forced activation|旧自動制御|dynamic profile/reapply|mode-only APIだがforced static profile由来|snapshot + forced mode + SocChargeMode max|
|read-back mismatch|fail closed|fail-safe/durable handoff強化|即fail + standby|維持|
|device schedule ownership|dynamic profile側|dynamic profile側|固定FORCED profileが再注入可能|current value保持|
|raw target stop|monitor|monitor|monitor|monitor|
|old lease/persistence|あり|強化|削除/簡素化|復活させない|
|SOC plan/live divergence|世代依存|観測あり|live monitor優先|live優先 + diagnostic|

## 17. 完了条件

次を全て満たすまでCOMPLETEにしない。

1. 事故レポートとCSV証跡を読んだ。
2. 事故時本番世代とcurrent masterが同一でないことを明示した。
3. `3cdd48c`を完全なlast-known-goodと誇張していない。
4. `72697ab`の50% candidate/raw target契約を維持した。
5. `badd209`の時間所有権設計を維持した。
6. 旧lease/reapply/persistenceを復活していない。
7. forced activationがcurrent snapshot基準になった。
8. forced時の変更fieldが`batteryOperatingMode`と`SocChargeMode`に限定された。
9. current charge/discharge windowsをforced activationが上書きしない。
10. read-back mismatchをfail closedのまま維持した。
11. plan/live SOC divergenceを観測可能にした場合、それをforced停止gateにしていない。
12. focused regression tests成功。
13. protected historical contract更新。
14. quality workflow成功。
15. source変更後のCodebaseMemory refreshが必要なら1回だけ実施。
16. production deploymentは別途明示承認がない限り実施しない。

## 18. 最重要判断

今回の事故を理由に複雑な旧controllerへ戻してはいけない。

事故から得るべき教訓は「安全機構を増やすこと」だけではない。

現在の設計では、責務を狭くした関数が本当に狭いmutationしか行わないことが重要である。

`run_kpnet_mode_only_profile()`の03 forced branchは、名前どおりの最小mutationに近づける。device forced candidate 50はforced開始のためだけに使い、raw SOC targetはmonitorが停止判定として保持する。これが、過去の実機契約・現在の時間所有権・今回の事故証跡を同時に満たす最小修正である。

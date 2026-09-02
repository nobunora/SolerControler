# 2026-09-02 朝 SOC 0% 調査

Result: INVESTIGATION_COMPLETE_CAUSE_NARROWED
Primary classification: PLAN_GENERATION_OR_PREP_FAILURE
Incident date: 2026-09-02
Production mutation performed: NO
Normal 03 manual execution performed: NO

## 1. Incident statement

利用者が2026-09-02朝に蓄電池SOC 0%を確認した。公式KP-NET監視CSVでも07:00、07:30、08:00 JSTのSOCは0%であり、利用者報告と一致する。調査は読み取り専用のCloud Run、Cloud Scheduler、Cloud Loggingおよび公式監視CSV取得に限定した。

## 2. Repository/source baseline

- 調査開始時の最新master: `618d6e2c52d4de48ab8e6f535b1bce55d244aead`。
- provenance実装source: `6eee4d9c861c03d52ee8e93a003e9c531c36f37c`。
- CodebaseMemory project `C-VSC-SolerControler` はrefresh後 `ready` 相当、6269 nodes / 18732 edges、skipped 0、parse-partial 9。対象シンボルはgraphと直接sourceの両方で確認した。
- 現行03経路は [`_run_adjust_03`](../../../app/runtime/slot_orchestration.py) がinitial CSV、plan準備、monitorの順に実行する。plan準備例外時にplanが存在しなければfail-safe standbyを一度実行してreturnする。
- [`_ensure_night_plan_available`](../../../app/runtime/cloud_job.py) は当日dateを指定して`energy_model_main.py`を最大240秒で実行する。monitorへ到達した場合だけ`03-plan-provenance`を出力する。

## 3. Production rollout provenance

- 既存の公式deployment stateはsource commit `6eee4d9c...`、status `complete`、jobs `success`を記録している。
- 対象03 executionのimmutable image digestは、調査時のrunner `latest` digestと一致した。
- したがってprovenance marker欠落をimage rollout mismatchとは分類しない。対象executionはmonitorへ到達しなかったと判断する。

## 4. Scheduler and execution timeline

時刻はJST。

| slot | Scheduler | schedule/timezone | created | started | completed | result |
| --- | --- | --- | --- | --- | --- | --- |
| 23 | ENABLED | `0 23 * * *` / Asia/Tokyo | 09-01 23:00:17 | 23:00:31 | 23:04:47 | succeeded=1、4 conditions=True |
| 03 | ENABLED | `0 3 * * *` / Asia/Tokyo | 09-02 03:00:01 | 03:00:19 | 03:08:07 | succeeded=1、4 conditions=True |
| 07 | ENABLED | `0 7 * * *` / Asia/Tokyo | 09-02 07:00:01 | 07:00:11 | 07:02:59 | succeeded=1、4 conditions=True |

4 conditionsはCompleted、Started、ContainerReady、ResourcesAvailableで、すべてTrueだった。03 Scheduler last attemptは03:00:01 JSTで、scheduled executionとcreation時刻が対応する。

## 5. 03 provenance payload

対象03 executionのmarker count:

```text
provenance_line_count = 0
contract_line_count = 0
soc_line_count = 0
stop_reason_line_count = 0
```

monitorへ到達していないためprovenance payloadは存在せず、以下はすべて`NOT_PROVABLE`である。

```text
schema_version = NOT_PROVABLE
forecast_date = NOT_PROVABLE
plan_sha256 = NOT_PROVABLE
base_target_soc_7_percent = NOT_PROVABLE
final_target_soc_7_percent = NOT_PROVABLE
required_night_charge_kwh = NOT_PROVABLE
optimizer_kind/objective = NOT_PROVABLE
max_target_soc_percent_after_guards = NOT_PROVABLE
active_constraints/candidates/constraint_details = NOT_PROVABLE
```

したがって`TARGET_ZERO_BRANCH`と`TARGET_POSITIVE_BRANCH`のどちらも確定しない。

## 6. Runtime SOC timeline

`03-monitor soc`は0件で、runtime SOC timelineは作成不能である。

```text
first SOC = NOT_PROVABLE
last SOC = NOT_PROVABLE
minimum/maximum SOC = NOT_PROVABLE
realtime/csv/unavailable reading counts = 0 / 0 / 0
stop_reason = NOT_PROVABLE
```

## 7. Official monitoring timeline

派生証拠は [`zero_soc_evidence_2026-09-02.csv`](./zero_soc_evidence_2026-09-02.csv)。公式wrapperで2026-09月CSVをread-only取得し、必要列と23:00～08:00の行だけを転記した。

| JST | SOC | charge | discharge |
| --- | ---: | ---: | ---: |
| 09-01 23:00 | 1% | 0.032 kWh | 0.000 kWh |
| 09-02 00:00 | 1% | 0.000 kWh | 0.000 kWh |
| 03:00 | 1% | 0.000 kWh | 0.000 kWh |
| 03:30 | 1% | 0.000 kWh | 0.000 kWh |
| 04:00 | 1% | 0.000 kWh | 0.000 kWh |
| 04:30 | 1% | 0.000 kWh | 0.000 kWh |
| 05:00 | 1% | 0.000 kWh | 0.000 kWh |
| 05:30 | 1% | 0.000 kWh | 0.000 kWh |
| 06:00 | 1% | 0.000 kWh | 0.000 kWh |
| 06:30 | 1% | 0.000 kWh | 0.000 kWh |
| 07:00 | 0% | 0.000 kWh | 0.013 kWh |
| 07:30 | 0% | 0.000 kWh | 0.000 kWh |
| 08:00 | 0% | 0.000 kWh | 0.000 kWh |

03:00～07:00（07:00行を除く）の合計はcharge 0.000 kWh、discharge 0.000 kWh。CSVは30分粒度である。

## 8. Runtime-vs-official SOC comparison

runtime SOC markerがないため個別比較表は作成不能で、判定は`NOT_PROVABLE`。official CSV単独では03:00～06:30にSOC 1%で横ばい、充電0.000 kWhだったことを証明する。

## 9. Forced mode/read-back evidence

- `KP-NET mode-only workflow failed`: 0件。
- `KP-NET settings read-back mismatch`: 0件。
- `03 standby failure`: 0件。
- 03:07台にLogin、Settings page opened、03:08台にLogout/exit(0)があり、source契約上はplan無しprep failure後のfail-safe standby経路と一致する。
- `03-monitor`が0件なのでforced pathへ到達した証拠はない。`Forced write/readback failure`は`NOT_PROVABLE`であり、`READBACK_GATE_PASSED`とも表現しない。

## 10. Plan generation evidence

03:03:06 JSTに次の当日指定起動ログがある。

```text
energy_model_main.py env_updates={'FORECAST_DATE_OVERRIDE': '2026-09-02'}
```

その後、plan完了、provenance、monitor contract、runtime SOCのログはない。約213秒後にtransport timeout警告が2件、約242秒後にfail-safe standbyと整合するKP-NET Loginが始まった。現行sourceではenergy model child processの上限は240秒で、timeout/exception後にplanが無ければstandbyしてreturnする。この時系列とsource分岐を合わせ、`PLAN_GENERATION_OR_PREP_FAILURE`へ原因範囲を絞る。

ただし、`_run_adjust_03`がprep exception本文をログへ残さないため、child processの具体的な失敗例外とplan不存在判定をCloud Loggingだけで直接証明できない。このためResultは`CAUSE_PROVEN`ではなく`CAUSE_NARROWED`とする。plan date mismatchは`NOT_PROVABLE`。

## 11. 23/07 secondary evidence

- 23 executionは成功し、standby mode-only failure/read-back mismatchは0件。
- 07 executionは成功し、green mode-only failure/read-back mismatchは0件。
- 03 executionは03:08:07 JSTに完了しており、06:55 JST以降の03由来ログ/device writeはない。
- 07 failureを朝SOC 0%の原因へ帰属しない。07時点でofficial SOCは既に0%だった。

## 12. Root-cause classification

```text
Primary classification = PLAN_GENERATION_OR_PREP_FAILURE
Result = INVESTIGATION_COMPLETE_CAUSE_NARROWED
```

Scheduler未実行、execution未実行、image mismatch、forced read-back failure、SOC unavailable stop、monitor cutoff、target reached、realtime/official disagreementを支持する必須証拠はない。特にplan targetはmonitor未到達のため0か正値かを判定できない。

## 13. What is proven

- scheduled 03 executionは存在し、03:00 JSTに起動した。
- deployment後のimmutable runner imageで実行された。
- initial CSVは完了し、当日dateでenergy modelを起動した。
- monitor/provenance/forced pathのログへ到達しなかった。
- official CSV上、03:00～07:00の充電は0.000 kWh、07:00/07:30/08:00 SOCは0%。
- 07/23 executionは成功し、抽出対象のmode-only failure/read-back mismatchはない。

## 14. What is not proven

- 当日planのbase/final target、optimizer、constraint、candidate、plan hash。
- energy model child process内の最終例外種別と、例外時のcontainer filesystem上のplan不存在を示す明示ログ。
- 03 runtime SOC、stop reason、forced write/read-backのhistorical exact values。
- runtime-vs-official SOC disagreement、plan date mismatch。

## 15. Next fix scope

```text
NEXT_FIX_REQUIRED = YES
PROVEN_DEFECT_BOUNDARY = app/runtime/slot_orchestration.py::_run_adjust_03 prep-exception observability and app/runtime/cloud_job.py::_ensure_night_plan_available bounded plan generation
MINIMUM_ALLOWED_FILES = app/runtime/slot_orchestration.py, app/runtime/cloud_job.py, tests/test_cloud_job_runner.py
PROTECTED_FILES_NOT_TO_CHANGE = 23/03/07 ownership, 06:45/06:50/06:55 time fences, exact target stop, forced mode-only/read-back contract, SOC parser, optimizer/constraints
REQUIRED_REGRESSION_TEST = plan generation timeout with no usable plan emits an explicit sanitized prep failure reason and performs exactly one fail-safe standby without entering monitor/forced control
PRODUCTION_DEPLOYMENT_REQUIRED = YES
```

この調査では修正しない。将来の修正では、まずenergy modelが240秒内に完了しなかった具体的要因を再現し、単なるtimeout延長より前に実行時間と依存I/Oを確認する。

## 16. Safety/non-mutation statement

production source edit、production deployment、normal 03 manual execution、Scheduler変更、settings round-trip、実機設定write、Firestore投入は行っていない。Cloud/Scheduler/Loggingはread-only照会のみ。KP-NETは公式CSV取込wrapperを`-SkipFirestoreIngest`で実行した。tracked fileにcredential、token、resource ID、生HTML、cookie、full env dumpは保存していない。

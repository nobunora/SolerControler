# Codex実行指示: 2026-09-01 03実行の確定確認とダッシュボード警告の契約整合

## 0. 目的

この作業は次の3点を順番固定で完了する。

1. 2026-09-01 03:00 JSTの本番03 Jobについて、デプロイ済みexact-targetログをread-onlyで確認し、100%目標到達/停止理由を確定する。
2. ダッシュボードの「目標SOC未達」が夕方の`pv_charge_end_soc_percent`を朝7時目標と比較している意味不一致を修正する。
3. 現行03 runtimeがFirestore/DBへ設定イベントを永続化しない設計なのに、dashboardがイベント不在を即「設定完了未確認」と判定する世代不一致を修正する。

03 runtimeの制御挙動、23/03/07 ownership、時刻フェンス、KP-NET設定値、03 persistenceは変更禁止。

## 1. baseline

開始基準master:

```text
04d1fb0f231140de7b7b717824cd5402ff870a70
```

この直前のproduction/report基準:

```text
5de2cf7f1372458e861d1b1f613504d053838a70  fix: require exact SOC target before 03 standby
f8bc8c5cd0ed945f039be7b71bb37d716f551444  docs: report exact SOC target stop remediation
04c81225d8cc37e7cd62c4f2bd982beca090a5bf  docs: record dashboard warning investigation 2026-09-01
```

`5de2cf7f...`は本番反映済み。03 target reached条件は:

```text
observed SOC >= plan target
```

legacy `ADJUST03_FORCE_STOP_SOC_MARGIN_PERCENT`は互換性のため残るがtarget reached判定には使用しない。

## 2. 既知事実

### 2.1 2026-09-01 03 Job

既存調査で次は確定済み。

```text
03 Scheduler = ENABLED
cron = 0 3 * * *
timezone = Asia/Tokyo
execution start ≈ 2026-09-01 03:00:01 JST
execution end   ≈ 2026-09-01 06:10:01 JST
Completed=True
ResourcesAvailable=True
Started=True
ContainerReady=True
succeededCount=1
```

`Completed successfully`だけでは100%到達を証明しない。

### 2.2 現行03ログ契約

`app/runtime/cloud_job.py::_monitor_partial_forced_and_stop`は最低限次をflush付きで出す。

```text
03-monitor contract target=... exact_target_stop=true configured_stop_margin=... applied_stop_margin=0.00%
03-monitor soc value=... source=... observed_at=... target=... action=continue|target_reached|soc_unavailable
03-monitor stop reason=target_reached ...
03-monitor stop reason=soc_unavailable ...
03-monitor stop reason=monitor_cutoff ...
```

### 2.3 03はFirestore persistenceを持たない

現行契約:

```text
03 has no Firestore fallback/persistence path
No retry and no DB/Firestore pipeline
```

protected contractにより03へ以下を戻すのは禁止。

```text
lease
terminal persistence
Firestore
DB
forced reapply
manual hand-off
```

dashboard警告を直すために03 runtimeへFirestore書込みを追加しない。

### 2.4 現行SOC警告の意味不一致

`app/dashboard/warnings.py`は現在:

```python
pv_end_soc + 5.0 < target_soc
```

で`目標SOC未達`を出す。

2026-08-29実績:

```text
目標 = 100%
06:30 = 100%
07:00〜14:30 = 100%
16:30 = 56%
```

`pv_charge_end_soc_percent=56%`は「その日の最後のPV>0かつ充電>0サンプル」のSOCで、朝7時SOCではない。

## 3. 必読ファイル

この順番で読む。

```text
AGENTS.md
docs/current/agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md
docs/current/agent/codebase_memory_shared_graph_usage_ja.md
docs/current/ops/PRODUCTION_DEPLOYMENT_RUNBOOK_JA.md
docs/completed/reports/soc_exact_target_stop_remediation_2026-08-31.md
docs/completed/reports/dashboard_warning_investigation_2026-09-01.md
app/runtime/cloud_job.py
app/runtime/night_soc_time_contract.py
app/dashboard/warnings.py
app/dashboard/slice_assembler.py
app/dashboard/service.py
app/dashboard/firestore_repository.py
app/dashboard/sqlite_repository.py
app/dashboard/postgres_repository.py
app/dashboard/schedule.py
app/operations/firestore.py
tests/test_dashboard_data.py
```

## 4. Git preflight

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
git fetch origin
git rev-parse origin/master
```

tracked user changesあり -> STOP。
非fast-forward -> STOP。
stash/reset/force pushで利用者変更を消さない。

## 5. CodebaseMemory preflight

既存MCPでstatusを1回確認。

readyなら以下だけqueryする。

```text
app.dashboard.warnings.build_dashboard_warnings
app.dashboard.slice_assembler.dashboard_warnings
app.dashboard.slice_assembler.build_dashboard_slice
app.dashboard.service.merge_forecast_hourly_actuals
app.dashboard.firestore_repository._firestore_forecast_hourly_between
app.dashboard.sqlite_repository.load_sqlite_query_snapshot
app.dashboard.postgres_repository.load_postgres_slice
app.dashboard.schedule._build_latest_schedule_from_events
app.runtime.cloud_job._monitor_partial_forced_and_stop
```

必ずsource/`rg`で照合する。

transport closed/connection failureなら:

```text
reinstall禁止
upgrade/downgrade禁止
MCP設定編集禁止
別project作成禁止
無差別reindex禁止
index_repository連打禁止
```

exact error保存 -> statusをもう1回だけ -> 2回目もtransport failureなら:

```text
CBM_TRANSPORT_BLOCKED_USER_AUTHORIZED_SOURCE_FALLBACK
```

と記録し、`rg` + direct source inspectionで継続する。CBMを使えたと報告しない。

最終artifact更新時まで復旧しなければsource/test/dashboard fixはpush可能だが、artifactを偽造せずResult=`PARTIAL_CBM_BLOCKED`。

# Part A: 2026-09-01 03実行ログを確定

## 6. production read-only初期化

PowerShell 7のみ。

```powershell
. .\scripts\production_env.ps1
Import-ProductionEnv
$projectId = Get-RequiredProductionEnv 'GCP_PROJECT_ID'
$region = Get-RequiredProductionEnv 'GCP_REGION'
$gcloud = Join-Path $PWD 'scripts\gcloud.ps1'
```

project/secret/account/credential値を出力しない。

## 7. execution特定

```powershell
& $gcloud run jobs executions list `
  --job solar-battery-03 `
  --region $region `
  --project $projectId `
  --limit 10 `
  --sort-by '~createTime' `
  --format json
```

対象:

```text
UTC 2026-08-31 17:55Z ～ 21:15Z
JST 2026-09-01 02:55 ～ 06:15
```

対象executionを1件へ固定。

確認:

```text
create/start/end
Completed
ResourcesAvailable
Started
ContainerReady
succeededCount
failedCount
image/revision/digest（取得可能範囲）
```

execution IDをtracked reportへ保存しない。

## 8. exact-target application log抽出

通常03 Jobを新規実行しない。既存executionのlogだけ読む。

対象job+時刻範囲へ限定し、最低限:

```text
03-monitor contract
03-monitor soc value=
03-monitor stop reason=
03-forced-start
03-target-reached-standby
03-monitor-cutoff-standby
read-back mismatch
batteryOperatingMode
```

を抽出する。生ログ全体をtracked fileへ保存しない。

## 9. execution分類

必ず1つ。

### `EXACT_TARGET_CONFIRMED`

```text
contract target確認
未達SOC action=continue確認
stop reason=target_reached確認
stop時SOC >= target
```

target=100なら100以上でtarget_reached。

### `MONITOR_CUTOFF_BEFORE_TARGET`

```text
stop reason=monitor_cutoff
final SOC < target
```

### `SOC_UNAVAILABLE_BEFORE_TARGET`

```text
stop reason=soc_unavailable
```

### `RUNTIME_ERROR_OR_READBACK_FAILURE`

exception/read-back mismatch等。

### `LOG_EVIDENCE_BLOCKED`

executionはあるがcontract/SOC/stop reasonを取得できない。

`Completed successfully`だけをEXACT_TARGET_CONFIRMEDにしない。

## 10. 03側追加修正禁止

Part Aがcutoff/unavailable/errorでもこのtaskでは以下を変更しない。

```text
app/runtime/cloud_job.py
app/runtime/soc_reading.py
app/runtime/night_soc_time_contract.py
app/kpnet/**
```

別SOC incident候補として最終報告し、dashboard修正は継続する。

# Part B: 朝7時SOC警告を正しい観測値へ変更

## 11. 方針

新Firestore collection/schemaは作らない。

既存:

```text
monitoring_samples -> forecast_hourly actual merge -> dashboard warnings
```

を使う。

`forecast_hourly`へ各hourの先頭サンプルを追加し、07時bucketの最初のSOCが**07:00ちょうど**の場合だけ朝SOC判定に使う。

理由:

```text
07時bucket latest SOC -> 07:30等の放電後になり得る
06時bucket latest SOC -> 06:45まで充電した場合に早すぎる
```

従って07:00 exact sampleだけ使う。

## 12. `app/dashboard/service.py`

`merge_forecast_hourly_actuals()`で既存:

```text
latest_sample_at
actual_soc_percent = 最新有効SOC
```

を維持し、追加:

```text
first_sample_at
opening_soc_percent
```

契約:

```text
first_sample_at = hour内最初のmonitoring sample timestamp
opening_soc_percent = hour内最初の有効SOC
```

有効SOCはfiniteかつ0..100。

既存`actual_soc_percent`の意味変更禁止。

## 13. Firestore backend

`_firestore_forecast_hourly_between()`はraw monitoring rowsを`merge_forecast_hourly_actuals()`へ渡すため、新Firestore schema/write不要。

各forecast rowに:

```text
first_sample_at
opening_soc_percent
```

が追加されることをtestする。

## 14. SQLite backend

`load_sqlite_query_snapshot()`のhourly queryへ追加。

```text
first_sample_at = MIN(ts)
opening_soc_percent = hour内最初のnon-null soc_percent
```

opening SOC用windowは`ORDER BY ts ASC`。
既存latest SOCは`ORDER BY ts DESC`のまま。

## 15. PostgreSQL backend

`load_postgres_slice()`もSQLiteと同一field/semantics。

```text
first_sample_at = MIN(ts)
opening_soc_percent = hour内最初のnon-null soc_percent
```

backend間でfield名・意味を完全一致。

## 16. `app/dashboard/warnings.py`

`build_dashboard_warnings()`へ`forecast_hourly`引数追加。

exact helper:

```python
def _morning_target_soc_observation(
    forecast_hourly: list[dict[str, Any]],
    *,
    date_iso: str,
) -> tuple[float, str] | None:
```

固定処理:

1. row date == date_iso
2. row hour == 7
3. opening_soc_percent finite 0..100
4. first_sample_at parse
5. timestamp date == date_iso
6. local clock == **07:00:00 exactly**
7. 成立時だけ `(opening_soc_percent, first_sample_at)`
8. 不成立は`None`

07:30を07:00観測として使わない。

## 17. `soc_target_unreached`

現行:

```python
pv_charge_end_soc_percent + 5.0 < target_soc
```

を削除。

`pv_charge_end_soc_percent`をこの警告に一切使わない。

新判定:

```text
exact 07:00 observationあり
AND observed_soc_percent < target_soc
-> soc_target_unreached
```

5pt marginを入れない。

warning codeは`SOC_target_unreached`ではなく既存exact code:

```text
soc_target_unreached
```

を維持。

タイトル:

```text
朝7時目標SOC未達
```

message:

```text
<date> 07:00のSOCが目標より低いです。
```

detail:

```text
date
target_soc_percent
observed_soc_percent
observed_at
source = monitoring-sample-07:00
```

`pv_charge_end_soc_percent`はdetailへ入れない。

## 18. 07:00 exact sampleがない場合

```text
soc_target_unreachedを出さない
```

06:30/07:30/PV終了SOCへfallback禁止。

# Part C: settings event不在の誤警告修正

## 19. event-backed helper

`app/dashboard/warnings.py`へ:

```python
def _has_plan_specific_settings_event(latest_schedule: dict[str, Any]) -> bool:
```

True条件は最低1つのplan-specific event evidence。

次のいずれか:

```text
recorded_at 非空
settings_completed_source_doc_id 非空
settings_completed_run_id 非空
schedule_source in {03-monitor, 03-dynamic, 03-no-charge}
```

ただし:

```text
status=fallback-default
AND event evidence全空
```

はFalse。

`plan_date`だけではTrue禁止。

## 20. `settings_completion_unconfirmed`

新条件:

```text
has_plan_specific_settings_event == true
AND settings_completed == false
AND status not in SETTINGS_COMPLETED_STATUSES
```

standalone 03で:

```text
fallback-default
schedule_sourceなし
recorded_atなし
settings eventなし
```

を設定失敗扱いしない。

warning messageは:

```text
記録された設定イベントに正常完了を確認できません。
```

## 21. `monitor_schedule_missing`

同じevent-backed条件を追加。

standalone 03 + fallback-default + plan-specific eventなしなら出さない。

plan-specific eventはあるがschedule情報だけ欠落している場合は維持。

03 runtimeへschedule persistenceを戻さない。

## 22. `app/dashboard/slice_assembler.py`

`dashboard_warnings()`へ`forecast_hourly`引数追加。

`build_dashboard_slice()`から:

```python
raw.forecast_hourly
```

を必ず渡す。

backend-specific warning分岐禁止。

# Part D: 必須tests

## 23. test file

原則:

```text
tests/test_dashboard_data.py
```

だけ。既存構造上不可避なら既存dashboard test fileを1つ追加可。新規test fileは禁止。

## 24. 必須test名

### 24.1

```python
def test_soc_target_warning_uses_exact_0700_soc_not_pv_charge_end_soc():
```

fixture:

```text
battery date=2026-08-29
target=100
pv_charge_end_soc_percent=56
forecast hour=7
first_sample_at=2026-08-29T07:00:00
opening_soc_percent=100
```

expect `soc_target_unreached` absent。

### 24.2

```python
def test_soc_target_warning_fires_when_exact_0700_soc_is_below_target():
```

fixture:

```text
target=100
07:00 opening=93
pv_charge_end_soc=100  # 意図的に逆
```

expect warning present、observed=93、target=100。

### 24.3

```python
def test_soc_target_warning_does_not_substitute_0730_for_missing_0700_sample():
```

first_sample_at=07:30、opening=80、target=100 -> warning absent。

### 24.4

```python
def test_merge_forecast_hourly_actuals_keeps_opening_and_latest_soc_separate():
```

monitor:

```text
07:00 SOC=100
07:30 SOC=86
```

expect:

```text
first_sample_at=07:00
opening_soc_percent=100
latest_sample_at=07:30
actual_soc_percent=86
```

### 24.5

```python
def test_settings_completion_warning_is_suppressed_without_plan_specific_event():
```

fallback-default + plan_date set + completed false + recorded_at None + sourceなし -> warning absent。

### 24.6

```python
def test_settings_completion_warning_remains_for_event_backed_incomplete_run():
```

recorded_atあり + schedule_source=03-monitor + completed false -> warning present。

### 24.7

```python
def test_monitor_schedule_missing_is_suppressed_for_standalone_fallback():
```

night charge >0でもplan-specific eventなしならwarning absent。

## 25. backend parity

SQLite/Postgres/Firestore hourly rowsが同じfield契約:

```text
first_sample_at
opening_soc_percent
latest_sample_at
actual_soc_percent
```

を持つことを既存fixture/fake/query assertionで確認。

新しい実Postgres環境を要求するtestは禁止。

# Part E: 品質

## 26. code-quality-audit

pytest前にrepositoryのcode-quality-audit Skillを実行。

## 27. Ruff

```powershell
python -m ruff check `
  app/dashboard/warnings.py `
  app/dashboard/slice_assembler.py `
  app/dashboard/service.py `
  app/dashboard/firestore_repository.py `
  app/dashboard/sqlite_repository.py `
  app/dashboard/postgres_repository.py `
  tests/test_dashboard_data.py
```

formatter禁止。

## 28. focused

```powershell
python -m pytest -q tests/test_dashboard_data.py
node tests/test_dashboard_calculations.js
node tests/test_dashboard_modules.js
node tests/test_dashboard_bootstrap.js
```

## 29. related

```powershell
python -m pytest -q `
  tests/test_dashboard_data.py `
  tests/test_cloud_job_runner.py `
  tests/test_night_soc_time_ownership.py `
  tests/test_night_soc_protected_contract.py
```

protected tests failureをdashboard側で回避しない。失敗ならSTOP。

## 30. full quality

```powershell
python -m ruff check .
python -m mypy app scripts --show-error-codes
python -m pytest -q
python scripts/security_check.py
```

repository標準Import Linterが利用可能なら通常手順で実行。未導入toolをinstallしない。

# Part F: commit/push/CI

## 31. source commit

実装+tests:

```text
fix: align dashboard SOC and settings warnings
```

Part Aの生クラウドログは含めない。

## 32. 既存reportへのfollow-up

Part Aでstop reasonを確定できた場合のみ既存:

```text
docs/completed/reports/dashboard_warning_investigation_2026-09-01.md
```

へ短い`Follow-up: 2026-09-01 03 exact-target execution`節を追記可。

新しい`docs/completed/reports/`ファイルは禁止。

記録可:

```text
classification
contract target
final relevant SOC
stop reason
SOC source
sanitized timestamp
```

記録禁止:

```text
execution ID
project ID
account
secret/credential
raw unlimited log
```

別commit:

```text
docs: confirm 2026-09-01 03 stop reason
```

`LOG_EVIDENCE_BLOCKED`なら追記不要。

## 33. CodebaseMemory final refresh

source-bearing commit後、CBM利用可能なら1回だけrefresh/index。

生成:

```text
.codebase-memory/artifact.json
.codebase-memory/graph.db.zst
```

`artifact.json.commit`はartifact生成直前のsource-bearing HEAD。

artifact-only commitでHEADが進んでも二度目refresh禁止。

commit:

```text
chore: refresh CodebaseMemory dashboard warning graph
```

CBM blockedならartifact手編集/偽造禁止。

## 34. push

```powershell
git status --short
git pull --ff-only origin master
git push origin master
```

force push禁止。push後:

```text
HEAD == origin/master
worktree clean
```

## 35. GitHub Actions

最終stateのquality:

```text
completed/success
```

必須。failure/cancelled/timed_outならproduction禁止。

# Part G: production dashboard deploy

## 36. scope

このtaskでproduction source変更はdashboard only。

03 runtime/KP-NET/job scripts変更禁止。

runbook全文再読後:

```powershell
pwsh -NoProfile -File scripts/production_deployment_gate.ps1 -RunPreRelease
```

PASS必須。

## 37. deploy

StatePathは1つだけ。

```powershell
$statePath = "artifacts/deployment_state/production-$(Get-Date -Format 'yyyyMMddTHHmmss').json"

pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 `
  -SkipPreRelease `
  -DeploymentScope auto `
  -StatePath $statePath
```

`auto`がdashboard-onlyを選ぶことを確認。

battery jobs/settings roundtripを変更対象と判定した場合はchanged filesと矛盾するため低レベルcommandへ逃げずSTOP。

このdashboard-only taskで実機settings round-tripを強制実行しない。

## 38. deploy中断

外側PowerShell切断時:

```text
新StatePath禁止
新deployment禁止
state JSON手編集禁止
```

既存StatePathとcloud terminal stateをread-only確認し、runbookどおり同一StatePathでResume。

## 39. post-deploy確認

read-onlyでdashboard/API確認。

2026-08-29:

```text
07:00 SOC=100なら soc_target_unreachedなし
pv_charge_end_soc=56でも朝目標警告に使用されない
```

standalone no-event状態:

```text
settings_completion_unconfirmed がevent不在だけでは出ない
monitor_schedule_missing がevent不在だけでは出ない
```

他warningを消すための追加変更禁止。

# Part H: STOP条件

以下は即STOP。

```text
tracked user changes
non-fast-forward
CodebaseMemory graphに合わせるためsource改変が必要
07:00 exact sample取得に新Firestore schemaが必要
03 runtimeへFirestore persistenceが必要
23/03/07 ownership変更が必要
06:45/06:50/06:55変更が必要
KP-NET設定変更が必要
通常03 Job手動実行が必要
warning修正にcloud_job.py変更が必要
read-back strictness変更が必要
secret/credential tracked保存が必要
full test failureを無視しないと進めない
production gate failure
CI failure
DeploymentScope autoがunexpected battery job changeを要求
```

# Part I: 最終報告

```text
Result: COMPLETE | PARTIAL_CBM_BLOCKED | BLOCKED

Baseline:
- starting HEAD:
- source commit:
- optional report commit:
- optional CBM artifact commit:
- final remote HEAD:

2026-09-01 03 execution:
- classification: EXACT_TARGET_CONFIRMED | MONITOR_CUTOFF_BEFORE_TARGET | SOC_UNAVAILABLE_BEFORE_TARGET | RUNTIME_ERROR_OR_READBACK_FAILURE | LOG_EVIDENCE_BLOCKED
- target:
- final relevant SOC:
- SOC source:
- stop reason:
- normal manual 03 execution performed: NO

Dashboard SOC warning:
- old source: pv_charge_end_soc_percent
- new source: exact 07:00 opening SOC
- exact 07:00 missing fallback: NONE
- 8/29 target=100 / 07:00=100 / pv-end=56 regression: PASS|FAIL

Settings warning:
- standalone no-event false warning suppressed: PASS|FAIL
- event-backed incomplete warning preserved: PASS|FAIL
- 03 Firestore persistence added: NO

Quality:
- Ruff:
- mypy:
- focused pytest:
- related pytest:
- full pytest:
- security_check:
- GitHub quality:

CodebaseMemory:
- preflight:
- fallback used:
- final refresh:
- artifact source commit:

Production:
- pre-release gate:
- deployment scope:
- deployment state:
- dashboard post-deploy check:

Protected contracts:
- 23 ownership unchanged: YES
- 03 ownership unchanged: YES
- 07 ownership unchanged: YES
- 06:45/06:50/06:55 unchanged: YES
- KP-NET settings behavior unchanged: YES
- 03 Firestore/DB persistence added: NO
```

`Result: COMPLETE`は全品質、CI、dashboard deploy、post-deploy確認まで成功し、protected contract不変の場合だけ許可する。

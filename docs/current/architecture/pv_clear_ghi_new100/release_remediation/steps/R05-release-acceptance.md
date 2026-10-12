# R05 正規導入・同原本表示・実機復元の受入

状態：**PLANNED、今回deployment/修復/job/backup/browser/device試験0**。依存R00〜R04。対象はseparated runtimeの現行正規wrapper。ユーザーの本番導入承認は親が受領済み。承認を再要求するためのstageではなく、具体的gateと実測を揃えて実行するstageである。ただし新しいprepared repair state/復旧対象/時間帯overrideへの承認を推測しない。

## 入口・参照範囲

[README](../README.md)、[R01](R01-validation-contract.md)、[R02](R02-source-repair.md)、[R03](R03-derived-history.md)、[R04](R04-adapter-proof.md)、[gates](../../gates.json)、[production runbook](../../../../ops/PRODUCTION_DEPLOYMENT_RUNBOOK_JA.md)全文と `solar-production-deployment` Skillを実行直前に読み直す。今回このrunbook/Skillと下記wrapperを読んだ。過去成功state、HTTP200、provider status1、Job exit0は今回のphysical/control/asset成功を意味しない。

必要readsetは `scripts/deploy_production_from_env.ps1`→`deploy_separated_runtime.ps1`、`production_deployment_gate.ps1`、`deploy_control_jobs_image_only.ps1`→`run_control_postdeploy_live_probe.ps1`/`assert_control_probe_evidence.ps1`、canonical job/import/Drive wrappersだけ。R04のserver/source/SDK調査を再探索しない。

## 変更対象

今回source/script変更0。次回は既存release stage内へR02/R03を接続する限定差分だけを実装する。

| path/symbol | PLANNED限定差分 |
| --- | --- |
| `scripts/deploy_production_from_env.ps1` parameter/delegation | private repair state pathとReviewedStateSha256、AdapterProofFixturePathをseparated wrapperへ転送する一回引数だけ。新env key/credential/log本文を追加しない |
| `scripts/deploy_separated_runtime.ps1` parameters/state identity / `calculation` stage action | repair state path/hash/merged commitをresume identityへ結び、下記順序でR02/R03 wrapperを呼ぶlocal function仮称 `Invoke-PreparedMonitoringRepair` を追加。12stageの数/既存名は維持し、calculation successは全substep受入後だけ |
| 同state保存・resume照合 / `Invoke-Stage`のbuild/calculation skip資格 | repair receipt SHAをcalculation stageの既存resultへ関連付ける。repair journalはR02、唯一採用receiptはR05、release stateはhash参照だけ。buildは固定digest/fixture/proof receipt一致、calculationは固定採用原本/最終receipt一致を確認してからsuccess skip。旧successにreceiptが無い時は停止し、successという文字だけでskipしない |
| 同 `build` stage action | candidate image digestsをproof前にstate保存。planner同digestだけへstdlib direct-case runner/fixtureをread-only mountしR04 nativeproof、receipt照合後だけbuild success。python entrypointでapp/SDKはimage内。control/webへplanner SDK/pytestを要求しない。別digest/localSDK結果でskipしない。新stageは作らない |
| `scripts/run_cloud_job_from_env.ps1` forecast実行/確認 | 一回引数 `-ExecutionReceiptPath` を追加。forecast execution応答を既存executeのformat=jsonでprivate captureし、exact execution名で状態/ログを確認。latest list limit1を今回executionの証拠にしない。他slotの動作/実機操作は不変 |
| `docs/current/architecture/pv_clear_ghi_new100/steps/S10-backup-release.md` の「実装手順・分岐」1/7、「中断条件」G07、「出口・引継ぎ」 | 次の実装時に、本R05の候補build→同digest nativeproof→build success→planner以降という段階境界への参照を追加する。候補構築前はR04の準備/品質を必須とし、同digest実server未証明はbuild successとplanner以降を停止する。G07の実証条件、runtime/制御gate、G08承認は緩和しない。今回旧S10/gates.jsonの変更0 |
| `scripts/run_monitoring_source_repair_from_env.ps1` / `scripts/repair_monitoring_source.py` | R02/R03所有の新正式一回CLIを呼ぶだけ。ここで修復処理を二重実装しない |
| PLANNED `tests/test_separated_release_repair_order.py` | wrapperの固定commit/repair-before-calculation/control停止/resume regression。cloud/deviceはstub、実操作証明へ昇格しない |

CLI引数仮称 `-MonitoringRepairStatePath <private-local-state> -ReviewedRepairStateSha256 <sha256>` は今回修復を伴うreleaseに必須で、新恒久feature flagではない。中身はR02 prepareで固定し、差分reviewと対象承認receiptを先に得る。通常の修復なしreleaseへこのstateを自動探索/流用しない。state schema拡張はexisting deployment schema1内のoptional repair identity/receiptだけとし、既存state読取・stage statusを保持する。外部公開schema/新frameworkは0。

## 変更禁止

追加一回引数 `-AdapterProofFixturePath <private-fixture>` はR04 fixtureの入力経路だけで、別実行modeではない。fixture自身のfixture_sha256を除いたcanonical SHAをrelease stateのidentityへ固定し、Resumeで差替え不可。修復stateの2引数とは目的を混ぜない。isolated OCI/権限/credential配送はfixture preflightで実証し、不明ならbuild success前に停止する。

制御時刻、mode ownership、23/07 DB非依存、03 archive20秒/operation240秒/reserve60秒、HISTORICAL_FAILURE_LOCK、PV/load/SOC計算、料金式、public DTOを変更しない。scheduler/IAM/secret/new env key/new collection/new leaseは0。R01のSQL容量/SOC48不一致をignore/ApprovedCalculationPatchSha256だけで合格へ変更しない。G06のavailability/旧scenario不足をreplay捏造・deployment承認で解消したとしない。

settings用legacy TARGET_SOC=50をseparated runtimeのmode-only probeへ流用しない。新修復CLIを旧production image/sourceで実行しない。dashboard GETから修復/履歴rebuild/current昇格を呼ばない。画像pruningや予防rollbackを追加せず、現行wrapperの失敗時rollback/復元だけを守る。

## クラス・変数差分

NEW production classは0。wrapper local値は `repairStatePath/reviewedRepairStateSha256/repairReceipt/calculationReceipt/adapterProofFixturePath/adapterProofFixtureSha256`。snapshotPhase/校正更新stageは撤回。secret・project/account/resource識別子はGitignored `.env`を `scripts/production_env.ps1` 経由で読み、tracked docs/command/stdoutに展開しない。

固定対象はmerged40char commit、approved calculation patch hash（必要時だけ）、repair prepared state SHA、R04 server receipt SHA、run-private exact raw SHA/control version/forecast run、state SHA/pack SHA/expected generation。candidate生成時刻とcontrol採用時刻/実績更新時刻を分け、同vintage判定をdateのみへ弱めない。

既存deployment stateは `kind=separated_runtime_deployment/schema_version=1`、commit/approval/timeoverrideと12stagesを保持。successに加えて必須receiptのnative read-back/identity一致だけがskip資格。running/failedはterminal証拠確認後にresume。`control.status=running` はdevice exact restore確認まで停止。修復stateのunknown_commitとdeployment失敗を分け、手でsuccessを入力しない。repair journalは修復unit/history/verifyだけを所有、唯一採用receiptのbody/statusを複製せずSHA参照だけ。R05 PowerShellから修復journalへwrite0。

## 実装手順・分岐

1. R00 mandatory品質/秘密check、R01 common-field parity＋Firestore raw PV/load/容量/SOC strict、R04のtarget source契約/限定差分/local回帰/隔離server準備を完成。**候補digestが必要なR04 nativeproofは下記build内で実行し、PASS前はplanner以降へ進めない**。image未生成の段階で同digest証明済みとはしない。G04/G07/G06の未完を正確に列挙する。G06資料不足で必要採用判定が成立しない場合はその箇所でINCONCLUSIVE、全導入PASSとしない。prospective範囲で採用する判断が必要なら結果と限界を具体化して限定判断を記録し、historical gateをCLOSEDにしない。
2. R02 prepareで原本CSV manifest/source35/candidate13/予想daily・cost/rollback preimage/private stateを固定し、write0 dry-runを提示。deployment承認は受領済みでも、この新prepared stateの具体的差分・write scope承認は別receiptが必要。未受領ならそこだけ停止。R03の計画/試験とR04のfixture・隔離server準備/品質は候補build前に済ませ、同candidate digestの実server proofはbuild内で完了し、build successとplanner反映前の必須条件とする。
3. canonical `pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 -ValidateOnly` と `pwsh -NoProfile -File scripts/production_deployment_gate.ps1 -RunPreRelease` を実行する。修復CLI接続後の最新commitで品質/全必要test/security、`.env` ignored/unstaged、固定commit/backup/stateを確認。CLI引数/repair hashのValidateOnly転送回帰も通す。
4. `scripts/deploy_production_from_env.ps1 -StatePath artifacts/deployment_state/production-<timestamp>.json` を使用し、実装済repair CLI引数を付ける。外部targetを手組みしたgcloud commandで代用しない。現在未実装の引数を今実行しない。以下の既存12stageとcalculation内の限定順序を守る。

| 既存stage | 必須内容・止まる条件 |
| --- | --- |
| preflight | exact clean commit、canonical gate/backup/validation。過去commitのpassedをreuseしない |
| parity | 現行baseline parity＋R01二経路原本証明。今回既存48行不一致を解消する。repair後の再照合もcalculation内で必須 |
| inventory | 現行job/web image・operation state backup、実行中control/import/producerの確認。停止・取消権限を発明しない |
| build | 同commitからplanner/control/web immutable digest、secret/artifact非混入。digests state保存後、R04 nativeproofは同planner digestでstdlib runnerを実行しconstraints targetFirestore2.34/auth2.60/Storage2.19/grpc1.84との実測版一致を確認。control/webのrole別依存は保持。server/実行基盤不在はbuild INCONCLUSIVE/停止、nativeproof PASS前のplanner反映0。fixture/proof SHAでresume資格確認。R04のtests/g07/**転送除外を各active Cloud Build upload manifestで確認。既存3scope ignoreのtests/除外を保持し、ignore変更0 |
| planner | 新planner/Drive image反映・既存job idle確認。forecast jobのcommand/argsをpython planner_job_main.pyへ反映後、D直前に照合。旧imageから新repair CLIを呼ばない |
| calculation | **下記A→Fを実装して全て成功後だけsuccess**。修復前forecast実行を先行させない |
| soc_api_credentials | 現行configure wrapper、秘密非表示/既存責任だけ |
| control | 候補imageの既存dedicated live probe→physical restore証明→23/03/07 rollout。FAILならscheduled images未変更/既存rollback結果を記録 |
| web | fixed web digest、revision Ready/serving一致、bootstrap/history静的asset同原本受入 |
| smoke | canonical `run_cloud_job_from_env.ps1 -Slot 07 -DryRun`、SET無し |
| drive_backup | canonical Drive wrapper、generation/hash/read-backとcleanupを別判定 |
| verification | 6jobs role digestとweb digest/最新Ready=最新Created、job4条件/failedTasks0、display/device/backup receipt一致 |

calculation内 `Invoke-PreparedMonitoringRepair` は既存forecast job実行**直前**。A: 固定commit/新planner image/reviewed state照合→R02 source。B: R03 daily→cost→通常校正入力範囲確認、保存側read-back。中間overall=PARTIAL、fit/asset/Drive起動0。C: source/派生receipt/native versionと旧pack復旧bundleを再確認。D: canonical forecast jobを一回起動し、通常plannerが新予測/校正/archive/control CAS/投影/current bootstrapを所有。E: 採用確定receiptの原本を使い正式repair CLI `-Stage history` でhistoryだけfull rebuild。F: 投影read-back確定後、既存sync_validation_state.pyのfirestore-to-sqliteを同検証pathで再実行→唯一parity CLIへ `--end-date receipt.target_date --window-days 31` →repair CLI `-Stage verify`。days[receipt.target_date]がNEW_ADOPTED/PASS、run/rawSHA/version一致、new_required_days>=1/new_verified_days==required、current/history同原本、全修復/校正結果を確認してcalculation success。同期は既存production_env経由の正規wrapper環境内。KP CSVの報告-Dateを新run同期へ流用しない。

Dの通常取込はcurrent4日、日計/変更月costを含む。R03の9/20–21修復とは対象を分け、同一入力で修復計算を再起動しない。fresh inputに伴う通常Dの後続料金更新は許可済み正常workflowとしてsource/output hashesとexact executionで因果を記録する。D後も修復13payload/旧35不変、修復日計/September料金/October累積基準と採用plan金融入力の整合を再確認する。R03 fit0、D updater一回/fit・publish各最大一回。予測前asset公開、D後bootstrapの無条件再PUT、Drive二重起動0。

D再開は起動前intentと唯一の採用receiptで判定する。R05 local Invoke-PreparedMonitoringRepairが `${MonitoringRepairStatePath}.d/forecast-intent.json` にprepared SHA/planner digest/command,args/対象日/起動前current native versionと参照原本SHAを固定。canonical wrapperのExecutionReceiptPathは同directoryのforecast-execution.json。planner.mainへCAS確定後・投影前の既存identity項目だけの構造化成功logを追加し、exact executionログとcontrol immutable version/raw/archive/currentを照合できた場合だけR05がadopted-forecast.jsonを保存する。history/verifyは同固定pathのreceiptを読取、別CLI引数や別採用stageを作らない。release state/repair journalはSHA参照だけ。
送信後ACK不明でも候補identityログとnative採用が一致すれば採用済みとして投影だけ復旧。execution名不明、ログ前crash、候補複数、他run/current差替えで一意binding不能ならUNKNOWN/STOP、時刻/commitだけで採用を推測せずplanner再起動禁止。採用後E/F失敗のresumeはplanner.main/生成/archive/control CASを呼ばない。
既存currentはcontrol_plan_current/{target_date}、immutableはcontrol_plan_versions/{version}。REST側payloadはfields.payload.stringValueで、decodeは既存PlanStore契約を使う。exact executionはprivate metadata.name、status.conditions/status.failedCount、execution_name log labelとtarget/version/rawSHAを照合する。missingを成功/failed0へ変換しない。public要約にresource名を出さない。
同raw復旧はcontrol_plan_versions/{version}.payload→PublishedPlan.from_document、night_plan_decisions/{date}--{rawSHA}との一致→既存project_forecast_plan→project_plan_read_models→project_model_parameters→未完のcurrent bootstrapのみ既存publisher→history専用writer→read-back。原本はprivate固定pathへ保存し再open差替えを検証、成功済assetは同nativegeneration/hashなら再PUT0。この復旧接続は未実装。各projectionにはR04の同absolute deadlineを渡し、制御採用は繰り返さない。

この順序により、修復前billing欠損で初回forecast jobが失敗して修復stageに到達できない循環を避ける。source stage成功/cost失敗等のpartialはR02 stateに残し、resume時はsource read-backで一致確認後に未完派生へ進む。forecast job exit0でもprojection failure log/asset未公開があればE/Fは非0。

旧image rollback互換は現在**未検証**。control rollout前にinventoryで保存した旧immutable digestを使い、修復後rawと新/旧packのread-only decodeおよびsmokeを隔離fixtureで実行する。SET/新計算/書込は0。互換PASS後だけ現行wrapperの対象範囲内rollbackを復旧候補にできる。失敗・不能ならSTOPとして、具体的な不互換field/旧digest/対象roleと必要対策を別reviewへ渡す。新/旧calibration pack/currentや修復rawを無断で戻して互換を作らない。この確認は先制rollback実行ではない。

5. live probeは `deploy_control_jobs_image_only.ps1`→`run_control_postdeploy_live_probe.ps1 -SeparatedRuntime` の既存所有で実行。通常07:15〜22:29 JST、実行中controlが無いこと、dedicated probe job既存を確認。2026-10-13の03時動作を考慮した利用者指示に限り既存 `AllowOutOfWindowLiveProbe` を使う。override時も02:45–03:15、06:45–07:15、22:30–23:15の半開区間とactive/unknown controlを拒否し、既存時刻所有権・定時処理を変更しない。完全デプロイ後のSOC-only試験はR06の最新03 terminal成功証拠も必須とする。

FのNEW受入は `Assert-AdoptedForecastEvidence` が唯一parity CLIの `[forecast-evidence]` JSON一行をparseして検査する。`required_evidence_status/common_parity_status` は両PASS、NEW required>=1かつ全verified、各NEW dayはevidence_required/original/metadata/capacity=True・SOC17/17、採用日のtarget/raw SHA/control_version/runがadopted receiptと一致することをANDする。容量の24行検証はR01のcapacity_verifiedが担う。`post-adoption-evidence.json` とSHAをcalculation stateへ保存し、resumeもSHA・内容bindingを再検査してからfresh parityを行う。CLI flag追加・二重coordinator呼出は0。全履歴21日がINCONCLUSIVEでも必要scopeとNEW受入を別に記録し、履歴原本coverageを改ざんしない。今回のreleaseは `MonitoringRepairStatePath/ReviewedRepairStateSha256` を必須の呼出条件とし、省略した旧no-repair routeを使用しない。
6. `assert_control_probe_evidence.ps1` に従い forced/green各candidate map=`BatteryOperatingMode`のみ、changed_fields=`batteryOperatingMode`のみ、operation ID、requested=observed、readback_fieldsを検査する。hold_seconds=60、restore_verified=true、mutation_outcome≠unknownが必須。元snapshotへのexact restore/read-backを確認し、forcedにSocChargeMode、greenに追加SOC設定を書かない。source revision=release commit、原本SHA/freshness/同calculation generationをprobe execution logと照合する。
7. browser受入はGETだけ。初期31日/current、修復9/20/9/21の明示history navigation、PV/load/SOC/容量・learned/model診断・金融・夜間・missingの表示を原本と照合。48完整日表示はreview.complete_day=trueだけ、00/30片方欠けを0へしない、unknown provenanceをconfirmedへしない。23時保存SOC優先、0〜6参考推計labelと混同しない。初期request数/圧縮bytes/cache/ETag304/lazyhistory/asset-presentDB0をR04と同方式で実測する。cloud production bytes値は未計測なので数値を予告しない。
8. Drive前後generationをcanonical wrapperで取得し、raw/detail/fulltrace/完全packのoffline hash roundtripを検査。Driveに無いClearGhiStore current/state/actual/certはR03 private recovery bundleで別保全、明示選択restore proofを参照する。production current restoreはこの導入の自動処理ではなく、必要時に選択state/currentgenerationをreviewした別復旧操作。saved planから自動昇格0。
9. interruptionは同StatePath＋`-Resume`。runningを成功扱いせず、process/Cloud execution/元データnative read-back/device terminal restoreを先に調べる。ACK喪失unknownは同raw/version read-backで確定する。restore未確認なら新probe/controlを重ねない。途中asset/hash不整合は投影producerの固定raw再公開を優先し、生oldblob戻しや即rollbackを提案しない。

## テスト・PASS条件

以下**PLANNED**。品質gateを先に実行、今回testは実行していない。実cloud/deviceはcanonical wrapperのdeployment validation taskでありfake速度/HTTP応答に置換しない。

| 判定/仮test | exact PASS |
| --- | --- |
| `test_calculation_repairs_before_forecast_and_control` | source→daily/cost/inputcheck→D通常planner→E history-only→sync/R01/verify順。修復前forecast0、R03fit/asset0、Dcalibration一回、bootstrap一公開/history一公開、12stage名保持 |
| `test_repair_receipt_failure_blocks_control` | hash/commit/drift/unknown/daily/cost/asset failureでcalculation success0、control呼出0、既成功repair receipt保持 |
| `test_resume_requires_same_repair_identity` | 別state/hash/commitを拒否、running/unknown成功skip0、exact read-back一致で確定済unitだけreuse |
| `test_build_candidate_proof_precedes_planner` | buildがdigest生成→同digest/SDK nativeproof→receipt成功後だけplanner順。未知/別digest/未準備serverはplanner/control呼出0、source AST PASSだけでbuild success0 |
| `test_adopted_plan_resume_does_not_regenerate` | D採用後E/F失敗→同receiptの原本から未完投影/historyだけ回復、generate/archive/control CAS0。exact execution/log/native identity不足はUNKNOWN/STOP、latest別run/同commit別plan拒否、bootstrap成功済再PUT0 |
| `test_single_writer_and_target_window` | command更新後plannerentry一致/legacy拒否。R03修復scopeとD fresh-input料金更新因果、calibration範囲外STOP、immutable actualSHA照合。F同期後の同SQLpath/明示target日/NEW>=1、default昨日の未検査をPASSにしない |
| `test_build_resume_requires_same_proof_fixture` | fixture差替え、別plannerdigest、proof無し旧buildsuccessは停止。stdlib runner/role別SDK/readonly mount、proof前digests保存。imageへpytest追加0 |
| `test_previous_digest_reads_repaired_sources_without_mutation` | inventory旧digestで修復raw/newpack/oldpack read-only decode/smoke PASS、remote write/SET0。未知field/decoder不互換はSTOP、pack/current/rawを戻すwrite0 |
| `test_live_probe_evidence_mode_only_restore` | existingassertがrequested/observed/operation/candidate/changed fields/60秒/restoreを拒否・受理。sourcecommit mismatchedはrollout0 |
| runtime source/derived/current | R02 raw48＋old35不変/candidate13、R03 daily/cost/actual/pack/historical assets native/hash一致、同raw projection、R01 strict＋common parity drift0 |
| runtime release | 固定commit/digests/state success、Completed/ResourcesAvailable/Started/ContainerReady allTrue、failedTasks0。projection/asset log失敗無し |
| runtime physical | forced+green SET/requested=observed/60秒/exact restore、UNKNOWN無し。同vintage raw/producerrevision一致。Cloud Run status1だけは不足 |
| runtime read/backup | initial/lazy/DB0/cache実測、current/history同原本表示、修復旧日反映、Drive原本SHA、別current recovery証拠、cleanup成否別記録 |

新local回帰予定commandは `python -m pytest tests/test_separated_release_repair_order.py -q`。このNEW fileは未作成で、command実行済みとはしない。既存wrapper regressionもR00 mandatory suiteで維持する。実server suiteはR04 runnerに従い、実deviceは上記canonical releaseのcontrol stageに含める。

## 中断条件

現在のSQL容量/SOC48 gate失敗、原本hash/forecast generation/asset mismatch、repair prepared-state未承認/競合、G06必要採用根拠不足、固定commit不一致、品質未完、activecontrol、時間帯条件不成立、CloudJob4条件不足、physicalUNKNOWN/restore不足は、それぞれ該当stageで停止。R04は候補build前にfixture・隔離server準備/品質/target source契約が必須。同planner digestの実server・期限・writer証明未完はbuild successとplanner以降を停止するが、準備と品質が揃った承認scope内の候補構築を未生成digest証明不足だけで禁止しない。校正正常窓外、採用binding不明、history/最終receipt欠落、中間PARTIALだけの最終success、旧digest互換未証明を無視したrollback可能宣言も停止。停止理由と確定済stageを分け、不能をclosedとしない。

## 出口・引継ぎ

PASSはrelease commit/image/state、source修復receipt、同raw current/asset/browser/physical/backup/性能が全て揃う場合だけ。FAILは既知不一致、INCONCLUSIVEは準備/証拠不足またはcommit不明。G04/G07/G08を証拠ごとに更新し、G06 historical不明等は残るscopeを明記する。最小次手は今のR01不一致を解消し、R02prepared差分/R04隔離server証拠を具体化した後、この限定wrapper接続を実装すること。

# R04 実backendの競合・中断・復旧・性能を証明する

状態：**PLANNED、実装/試験/実server操作未実施**。依存R00/R01、修復を含むfixtureはR02/R03。関連G04/G07。現在の結果はINCONCLUSIVEで、文書完成をgate CLOSEDへ変換しない。

## 入口・参照範囲

正本は[調査](../research-adapter-proof.md)、[gates](../../gates.json)、[公開契約](../../publication-contracts.md)、[R03](R03-derived-history.md)。readsetはclear_ghi_store/clear_ghi_calibration_io、forecast_persistence、firestoreのparameter projection、separated_runtimeのplan_store/planner、dashboard snapshotsのpublication/readだけ。関連5testのfake/transport分類は調査表に固定する。親のCBM target＋1hopを使用、独自CLI起動0。

初めにinstalled SDK版、Java/gcloud component、loopback listener、対象DB/bucketの未設定を再確認。Java24確認済みだがemulator component install状態は未確認。emulator起動・依存追加は今回行わない。[公式emulator限界](https://docs.cloud.google.com/firestore/native/docs/emulator?hl=en)に従い、emulatorと実Firestoreを別receiptにする。

**localとproduction targetのSDK差を入口で分ける。** 今回読んだlocalはFirestore2.31.0/auth2.58.0。親のbuild配置調査では `constraints-runtime.txt:16` はFirestore2.34.0、同13行はauth2.60.0、Storage2.19.0/grpc1.84.0固定である。親が取得したtarget Firestore2.34 wheelのTransaction/BaseTransaction/_TransactionalをAST読取で照合し、API/state順序は下記保持契約と同型、begin/commit/rollbackの明示timeout不足もtargetで確認した。**PASSはsource構造読取のscopeだけ**で、helper実装/runtime/server対応済みではない。[原本SHA・method証拠](../../../../../../artifacts/analysis/release-remediation-sdk-20261012.safe.json)を参照。auth2.60 wheelは取得済みだがrefresh経路未調査、release候補と同container digest/SDK版でのnative proofも未実施。local2.31結果でこれらを代替せずINCONCLUSIVEを維持する。今回依存install/upgradeは0。

## 変更対象

以下は**次の実装時の限定許可候補**。今回production差分0。型/例外/API戻り形を保ち、generic SDK subclass/proxyや全経路のrewriteは作らない。

| symbol/path | 必要最小差分・責任 |
| --- | --- |
| `app.operations.forecast_persistence.project_forecast_plan`、`app.operations.firestore.project_model_parameters/_project_model_parameter_values`、`app.backup.plan_snapshot.project_plan_read_models/_update_plan_read_models` | readだけでなくbegin/commit/rollbackを含む有限transaction budget。同SDK request/transaction ID/write順を維持する一つの局所RPC lifecycle helperをforecast_persistence内で共有し、既存max_attemptsを3/3/1で保持。各entryへoptional deadline、default通常経路は維持。独立retry loopを作らない |
| `app.operations.forecast_snapshot.persist_forecast_snapshots/_persist_firestore` | project_forecast_planから同じoptional deadlineを渡し、snapshot doc.get/450以内batch.commitのtimeout=min(4,残秒),retry=Noneを限定追加。immutable id/first provenance/通常default不変。ACK不明は全対象snapshot IDのnative read-backまでUNKNOWN、未確定のまま投影成功にしない |
| `app.separated_runtime.plan_store.FirestorePlanStore.publish` | 全3attempt共有monotonic deadline60秒、GET/POST各min(残秒,既存上限)、retry開始前期限検査。timeout後commit unknownを固定version read-backで分類。pointer CAS/公開APIを維持 |
| `app.separated_runtime.planner.main` | server拒否/送信後不明を区別する局所log。CAS確定後・投影前に既存target_date/control_version_id/raw_plan_sha256の構造化成功logを追加しexact Cloud executionへbinding。その直後、最初のprojection前にprojection_deadline=monotonic()+60を一度生成しproject_forecast_plan/project_plan_read_models/project_model_parametersへ同値を渡す。snapshotへ下位伝播、途中reset0。新schema/env0。採用後復旧はR05の同raw projectionだけで通常mainを再呼出しない |
| `app.dashboard.snapshots.load_precomputed_snapshot` | cold downloadにもtimeout4秒/retry=Noneを明示。cache30秒、ETag/304、GET DB0を維持 |
| 同 `_upload_artifact` / `write_dashboard_snapshots` | 最後のsource確認後のlate mutationをupload後再照合で検出。drift時は成功receiptを出さず、固定rawによる既存producer再生成を最大1回。既存writer直列化の実証と組み合わせる。GCS CASだけでDBとのatomicityを宣言しない |
| `app.forecasting.clear_ghi_store.ClearGhiStore.restore_current` | 原則変更0。real server/full valid calibration fixtureを追加して既存選択hash/CAS/全原本証明を実行 |
| `app.operations.firestore.recalc_monitoring_daily_metrics` / `recalc_cost_daily_from` | R03所有の限定deadline/on_progress optional keyword改修を必須入力として受領。query.stream/batch.commitの各RPC timeout=min(4,残秒)/retry=None、送信後ACK不明/progress/read-backの実server fixtureをここで証明。式/450batch/既存戻り型（日計dict[str,int]、料金int）/default通常経路は不変、改修をここで重複実装しない |
| **NEW test-only** `tests/g07/adapter_proof.py` / `tests/g07/firestore_server_cases.py` / `tests/g07/gcs_server_cases.py` | 全てtest_ prefix無し、明示runner実行のみで通常pytest不収集。server不在をskipして全PASSにしない。下記private設定/receipt/guardのみ |
| 関連既存5test | 限定deadline/unknown/遅延race/完全pack fixture回帰を追加。R03 paging実装/fixtureは再実装せずimport・receipt参照 |

local2.31観察に加えtarget2.34 wheelで、`_begin(self,retry_id=None)` はdatabase/options、`_commit` はdatabase/writes/transaction ID、`_rollback` はdatabase/transaction IDをSDK APIへ渡すが明示timeoutは無いと確認した。局所helperはこのtarget requestを保ち、timeout=min(4,残秒), retry=Noneを明示する。installed library/global monkeypatch/new classによる動作変更は禁止。以下はtarget `_Transactional` / `_pre_commit` とAST照合済みの保持契約。実装後回帰とnative server proofは別に必要で、target runtime対応済みとは現時点で記録しない。

1. attempt開始前にtransaction cleanupを行い、前attemptのwrite batch/transaction IDを持ち越さない。beginへ `retry_id=first_id` を渡す（初回None、最初に取得したIDを後続attemptで固定）。
2. callbackを実行しそのattemptのwrite batchを生成、commitへ同transaction IDとwritesを渡す。callbackを理由なく二度呼ばない。
3. retry資格はread-write transactionの**commitが返したABORTEDだけ**。max_attemptsはcallerが既存契約の3/3/1を渡す正整数で、repair入力やenvから変更しない。readonly、begin/callbackの例外、permission/precondition/deadline/ACK喪失をABORTED retryへ読み替えない。
4. 成功commit後はcleanupし、server応答の `write_results=list(response.write_results)` / `commit_time` を既存Transaction objectへ反映、callback resultをそのまま返す。exhausted ABORTEDは最後のAbortedをcauseとするValueErrorで、成功resultを返さない。
5. 失敗経路はrollbackを有限cleanup予算で行い、rollbackのfinallyでcleanupする。rollback結果が不明なら元error/causeとcleanup不明を記録する。送信後のACK喪失はUNKNOWNで、ABORTED再試行とは別の固定raw/version read-backへ進む。

既存 `firestore -> forecast_persistence` import方向を保持し、plan_snapshotも同helperをimportする。helperからfirestore/plan_snapshotをimportしない。確認した直接importではcycleなし（night_plan_archiveからplan_snapshotへの逆edgeは無い）。新module/classや別deadline機構を作らず、三module import/直接依存assert＋mandatory import-linterで実装後確認する。実装でこの限定境界を守れない場合は具体的diffをreviewし、本段を未実装PASSにしない。

## 変更禁止

03/23/07の時刻・240秒operation・60秒reserve・20秒archive・DB非依存・mode ownership、historical lock、PV式/旧residual/温度/load、予測historical pair/06:30を変更しない。新production env key/collection/table/lease/HTTP schema/bucket/project/IAMは0。calibration currentをsaved planから自動生成しない。immutable rawをshared pathから再openしない。forecast更新にdashboard actual-only行を混ぜない。

source-map component/native timestamp/count/digest/8KiB、existing forecast/control identity、asset path/31日bootstrap/lazyhistory/DB0を保持。全history monitoring pagingと総52,608件・179日fixtureはR03所有。期限・sentinel・metadata超過をretry無限化、limit単純拡大、恒久no-op flag、oldasset永久保持でPASSにしない。

## クラス・変数差分

production new classは0。局所helper仮称 `_run_bounded_projection_transaction(client, callback, *, deadline:float, max_attempts:int=3)` は既存callback戻り形を返す。`deadline/remaining/attempt/transaction_id`はrun-private、環境設定へ出さない。projection callerが一つのabsolute deadlineをsnapshot保存・forecast投影・readmodel・parameter投影へ伝え、各helperで更新しない。全体60秒、期限後rollbackのみ別4秒cleanup予算。OS/DNS hard cancellationやtimeout時remote write0を保証せず、観測elapsedとunknown read-backを記録する。

snapshot/transaction/readmodel/parametersの期限漏れを同じ予算で検証する。制御採用後に期限不足なら元raw/versionをreceiptへ残して投影だけ未完とし、計算・control CASを再実行して埋め合わせない。通常deadline=Noneでは既存decorator/SDK default/戻りを維持する。

NEW test-only `BackendProofFixture` は対象/所有nonce・合成入力・期待hash一覧、`RpcRecorder` はrequest/read/write/bytes/elapsed/generation/errorのみ、`RaceBarrier` は指定boundaryでworkerを待たせる。recordにsecret/raw本文/project/account識別子は書かない。

test-only配置は `tests/g07/` に固定する。親の追加source調査では `deploy_separated_runtime.ps1:135` が `.gcloudignore-separated` を明示選択し、`.gcloudignore-separated` / `.gcloudignore-runner` / `.gcloudignore-dashboard` の3scope全てが `tests/` を除外する。planner/control/webおよびlegacy runner/dashboard DockerfileはCOPY app等を明示し、tests/はimage外。従ってignore変更は不要・許可対象外で、source上の転送除外経路は確認済み。各active Cloud Buildのnative upload manifest/実転送bytes検査は**PLANNED/未計測**で、実測0とは書かない。`.dockerignore` 自体はtests/を除外しておらず、ignoreを介さない直接local Docker buildではimage外でもcontextへtest fileが送られる既存限界を残す。この直接buildをcanonical Cloud Buildの転送証明に使わない。

private fixture設定はGitignored `artifacts/adapter_proof/<nonce>/fixture.json`（schema1）。必須keyは`kind`=emulator/isolated_cloud、`operation_id`、`backend`、`endpoint`（emulatorだけ）、`project_id`/`database_id`（private）、`bucket`/`prefix`（GCSだけprivate）、`owned_document_paths`、`owned_object_names`、`fixture_sha256`、`approval_receipt_sha256`（isolated_cloudだけ）。既存production `.env` keyを上書きせず、repository env loader/production_envによる既存production identityとの照合を行う。**実隔離DB/projectの選定とこのtest-only private設定の準備/権限は未設定、運用追加承認stageが必要**。production承認を隔離infra新設承認へ流用しない。既存test project/databaseを明示選び、production identityを流用した場合は起動前FAIL。既存GCS bucketを使用する場合でもこのoperation専用prefix外のwrite/delete0を必須にする。

local proof receipt schema1：`operation_id/source_commit/fixture_sha256/backend_tier/sdk_versions/image_digest/target_sdk_wheel_sha256/stages/cases/owned_paths/cleanup_status`。sdk_versionsは実行planner container内の実測で、target Firestore2.34/auth2.60/Storage2.19/grpc1.84との一致を検査する。control imageはFirestore/Storageを持たず、同SDKが全roleにあると要求しない。caseは`name/status/reason_code/attempts/reads/writes/request_bytes/response_bytes/elapsed_ms/raw_sha256/control_version_id/forecast_run_id/generations`。statusはPLANNED/RUNNING/PASS/FAIL/INCONCLUSIVE。production deployment state/HTTP DTO/DBにこのschemaを追加しない。

本番requirementsにpytestは無い。nativeproofはplanner immutable digestだけでstdlib direct-case/assert runnerを実行し、host品質/回帰のpytestとは分ける。container entrypointは明示python、runner/fixtureはread-only mount、app/SDKは同imageからimportする。pytest/新依存をimageへ追加しない。隔離OCI実行基盤は現在未準備でINCONCLUSIVE、別imageやlocal SDKで代用しない。fixture_sha256は自身のkeyを除いたcanonical JSON SHA。R05の一回引数からfixtureを受け、実行前後に同SHAを確認する。

## 実装手順・分岐

1. R00の品質前提、R01のstrict原本照合、R03のfullhistory paging差分とdaily/cost有限RPC/progress差分、親のtarget2.34 wheel原本API/state照合receiptを受け取る（source読取確認済み）。auth2.60 refresh経路と同image/runtime/native serverは未確認でINCONCLUSIVE、local2.31回帰から昇格させない。projectionの期限対策だけで修復全chain有限とは宣言しない。source期限差分を先に実装してmandatory品質を通す。fake→SDK-localtransportの既存回帰を通してから、release候補と同digest/実測SDK版のcontainerからserverへ進む。
2. `adapter_proof.py` preflightはfixture schema/hash/commit/approval/target identity、emulator loopback address、owned prefix/pathを検証。credentials/production `.env`をemulator clientへ渡さずAnonymousCredentialsを使う。RESTPlanStoreはtest instanceのrootをloopbackへ向け、production URLに出るrequestをsession guardで即FAILにする。real server clientはstub置換しない。SDK-localtransportへfallbackしてPASSとしない。
3. emulatorはlocalhost専用・operation-owned foreground subprocessで開始しPID/port/command/版をreceipt化。health/read-back完了後に試験、finallyでそのPIDだけ停止。既存daemonを止めない。server未準備はINCONCLUSIVE/非0で停止。third-party GCS emulatorは追加依存として今回導入せず、あってもrealGCSの証明には昇格しない。
   同digest nativeproofではtest runner/fixtureを隔離containerへread-only volumeとして与え、production app/SDKは候補image内からimportする。testをimageへCOPYし直した別digestを同候補と扱わない。credential/設定は隔離対象分だけ、production `.env`全体をmountしない。現在local docker command無しのため、このcontainer実行基盤も未準備ならINCONCLUSIVE。既存の承認された隔離OCI実行基盤を選定し、無断install/infra作成をしない。
4. isolated actual Firestoreは選定した非production DBの**既存collection名**で、合成日/nonce IDだけをseed。GCSは既存bucketの`adapter-proof/<operation_id>/`だけ。seed前に既存対象不存在を検査し、production path/canonical published asset/current/control pointerへのwrite0をguardする。実project/index/concurrency/権限が未確認なら実server stageへ進めない。
5. 基本fixtureは2target日×2generation、同day24PV/load、保存SOC17と同容量、control/raw identity、legacy sample5、完全decoder-valid calibration/site/basis/13pair/available_at/actual+cert originals。night/day境界と履歴は既存契約値を使う。R02のraw35＋candidate13、R03の8,526raw/179daily/3page/sentinel2 fixtureを同serverへ投入し、個別旧source-map上限も試す。
6. barrierを「pointer read→commit」「transaction read→commit」「final DB source read→GCS upload」「current original保存後→CAS」「bootstrap成功→history」に置く。old worker Aを止めnew Bを完了後Aを解放する。ABORTED/409/412、同版retry、input drift、legacy binding、削除は実server response/native versionで判定。乱数sleepでraceが起きたと推測しない。
7. worker crashをarchive後/採用ACK直前/採用後projection前/forecast後params前/pack後current前/asset片方後へ入れる。切断後のcommit結果はUNKNOWN。固定raw/versionとserver current/immutable objectsを別read-backしてaccepted/rejected/conflict/unknownを確定し、同版projectionのみresume。同版sample増分0、古版上書き0を読む。読めなければINCONCLUSIVE。
8. late DB mutation testでGCS後再照合がdriftを捕捉することを確認する。再生成が既存CASを通れるのは、変更componentのnative maxが前進しcount非減、他componentも既存monotonic条件を満たす場合だけ。古いdoc訂正でmax不変/digestだけ変化、または削除/count減の場合は保守FAIL/G04 OPENとして停止する。時刻/scope前進を偽装せず、guardを緩めて再生成成功としない。receiptへbefore/afterのmax/count/digestと拒否理由を保存する。捕捉だけではatomicity保証でない。全source producerとasset writerの呼出し/同時実行実態を列挙し、既存workflowによる直列実行・idle確認が可能な条件を実証する。成立しない並行窓はFAIL/G04 OPEN、運用停止や新leaseを無断追加しない。
9. restoreはDrive offline rawとClearGhiStore currentを別実行。完全pack/fulltrace bytesをDrive tempへ戻し、全file hash確認後のみready。storeは選択stateSHA→pack→全actual/cert originals→site/basis/target/as_of→expected generationCAS。corrupt/missingだけ回復、valid異版は拒否。旧current参照の自動昇格0。local複数writerはサポートと主張せず単一writer条件を検査する。
10. request pathはbrowser→HTML/versionedJS→bootstrap artifact→explicit navigation history artifact。GETでraw scan/Firestore fallbackしない。cold/warm/ETag304/31日initial/historylazyのrequest数、compressed bytes、GCS call/cache hit、DB read0、producer query/read/write/time/metadataを測る。cold timeoutも実遅延transport/serverで観測しhard cancellationとは呼ばない。
11. cleanupはowned exact document pathだけ、GCSはseed/read-backで得た**exact generation付きdelete**だけ。別generation/別hashなら削除せずINCONCLUSIVEを残す。operation-owned emulator/PID/temp path以外を止めない/再帰削除しない。消せなかったresourceを成功cleanupにしない。[GCS precondition](https://docs.cloud.google.com/storage/docs/request-preconditions)を維持する。

## テスト・PASS条件

**全てPLANNED。** server runnerは `python tests/g07/adapter_proof.py --fixture <private-fixture> --stage <preflight|firestore|gcs|restore|performance|cleanup>`、native server casesはrunnerが `firestore_server_cases.py` / `gcs_server_cases.py` の明示関数を直接呼びassert/receiptへ記録する。pytest import/call0。test_ prefix無しなので通常pytestで自動収集しない。host側の既存pytest品質/回帰は維持する。preflight不在をskipで隠さない。exit0=全要求PASS、exit1=FAIL、exit2=INCONCLUSIVE。現時点fileは未作成でcommand実行不可。

| 仮test/判定 | exact PASS |
| --- | --- |
| `test_projection_server_atomic_abort_retry` | 実Firestore commit response/native read-back、26writesが同generation、ABORTEDのみ最大3、同版sample5→6→6、古候補mutable write0 |
| `test_bounded_helper_preserves_transactional_lifecycle` | SDK-localtransport fixtureで初回commit ABORTED→次attempt成功：cleanup/begin(first_id)/callback/writes/commit順、次batch26件で52件累積0、結果/write_results/commit_time一致。3回ABORTEDはValueError cause=最後のAborted、rollback/final cleanup。begin/callback/readonly/precondition例外はretry0。commit ACK喪失はUNKNOWN→固定raw read-backでcallback再実行0 |
| `test_projection_helper_import_direction` | helperからfirestore/plan_snapshot import0、三caller→一helper、三module importとmandatory import-linter PASS。独立retry loop0、attempt契約3/3/1 |
| `test_snapshot_and_readmodel_share_projection_deadline` | snapshot get/commitとforecast/readmodel/parametersの同absolute deadline、max3/3/1、normal default不変。ACK喪失全snapshot ID read-back、readmodel read-before-write保持、不明ならsuccess0 |
| `test_normal_planner_passes_shared_projection_deadline` | CAS確定/identity log→一deadline生成→forecast/readmodel/parameters同値→snapshot下位伝播。defaultNoneのまま本番だけ無期限になる接続を拒否、control採用前deadline開始0/途中reset0 |
| `test_native_runner_uses_planner_stdlib_only` | native runner pytest import/call0、host pytest保持、同planner digest/SDK/readonly runnerとfixture。control/webへFirestore/Storageを要求0、別image/localSDK代替0 |
| `test_target_sdk_lifecycle_and_native_container_identity` | source読取済targetFirestore2.34 wheelSHA/API/state順序とhelper実装を回帰照合、取得済auth2.60 refresh経路を残予算/unknown契約に確認、native server実行container digest=release候補、container SDK版=runtime constraints。local2.31結果は別receiptでtarget証明へ流用0。refresh/runtime未確認はINCONCLUSIVE、API差未解決/digest版不一致はFAIL |
| `test_control_commit_ack_loss_readback` | 切断後UNKNOWN→固定control version/rawshaを実read-back。異版上書き0、acceptedならprojectionのみresume、physical control再実行0 |
| `test_gcs_generation_zero_and_pinned_read` | 実GCS create0競合拒否、read pinned generation、stale upload拒否、cleanup exactgeneration、scope外write/delete0 |
| `test_clear_ghi_server_crash_restore` | pack/state/original保存順、crash current保持、selected fullvalidmanifest復旧、wrongsite/latecutoff/valid異版拒否、全source原本SHA一致 |
| `test_drive_full_valid_pack_offline_restore` | canonical fullpack/run/raw bytes一致、全部local再読、改竄/conflictはwrite前拒否、多file crashはpartialと判定、自動current復旧0 |
| `test_late_source_change_and_split_asset_crash` | after-last-check mutationを検出して成功扱い0。nativeMax前進/count非減のcaseだけ再生成同rawが既存CAS条件で成功。古doc訂正max不変とdelete/count減は保守FAIL/G04 OPEN、scope/time偽装0・guard緩和0。既存writer直列条件、古history/cache binding誤認0 |
| `test_g07_files_excluded_from_build_transfer` | 各実際のcanonical Cloud Build upload manifestでtests/g07/** absent、image COPYへ追加0、既存3ignore不変。source除外経路確認とnative manifest/転送bytes実測を分離し、未計測なら測定caseはINCONCLUSIVE。通常pytest自動収集0、direct local Docker context転送の既存限界を別記録 |
| `test_transaction_and_rest_absolute_budget` | 明示全体60秒、RPC残秒上限/retryNone、cleanup別4秒まで。elapsed/request count記録、期限後通常RPC開始0、unknownをwrite0と誤記0 |
| `test_derived_query_batch_budget_and_ack_readback` | R03 absolute deadlineをdaily/costのquery.streamと450doc以内各batch.commitへ共有。既存式/既存戻り型（日計dict[str,int]、料金int）/通常default不変、各RPC min(4,残秒)/retryNoneを実SDK/serverで観測。batch ACK喪失はprogress UNKNOWN→対象output native/hash read-back、確定前stage success0、期限後追加batch0。paging/projectionだけPASSでも本case無しなら全chain INCONCLUSIVE |
| `test_fullhistory_server_budget` | R03全8,526/3page＋sentinel2がdigestに入り、revision60秒/upload24秒/metadata8192bytes以内。総52,608超・badcursorは拒否 |
| `test_artifact_read_path_budget` | asset present DBread0、bootstrapのembed/fetch重複0、historyはnavigation時のみ、304/body0とETag一致、immutable JS/cache維持、cold期限有限 |

fake/SDK/emulator PASSとreal server PASSを別caseに記録する。emulator限界のindex/lock/concurrency/size/timeoutはisolated actualFirestoreで検証する。production gzip/cache/device値はR05の実測であり本段の合成fixtureから推測しない。

## 中断条件

fixture/target/approval不明、target原本SHA違い/API-state保持回帰失敗、auth refresh期限未確認、nativeproof container digest/SDK版不一致、production identity流用、server未準備、canonical infra wrapper不能、deadline/unknown未確定、source後退/削除/limit/metadata無視、singlewriter未証明、cleanup scope外、mandatory品質未完は停止。response timeout、local transport、Cloud Job exit0だけでは実server PASSにしない。

## 出口・引継ぎ

R05へcommit/fixture/target検証receipt hash、tier別ケース、exact raw/state/calibration hashes、server native revisions/generations、性能before/after、cleanup残件を渡す。G07 CLOSEDは全必要実server・復旧・有限期限・読取性能がPASSの時だけ。G04は同vintage/実績source monotonicとwriter条件を満たすまでOPEN。未準備infraはINCONCLUSIVEとして対象選定/運用承認という最小次手を示す。

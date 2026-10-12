# R04 実backendの競合・中断・復旧・性能を証明する

状態：**source実装済み・local focused検証中、実サーバーnativeproof/本番反映は未実施**。依存R00/R01、修復を含むfixtureはR02/R03。関連G04/G07はOPEN。source/local回帰をnative実証へ変換しない。

## 入口・参照範囲

正本は[調査](../research-adapter-proof.md)、[gates](../../gates.json)、[公開契約](../../publication-contracts.md)、[R03](R03-derived-history.md)。readsetはclear_ghi_store/clear_ghi_calibration_io、forecast_persistence、firestoreのparameter projection、separated_runtimeのplan_store/planner、dashboard snapshotsのpublication/readだけ。関連5testのfake/transport分類は調査表に固定する。親のCBM target＋1hopを使用、独自CLI起動0。

ユーザー最新指示に従い、正式検証先は既存本番Firestore/GCSと既存dedicated Job。新project/DB/infraを作らず、owned synthetic scopeだけを使う。local SDK/transport/emulatorは実サーバー証拠を代替しない。[公式emulator限界](https://docs.cloud.google.com/firestore/native/docs/emulator?hl=en)も保持する。agentによる今回のcloud操作0、実行は親の正規wrapperに委ねる。

**localとproduction targetのSDK差を入口で分ける。** 今回読んだlocalはFirestore2.31.0/auth2.58.0。親のbuild配置調査では `constraints-runtime.txt:16` はFirestore2.34.0、同13行はauth2.60.0、Storage2.19.0/grpc1.84.0固定である。親が取得したtarget Firestore2.34 wheelのTransaction/BaseTransaction/_TransactionalをAST読取で照合し、API/state順序は下記保持契約と同型、begin/commit/rollbackの明示timeout不足もtargetで確認した。**PASSはsource構造読取のscopeだけ**で、helper実装/runtime/server対応済みではない。[原本SHA・method証拠](../../../../../../artifacts/analysis/release-remediation-sdk-20261012.safe.json)を参照。auth2.60 wheelは取得済みだがrefresh経路未調査、release候補と同container digest/SDK版でのnative proofも未実施。local2.31結果でこれらを代替せずINCONCLUSIVEを維持する。今回依存install/upgradeは0。

## 変更対象

以下の限定source差分を実装。型/例外/API戻り形を保ち、generic SDK subclass/proxyや全経路のrewriteは作らない。production操作は0。

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
| NEW `scripts/run_server_adapter_proof_from_env.ps1` | production_env/gcloud既存経路。専用settings-roundtrip Jobのidle確認→元spec保存→候補planner image/明示python/固定bundle loader argsだけ一時変更→exact execution→finally元image/command/argsへ復元read-back。env/resources/retry/IAM/Scheduler変更0、physical settings入口を呼ばない |

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

test fixtureはplain dict、同期は既存stdlib Barrier、NEW test-only `RpcRecorder` は実SDK instanceのcall/timeout/retry/write数/elapsedだけを記録。server応答を偽造しない。public summaryへsecret/raw本文/project/account識別子を出さない。private failure情報もtoken/class/SDK object dumpを含めない。

test-only配置は `tests/g07/` に固定する。親の追加source調査では `deploy_separated_runtime.ps1:135` が `.gcloudignore-separated` を明示選択し、`.gcloudignore-separated` / `.gcloudignore-runner` / `.gcloudignore-dashboard` の3scope全てが `tests/` を除外する。planner/control/webおよびlegacy runner/dashboard DockerfileはCOPY app等を明示し、tests/はimage外。従ってignore変更は不要・許可対象外で、source上の転送除外経路は確認済み。各active Cloud Buildのnative upload manifest/実転送bytes検査は**PLANNED/未計測**で、実測0とは書かない。`.dockerignore` 自体はtests/を除外しておらず、ignoreを介さない直接local Docker buildではimage外でもcontextへtest fileが送られる既存限界を残す。この直接buildをcanonical Cloud Buildの転送証明に使わない。

private fixtureはGitignored JSON/schema1、必須keyはkind=production_server、source_commit、operation_id（12–80文字lowercase/数字/hyphen）、backend、project_id/database_id/bucket（private）、target_date（今日+366日より未来）、prefix=`adapter-proof/<operation_id>/`、owned_document_paths（distinct2件以上）、owned_object_names（distinct2件以上）、fixture_sha256、approval_receipt_sha256。実Firestore writeは既存 `forecast_hourly_snapshots/<operation_id>-...` のexact allowlistだけ。count/operation_id/future dateのsynthetic payloadで、fixed model_parameters/real current/通常計算を更新しない。GCSはexact allowlistとowned prefix、bundleだけprefix下の固定SHA名。production identityを理由に拒否せず、承認scope/ownership不足ならFAIL。新production env key0、既存Job ADCを使用しIAMを追加しない。

local proof receipt schema1：`operation_id/source_commit/fixture_sha256/backend_tier/sdk_versions/image_digest/target_sdk_wheel_sha256/stages/cases/owned_paths/cleanup_status`。sdk_versionsは実行planner container内の実測で、target Firestore2.34/auth2.60/Storage2.19/grpc1.84との一致を検査する。control imageはFirestore/Storageを持たず、同SDKが全roleにあると要求しない。caseは`name/status/reason_code/attempts/reads/writes/request_bytes/response_bytes/elapsed_ms/raw_sha256/control_version_id/forecast_run_id/generations`。statusはPLANNED/RUNNING/PASS/FAIL/INCONCLUSIVE。production deployment state/HTTP DTO/DBにこのschemaを追加しない。

本番requirementsにpytestは無い。nativeproofは同planner digest、stdlib direct-case/assert runner。3test-only fileとfixtureをzipに固定し、GCS generation0 upload→read-back→SHA/generation pin loaderで専用Jobへ供給する。zip member集合は4file固定で、app/SDKはimage内からimportする。pytest/新依存/別imageを追加しない。継承PLAN_SOURCE_REVISIONが旧値でも一致と偽称せず、source_commitは固定fixture/buildとcandidate digestへbinding、runtime_plan_source_revisionは別観測。fixture_sha256は自身を除くcanonical JSON SHA、実行前後一致必須。

## 実装手順・分岐

証拠の実行stageを分ける。**local regressions**はSDK lifecycle/原本26write/full calibration pack/Drive offline復元/8,526paging/browser cache契約のfixture。**R05 build内native adapter**は同planner digestでowned2docs/2objectsだけの実SDK transaction/CAS/ACK/crash/deadline/checkpoint/cleanup。**通常D後のfull workflow受入**は実採用raw/pack・全actual/cert originals・Drive・native全history paging・bootstrap/history/browser cold/warmのR03/R05証拠。build adapter PASSでG07/G04全体をCLOSEDにせず、後段の証拠が揃うまでOPENを維持する。

1. R00の品質前提、R01のstrict原本照合、R03のfullhistory paging差分とdaily/cost有限RPC/progress差分、親のtarget2.34 wheel原本API/state照合receiptを受け取る（source読取確認済み）。auth2.60 refresh経路と同image/runtime/native serverは未確認でINCONCLUSIVE、local2.31回帰から昇格させない。projectionの期限対策だけで修復全chain有限とは宣言しない。source期限差分を先に実装してmandatory品質を通す。fake→SDK-localtransportの既存回帰を通してから、release候補と同digest/実測SDK版のcontainerからserverへ進む。
2. preflightはfixed commit/fixture hash/承認/未来日/owned exact pathsとprefixを検証する。本番clientで通常formula/forecast生成を呼ばない。SDKローカル応答stubでnative PASSとしない。
3. 正規server wrapperは専用Job/23/03/07/forecast/Driveのidleを確認し、既存Job全specをprivate保存。image/cmd/argsのみ一時overrideし、env/resources/retry/IAM/Schedulerを保持。固定generation/hash bundle loaderを明示pythonで実行する。KP-NET/settings entrypointへ入らずSET0。既存SA権限不足/未知executionは停止、新IAM/infraを足さない。
4. seed前にexact owned2docs/objectsの不存在を確認。実SDK native conditional commit/GCS generation CASを実行しread-back。実current/global coefficient/既存assetへのwrite0。cleanupはnative version/generation preconditionで自operation分だけ、最後にabsenceを再読する。
5. local fixtureは同day24PV/load/保存SOC17/容量、legacy sample5、完全decoder-valid calibration/13pair/actual+cert、R02 raw35+13、R03 8,526raw/179daily。build native fixtureへこれらproduction formula/datasetを投入せず、owned2docsのcount/vintage/future dateと2objectsのbytesだけを使う。全history数量/原本packの実server照合は通常D後のR03/R05受入に渡す。
6. native2clientの同update_time/generation条件writeをBarrierで開始し、片方だけ成功/他方precondition拒否を実read-backで確認。immutable batch create conflictもnative拒否を読む。production計算のsample/原本26writeはSDK-localtransport回帰と通常D原本照合で別に検証する。
7. test-only instance RPC recorderは実server commit応答の後だけclientへTimeoutErrorを返し、UNKNOWN→native read-back→同vintage callback write0を検査する。別workerが実commit後os._exit(23)、再workerは同markerを読んでwrite0。global SDK改変/偽server成功応答0。expired deadlineでbegin RPC0、native RPC timeout/retry/count/bytes/elapsedを記録する。
8. late DB mutation testでGCS後再照合がdriftを捕捉することを確認する。再生成が既存CASを通れるのは、変更componentのnative maxが前進しcount非減、他componentも既存monotonic条件を満たす場合だけ。古いdoc訂正でmax不変/digestだけ変化、または削除/count減の場合は保守FAIL/G04 OPENとして停止する。時刻/scope前進を偽装せず、guardを緩めて再生成成功としない。receiptへbefore/afterのmax/count/digestと拒否理由を保存する。捕捉だけではatomicity保証でない。全source producerとasset writerの呼出し/同時実行実態を列挙し、既存workflowによる直列実行・idle確認が可能な条件を実証する。成立しない並行窓はFAIL/G04 OPEN、運用停止や新leaseを無断追加しない。
9. local Drive fullpack/全originals復元回帰と、build native owned checkpointの選択generation復元を別結果にする。通常D後はR03/R05が実raw/fullpack/Drive offline read-backとstateSHA/全actual-cert原本を照合する。production current復旧は選択manifest/expectedgenerationの別承認手順で、自動昇格/valid異版置換0。native runnerのcheckpoint PASSはClearGhiStore全入力復旧やDriveの実証ではない。
10. local cache/DB0/ETag fixture、build native ownedobjectsのRPC/count/bytes/elapsed、D後の実bootstrap/history/browserを別測定にする。実request pathはHTML/versionedJS→bootstrap artifact→明示navigation history、GET rawscan/Firestore fallback0。R03/R05がcold/warm/304/31日initial/lazyhistory/圧縮bytes/nativepagingを実測しG04/G07へ記録する。ownedobjectsの時間を本番画面時間へ転記しない。hard cancellationは保証しない。
11. cleanupはowned exact document native update_time、GCS exact generationでのみdeleteしabsence再読。別generation/versionは消さずFAIL。finallyで専用Job元image/command/argsへ復元しenv/resources不変もread-backする。復元不明なら次のproof/physical probe禁止。自分が作成したbundleだけexactgeneration cleanup、再利用した既存bundleは削除しない。[GCS precondition](https://docs.cloud.google.com/storage/docs/request-preconditions)を維持する。

## テスト・PASS条件

native runner/casesは実装済み、**実サーバー実行はPLANNED/未実証**。正式入口は `scripts/run_server_adapter_proof_from_env.ps1 -ImmutablePlannerImage <digest> -FixturePath <private-fixture> -ReceiptPath <private-receipt>`。runnerはstage=allで全native storage casesとcleanupを実行する。個別stageはpreflight/firestore/gcs/restore/performance、standalone cleanupは既存native receipt選択なしではINCONCLUSIVE。pytest import/call0、通常pytest自動収集0。host品質/回帰を先に満たす。exit0=選択scope全PASS、exit1=FAIL、exit2=INCONCLUSIVE。ここでのstorage primitive PASSは通常D/画面/実機の受入を代替しない。

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

fake/SDK/emulatorとactual serverを別記録する。owned native storage primitiveのPASSを、実current calibration/原本backup/browser/deviceのPASSへ拡大しない。通常Dの実原本/実際のbootstrap-history/read-pathはR03/R05で検証する。auth2.60構造読取ではtimeoutがbefore_request/refreshにも渡りTimeoutGuard残予算を再requestへ伝える。native runnerは新SDK clientの最初のowned Firestore GET/GCS reloadを再利用し、前後のcredentials.validのboolと当該RPCのelapsed_msだけを記録する（追加API0、token/raw値出力0）。False→Trueならactual ADC initial refresh observedをPASS、遷移を観測しない場合は当該caseをINCONCLUSIVEとし、runner全体もPASSへ昇格しない。elapsedは認証と当該読取を合わせた実測であり、実行前の現時点では未実証。OS/DNS hard cancellation/remote commit cancellation保証無し。

## 中断条件

fixture/target/承認scope不明、owned allowlist違反、target原本SHA違い/API-state保持回帰失敗、native runtime SDK/digest不一致、専用Job/SA未準備、auth refresh/elapsed期限未実証、deadline/UNKNOWN、source後退/削除/limit/metadata、Job復元/cleanup未確定、mandatory品質未完は停止。production identityを理由に拒否しない。SDK token/class dumpは禁止。failureは安全なcategory/step/private frame情報を保存、response timeout/Cloud Job exit0だけでnative PASSとしない。

FAILの最小診断としてadapter_proof.mainの既存安全builderを再利用し、receipt/structured stdout/failure.private.jsonへfailure_chainとfailure_chain_truncatedを記録する。__cause__/__context__をroot/cause/context・parent indexで結び、unique exception最大8nodes、各traceback最後32framesで打ち切り、同じ例外objectの循環を除外する。各nodeはexception_type/category/relation/parent/frames、frameはbasename/file・function・lineだけで、message・絶対path・locals・auth/resource値を含めない。これによりfinally cleanupがprimaryを置き換えた際も両frameを照合できる。classes/flags追加0、servercase・実SDK操作・timeout・retry・cleanup・FAIL exit非zeroは不変。cases0をwrite0へ解釈せず、全runの期限保証や本番timeout修復へ昇格しない。

実safe chainから確認したharness修正はRpcRecorder.__getattr__のbatch_get_documents戻り値だけをiter(list(response))とし、SDK DocumentReference.getのnext(iterator,None)契約を保持する。eagerなstream消費・計測・RPC timeout/retry・commit後ACK欠落注入は保持する。runの既存recorder復元をfinallyの先頭へ移し、cleanup RPCではoriginal transportを使う。Firestore document.deleteはcommit RPCを使うため、未消費のdrop_commit_ackをcleanupへ持ち込まない。flagを強制解除せずbindingの対象範囲を分ける。ローカルactualSDK2.31のfake GAPIC/no-network回帰と、target2.34 wheel/source419のiterator契約・native frameを別証拠にする。app/SDK version/全期限/retry/protected source変更0、全workflow proofは次native receiptを待つ。

GCS2.19のBlob.reloadは既存blob.generationをqueryへ含める。別clientのCASが世代を更新してもinitial Blobの古いgenerationは自動更新されないため、winnerのupload ACKから(payload,generation)を返し、同generationを指定した新Blobでread-backする。generationをlatest無条件GETから推定しない。initial/checkpoint/restore uploadのACKgenerationもread-back前に保持し、cleanupはその選択済generationだけを削除する。本文はoperation_idのraw UTF-8、CAS winnerは同rawに-a/-bを付した値であり、別のownership metadataを追加しない。actualStorageSDK2.19/fake resource callbacksのローカル回帰は世代非保持bucketモデルで競合・原本checkpoint復元・exact cleanup・各ACK後読戻し失敗を検証する。native server proof、ADC refresh、fullpack/Drive/browser証拠へ昇格せず、全timeout4/retryNone/世代条件を保持する。

## 出口・引継ぎ

R05へbuild adapter receipt/SDK版/digest/fixture SHA/owned native revisions/generations/cleanupを渡す。raw/state/pack/全originals/Drive/nativepaging/browserの後段receiptはR03/R05が取得する。G07 CLOSEDは両stageの必要証拠が全てPASSの時だけ、G04もfull workflowの同vintage/実績source/writer/性能が揃うまでOPEN。source/local回帰は実serverの代用にしない。

# 実adapter・公開・復旧の調査正本

基準 `1dd8786`、2026-10-12 JST。今回実施したのは限定source/既存test/installed SDKの読取とローカル環境の非通信確認だけ。**試験実行、emulator起動、cloud接続、依存install、production mutationは0。** 下記の追加試験・source差分はPLANNED。実行仕様は[R04](steps/R04-adapter-proof.md)、導入仕様は[R05](steps/R05-release-acceptance.md)。

## 証拠の強さと現在の到達点

| 読んだ既存test | 実体 | 証明するもの / 証明しないもの |
| --- | --- | --- |
| `tests/test_clear_ghi_store.py` | 実temp filesystem＋FakeStorage/FakeBlob、時刻/replace/linkの差し替え | 原本hash/順序/ローカル中断/論理CAS/復旧。GCS server generation競合、複数processのlocal CASは未証明 |
| `tests/test_forecast_projection_generation.py` | 実Firestore Client、`_firestore_api_internal`をlocal SdkTransportへ差し替え | BeginTransaction/BatchGet/Commit protobuf、transaction ID、26write、同版sample 5→6→6。server atomicity、ABORTED retry、isolation/期限は未証明 |
| `tests/test_dashboard_publication_generation.py` | 論理PublicationBlob/RevisionClient＋実GCS ClientのHTTP callback差し替え | multipart `ifGenerationMatch=0`、history GET `ifGenerationMatch=7`、旧版拒否/source drift。実network/server、DB→GCSの並行窓は未証明 |
| `tests/test_control_publication_provenance.py` | 注入sessionのget/post応答 | REST currentDocument/409分岐。server commit、ACK喪失、actual writer間raceは未証明 |
| `tests/test_pv_clear_ghi_backup_roundtrip.py` | fake Firestore/Storage＋offline temp file | Drive schema2の原本bytes/full JSON roundtrip。fixture packはproduction decoderの全training/usable要件を満たす証拠ではない |

この表は今回読んだ実装の分類であり、今回のtest PASS記録ではない。既存gatesのローカル実行記録を再実行したと表現しない。G04/G07はOPEN。親のCBM ready8833 nodes/31107 edgesのtarget＋1hopをroutingに使い、独自CLI/daemonを起動していない。

## 未準備環境を具体的に分ける

installed versionsはFirestore 2.31.0、Storage 2.19.0、google-api-core 2.38.0、google-auth 2.58.0、grpcio 1.84.0。`gcloud.ps1`/`java.exe`は見つかり、Javaは24.0.2。docker commandは見つからない。`FIRESTORE_EMULATOR_HOST`/`STORAGE_EMULATOR_HOST`は未設定、確認したloopback用port 8080/9090/4443にlistener無し。別portの存在、gcloud emulator componentのinstall状態、隔離cloud project/bucket、権限・請求は**未確認**。Java不足と断定せず、現在のJava21以上要件は満たす。

これはlocal観察版であってproduction targetではない。親の追加調査では `constraints-runtime.txt` の16行はFirestore2.34.0、13行はauth2.60.0、Storage2.19/grpc1.84固定。親がtarget wheel原本を取得し、Transaction/BaseTransaction/_Transactionalだけをread-only ASTで検査した。[安全なmethod/SHA証拠](../../../../../artifacts/analysis/release-remediation-sdk-20261012.safe.json)のmodeはwheel_source_read_only、runtime_versionは対象版2.34.0を示し、実runtime実行を示さない。Firestore wheel SHA256は `a75ae79cb2567daae417538dfbfe5d56404754f51483083e43d995b25947d5eb`。auth2.60 wheel SHA256は `e78414cd2e22cc02884768707def024ca32da17695f2c0e6194cdbe1316c4cd4`（取得/hash確認だけ）。

**target2.34構造確認はPASS/source読取のみ**。beginにretry_id、commitにdatabase/writes/transaction ID、rollbackにdatabase/IDを渡すが明示timeoutは無い。_pre_commitはcleanup→begin(retry_id)→current ID/初回retry ID保持→callback、commit ABORTEDだけread-writeでretry、exhaustはValueError from最後のAborted、失敗rollback/finallycleanup、成功cleanup→write_results list/commit_time反映というR04保持契約と同型だった。対象SDKにも有限期限不足があるというsource根拠は確定した。

auth2.60 wheelは取得済みだがrefresh経路未調査。helper実装、同候補image digest/runtime版、emulator/actual server、elapsed/remote commit結果は未検証でINCONCLUSIVE。local Docker command不在という実image検証の未準備も残す。2.31の実行結果やtarget wheel ASTをnative proofへ昇格せず、依存install/upgradeを今回実施しない。

親が当日取得した一次資料では、[Firestore emulator](https://docs.cloud.google.com/firestore/native/docs/emulator?hl=en)はproductionのtransaction/concurrency、timeout、size、index要件を完全再現しない。したがってemulatorで通っても実Firestoreの試験を省略してG07をCLOSEDにしない。[Firestore transaction制限](https://firebase.google.com/docs/firestore/manage-data/transactions)のlock20秒、total270秒、idle60秒は、本アプリの全体60秒予算とは別。GCSの[request preconditions](https://docs.cloud.google.com/storage/docs/request-preconditions)はgeneration0の不存在createと正確なgenerationによる更新/削除を規定する。今回資料取得は親担当で、ここで外部serviceに接続していない。

## 有限期限の実装差

| source・symbol | 観察した有限性 | 残る穴 |
| --- | --- | --- |
| `app.forecasting.clear_ghi_store.ClearGhiStore` | monotonic絶対deadline既定60秒、cloud各RPC min(4,残秒)/retry=None | 境界で期限検査するcooperative方式。OS/DNS/remote commitをhard cancellationした保証はない。local単一writer前提 |
| `app.operations.forecast_persistence.project_forecast_plan` / `firestore._project_model_parameter_values` | read timeout3/retry=None、transaction max_attempts3 | installed SDKのbegin/commit/rollbackにはcall siteからtimeout/retryが渡らず、全体deadline無し。readの有限性だけで全transaction PASS不可 |
| `app.separated_runtime.plan_store.FirestorePlanStore.publish` | GET/POST各 timeout=(10,60), max_allowed_time=60、CAS最大3回 | 6requestまであり、全publish60秒上限ではない。AuthorizedSessionはmax_allowed_timeをcall境界で検査し、長いrequestが上限後まで続く可能性を明記している |
| `app.dashboard.snapshots._upload_artifact` | 全体24秒/max3、各RPC min(4,残秒)/retry=None、retry毎source再読 | 最後のDB source確認からGCS commitまで非atomic。DBがその間に進めばGCS CAS単独ではfreshnessを保証できない |
| 同 `_read_dashboard_source_epoch` | revision全体60秒、各RPC min(4,残秒)、8KiB metadata、sentinel | monitoring全history 8493件で3660上限超過を親が実データ確認。R03のpaging/総52608上限へ接続し、期限内取得を実測する |
| 同 `load_precomputed_snapshot` | 30秒memory cache | cold `download_as_bytes()`のtimeout/retryが明示されない。warm cacheのDB0はcold読取期限の証明ではない |

SDK private transaction hookをもし局所adapterで制限する場合も、installed SDKをglobal monkeypatchせず、同request/protobuf/transaction ID/rollback/例外を維持する回帰が必要。サーバーへの送信後timeoutは「不採用」と同義ではなく、exact raw/versionのread-backが必要。R04がこの追加実装入口を定義する。期限超過のresponseだけからremote write0と断定しない。

## immutable、mutable、並行writerの境界

ClearGhiStoreはactual/cert原本→pack→state manifest→current CASの順。hash付き原本はcreate generation0、既存原本はexact bytes照合。readはreload取得generationでpinする。同(date,source SHA)を検証済previous stateからreuseする場合、原本再upload/readを省略する。これは毎run全原本の存在を再検証する契約ではなく、選択manifestの明示restore時に全参照原本を検証する。

forecast projectionはprivate fixed rawからimmutable hourly snapshotを先に保存し、current26writeをtransactionで進める。model parametersは同raw retryでsample二重加算を避け、legacy→採用bindingだけ追加できる。control REST commitはversion docとcurrent pointerを同commitへ入れ、pointerのupdateTime/existsでCASする。version doc自体にcreate-only preconditionは無い。内容addressed versionの固定bytesと同version payload一致をserver read-backで検査する。

plannerはarchive→control採用→forecast/readmodel/parameter/dashboard投影。採用後投影失敗をlogしてもreturn0になり得るため、Job成功だけでasset成功を示さない。control commit ACK喪失もpublication_rejected logに入る可能性があり、そのlogはserver拒否/write0証明ではない。固定versionの採用read-back後に投影だけを再開し、controlや計算の再実行で履歴を作り直さない。

bootstrap/historyは別GCS objectとして公開される。片方成功後のcrashはmultiobject rollbackされない。原本PV/load/SOC/容量と既存run bindingを両object/画面で照合し、古history・cache・同時source訂正を含める。新manifest/leaseを増やして解決した扱いにしない。最終source-read→uploadの窓は実試験で検出し、R04の局所再照合と既存producer直列化条件を揃えるまでG04はOPEN。

## 復旧対象は二つのcurrentを混同しない

| 対象 | 現行復旧の実態 | 必須選択証拠 |
| --- | --- | --- |
| Drive schema2 raw plan | `drive.PLAN_BACKUP_COLLECTIONS`はnight_charge_plans/night_plan_decisions/forecast_plans/forecast_hourly_snapshots。embedded raw→offline `restore_snapshot_payload` | original raw SHA、detail SHA、target/run/control binding。全候補検証後local書込だがmulti-file crash atomicではない。完了後全file read-backが必要 |
| standalone calibration IO | `clear_ghi_calibration_io`のhash-named pack＋小current `pack_sha256` | selected pack SHA/site/basis/training/as_of/target妥当性。caller単一writer。下段store currentとschemaもpathも別 |
| ClearGhiStore combined current | state schema1：model/basis/site、calibration_pack/SHA、training_as_of、clear_days。hash state/pack/actual/cert originalsを背景保存 | state SHAを明示選択し、全原本read-back、13pair/available_at/aggregate SHA、expected generation、target/as_of検査後restore_current。valid異版currentを置換しない |

Drive rawのfulltrace/pack objectが戻っても、clear_daysと全actual/cert原本の取得・current復旧にはならない。既存backup schema/collectionを拡張せず、R03のprivate recovery bundleでstate/原本の選択hashを保全する。saved planからcoefficient currentへの自動昇格は0。

Drive embedのarchive downloadはbytes checksumを検査するが、GCS generation pinを呼出しで明示しない。従って復旧原本bytes一致は検査できても「そのGCS generationを読んだ」とまでは証明しない。R04 server fixtureは選択source generationを別receiptで記録し、既存Driveの保証範囲を拡大解釈しない。実Drive roundtrip/実GCS restore/多file途中crash/完全なproduction calibration fixtureの実行は未検証。

## 未完と次の最小作業

test-only build経路は親の追加限定調査で確認した。予定配置を `tests/g07/{adapter_proof.py,firestore_server_cases.py,gcs_server_cases.py}`（test_ prefix無し/明示runnerだけ）へ統一する。`deploy_separated_runtime.ps1:135` は `.gcloudignore-separated` を選択し、同ignoreと `.gcloudignore-runner` / `.gcloudignore-dashboard` は全てtests/を除外する。planner/control/webとlegacy runner/dashboardの明示COPYにtestsは含まれない。ignore変更は不要。**この既知routeはsource確認であり、native upload manifest/転送bytesは未実測**。`.dockerignore` にはtests除外が無く、直接local Docker buildではimage外でもcontextへ転送される既存限界がある。新配置を理由にこの限界を修正済み/全build転送0とは扱わない。

R00/R01のSQL parityと原本strict照合、R02/R03の修復・fullhistory pagingを先に完成させる。R04は上記期限/ACK不明/late upload穴の限定差分→mandatory品質→fake/SDK回帰→emulator→隔離actual server→性能/復旧の順。R05はそのreceiptを消費して正規releaseを実行する。歴史availability/旧scenario不足のG06は独立に残り、同vintage・economic採用判断を未検証のまま閉じない。

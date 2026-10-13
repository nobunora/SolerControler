# R03 日計・料金・校正・全履歴・backupの伝播

依存: R02 source receipt確定。文書の完成と、実装/SDK/本番全連鎖の完了は別。現在は調査とPLANNED仕様。
通常producerの式/GET/schemaを保ち、一回限りrepair runnerから既存計算を明示順序で呼ぶ。

10/13追加実装は修復costの範囲を閉じた過去暦日へ固定する。fresh prepareが9/1〜設定timezoneのTODAY00をexclusive範囲とし、JST10/13なら9/20訂正を10/12まで42日へ再集計する。開いている10/13 actual/costは通常Dが所有し、Dの正の実測appendを修復scopeへ含めない。strict prepared source/count/native revision・policy・baseline・既存SDK予算/保存unit/最終照合を緩めない。append再認証helper・専用receipt fields・carryover例外は撤回し、新helper/flag/classは追加しない。

通常Dのcost再集計は `workflow._ingest_firestore` のchanged_count>0が条件で、変更月の月初から実行し、その直前cost_dailyのcumulative baselineを読む。10月なら修復済9/30を基準に10月分を再集計する。changed_count0や既run経路ではcostを再計算しないため「Dが必ず開いている当日料金を更新する」とは言わない。今回の旧43行read-only診断は10/13を含むbusiness値の一致を示したが、新D後の受入は改めて確認する。

Fのread-only金融確認では、保持した旧43日scope/同policy/baselineを既存 `_source_range`・`_cost_expected`・`_matches` で再計算し、current-dayを含む全対象native cost_dailyのdate/self_consumption_kwh/savings_yen/cumulative_kwh/cumulative_yenを照合する（既存rel_tol=1e-10/abs_tol=1e-8）。source前後digestも一致を要求し、updated_atを金融値へ混ぜない。changed_count/cost再計算ログが存在すればexact executionへ関連付け、ログ不在を再計算成功へ推測しない。changed0なら既存当日値が修復済baselineで正しいことをreadbackで要求し、不一致/drift/不足はSTOP。これは金融受入用read-only検証でrepair endの拡張・raw/cost mutation・旧manifest/journalの書換えを行わない。current/history同原本・全source epochも既存最終条件を保持し、並行writerをatomicに防いだとは宣言しない。

## 入口・参照範囲

readset: [調査正本](../research-source-repair.md)、[R02](R02-source-repair.md)、[publication契約](../../publication-contracts.md)、[日計 (line 738)](../../../../../../app/operations/firestore.py)、[料金 (line 441)](../../../../../../app/operations/firestore.py)、[校正 (line 61)](../../../../../../app/energy_plan/clear_ghi_update.py)、[snapshot (line 384)](../../../../../../app/dashboard/snapshots.py)、[Drive scope (line 179)](../../../../../../app/backup/drive.py)。
保護正本とcommit `541cd60`の完全な直前請求期間、`93e8c3c`/`7899423`の当時forecast原本保持を先に確認する。
R02のprivate state/preimage/確定raw48slots/source revisionsを使い、時刻が進んだからcost末端やCSV候補を暗黙に拡張しない。
G07はfake/transportだけでは解決しない。実adapter競合/期限/readbackのR04証拠と各stageの検証を要求する。
R05の新コード反映後calculation内でR02 source→R03 daily/cost/校正入力確認を先に終える。その後Dが通常forecast/校正/current bootstrapを生成・採用し、Eが同じ採用原本のhistoryだけをfull rebuild、Fで全最終verifyする。予測前asset公開を撤回し、中間根拠は保存データのread-backだけ。controlは最終同世代asset/原本照合の後であり、本stageから起動しない。

## 変更対象

| qualified symbol | 種別 | 限定差分/入力 |
| --- | --- | --- |
| `app.operations.monitoring_repair.regenerate_derived` | NEW | 下記signature。既存日計/料金を直線的に呼び、before/after source digest・出力照合・stage receiptを記録。generic client/proxyを作らない。 |
| `app.operations.firestore.recalc_monitoring_daily_metrics` | EXISTING LIMITED | NEW optional deadline/on_progressを既存read/write点へ追加。calendar_dates={9/20}、dashboard_affected_dates={9/20,9/21}。式/450境界/既存戻りdictは保持。全履歴日計が別に必要なら14日単位の固定scopeで呼ぶ。 |
| `app.operations.firestore.recalc_cost_daily_from` | EXISTING LIMITED | NEW optional deadline/on_progressを既存read/write点へ追加。start_date=9/1、end_ts=prepare時に固定した終端、policyは既存PipelineConfig値。式/450境界/戻りintは保持。raw変更0でも独立cost stageとして呼ぶ。 |
| `app.energy_plan.clear_ghi_update.load_calibration_for_context` / `app.forecasting.clear_ghi_observations.observed_day_records` | EXISTING READ ONLY | R03は通常contextの対象範囲/修復raw/旧packを確認するだけ。校正取得・fit・current更新はR05 Dの通常planner一回が所有。corrected_dates/recheck_dates/旧日readerの予定追加を撤回し、既存sourceを変更しない |
| `app.dashboard.snapshots._read_dashboard_source_epoch` | EXISTING LIMITED | raw monitoring revision queryのみ下記paginationへ。native version/全id/digest/source scopes/期限を保持。他collectionの3660+1拒否は維持。 |
| `app.dashboard.snapshots.write_dashboard_history_snapshot` | NEW | 既存builder/validator/artifact/uploadを使うhistory専用一関数。bootstrapはメモリ内検査のみ、bootstrap gzip/PUT/filewrite0。既存write_dashboard_snapshotsとGET/defaultは不変 |
| `scripts/run_drive_backup_cloud_from_env.ps1` | EXISTING RECEIPT ONLY | 呼出ownerはR05。R03はprivate recovery bundleと前後receipt検査だけ、Driveを再起動しない。backup schemaを変えない |

## 変更禁止

- `calculate_daily_costs`、`apply_cumulative_baseline`、tariff/tier/window/請求締日、monthly_projection previous period、SOC目的関数を変えない。暦月料金と請求期間の違いを新しい単価/締日で解消しない。
- `recalc_battery_end_of_day_soc`/settings由来target、model_parameters.sample_count、過去forecast/plan/load原本・issued/run/calibration identityを再生成/上書きしない。
- `snapshots._validate_publication_payload`、`_check_source_epoch_order`、`_upload_artifact`のCAS/3tries/24秒、8KiB、cache/ETag/31日bootstrap/history遅延GETを変更しない。
- `ClearGhiStore.publish/restore_current`のimmutable原本検証/current CAS/monotonic cutoff、site fingerprint/basis/認証条件を緩めない。
- new runtime flags/env/IAM/index前提/DB collection/schema/dependency、共有lease/epoch doc、GET追加DB/fallback、historyのsilent truncateを導入しない。
- Cloud Run制御Job、23/03/07 time ownership、実機設定、03 archive20秒、control plan pointerを操作しない。

## クラス・変数差分

| qualified signature | role / return / failure |
| --- | --- |
| NEW `app.operations.monitoring_repair.regenerate_derived(*,client:Any,cfg:PipelineConfig,state_path:Path,stage:Literal['daily','cost','history'])->dict[str,Any]` | 既存PipelineConfigはapp.operations.sqlite所属、cfg.db_path/data_backend/artifacts_dirを使いfrom_env0。入力source/採用原本をpin→既存計算又はhistory専用writer→再読/出力照合。receiptはstatus/input/output hashes/read/write counts。scope違反/予算/入力不足はValueError、RPC例外はunknown/failedとして非0。 |
| EXISTING LIMITED `app.operations.firestore.recalc_monitoring_daily_metrics(client:Any,*,calendar_dates:set[str],dashboard_affected_dates:set[str],updated_at:str,deadline:float\|None=None,on_progress:Callable[[dict[str,Any]],None]\|None=None)->dict[str,int]` | 通常defaultは旧query/get/commit呼出・戻りdashboard/pv_charge_endを維持。修復だけdeadlineを全read/計算/450commitへ伝え、on_progressをunit毎に呼ぶ。戻りcountを全unit verifiedの代用にしない。 |
| EXISTING LIMITED `app.operations.firestore.recalc_cost_daily_from(client:Any,*,start_date:str,end_ts:str,day_rate_yen_per_kwh:float,updated_at:str,tariff_mode:str="flat",night8_day_start_hhmm:str="07:00",night8_day_end_hhmm:str="23:00",night8_day_tier1_upper_kwh:float=90.0,night8_day_tier2_upper_kwh:float=230.0,night8_day_rate_tier1_yen:float=31.80,night8_day_rate_tier2_yen:float=39.10,night8_day_rate_tier3_yen:float=43.62,night8_night_rate_yen:float=28.85,deadline:float\|None=None,on_progress:Callable[[dict[str,Any]],None]\|None=None)->int` | 既存policy/結果式/450境界/len(results)の戻りを維持。修復だけmonitoring/prior cost queryと全commitを有限予算化。既commit後例外はrollbackとして扱わない。 |
| EXISTING `load_calibration_for_context` / `observed_day_records` | 署名・変数追加0。R03では呼出してfitせず、Dのcontextが修復日を含むことと取得したraw/hashを準備/後検証する。正常窓外の訂正はこの限定packetではSTOP、別historical仕様が必要。retained/provider gapを修復PASSへ昇格しない |
| EXISTING LIMITED `app.dashboard.snapshots._read_dashboard_source_epoch(client:Any,*,history_scope:bool,deadline:float\|None=None,history_compare_before:str\|None=None)->dict[str,str]` | return型/既存component名/orderingは不変。raw取得だけ下記bounded page。source revision不足・native型不足・予算/期限はraiseしてoldretain。 |
| NEW `app.dashboard.snapshots.write_dashboard_history_snapshot(db_path:Path,*,published_plan:PublishedPlan,storage_client:Any\|None=None,source_client:Any\|None=None,deadline:float\|None=None)->dict[str,Any]` | history-only固定動作。新boolean/実行mode無し。existingbuilder(existing_history=None)とbootstrap validatorを再利用し、history artifactだけ同sourceepoch/CASで公開。deadline Noneは開始monotonic+60を一つ。source取得とbuilder前後で共有し、既存upload24秒budget/総elapsedは別記録。builder内部にはdeadline APIが無いためhardpreemptionを保証せずR04で測定。戻りはgeneration/etag/sha/source/identityのreceipt、source/CAS未収束は非0 |

`_read_dashboard_source_epoch`内部のlocal `monitoring_max_documents=48*1096`（52,608）だけを追加。新global定数/runtime設定は作らない。既存page limit=3,660を保ち、raw aggregateだけ複数pageを許可する。
stateにはR02共通stage receipt＋cost_start_date/cost_end_ts/affected_dates/policy_sha256/source_before_sha256/source_after_sha256/output_sha256/read_counts、校正old/new current generation/state_sha256、asset各generation/etag/sha256/rebuild_mode、Drive各generation/archive_sha256を記録する。
history/verifyはR02の一回CLIへ集約し、入力identityは `{target_date,raw_plan_sha256,control_version_id,generated_at,repair_manifest_sha256}`。repair_manifest_sha256はReviewedStateSha256、採用receiptが唯一のplan選択根拠。phase flag/前後4receiptは作らない。Dがcurrent bootstrap、Eがhistoryの別assetを所有し、正常D成功後のbootstrap再PUT0。
resumeは唯一の採用原本版＋修復manifest SHAと各asset read-backを照合し、原本差替えは停止する。source/daily/costまでの中間read-backだけではoverall PARTIAL、verifyは全最終条件を評価する。
project IDやobject/folder URIの公開はしない。private preimageには原本復旧に必要な値を保持し、公開logはhash/count/date/status/reasonだけ。

### 日計/料金SDKの限定予算・progress契約

現APIのquery.stream()/batch.commit()はtimeout無し、戻りは件数であり、CALL ONLYでは有限予算/commitunit receiptを実現できない。今回の限定許可は上記2関数のSDK境界と任意callbackだけ。料金式・欠測stickyNone・有効0・night window・450境界・通常呼出を変えず、historical lockの退役/変更は行わない。
適用点: costのmonitoring_samples stream/prior cost_daily stream/各450commit/最後のcommit、dailyのmonitoring_samples stream/battery_daily_metrics document.get/各450commit/最後のcommit。deadline指定時は全SDK read/writeへtimeout=min(4,deadline−monotonic),retry=Noneを渡し、request開始前/各stream row/stream完了/計算後/commit前に残り予算を確認する。消費途中のstreamを完全入力として計算/保存しない。
通常deadline=None/on_progress=NoneではSDK呼出引数・既存戻り型/値・450unitを保持する。callbackだけ指定された場合はprogressのみを追加し、SDK既定timeout/retryを変更しない。repair callerは必ず両方を渡す。
repair runnerのdaily工程は30秒、cost工程は60秒をbefore source readからafter/output照合まで共有する。関数内/450unit/streamごとにdeadlineを更新しない。unknown commit/progressのrecovery readbackは別の最大20秒・write0工程として記録し、期限切れ後に通常計算/commitを継続しない。drift再生成は最大1回で、attemptごとの予算/総elapsedも記録する。
progress eventは`stage,unit_index,event,collection_counts,document_ids,date_start,date_end,document_count,unit_payload_sha256,acked_update_times,elapsed_ms,reason_code`。event=`unit_prepared|commit_ack|commit_unknown|progress_unknown`。doc候補payloadはprivate state側でpinし、stdoutにはhash/countのみ出す。unit_preparedでcommit前の候補と範囲をlocal journalへ保存し、ACK時にSDK WriteResultのnative update_timeを対応付ける。callbackは別DB/leaseを書かない。
unit_prepared callback失敗はそのunit未commitで停止するが、以前のunit ACKを取消扱いにしない。commit_ack callback失敗やjournal保存不能はprogress_unknown、SDK commit例外はcommit_unknownとしてcallerへ失敗を伝え、準備済unitのbounded readbackで全一致/部分/未適用/不明を確定する。例外を全件rejected/no-writeへ変換しない。
最大source readはprivate manifestの固定対象範囲＋予定native doc countで制限し、dailyには必要な前日23時〜翌々日0時、costには月初〜fixedend＋baseline1docだけを渡す。repair deadline指定時のmonitoring queryだけは範囲内30分slot数＋1のlimitを付け、超過sentinelで保存前FAIL。prior costは既存limit1、battery getはcalendar_dates件数内。unit_preparedのcollection_countsにはsource read数も含めcallbackが予定countを照合し、未知追加doc/partial stream/不足read countはFAIL。既存2関数の戻りは変更せず、runnerは全commitunit receipt＋source before/after＋全output readbackが揃った時だけstageをverifiedとする。

## 実装手順・分岐

1. **派生preflight**: R02 source48/readback確定、対象9/20と翌日9/21、fixedcost範囲、旧cost/日計/asset/校正currentのpreimageを照合。旧8/31のcumulative baselineとnative versionをpinする。不明/既に壊れていれば、開始日拡張は新prepare/reviewへ戻す。
2. **daily**: 保存rawの9/19 23時〜9/22 00時までの必要範囲をbefore digestで確認し、既存recalcを呼ぶ。9/20の6energy/朝SOC/PV end、9/21の前夜窓を独立照合。datasetに9/21値変更が無くても計算経路とsource範囲を検証する。
3. **cost**: 9/1〜fixedend全interval＋baseline/policyをbefore digestでpinし、上記optional deadline/on_progressを渡して既存recalc_cost_daily_fromを**1回**呼ぶ。出力unitはその範囲の全cost_daily行。SDK batch450境界は既存のまま、ACK/unknown progressと未完rangeをreceiptへ記録する。daily呼出にも同じ任意引数契約を使う。
4. 月途中開始やchunkごとのtier resetをしない。9/21〜9/30 savingsは9.328day buyの訂正後累積で、10月のtierは新暦月でreset、10月cumulativeは訂正済Septemberを基準にする。今回約42日程度でも実行時のfixedendで行数を数え、固定件数を発明しない。
5. 検証は既存calculate_daily_costsによるread-only候補と保存outputを照合し、別fixtureでは手計算oracleで式/境界を確認する。同関数の二度呼びだけを料金式の独立証明としない。差分load-buy=.035を節約量へ直接置換しない。
6. 各daily/costのafter source digestがbeforeと異なれば**FAIL/source_drift**。書込済みoutputを取り消したと説明しない。asset/backup-successへ進めず、同scopeの全outputを最大1回再生成・再照合する。再drift/不明commitは停止しpartial stage/rangeを記録する。
7. raw→derived完全atomicは保証しない。outputとsourceの照合後〜他producer書込の窓は残る。commit response不明はoutput readbackでverified/partial/unknownを分類。resumeはraw変更0でも失敗cost stageを独立再生成し、changed_countを成功条件にしない。
8. **校正準備（read-only）**: Dのtarget dateに対する現在＋前請求期間の通常context読取範囲に9/20が含まれることを、実際のstart/endとraw timestampで検査する。現在の10/12〜13想定では含まれ得るが、実行日から推測でPASSにしない。修復日が範囲外ならSTOP/別historical仕様へ渡し、特殊reader/flag/暗黙の期間拡張を追加しない。既存calibration/as_of injectionで固定計算する別案は、context/Job入力配送が増えるため今回採らない。
   既存load_monitoring_historyのstartはprevious_billing_period_for_target(target_date).previous_start、endはtarget_dateT00:00。修復日05:00>=start、17:30<endが必要。stored/CSV mergeはCSV優先なので範囲一致だけでは不足し、Dの26unique dt/pv入力が修復後保存ts/pv_kwhと一致することを後段の観測原本hashで証明する。特殊なcontext flagを追加しない。
9. 05〜17時26samplesの17:30だけが今回追加窓に入る。既存衛星観測・認証・fitを使用し、実測/certification availabilityは今回取得終了時刻、既存同hashの日は旧availabilityを保持。未来/当日を学習せず、target/as_ofを遡及させない。
10. fit/store更新はDの通常 `load_calibration_for_context` 一回だけ。D後に既存`pv_calibration_observations`イベントのexcluded_dates/retained_observation_gapsとimmutable actual/certification/calibration/stateをread-only照合しrepair receiptへ取り込む。included/valid-not-certifiedは根拠付きで区別、actual不足/provider gap/retained failureは未解決。理由が無い旧day未収録を「非晴天」と推測しない。
11. 新packが不要なら修復rawの観測hash/records同値＋再検証理由を記録する。R03からfit/publish/restoreを呼ばない。校正current変更は将来runだけへ適用し、採用済plan/forecast/pv_model summaryは当時のpackを保持する。
    実測hashの正本はSHA(canonical_bytes({'date':対象日,'samples':[{'hour':h,'actual_halfhour_kwh':[pv[h:00],pv[h:30]]} for h in 5..17]}))。採用immutable state.clear_days[date]のactual_sha256/actual_source/13hours、certification_sha256/認証原本hash/availability<=as_of/site/basis、D raw decision_rationale.pv_clear_ghi.calibration_sha256/calibration_packを照合する。非晴天は既存excluded_dates.not_certified_clearとtraining_dates除外の証拠が必要。provider gap/未読/retainedを同値変更不要と呼ばない。
12. **revision paging**: monitoring_start〜endの固定範囲をwhere(ts>=start).where(ts<end).order_by("ts").order_by("__name__").select(["ts"])で取得。1pageはlimit3661、先頭3660を採用しsentinel有なら最後の採用snapshotをstart_afterして続ける。最後のsentinelを捨てて終了しない。before/materialize/after/CAS retryの全再読で同じ全範囲を使う。既存indexでSDK queryが通る証拠はR04で確認し、新indexを必要とする場合は停止する。
13. 合計52,608超、欠落cursor、非増加cursor、重複doc異version、native update_time欠落、deadlineはFAIL。accepted全docを従来groupsへ入れ、最終max(update_time)/count/SHA(sorted(id,version))を計算する。metadataへpage数分のvectorを増やさず既存componentを保持する。
    pageごとにdeadlineを新設せず、既存引数deadlineを全pageで共有する。各SDK streamは残り予算と4秒の小さい方・retry=Noneを守り、sentinelを含む実読取件数/SDK request数/bytesを記録する。
14. 8,526rawは3page（3660/3660/1206、sentinel読取2を別計上）。正常179日PASSには他source query各3660以下、全map取得60秒/毎upload再検証24秒内、native map8KiB内が必要。実SDK時間を測り、期限超過を上限緩和/oldretain永久運用で隠さない。
    8,526は10/12追加も含むローカルstress fixture。R02の13件だけなら同baselineで8,506（3660/3660/1186）。本番総件数は実読取値で照合し、8,526を本番成功件数として固定しない。
15. **history専用公開**: 採用receipt.control_version_idからcontrol_plan_versions/versionのpayloadをPublishedPlan.from_document(target_date=receipt.target_date)で取得、sha/version/generated_at一致を確認。callerはcfg.db_pathを渡す。helper内はproducer cache clear→sourceepoch(history_scope=True)→payloads=build_dashboard_snapshot_payloads(db_path,existing_history=None)→_validate_publication_payload(payloads[BOOTSTRAP_KIND],published_plan)→afterepoch一致→artifact=artifact_from_payload(HISTORY_KIND,payloads[HISTORY_KIND])→location=_upload_artifact(artifact,storage_client=...,publication_identity=...,source_epoch=...,source_client=...,history_scope=True)→native read-back→receipt。bootstrapはin-memory検査のみ、gzip/PUT/filewrite0。新raw生成・校正fit・control CAS0。actual/costは修復後原本、過去forecast原本は保持。
    既存builder戻りはdict[str,dict]、validator戻りNone、既存SnapshotArtifactはkind/raw/gzip_bytes/etagだけ。upload戻りはstr locationでgeneration属性は無い。receiptのgenerationはnative object read-backで取得し、str URIやartifactの属性から捏造しない。URI/bodyはprivate、公開はhash/count/statusだけ。
    publication_identity:dict[str,str]は既存publisherと同じinline式で、target_date=published_plan.target_date、generated_at=json.loads(published_plan.raw_json)['generated_at']、control_version_id=published_plan.version_id、raw_plan_sha256=published_plan.sha256。source_epoch:dict[str,str]は_read_dashboard_source_epoch(source_client,history_scope=True,deadline=deadline)の戻りをそのまま使い、builder後も同引数で再読して完全一致を確認する。uploadへ両dictを渡し、generated_at/sourceepochを現在時刻へ置換しない。
16. full rebuildでも過去forecast原本は保持。GCS asset2個の更新は横断atomicではない。bootstrap成功/history失敗なら各generationを別記録し未完扱い、同固定scopeから再公開する。legacy assetは既存初回移行規則、delete/count減少は従来保守拒否を維持する。
17. **backup**: R05が所有する前後backupのreceipt/generation/hash/read-backを消費し、R03からDrive wrapperを再起動しない。含まれるraw/daily/cost/forecast/planを確認し、Job cleanup成否を別記録。校正current/state/originalsとGCS assetsはDrive scope外なので、private recovery bundleに旧current generation＋immutable state hash/関連原本hashを別保持する。
18. 復旧は自動rollbackしない。source/derived復旧は現在native versionがreceiptと一致する範囲だけを別承認で戻し、修復後sourceから再生成する。校正は既存restore_currentを選択state SHA＋現在generationで使う。assetの生oldblob戻しはCAS/monotonicを破るため通常再生成を優先し、逆行が必要なら別review。成功Drive generationは削除しない。

## テスト・PASS条件

以下は**PLANNED NEW** `tests/test_monitoring_repair_derived.py`、既存`tests/test_clear_ghi_runtime.py` / `tests/test_dashboard_publication_generation.py`への追加。historical helper/testの予定新設は撤回、既存codeの削除ではない。
実行前はmandatory品質gate＋code-quality-audit。今回tests/Cloud実行0。既存fake/transportとserver/runtimeの列を分けて結果を記録する。

| ID | fixture/command | exact PASS |
| --- | --- | --- |
| DERIVED-01 | real48 CSV/保存raw＋9/19 23時/9/21夜間。`python -m pytest -q tests/test_monitoring_repair_derived.py -k daily` | daily32.002/load、30.530/buy、11.945/daybuy、前夜窓独立集計一致、未知0捏造0、settings/forecast/control mutation0。 |
| DERIVED-02 | 9/1〜10月fixedend・8/31baseline・tier90/230跨ぎ・13追加。same file `-k cost` | 全cost行oracle一致、9/21以後tier影響、10月tierreset/cumulative継承、policy/式不変、月途中chunkreset0。 |
| DERIVED-03 | raw unchanged retry/同一run/中断450境界。same file `-k resume` | rawwrite0でも全未完cost再生成、完了readback、partialrange検出、数値同値、false success0。 |
| DERIVED-04 | daily/cost source query前後の旧日訂正/commitresponse loss/再drift。same file `-k drift`＋R04 server | 1回限定再生成、未収束FAIL、unknown readback分類、asset upload0、書込済みderivedをno-writeと呼ばない。 |
| DERIVED-05 | current＋previous billing範囲内外/17:30=0補充/非晴天/providergap。same file `-k calibration_owner`＋既存clear_ghi_runtime回帰 | R03 fit/publish0、D通常updater一回、範囲外STOP、26窓/rawhash/availability一致、valid0保持、providergapをPASSにしない、oldforecast bytehash不変。正常非認証と旧day未読を区別 |
| DERIVED-06 | 8526raw＋179daily＋実データ相当残source。`python -m pytest -q tests/test_dashboard_publication_generation.py -k paged` | 全8526をdigestへ含め3page/sentinel2を記録、page境界/同tsdocid/旧日訂正を検出、before/after一致、metadata<=8192、fullhistory成功。 |
| DERIVED-07 | raw52609/無限cursor/非増加cursor/native不足/他collection3661/期限。same file `-k paged_budget` | 明示FAIL・old asset保持、部分history公開0、silenttruncation0、GET DB0。 |
| DERIVED-08 | 旧9/20history＋再生成cost＋固定採用原本＋drift/CAS race/resume。same file `-k repair_history` | historyだけ一公開、bootstrap PUT/filewrite0、源データread-backが予測前中間根拠、採用receipt同identityで最終verify。全source-map一致、ETag/304・31日initial/lazyhistory保持、CAS old-generation overwrite0 |
| DERIVED-09 | emulator又は明示承認serverの同数量。R04指定adapter command＋snapshot repair CLI verify | SDK timeout/retry/request/readcounts・serializedbytesを実測し60秒/24秒/8KiB内。fake速度を本番値としない。未計測ならG07 OPEN。 |
| DERIVED-10 | Drive quota fallback/cleanup failure＋current別bundle。same file `-k backup`＋R05 canonical wrapper receipt | R03 Drive起動0、R05だけが前後backup owner、generationhash/restore一致、校正scope外を明示、cleanup失敗をbackup未保存と混同しない、秘密/原本ログ0 |
| DERIVED-11 | 2関数default呼出と修復呼出/stream途中timeout/期限前後/450+最後unit。same file `-k deadline_progress` | default SDK引数・式・戻りcost int/daily dict/450境界不変。修復は全stream/get/commitでmin(4,remaining),retryNone、partial入力write0、期限後request0、ACK/未完unit一致。 |
| DERIVED-12 | commitACK後callback/journal failure、commit応答喪失、先行unitACK＋後続未commit/不明。same file `-k progress_failure`＋R04 server | committedを取消扱いしない、progress_unknown/commit_unknownを分け、20秒bounded readback/局所journalで適用状態を確定。全unit未確定ならverified0、blind全再commit0。 |

既存server regressionは`tests/test_dashboard_server.py::test_bootstrap_http_uses_precomputed_gzip_etag_without_db`、history fallback境界は同fileのmissing_history_snapshot testを維持する。
partial source4/17/当日10/12はcomplete48と呼ばない。fixtureでは未取得を保持し、修復対象の定義外の金融値を変更しない。

追加回帰DERIVED-13は `tests/test_monitoring_repair_derived.py::test_closed_calendar_cost_resume_ignores_positive_current_day_owned_by_normal_producer`。TODAY00以後の正のload/buyが修復cost sourceへ入らず、通常producerが当日self_consumption_kwh=9を計算した後も閉じた過去日のsame-source resumeで追加commit0、当日値を修復から書き換えないことを確認する。これを通常Dの本番再計算・当日金融readback済みという証明へ昇格しない。

既存 `tests/test_dashboard_snapshots.py::test_first_run_outside_slot_23_builds_bootstrap_only` はlocal fixtureのDATA_BACKENDをsqliteへ固定し、`_unexpected_firestore_client()` を既存Firestore openerへ置いてAssertionErrorで外部読取を検出する。ambient backendに依存せずbootstrapだけを検査する。production dashboard source、GET/cache/source epochは変更0。

## 中断条件

baseline/policy不明、派生scope不足、input/source drift未収束、output不一致、校正未解決/原本CAS競合、source予算/期限/metadata超過、asset CAS未収束、backup hash不足はstage非0。
任意stage失敗時は前stageの確定結果と未完unitを残す。通常producer/制御Jobを止める権限をこのCLIから発明せず、必要な運用調整は別承認へ渡す。
source→derivedのnonatomic窓とsource再検証→GCS uploadの窓は残る。state/after digestは観測時点の整合証拠であり、全writer排他の証明ではない。

## 出口・引継ぎ

source/daily/cost中間read-backだけではoverall PARTIAL。修復全連鎖PASSはraw48、daily/前夜/全cost範囲、Dの校正結果と唯一採用receipt、current bootstrap/history generationの同原本照合/全最終verify、R05所有backup/read-back、G07実adapter証拠が揃った時だけ。
known partial day/旧forecast原本/旧pack採用計画は保持。R04独立reviewへstage receipts、diff counts、最大read/time/bytes、未解決理由を渡す。
文書の完成時はPLANNED完了で停止し、本番Write/Cloud実行/deploy/merge承認を求める前にreviewableな候補を準備する。

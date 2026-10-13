# R02 保存実測の限定修復

依存: R00/R01の証拠・backend parity。実書込前にR04のadapter証明を要求する。文書は実装可能な手順を定義するが、実装・本番承認・SDK server試験は未完。
本stageは9/20の欠測13区間だけを対象にする。10/12の20件や他日の既存値訂正は別scopeとして再準備する。
ローカル総8526件には10/12追加20も含まれる。本stageだけなら同baseline8493から8506件だが、本番の他日通常更新は別に存在し得るため総件数を固定PASS条件にせず、対象日48/追加13/他日write0を確認する。

10/13の限定実装は、同じCSV原本の9/20全48行が既にnative保存済みの場合にもfresh prepareを受け付ける。許可件数は `(existing,candidates)=(35,13)` または `(48,0)` だけ。後者は全MONITORING_FIELDSのCSV一致、energy/SOC非bool、全48行のnative preimageを固定し、raw create/update/commit0を要求する。以下の35/13記載は初回create分岐に適用し、fresh0の受入は全48 native不変で判断する。欠損1行・異値・native driftを部分修復へ読み替えない。

同じ既存3関数を使い、`verify_source_repair.candidate_count=len(state["candidates"])` で分類する。候補0のverified receiptは `attempted_count=verified_count=raw_writes=0` で、これは候補件数の結果であり48行の検査を省いた意味ではない。`apply_source_repair` はSDK batchを作らず、既存 `_pin_calibration_preimage` によるD前原本/packのprivate保全と `calibration_preimage_sha256` 検査を保持して戻る。prepare時刻を既存48行のingested_atへ書き直さない。

旧13件のmanifest/journal/承認SHAは保持し、別StatePathの新commit prepareと新承認SHAを使う。正規入口は `run_monitoring_source_repair_from_env.ps1 -Stage prepare -StatePath <fresh-private-state> -CsvRunDirs artifacts/20261012-203830`。prepareへWrite/Resumeを付けず、既存stateを置換しない。`scripts.repair_monitoring_source.main` は既存cfg.timezoneの当日00:00をexclusive cost_end_tsへ固定する。JST10/13なら9/1〜10/13T00:00:00（10/12まで42暦日）、対象9/20・影響9/20–21を結果で確認する。開いている10/13料金は通常Dの責任と分離し、prepare後にendを時刻進行で拡張しない。新flag/helper/class/env/schema追加0、全体gate・本番連鎖受入は未完。

## 入口・参照範囲

readset: [調査正本](../research-source-repair.md)、[publication契約](../../publication-contracts.md)、R01結果、[import wrapper (line 1)](../../../../../../scripts/run_kpnet_import_from_env.ps1)、[CSV sync (line 119)](../../../../../../app/operations/firestore.py)、[日付選択 (line 45)](../../../../../../app/operations/monitoring_sync.py)、[workflow (line 374)](../../../../../../app/operations/workflow.py)。
過去障害保護正本と引用commit `541cd60`/`93e8c3c`/`7899423`を先に読む。23/03/07制御・SOC・料金式・forecast原本は今回修復対象に含めない。
source evidenceはprivate manifest付きCSV。研究用DB/CSV、credential、project/database ID、bucket/folder IDは公開doc/log/stateへ出さない。
`source-v2.sqlite`はvalidation projectionなので、Firestore native復旧backupの代わりにしない。
本番履行順はR05正本に従う。新コード固定commit/planner imageの反映後、calculation段階の独立source repair入口を先に実行する。旧imageにNEW CLIがあると仮定せず、欠損billingで失敗するforecastの成功をsource修復前提にしない。

## 変更対象

| 位置・qualified symbol | 種別 | 限定変更/保存値 |
| --- | --- | --- |
| `scripts/run_kpnet_import_from_env.ps1` param/Process env | EXISTING | NEW optional `TargetMonths:string[]=@()`。Import-ProductionEnv後に既存KP_CSV_TARGET_MONTHSへoverride、defaultは旧動作。元の値/存在をfinallyで戻す。credential cleanup維持。 |
| `scripts/run_monitoring_source_repair_from_env.ps1` | NEW | 一回限りcanonical repair wrapper。`.env`を既存production_env経由で読み、下記CLIのみPythonへ渡す。 |
| `scripts/repair_monitoring_source.py::main` | NEW | source stageのprepare/apply/verifyとR03 stageの直線的dispatch。cloud writerはWrite＋承認state照合成功後のみ作る。 |
| `app.operations.monitoring_repair` | NEW | 下記plan/raw writer/readbackだけ。generic session/proxy、既存通常producerを包むframeworkは作らない。 |
| `app.operations.monitoring_sync.prepare_monitoring_csvs` | EXISTING CALL ONLY | production CSV reader、重複排除、欠測値保持をそのまま使用。対象修復はwindow=Noneで準備し、承認dateで絞る。 |
| `app.operations.firestore.sync_monitoring_csvs` | EXISTING PRESERVED | 正常取込の4日window/FullBackfill/unchanged metadata保持を維持。修復13件にはblind batch.setを使わない。 |

## 変更禁止

- `app.operations.workflow._ingest_firestore`のsettings/night plan/cost/weekly backupへ任意に入り直さない。既存pipeline run docを削除して再実行条件を偽装しない。
- `app.operations.monitoring_sync.meaningful_monitoring_payload`の8fieldsとduplicate conflict除外契約を変更しない。
- CSV `pv/load/buy/sell/charge/discharge/soc`を推計・補間・forecast値で補わない。既存35区間・source_csv/ingested_at・未知fieldsを変更しない。
- `TABLE_SPECS`、collection/schema/env/IAM/dependency、runtime flag、GET/fallback、_validate_publication_payload、GCS CASは追加/変更しない。
- `slot_orchestration`、night_soc_time_contract、03 archive最大20秒、23/07 DB非依存、実機API、settings roundtrip、forecast generation/control pointerは操作しない。
- `run_drive_backup_cloud_from_env.ps1`の実行はbackup用本番操作承認が必要。調査/prepareの中で暗黙実行しない。

## クラス・変数差分

NEW wrapper CLI: `-StatePath <local-private-path> -Stage <prepare|source|daily|cost|history|verify> [-TargetMonths <YYYY-MM,...>] [-CsvRunDirs <paths>] [-Write] [-Resume] [-ReviewedStateSha256 <sha256>]`。
historyは確定した採用receiptの同原本から履歴assetだけをfull rebuildする。verifyはread-only。旧案のSnapshotPhase、calibration更新stage、予測前後4receiptを撤回する。予測前の中間根拠はsource/daily/costのread-back、最終根拠は一つの採用receipt＋history/verify receipt。
`Write`なしはremote write0。month取得は明示TargetMonths指定時だけ既存import wrapperのNEW月引数＋SkipFirestoreIngestで行う。既存manifestを使う場合CsvRunDirsを指定し、最新directoryを自動選択しない。
state確定後のresumeではTargetMonths/CsvRunDirsによる差替えを拒否。新runtime envを導入せず、月overrideは既存keyへProcessだけ適用する。

| qualified symbol / signature | NEW role・return・例外 |
| --- | --- |
| `scripts.repair_monitoring_source.main(argv:list[str]\|None=None)->int` | CLI構文/承認/checkpointを検証。成功0、停止/未知/失敗は非0。秘密や原本をstdoutへ出さない。 |
| `app.operations.monitoring_repair.prepare_source_repair(*,client:Any,cfg:PipelineConfig,cost_end_ts:str,csv_run_dirs:tuple[Path,...],target_dates:tuple[date,...],state_path:Path)->dict[str,Any]` | production parserでcandidateを作り、native snapshotsとCSVを突合、private preimage/stateをatomic local replaceで保存。callerが既存configを一回読みfixed cost endを渡し、料金policy/影響範囲をimmutable manifestへ入れる。関数内でenvを再読/後からmanifestへ追記しない。CSV/値/48slot不一致はValueError、read timeoutは非0へ伝える。remote write0。 |
| `app.operations.monitoring_repair.apply_source_repair(*,client:Any,state_path:Path,reviewed_state_sha256:str)->dict[str,Any]` | 同hash・同対象・backup確認後、13不存在docへSDK WriteBatch.createを1commit。戻りはreceipt。precondition conflict/unknown commitは下記readback分類し、全失敗をrejectedと呼ばない。 |
| `app.operations.monitoring_repair.verify_source_repair(*,client:Any,state_path:Path)->dict[str,Any]` | 48slots/既存35不変/13payload一致/各native versionを再読。下記statusのreceiptとreasonを返す。値・型・unknownfield差分を黙って正規化しない。 |

private stateの既存pattern: `schema_version=1, repair_id, checkpoint_commit, prepared_at, target_dates, candidate_sha256, csv_manifest_sha256, database_fingerprint, reviewed_state_sha256, stages`。
private preimageには対象doc path・全native typed payload・update_time/不存在、source revision、backup artifact hashesを保存する。native payloadは既存SDKのDocument/Value protobufでlossless保存・再読取し、SQLite投影/str(datetime)を代用しない。公開要約にはhash/count/status/dateだけ出す。
ReviewedStateSha256はimmutable prepare部分のcanonical JSON SHAとする。対象日/候補/CSV manifest/DB fingerprint/commit/影響範囲/料金policy/固定end/preimage hashesを含め、reviewed_state_sha256自身・mutable stages・実行receiptは除外する。resumeでstage更新を理由に再承認hashを失効させず、source/preimage差替えは必ず失効する。
`stages[name]`はstatus=`pending|running|success|failed|unknown_commit`、started_at/finished_at/input_sha256/output_sha256/receipt_sha256/reason_codeを保持。runningをresume成功扱いにしない。
repair mainだけがimmutable manifest/実行journalを所有する。採用receiptはR05が隣接private directoryのadopted-forecast.jsonへ唯一保存し、history/verifyはそのhash/native identityを確認して読むだけ。release stateとrepair journalは採用receipt SHA参照を持つがbody/statusを複製しない。R05のPowerShellはrepair journalを書き換えない。月取得は子pwsh -NoProfileへ閉じ込め、parent.env/contextへoverrideを漏らさない。
NEW source receiptは`schema_version=1,repair_id,stage,target_dates,candidate_sha256,source_before_sha256,source_after_sha256,status,attempted_count,verified_count,raw_writes,elapsed_ms,reason_code,readback_at`。status=`verified|not_committed|partial|conflict|unknown_commit|committed_drift`。verifiedはserver readback確定時のみで、status以外の成功booleanを増やさない。
prepareでprepared_atをtimezone付きUTCとして確定し、新13rowのingested_atにその値を固定する。これは今回の修復取込準備時刻であり、9/20測定/CSV取得時刻でも実commit時刻でもない。実commit/readback時刻はreceiptへ別記録する。
candidate_sha256はtimestamp順のdocument_idと**実write完全payload**のcanonical JSON SHA。payload keysはts/pv_kwh/load_kwh/buy_kwh/sell_kwh/charge_kwh/discharge_kwh/soc_percent/source_csv/ingested_at。source_csvもmanifest由来値をprepare時にpinし、apply/retry時にpayloadや時刻を差し替えない。

## 実装手順・分岐

1. **read-only prepare**: HEAD/clean source/必須品質/R01、接続fingerprint一致を確認。9月manifest付き原本のhashをpinし、9/20だけをproduction readerで抽出。48個の00:00〜23:30、timestamp一意、6energy有限非bool非負/SOCの既存valid契約を確認する。
2. native保存35件とCSV8fields一致、追加予定13timestampの不存在を確認。他日の候補write0、既存更新0でなければ停止する。target_date/CSV範囲を混同せず9月CSV全体1,440件をwrite許可しない。
3. raw13/影響daily/battery/派生costのpreimageをprivate lossless backupへ保存しhash/readbackを確認。source原本hashは不変。Driveの事前data generationは明示承認された別stageで取得し、repair_idにreceiptを関連付ける。
4. prepared state/予定write13/予想差分をreview可能にして停止。本番Writeにはその時点の明示承認とReviewedStateSha256が必要。CLIのWriteだけで承認済みとはみなさない。
5. source stageは既存35/source/native versionと13不存在を再確認後、**1つの13doc atomic batch.create**をcommit。不存在preconditionで競合insertを上書きしない。SDK既定retryを無限に重ねない。全attemptの有限deadline/request retryはR04で実adapter証明する。
6. 成功応答でもreadbackする。commit timeout/connection lossなら13を再読し、全candidate一致はcommitted、全不存在はnot_committed、混在/異値はpartial/conflict、読取不能はunknown_commit。unknownをno-write/PASSとしない。
7. native precondition conflictでもreadback全一致なら先行writerの同candidate採用として記録可能。異値なら停止。13一致時はretry raw write0。35旧rowが変わった場合はcommitted_driftとして停止し、raw13成功を取消/自動削除しない。
   atomicityは13不存在docのcreate batchだけ。既存35の読取はそのbatchのpreconditionではないため、他writerによる35変更をpreventしたと宣言しない。前後read-backで検出して停止する。排他leaseや35への無意味な再writeを追加しない。
8. raw成功をstateへatomic保存し、R03へ固定receiptを渡す。daily/cost/calibration/assetsが未完の段階で修復全体PASSとはしない。
9. resumeは成功stageも入力hash/現在readbackを再照合してからskip。失敗・running・unknownはreadbackで状態を確定してから再開。別hash/別DB/別source候補は新prepare＋承認へ戻す。

## テスト・PASS条件

既存[monitoring_source_repair (line 161)](../../../../../../tests/test_monitoring_source_repair.py)はreal CSV parser→fake Firestore日計を確認するが、native create/server原子性の証拠ではない。
以下は**PLANNED NEW** `tests/test_monitoring_repair_command.py`。実装時はcode-quality-audit＋mandatory gateを先に完了する。今回実行していない。

| ID | fixture/command | exact PASS |
| --- | --- | --- |
| REPAIR-01 | 9/20保存35＋manifest CSV48。`python -m pytest -q tests/test_monitoring_repair_command.py -k prepare` | candidate13、old update0、他日write0、remote write0、日計期待値32.002/30.530/11.945、原本hash不変。 |
| REPAIR-02 | 欠slot/重複conflict/NaN/bool/negative/既存8field差分/hash差分。same file `-k invalid` | 非0、write0、reason特定、欠測を0へ変換しない、reviewed sha偽造不可。 |
| REPAIR-03 | prepare後に13の1件を競合insert。same file `-k conflict`＋R04 emulator/native server | batch全体abort、他12insert0、競合値不変。fakeだけでserver atomic PASSとしない。 |
| REPAIR-04 | 実SDK request transport。same file `-k request` | create13すべてexists=false、source payload/hash一致、native type/nanosecond roundtrip、bounded timeout/retry。transportはserver適用証明とは別。 |
| REPAIR-05 | server commit後response loss/commit前timeout/readback outage。same file `-k ambiguous` | committed/not_committed/unknownを正しく分類、retry rawwrite0又は未commit分だけ、異値overwrite0。 |
| REPAIR-06 | 同state retry/途中終了/別DB/別CSVhash/source_csv変更/prepare時刻変更。same file `-k resume` | runningをskipしない、成功readback再確認、prepared_at=13row ingested_at固定、全write payload SHA一致、候補差替え拒否、旧35値・metadata不変。 |
| REPAIR-07 | 未承認/Write無し/invalidStage/秘密を含む例外。same file `-k authority` | remote write0、state hash不一致停止、stdout/state公開要約にcredential/project ID/source body0。 |
| REPAIR-08 | 原本と復旧bundle/native型/明示rollbackdryrun。same file `-k backup` | lossless restore candidateとnative preimage一致。実rollbackは別承認、他writer更新を上書きしない。 |

追加回帰REPAIR-09は `tests/test_monitoring_repair_command.py::test_fresh_verified_48_source_manifest_has_no_cloud_write_and_pins_calibration` と `::test_fresh_verified_manifest_rejects_partial_or_different_source`（missing_one/different_value/boolean_value）。PASSはcandidate0/48 native不変、旧manifest不変、raw write/commit0、校正private pin/SHA保持。1欠損/異値/boolはprepare非0・cloud write0。REPAIR-10 `::test_prepare_cli_pins_closed_calendar_end_in_configured_timezone` は凍結時計/設定timezoneからTODAY00をpinし、翌日00へ戻さない。fixtureを本番native成功へ昇格しない。

## 中断条件

R01不足、原本/manifest/hash不一致、target48未完、旧35異値、未知native型をbackupできない、post-prepare drift、precondition conflict異値、deadline、unknown commitは停止。
4/17や当日未取得区間を本stageで埋めない。未完stateにsuccessを手入力しない。backup失敗はWrite前ならwrite0、Write後ならsource成功とbackup失敗を別記録する。
rollbackは自動実行しない。native現在版がrepair receiptと一致するdocだけを対象に、preimage復元/追加doc削除の計画を作り、明示承認後に既存値を保護して適用する。削除後はsource epoch count低下でasset CAS拒否し得るためR03復旧手順を再reviewする。

## 出口・引継ぎ

source stage PASSは本番native readback48slots、13candidate一致、35不変、承認/backup/commit receipt揃いの場合だけ。
R03へrepair_id/state hash、対象日、native source revisions、raw13の確定状態、private preimage hashを渡す。料金/校正/history/Driveを未検証のままsource全連鎖完了とは呼ばない。

# 公開・projection・表示・復元の共有契約

この文書は S07〜S10 の正本。計算・係数は [共通契約](contracts.md) と [データ契約](data-contracts.md)、未完条件は [gates.json](gates.json) を使う。2026-10-12の限定承認後、NEW入口と内部bindingをローカル実装した。文書完備・ローカル試験と実adapter/本番導入安全性のPASSを分ける。

## 現行と目標の順序

変更前 `planner.main` は `forecast_job.main(publish_read_models=False)` → mutable forecast保存 → `archive_plan_snapshot`（immutable＋latest/day）→ parameters更新 → control CAS → asset。互換wrapperの `publish_read_models=False` は現在もassetだけを抑止する。plannerは生成専用入口を使用する。

限定承認後の順序は以下に固定する。

1. recovery → CSV → db pipeline → energy model の生成。recovery は実績更新より前、直近31日以内の欠損原本のみ。生成専用入口は forecast の mutable projection / parameters / asset / control を書かない。db pipeline の実績取り込みは既存責務のまま。
2. run-private path の生成結果を一回読み、`PublishedPlan` と同一 `raw: bytes` に固定する。生成後の共有pathを再読して別runへ変わることを許さない。既存 `KP_NIGHT_PLAN_PATH` の互換名を変えない。
3. immutable候補を保存・read-back検証。拒否候補も `record_status="generated"` の生成証拠。採用・device applied と呼ばない。
4. 既存 `FirestorePlanStore.publish` の二doc commit＋current pointer CAS が成功した候補だけ先へ進む。新lease、別CAS、control pointerへのguard再書込は作らない。
5. 同raw由来の forecast/day/latest/parameters を各projection自身の世代条件で更新する。Firestore/GCS/SQLを一transactionと表現しない。projection transactionからcontrol pointerを読まない。
6. producerだけ `data.clear_dashboard_cache()` を呼び、投影世代と実績鮮度を検証して一payloadに固定し、既存URIのassetをgeneration CASで置換する。GET側では計算・修復・学習しない。

## 世代の比較と冪等性

`target_date`、timezone付き原本 `generated_at`、`control_version_id`、`raw_plan_sha256`、`forecast_run_id` は別識別子。hashの大小を日付・時刻・鮮度比較に使わない。`updated_at`・upload完了時刻をprovider発行時刻へ読み替えない。

本書のPV profileはfull traceの既存確定key `model_id` の意味で、`physical_clear_ghi_annual1` 固定。蓄電池運転profileとは別概念。新しいprofile key/pointerを追加しない。旧計画にtraceがない移行は通常retryから外し、G05/G07で承認されたリリース移行fixtureに限る。
controlは既存pointer再読/CAS loop内でprovenanceを比較する。同target/model_id/siteで双方 `provider_issued_at` 既知なら後退を拒否。既知→不明を無言で降格しない。不明同士は取得順を比較し鮮度未確定を明示。係数は同model_id/site/basis内の訓練 `as_of` 後退を拒否。モデル移行は通常retry overrideにしない。

day projectionは対象日内、global latest/parametersは対象日も後退させない。日付が同じなら生成時刻、同時刻なら同version/hashだけ冪等。異version競合は書込前に拒否する。実adapter上で自身の読取世代を更新条件へ束ねることがG07の証拠で、事前GETだけでは足りない。

`plan_snapshot._plan_order/_update_plan_read_models` は既存の単調更新を再利用する。現行は `_plan_order(previous) >= _plan_order(doc)` をskipする。同時刻異versionを例外拒否する機能が既にあるとは言わない。必要な入口検証とhelper再利用を分け、helperを全面再実装しない。

同版retryはraw/hash/generated_at/原本起源を維持する。parametersの `sample_count` は同projection版で増えない。既存parameter名/mean/varianceの意味を保持し、PV thetaを `model_parameters` に登録しない。

## 内部projection identityと移行

限定承認したNEW保存fieldは `target_date/generated_at/control_version_id/raw_plan_sha256` の4個。source-mapは順に原本forecast.date、timezone付き原本generated_at、既存PublishedPlan.version_id、固定rawのSHA256。既存collection/旧fields/公開HTTP schemaを維持し、外部APIを追加しない。forecast_plansの自身のdocをtransactionで読み、24forecast_hourly＋sunshine_daily＋forecast_plansの26writeへ同じidentityを付ける。model_parametersは全対象docを先にtransaction読取し、同原本/版retryはsample増分0、別新版は一回だけ既存値へ更新する。

identityなしlegacy docは既存date/forecast_issued_at/forecast_run_idを照合して初回移行する。parameter legacy docはcount5→初回6→retry6。standalone互換wrapperはcontrol_version_idを空文字として保存し、異なる新版を保存したとき旧採用identityを残さない。同rawのstandalone→採用では原本値を保ち採用版だけ束ね、parameter sample増分0。同raw wrapper retryは採用identityを保持する。古日/古時刻/同時刻異rawはtransaction内で拒否。旧partial fixture adapterの互換fallbackを実SDKのatomic証明に使わない。

## 保存先と読取経路

| 正本・用途 | 契約 |
| --- | --- |
| `decision_rationale.pv_clear_ghi` | pack・F/Q/C・最終PV24・時刻・修復のfulltrace一箇所。各hourへ全traceを24複製しない |
| `night_plan_decisions`＋GCS gzip raw | 同bytes/hash・既存provenanceを保持するimmutable生成証拠 |
| `control_plan_versions/current` | 採用済みhandoff。controllerは係数/予報を取りに行かない |
| `forecast_hourly_snapshots` | 当時の完全予測vintage。後日の再予想で置換しない |
| `forecast_hourly/sunshine_daily/forecast_plans/night_charge_plans/model_parameters` | 最終値と小さい由来識別のみの既存互換projection |
| bootstrap/history gzip | 原本と区別した高頻度表示cache。schema1、固定URI、gzip、ETagを維持 |
| Drive schema2 | 既存collection＋embedded rawからオフライン復元。独立した係数current参照の自動backup/復元を意味しない |

browser HTML shell → static JS → `/api/dashboard/bootstrap` → `server._serve_snapshot` → `load_precomputed_snapshot` → GCS/local gzip。assetありはDB read 0。assetなしbootstrapのみ31日bounded read。historyは明示操作でhistory endpointへ、assetなしは404、GET全履歴再構築は0。

producerは計画/実績 → persist/project → `write_dashboard_snapshots` → `load_dashboard_slice` → repository → storage → gzip。producerの120秒slice cacheをclearする。readerの別process artifact cache30秒は維持し遅延を測る。全GET cache無効化/TTL0、bootstrap二重転送、初期fulltraceを追加しない。

過去訂正は該当係数集合とmaterialized historyを再構築する。既存 `full_history_rebuild/update_history` の意味・slot所有を保持し、recent window外の訂正を永久に古くしない。request/read数は変更前後、raw/gzip bytesとcache hitは実測値を記録し、未測定値を0や改善済みとしない。

## assetの鮮度とproducer source-map

GCS固定URIのobject generation CASはlost updateを防ぐが、payload実績の鮮度を証明しない。限定承認したproducer-only source-mapは各DocumentSnapshotのnative update_time（nanosecond保持）とdocid/version全組のSHA256を使う。source query範囲ごとのmax update_time/count/full digestを保持し、描画時刻や保存済みingested_atをepochへ読み替えない。before/materialize/after同値を要求し、各CAS retry直前も同じcandidate source-mapを再読する。新manifest/collection/env/lease/flag/profile pointerは追加しない。

source範囲はbounds、sunshine_daily、battery_daily_metrics、forecast_hourly/snapshots/reconstructed/plans、dashboard_daily_metrics（14日追加）、night_charge_plans/latestと31日外analog plan、monitoring_samples（14日追加）、model_parameters、settings_events末尾40、latest battery、monthlyが参照するcost_daily全履歴。history full rebuildは既存全chunk範囲を覆うmapを取る。各query上限3660＋1sentinel、超過は明示失敗してoldretainしsilent truncateしない。既存GET/raw loaderは変更しない。小fixtureはrevision1回9doc read、before/afterで18＋CAS retry毎9の追加read。実データ量/課金は未測定でproducerログのdocument_readsから確認する。

GCS metadataは既存schema/etagにNEW4identityとgzip/base64 source_epochを追加する。全key/value UTF-8合計8KiB以下、超過は書込0。約3KiB規模のsource-scope encodingは全digestを保持する。初回legacy assetは検証済み候補を現在generation CASで移行し、以後identityなしwriterを拒否。同planで実績だけ新版になった場合もcomponentwise後退を拒否。query範囲前進を除くdoc数減少/成分欠落/同servermaxでdigest差は保守拒否して旧assetを維持。削除と新規追加が同時に起きdoc数不変・servermax更新となった場合はmapだけで削除を特定できず、native更新を含む現source snapshotとして扱う。

payloadのPV24/load24/SOC17/容量/対象日/forecast_runが採用原本と一致しなければuploadしない。GCS既存generation/identity/source-mapと比較し、`if_generation_match` を書込対象objectへ適用する。固定payload最大3試行、upload全予算24秒、各RPC最大4秒/retry=None。source-map初回/after読取は各60秒予算、CAS再読は残りupload予算内。後続importが最後のsource-map読取とuploadの間に入る非atomic窓は残り、常時最新/DBとGCS原子性は証明しない。generation/version比較はasset世代後退を防ぎ、後続producerが差分を再検出する。

既存history assetを引き継ぐincremental producerでは、古訂正へ新mapだけstampしない。producerはhistory gzipとmetadataを同じGCS generationのif_generation_matchで読み、旧mapのhistory_retained_before（日境界）とretained/source digestを同じ旧境界で再検証する。現source-mapには新境界のretained/sourceと旧境界のcompare_retained/sourceを含める。保持範囲のsourceはsunshine/battery/forecast各日資料/dashboard_daily_metrics/night plan/monitoring/cost/analog plan、model_parameters/settingsの静的表示はbootstrapの全mapに属しhistoryの空fieldへ新表示値を埋めない。

保持範囲が同値なら通常incrementalを継続する。31日境界が進んだ場合、旧境界〜新境界前日を既存load_dashboard_slice(include_static=False)で追加refreshしてからmergeし、その後の古い資料を新retained digestへ束ねる。古訂正またはretained版情報なしlegacy historyだけ既存bounded full rebuildへ分岐してログを残す。source-map全範囲before/after/CAS再読は同値要求を維持する。full rebuildで3660+1/8KiB/deadline budget超過した場合はoldretainし成功を偽装しない。通常bootstrapは31日を維持する。localassetはGCS metadataがなく毎回保守full rebuild、単独local fixtureでありdistributed CAS証明ではない。

既存 `persist_forecast_snapshots(...night_plan_path...)` のPath再読を維持する。NEW入口はrawとrun-private `private_plan_path` を受け取り、既存snapshot routineの前後にfile bytes/hash一致を検証する。generatorのtempfile.mkdtemp(prefix="forecast-plan-")下で生成後単一所有、別runは触らない。共有mutable pathは拒否、固定private fileの互換API再読は許容する。前後hashだけを排他証明にしない。snapshot backendの新rows API/署名変更は不要。

## NEW入口の署名・失敗境界

型 `Any` は既存storage adapter型、`PublishedPlan` は既存クラス。以下はローカル実装した内部入口で外部公開APIの新設ではない。

| NEW完全修飾名・署名 | 戻り・例外・役割 |
| --- | --- |
| `app.runtime.forecast_job.generate_forecast_plan() -> Path` | run-private生成path。既存 `_run` の失敗を伝播、検証不能は `ValueError`。mutable forecast/asset/controlを保存しない。旧mainは既存persist/任意assetを続ける |
| `app.backup.plan_snapshot.archive_plan_original(raw: bytes, *, storage: Any, firestore: Any, source: str, prefix: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]` | `(receipt, archived_doc)`。receiptは既存 `decision_id/date/detail_sha256/status="verified"`。read-back/hash不一致は `ValueError`、storage障害は伝播 |
| `app.backup.plan_snapshot.project_plan_read_models(archived_doc: dict[str, Any], raw: bytes, *, firestore: Any) -> None` | hash/日付/timezone/版を再検証、失敗は `ValueError`、adapter失敗は伝播。既存helperへ渡しpath再読しない |
| `app.operations.forecast_persistence.project_forecast_plan(client: Any, *, raw: bytes, plan: PublishedPlan, private_plan_path: Path, timezone_name: str, recorded_at: str | None = None) -> int` | 新規immutable snapshot行数。rawとplan.sha256/target、24値、run-private fileの前後bytes/hashを検証。不一致は `ValueError`、adapter失敗は伝播。共有path/単一所有未確定ならG07で停止 |
| `app.operations.firestore.project_model_parameters(client: Any, *, raw: bytes, plan: PublishedPlan, updated_at: str) -> None` | 同版retry冪等な既存parameter projection。raw/hash・世代不一致は `ValueError`、adapter障害は伝播。既存docへの世代binding未確定ならG07で停止 |
| `app.operations.forecast_persistence._forecast_projection_values(data: dict[str, Any], *, target_date: str, timezone_name: str, now: str) -> list[tuple[str, str, dict[str, Any]]]` **NEW** | 既存24値/SOC/容量式の純粋抽出。無効日はValueError、保存0 |
| `app.operations.forecast_persistence.projection_identity(raw: bytes, plan: PublishedPlan) -> dict[str, str]` **NEW** | NEW4field。原本/hash/日付/timezone不一致はValueError |
| `app.operations.forecast_persistence.projection_may_advance(previous: dict[str, Any], incoming: dict[str, str]) -> bool` **NEW** | 自身doc世代比較、同版False、旧版/競合ValueError、初回/新版/同raw採用binding True |
| `app.operations.firestore._project_model_parameter_values(client: Any, *, coeff: dict[str, Any], identity: dict[str, str], updated_at: str) -> None` **NEW** | transaction全read先行、値/identity更新・sample冪等。adapter/世代例外伝播 |
| `app.separated_runtime.plan_store._provenance(plan: PublishedPlan) -> dict[str, Any]` **NEW** | fulltraceのみ抽出、legacyは{}、I/Oなし |
| `app.separated_runtime.plan_store._provenance_time(value: Any) -> datetime` **NEW** | timezone時刻解析、不正はValueError |
| `app.separated_runtime.plan_store._validate_incoming_provenance(plan: PublishedPlan) -> None` **NEW** | identity、retrieved/training<=generated検証、不正ValueError、legacy互換 |
| `app.separated_runtime.plan_store._check_provenance(incoming: PublishedPlan, adopted: PublishedPlan) -> None` **NEW** | CAS再読内の同basis/site/provider/training比較、後退ValueError。不明provider理由ログ |
| `app.dashboard.snapshots._check_source_epoch_order(previous: dict[str, str], incoming: dict[str, str]) -> None` **NEW** | componentwise後退/欠落/保守delete拒否はValueError、保存0 |
| `app.dashboard.snapshots._read_dashboard_source_epoch(client: Any, *, history_scope: bool, deadline: float | None = None, history_compare_before: str | None = None) -> dict[str, str]` **NEW** | native全source/保持範囲revision mapとread計数ログ。source/budget不正ValueError、deadline RuntimeError、I/O例外伝播。compare境界は旧history metadata由来、env/flagではない |
| `app.dashboard.snapshots._read_history_publication(*, storage_client: Any) -> tuple[dict[str, Any] | None, dict[str, str]]` **NEW** | producerだけ既存history URIのgzip/metadataを同generationで読む。未配置(None,{})、schema不正ValueError、読取中CAS競合は伝播してoldretain。GET経路では使わない |
| `app.dashboard.snapshots._validate_publication_payload(payload: dict[str, Any], plan: PublishedPlan) -> None` **NEW** | producerで採用原本24値/SOC17/容量/run検証、混合ValueError、GET利用0 |

既存 `write_dashboard_snapshots` へkeyword-only `published_plan: PublishedPlan | None=None/source_epoch: dict[str,str] | None=None/source_client: Any | None=None`、既存 `_upload_artifact` へ `publication_identity: dict[str,str] | None=None/source_epoch/source_client/history_scope: bool=False` を追加。既存caller既定/API結果は保持し、raw legacy writerが採用済みassetを後退させる場合は拒否する。

実Firestore SDKのtransactionID/protobufとGCS SDK multipart CAS requestをローカルtransportで確認した。FIRESTORE_EMULATOR_HOST未設定でserver競合未検証。Firestore max_attempts3/read timeout3/retryNone、SDK _begin/_commitは既定RPC設定で独立全transaction deadline未証明。G07はOPEN。fake/transportを実サーバーatomic競合証明へ昇格しない。

## 障害・ログ・時刻所有

採用前失敗/拒否はmutable candidate書込0。CAS成功後projection/asset/backup失敗はcontrol成功を取消・再生成・再適用せず、同版だけ非制御jobで復旧する。最後の整合assetと表示時刻を保持し、最新controlと常時原子的に一致するとは説明しない。

既存JSON一行方式で `plan_publication_rejected/plan_projection_failed/dashboard_snapshot_rejected/dashboard_plan_diagnostics_unavailable` を記録。keysは `event/stage/target_date/run_id/model_id/input_sha256/calibration_sha256/raw_plan_sha256/control_version_id/as_of/retrieved_at/provider_issued_at/elapsed_ms/reason_code`。未決定はnull。body/raw HTTP/秘密/座標を出さない。siteはfingerprintのみ。生成失敗・採用後表示失敗・backup失敗をcatch-allで成功化しない。

03 archiveは独立子process最大20秒、06:45まで60秒未満で開始しない、失敗後制御継続。23/07 DB非依存、06:45/06:50/06:55と07時の単独所有を保持。Q01: legacy `_run_day_07` は `profile="green"` を直接helperへ渡す一方、保護文書はeconomyを記す。mode-only workflow alias/実配備入口未確認のため運用障害とは断定せず、PV差分では変更しない。

## 入口と終了判定

S07はG05、S08はG05/G07、S09はG04/G07、S10はG07/G08を要求する。限定ローカル実装・fixtureは承認範囲で進め、実adapter/性能未完中はgateをOPENのまま導入/本番PASSを停止する。依存stepの出口は文書/fixture/ローカル証拠の受渡しも含む。NEW試験は追加済み、未実証条件は各stepに残す。fake/SDK transport PASSは実Firestore/GCS/SQL race、本番read-back、年間精度のPASSではない。本番はcanonical wrapperとその時点の明示承認へ引き継ぐ。

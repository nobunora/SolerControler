# R01 共通backend parityとFirestore原本SOC・容量を両方検証する

依存R00。このstageはSQLを新DBschemaへ拡張しない方針で固定する。既存dashboard表示・HTTP・保存核を変更しない。

## 調査根拠と採らない案

同じ31日windowの744行をread-onlyで照合。10/10・10/11各24行だけが不一致。SQLに容量48点・SOC34点がなく、共通fieldの数値差は0。
Firestore経路：forecast_plans→history_reconstruction同vintage結合→service/slice→画面。SQL経路：sync.TABLE_SPECS/insert→既存SQLite時間表→sqlite_repository→service/slice。SQLはrun/issuedを保存するが容量/毎時SOCを保存しない。
既存追加仕様contracts:89はSQLを未連携/参考とし、新schemaを禁止する。単純にIGNORED_FIELDSへ2項目を足すと、本番側の間違った容量・SOCまで未検出になるため採らない。

## 変更対象と禁止symbol

| source/symbol | 指定する変更 |
| --- | --- |
| scripts/validate_dashboard_backend_parity.py::main | 同じFirestore snapshotの共通parityと新独立照合を実行。両検証の結果/coverageを出し、いずれかFAIL/必要資料不足なら非zero |
| 同file NEW `partition_forecast_metadata` | コピー上で、SQLが未対応の容量/SOCだけを、原本検証PASSの証拠がある行から共通比較対象外へ分ける。両側に値がある時は通常比較、片側の予期しない欠落はFAIL |
| 新 scripts/validate_forecast_display_evidence.py | raw/保存forecast_plans/時間行を照合するimport module。collector＋純関数だけ、main/独立CLI/新classなし |
| 新 tests/test_forecast_display_evidence.py | 独立照合の値/型/出典/件数/期限を試験 |
| tests/test_dashboard_backend_parity.py / test_dashboard_backend_parity_validator.py | 共通値driftを隠さない、未知fieldを隠さない、証拠なしpartitionを拒否する回帰追加 |
| scripts/pre_release_integration.ps1 | R00順序内で改修済parity mainを一回だけ呼ぶ。同main内の独立照合が必須。別CLI/二重取得/任意skipなし |

禁止：`compare_rows` のdefault比較意味、既存IGNORED_FIELDSの一括拡張、sqlite.ensure_schema/sync.TABLE_SPECS/insert/read SQL変更、model/SOC/容量のproduction計算式、history_reconstructionのrun優先lock、browser参考SOC式、全schema/endpoint/制御入口。
`_metadata_matches_forecast_row`・`_with_forecast_plan_metadata`は読取参照のみ。metadataの同日だけの結合へ弱化しない。

## 型・関数・結果項目

新classなし。以下を署名/結果の唯一の正本とし、stageごとの別class/別検証器を作らない（Any/Pathは既存標準型、dictはprivate内部）。

- `load_forecast_evidence_for_window(*, client: Any, storage: Any, start_date: str, end_date: str, selected_hourly: list[dict[str, Any]], original_dir: Path | None = None, deadline: float) -> dict[str, dict[str, Any]]`
- `validate_forecast_day_evidence(*, target_date: str, evidence: dict[str, Any], displayed_hourly: list[dict[str, Any]], displayed_battery_daily: dict[str, Any] | None) -> dict[str, Any]`
- `validate_forecast_window_evidence(*, start_date: str, end_date: str, evidence_by_date: dict[str, dict[str, Any]], displayed_hourly: list[dict[str, Any]], displayed_battery_daily: list[dict[str, Any]]) -> dict[str, Any]`
- parity moduleの `partition_forecast_metadata(*, sqlite_rows: list[dict[str, Any]], firestore_rows: list[dict[str, Any]], day_results: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]`

唯一のcoordinatorは既存parity.main。既存全6datasetのSQL/Firestore pair取得→collector→pure window/day検証→PASS日のpartitionコピー→既存compare_rows全6dataset→AND結果の順。collectorはI/Oを所有、pure検証/partitionはI/O0・入力mutation0。driftの全pair再取得最大1回と60秒deadlineはmainが所有し、nested helperで更新しない。
追加 `--original-dir` は既存parity CLIだけで受け同じcollectorへ転送。既存 `--sqlite/--end-date/--window-days` とdefaultを維持し、window整数1..31/date妥当をI/O前に検査。独立原本CLI/NEW mainは作らず、結果・exit・取得の入口を増やさない。
collectorのday bundleは `target_date,metadata_exists,metadata,stored_hourly,stored_battery_daily_exists,stored_battery_daily,original,original_sha256,original_generation,forecast_identity,evidence_kind,revisions_before,revisions_after,read_count,read_bytes,failure_reasons`。存在はboolまたはRPCunknownのNone。raw bytes/保存payloadはprivate内部だけ、public結果へ出さない。

DayResultの項目は `status,target_date,evidence_kind,metadata_verified,original_verified,capacity_verified,soc_points_verified,required_soc_points,daily_metadata_verified,daily_metadata_fields_verified,failure_reasons,original_sha256,forecast_identity,read_count,read_bytes`。status=PASS/FAIL/INCONCLUSIVE、verified項目は検証coverageのbool、point/field/read項目は非負int、failure_reasonsはlist[str]。forecast_identityは `target_date,forecast_run_id,forecast_issued_at,raw_plan_sha256,control_version_id`、legacy欠損はNoneを保持する。
WindowResultは `schema_version=1,status,start_date,end_date,days,coverage,read_count,read_bytes,failure_reasons`。daysはdate→DayResult、coverageは非負intの `selected_forecast_days,new_required_days,new_verified_days,legacy_required_days,legacy_verified_days,legacy_unavailable_days,legacy_no_metadata_days,actual_only_rows,capacity_points_verified,soc_points_verified,daily_metadata_fields_verified` と `sql_capacity_supported=False,sql_soc_supported=False`。NEW0/0をNEW受入100％とはしない。
parity.mainだけが `common_parity_errors`（既存6datasetすべての件数）、`common_parity_status` を付け、top statusをAND判定へ更新する。既存dataset件数表示を保持し、末尾に `[forecast-evidence] `＋safe canonical JSON一行。bytes/payload/URI/秘密識別子を出さない。exit0=両PASS、exit1=既知FAILまたは予期しない例外、exit2=必要証拠不足INCONCLUSIVE。結果欠落をPASSにしない。

日計の独立照合も必須。既存SQL validation projectionと本番表示はmerge関数を共有するため、共通daily parityだけでは共有誤りを検出できない。元 `battery_daily_metrics` のtarget/night chargeをnative version付きで一日一GET（最大31）し、保存側がNULLのfieldだけ同世代原本/metadata期待値で補われることを確認する。保存nonnull fieldはその値を保持する。row全体のplan_display_sourceを理由に両fieldを原本値へ置換しない。原本取得前後のrevision集合に当該daily docも含め、RPC失敗をNULL/不存在へ変換しない。dashboard GETの読取追加0。

既存 `PublishedPlan` はNEW control証拠がある場合のみ原本date/bytes/SHA検証に利用。旧計画にinputs_verifiedやversionを推測で追加しない。
evidence_kindのenumはNEW_ADOPTED/LEGACY_ORIGINAL/LEGACY_UNAVAILABLE/LEGACY_NO_PLAN_METADATA。結果keyの正本は上記DayResult/WindowResultだけ。
非対応SQL表示のcoverageは `sql_capacity_supported=False`, `sql_soc_supported=False`。これは検証全体PASSと別の情報。原本・値・資格の検証結果をbool一つへ縮約しない。
coverageのdaily_metadata_fields_verifiedはNULLからforecast由来値を補って独立一致したfield数だけ。保存nonnull維持を原本補完数へ加算しない。read_count/read_bytesはcollectorの証拠読取件数/logical bytesで、network圧縮転送の実測とは別。純legacy共通PASSをNEW verified数へ混ぜない。failure_reasonsはdate/field/固定reasonだけ、例外時もsafe JSON一回＋非zero。

## 原本の選択規則・同一世代

1. `forecast_hourly` / snapshotで選ばれた24時間のrun/issuedを固定する。provider順で最後を使わず、既存選択結果を用いる。
2. 保存forecast_plansを日付範囲query最大32docs（31日＋sentinel）、既存原本decisionを同じ日・run・issued・SHAで探す。NEWは `night_plan_decisions/{target_date}--{raw_plan_sha256}` を直接GETし、legacyは `night_charge_plans/{target_date}` の同issued詳細だけを候補とする。別日のlatest controlを候補にしない。1日1原本候補に絞れなければFAIL。source決定前に全部のarchiveを取得しない。
3. NEWは `control_version_id`, `raw_plan_sha256`, target date/generation、採用されたPublishedPlan/既存immutable decisionのbytesを照合。raw SHA違いはFAIL。原本取得が必要なら既存detail URIの明示objectだけをGETし、generation pin・bounded gzip/decoded bytesを検査する。bucket listや全文履歴scanは禁止。
4. legacyは同issuedの保存詳細がある場合だけ原本照合。既存Drive restore/今回凍結原本を指定する場合だけ、CLI `--original-dir` のprivate directoryからdate・generated_at・run・SHAの一致が証明できる1ファイルを選択する。timestamp同日だけの結合・latest controlの別run流用は禁止。完全詳細がなければ `LEGACY_UNAVAILABLE` と件数を明示し、原本を当日再計算して捏造しない。
5. 取得前後の証拠集合は、日ごとの選択済み全24forecast行（mutable/snapshotの同じselection）、forecast_plans、選んだnight_plan_decisions/night_charge_plans、原本generation/SHA。forecast行は全id＋native update_time＋run/issued/PV/load/chargeのsorted canonical digestを含む。metadata/rawだけが不変でもforecast行変化をdriftとして扱う。同期後SQLが古い場合は共通parityがFAILし、自動SQL上書きで隠さない。driftは最大1回、SQLite/Firestoreの全pairと全証拠をまとめて再取得し、2回目drift/期限超過はINCONCLUSIVE・非zero。行ごとの部分retryや異なるrun間の混成でPASSしない。
6. 最大原本31個、各decoded750,000 bytes、各RPC最大3秒（GCS4秒）、全体60秒。deadline残時間以下にtimeoutを設定し、SDK refresh/retryの隠れ延長はR04で実証する。実serverのtimeoutをこの数値指定だけで保証したとしない。

### legacy資料不足の扱い

既存2日の容量/SOCがある今回の差は、metadataとの同世代値照合と原本一致を両方必須にする。対象31日すべてにmetadataがあると仮定しない。
原本証明が必要なのは、NEWと、現に保存容量/SOCを表示するlegacyの日。純粋な旧PV/load行でforecast_plans **raw documentが存在しないことをnative snapshotで独立確認**した場合だけ `LEGACY_NO_PLAN_METADATA` とcoverageへ記録し、共通parityを検証する。`_forecast_plan_metadata_by_date` がinvalid/partial metadataを棄却して返した空dict、例外を握り潰した空dictをdocument不存在の証明に使わない。raw doc存在＋invalid/partialはFAIL、存在確認RPC失敗はINCONCLUSIVE。actual-only行はPV/load/charge/run/issued/update/sourceのどれにもforecast資格がなく、独立raw forecast doc不存在を確認できる場合だけ原本照合対象外。実測parityは引き続き必須とする。
必要legacy原本なしはINCONCLUSIVE・非zero。**実装担当が必要資料不足を非blockingへ変更してはならない。** NEWモデルの対象受入も全日原本照合100％必須。この読取原本が取得できるかは実装時の取得検証事項としてR05へ渡し、今回の文書レビューでPASSにしない。G06の全旧scenario不足はこのcapacity/SOC照合と別の検証範囲。

## 数値・時間の照合

- capacityは原本 `result.effective_capacity_kwh`、targetは `result.target_soc_7_percent`、night chargeは `result.required_night_charge_kwh`。bool/string/None/非有限を有効値へcoerceしない。capacityはstrict >0、**0もFAIL**。targetは0..100、night chargeは>=0。capacity/target/night chargeのraw→保存metadata→表示許容差は絶対1e-9（source単位kWh/百分点）とし、丸め/clipを追加しない。
- PV/loadの参照元は `original.forecast.date` と `original.daytime_soc_optimization.hourly_pv_forecast_kwh/hourly_load_forecast_kwh`。Python dict入力はstr(hour)優先、int(hour)fallback（JSON bytesではstring key）。production `app.operations.domain.extract_hourly_forecast_from_plan` と同じPython組込 `round(value, 4)` を使う（half-even、binary float表現を含め同じ操作）。Decimal量子化や別丸めへ置換しない。hourly weatherと両mapのhour集合から24行が必要で、宣言したlegacy PV窓外の暗黙night0だけは旧契約に従う。5/6 keyありならpv_start=5、それ以外は7、モデル窓内PV欠測と全時刻load欠測はNone→FAIL。任意欠測に0を代入しない。
- raw PV/loadはfinite非bool非負が必要。保存PV/loadと `round(raw_value, 4)` の一致を1e-9許容で検証。raw値との差の上限も各時間0.00005kWh、全日24×0.00005kWh以内とする。保存chargeは丸め済PV−loadではなく、productionどおり **未丸めraw PV−loadから** `round(max(0.0, raw_pv-raw_load),4)` を使い1e-9許容で照合。data mapperの誤りを共有しないよう、このraw参照式をpure validatorに直接明示する。
- 保存planned_hourly_soc_percentは07開始〜23終了の17点。hour23の終点と23〜24発電区間を混同しない。0..6の計画SOCを追加生成しない。
- 既存 `simulate_daytime_soc_percent` を**上記の原本→Python round4dpで生成されたPV/load24・同じtarget/capacity**に一回だけ適用して期待SOCを確認。SOC自体はproduction保存どおり追加roundなしで17点を保存/比較し、許容差は1e-7百分点。独立のpulse/energy-balance fixtureでhelper自身も検証する。最新容量・最新実測起点・温度再補正を使わない。
- 表示forecast_capacity_kwhは24行、forecast_soc_percentは17保存点に一致。SOC0は有効、None/欠損を0へ変換しない。metadataに有効17点があれば表示が脱落した時はFAIL。
- run/issued/SHA等と値の検査を別にする。偶然値が一致していても別runはFAIL。NEWでsource SHAが無い場合もFAIL。

## 共通parityと独立照合のAND

`compare_rows` は既存のlogical day/hour identity・duplicate・coverage・NULL/omittedの比較を維持。
純legacyのmetadataなし行でも、新純validatorで共通business numeric（PV/load/charge/実測energy/SOC）が存在する場合はfinite・非bool・非stringと範囲をチェックする。hourは0..23の整数でbool・fractionを拒否（既存backendsの数字hour stringはlogical identityとしてのみ許容し、値fieldはcoerceしない）。これにより既存 `_same_value` のTrue≈1やstr(value)一致だけで本検証がPASSにならない。raw原本型が提供されていないpurelegacyはその型の原本証明不足をcoverageへ明記し、新モデルの原本型検証PASSと混同しない。
partition許可fieldは容量・SOCの2つだけ。SQL値が未対応かつ同じFirestore値に独立照合PASSの証拠がある場合だけ外す。PV/load/charge/実測/SOC実測/run/issued・未知fieldは一切外さない。
独立照合FAIL/INCONCLUSIVEならpartition後の共通parityが0errorsでも全体非zero。先にignoreして後から任意検証を足す順序は禁止。
共有field parityと原本照合は同じselected snapshotに対して実行。Firestore dashboard GETに追加読取を入れず、検証CLIでだけ実施する。

## 予定テストとPASS条件

| ID | fixture / テスト名（予定） | 要求 |
| --- | --- | --- |
| V01 | `test_existing_48_rows_split_only_after_evidence_pass` | 48容量・34SOCの不足を再現、partition+独立照合で共通値0errors |
| V02 | `test_unknown_or_common_field_drift_still_fails` | PV/load/run/issued/実測/未知field差を必ずFAIL |
| V03 | `test_capacity_soc_drift_not_ignored` | 同日別run、容量1％違い、SOC1点違い、原本SHA違い、両側存在値違いをFAIL。日計2fieldの片方のみNULL/補完、もう片方保存nonnull維持を独立照合 |
| V04 | `test_invalid_numeric_evidence_fails` | bool/string/NaN/inf/負/範囲外/部分17点、正当SOC0/夜間charge0と不正capacity0、purelegacy True≈1や数値stringを区別 |
| V05 | `test_original_rounding_matches_saved_projection` | Python round(value,4) half-even/binary境界、str/int key優先、raw差chargeとrounded差chargeが違うfixture、legacy宣言窓外0と窓内欠測拒否、24行/17SOC追加round0、pulse/energy balance、23終点、容量温度再適用0。共有daily mergeを誤った結果へ差替えても独立原本/保存nonnull検査はFAIL |
| V06 | `test_missing_original_is_not_pass` | NEW欠落FAIL、legacy欠落INCONCLUSIVE、rawdoc不存在とinvalid/partial棄却やRPC例外を区別、actual-only資格、欠損を生成0で埋めない |
| V07 | `test_selected_snapshot_drift_and_limits` | query32sentinel、750kB、31object、全24forecast行だけが変わるdriftも検出、全pair1retry、60s deadline、原本なしbucketlist0 |
| V08 | `test_validation_writes_nothing` | Firestore/GCS/KPの全mutationを禁止fixtureにし0call、SQLもschema変更0 |
| V09 | `test_gate_requires_both_checks` | common0errorsでも原本失敗/不明なら非zero。任意skip0。integrationからparity一回/独立CLI追加0、collector取得一回（drift時全pair一retryのみ）、入力不変、original-dir転送 |

ローカルfixturePASSに加え、実データ48行の共通一致・metadata資格・原本coverageを記録する。実data全原本照合はこの設計作成時点で未実行。G04/G07のrace・性能はR04へ渡し、このstageだけでCLOSEDにしない。

## 出口とログ

既存commandは非zeroを維持し、新しい成功結果にSQL未対応件数と原本照合件数を表示。case_date/field/reason/countだけ公開し、raw JSON/URI/project/tokenをログしない。
R05へ旧legacy資料不足とNEW100受入100％の二つのcoverageを渡す。schema追加案はこのpacketの実装対象から外す。必要なら独立の仕様変更として判断し、R01の途中で混ぜない。

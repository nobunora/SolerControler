# NEW100 dashboard供給経路監査・追加実装詳細

2026-10-12、現working treeを対象にしたproducer→projection→snapshot→DTOの追加監査。**本書は設計・source確認であり、追加実装の承認/実装済み/本番表示PASSを意味しない。production source/testは変更していない。** JS/HTML/browserは別担当。親のCodebaseMemory ready 8275/29528・対象一hop 82edgeはnavigationだけに利用し、結論は以下のsourceと既存test契約で確認した。本追加監査ではpytestを実行していない。

正本は [共通契約](../contracts.md)、[データ契約](../data-contracts.md)、[公開契約](../publication-contracts.md)。以下のNEW helper/field/testは**PLANNED**。新endpoint/collection/env/feature flag/dependencyを追加しない。保存・制御・実績金融値を変更しない。

## 1. 経路と供給境界

1. 原本: `result_builder._clear_ghi_trace` が `decision_rationale.pv_clear_ghi` 一箇所へpack/時刻/24trace/内部精度totalsを保存する。[result_builder.py:23](../../../../../app/energy_plan/result_builder.py#L23)。`MODEL_ID=physical_clear_ghi_annual1`、`BASIS_VERSION=noaa_geometric_1min_midpoint_annual1_v1`、termsは `1/x/cos_phi/sin_phi`。
2. 生成・採用: `planner.main` はrun-private raw→immutable archive→既存control CAS→同raw projection。`PublishedPlan.raw_json/sha256/version_id` が原本binding。共有mutable local planの読戻しや別係数currentの読取でNEW100表示を再構成しない。
3. projection: `forecast_persistence._forecast_projection_values` がfinal PV24/load24、日合計/時間帯、forecast run、目標/容量/SOC17を既存docへ保存する。[forecast_persistence.py:84](../../../../../app/operations/forecast_persistence.py#L84)。`project_forecast_plan` は自身のforecast_plans docをtransaction読取し26writeへ同じ既存内部4identityを付ける。
4. adapter→DTO: Firestore/SQLite/Postgresのrow→`DashboardRawData`→`slice_assembler.build_dashboard_slice`→`service.assemble_dashboard_slice`→`DashboardData`。各list/dictを通す構造で、`DashboardData.pv_forecast_diagnostics` は既存dict。[models.py:8](../../../../../app/dashboard/models.py#L8)、[service.py:84](../../../../../app/dashboard/service.py#L84)。
5. producer asset: `snapshots.write_dashboard_snapshots` がproducer cache clear、fresh materialization、原本24値/SOC/容量/run検証、native source-map再検証、gzip固定、GCS generation CAS。browserのbootstrapはこのartifactを消費。GETが予測/学習/修復しない。通常bootstrap31日、historyは明示操作。schema1/ETag/reader30秒cache、producer120秒cacheとG04 source-map/8KiB/期限を維持する。
6. fallback: assetなしの既存bounded adapter読取、非static sliceは従来経路。Firestore診断はnight_charge_plans/latest、SQL診断は既存local JSON。この読取にNEW DB/APIアクセスを追加せず、採用identityのないfallbackでactive NEW100を旧モデルとして説明しない。

## 2. 全供給fieldの棚卸し

「保持」はsource/既存局所回帰の供給契約を指し、browser/live表示を確認したPASSではない。

| 表示群 | 現在のfieldと原本/保存元 | 状態・境界 |
| --- | --- | --- |
| hourly PV/load/昼充電 | `forecast_hourly[].date/hour/forecast_pv_kwh/forecast_load_kwh/forecast_charge_kwh` ← final optimization hourly map | 保持。4dp保存、Firestoreは24行/全hour/finite/同run確認後のみ採用。旧PV倍率再適用0 |
| 予測世代 | hourly `forecast_run_id/forecast_issued_at/source`、daily `forecast_run_id/forecast_issued_at/forecast_provenance_kind/forecast_is_reconstructed` | Firestore保持、SQLite既存column時保持。sourceは保存経路のgeneric名でNEW100 model_idとは別。Postgres identityは保存側から未対応 |
| planned SOC | forecast_plans `planned_target_soc_percent/planned_capacity_kwh/planned_hourly_soc_percent` → hourly `forecast_target_soc_percent/forecast_capacity_kwh/forecast_soc_percent` | Firestore同run joinで保持。SOC17は07〜22の開始値＋23時終了点。00〜06はこの原本予測の供給対象外 |
| actual hourly | `actual_load_kwh/actual_soc_percent/first_sample_at/opening_soc_percent/latest_sample_at` ← monitoring_samples | 保持。`service.merge_forecast_hourly_actuals` は実測を別fieldに付ける。actualを予想SOCと呼ぶ契約ではない |
| PV日計 | pv_daily `forecast_pv_total_kwh`、energy_daily `forecast_pv_kwh` | 保持。final24合計。energyはselected hourly PV/load同一pairを優先、別run日集計で一側を上書きしない |
| PV時間帯 | `forecast_pv_morning_kwh/midday_kwh/evening_kwh` | 保持。07〜09/10〜15/16〜22。full24日計と3part合計は同義でない（00〜06/23をpartに含めない） |
| legacy/raw totals | result `predicted_pv_kwh`、`final_predicted_pv_kwh`、trace totals `full_day_pv_kwh/decision_day_pv_kwh` | legacy resultをfinal24の代用にしない。金融decisionは07〜22、全日グラフは24区間。内部full precisionと公開4dpの差も区別 |
| PV/負荷実績と日差 | energy_daily `actual_pv_kwh/actual_load_kwh`＋同日のforecast pair | 実績供給保持。DTOに日差/率fieldなし、consumerで日/区間/availability一致を確認する必要。欠損を有効0へ変えない |
| 期間集計 | energy_daily日列、cost_daily、cost_monthly `month/period_start/period_end/self_consumption_kwh/savings_yen` | 期間締日/実績金融値保持。historyにはdaily列のみでhourly/diagnostics/review/staticは空。periodカードの完全性は現DTOにfull-day completeness markerなし |
| NEW100モデル診断 | diagnostics `plan_date/physical/forecast_correction/hourly_weather_pv_shape/overnight_load_forecast` | **欠落**: extractorはpv_clear_ghiを落とす。physicalは選択model/hash/repairedの小dictだけでpack training/時刻/terms/thetaを補えない |
| 学習日数・samples | 原本trace.calibration_pack `training_dates/training_samples/rank/condition_number` | 原本あり、現在DTO供給なし。学習は05〜17の13interval×認定日数。model_parameters.sample_countへ代替禁止 |
| model_parameters | 全name `mean_value/variance/sample_count` | legacy係数保存doc。sample_countは原本世代の更新回数、varianceは既存abs(mean)*0.05。旧PV docもfilterなしで供給。NEW100訓練数・統計varianceとして使わない |
| weather | pv_daily `forecast_temp_c/actual_temp_c`、原本forecast/hourly、保存hourly weather各field | 日温度供給あり。hourly weatherは保存されるがadapterのhourly SELECT/allowlistは主にenergy/identityであり、全weather fieldのDTO供給は未対応。要求UIがあるか別担当と照合 |
| warnings | `dashboard_warnings[].code/severity/title/message/detail` | SOC未達/03未記録/実績stale/reconstructed/original missing/actual missing/completionを供給。親確認ではNEW100 model IDを拒否しない。校正refresh成功を示すfieldではない |
| 日次review | `daily_review/daily_reviews` のPV/load/実績/SOC/expected buy・sell/analog/billing | Firestore静的sliceだけ供給、SQLは既定{}・[]。PV/loadはenergy正本pair。plan由来expected値はdate-only bindingでDF05が残る |
| 制御・時刻・完了 | latest_schedule `plan_date/charge_start_time/charge_end_time/night_window_*/day_discharge_window_*/discharge_fixed_window/soc_*_mode/status/recorded_at/slot/profile/settings_completed_*` | 同日event/detail.plan_date、battery.date、同run完了で構成。HTTP status/保存成功だけでphysical appliedを証明しない |
| 充電見積/目標 | latest_schedule `estimated_charge_power_kw/estimated_charge_minutes/estimated_charge_rate_percent_per_hour/charge_rate_source/charge_rate_sample_count/required_charge_percent_at_schedule/planned_target_soc_percent/planned_night_charge_kwh` | 正本/applied優先を保持。backendはdate-matching。別日hourlyへlatest_scheduleを流用する根拠はない |
| 料金・明細 | review `billing_period_*/billing_close_day/cumulative_*_buy_kwh/projected_*_buy_kwh/projected_*_cost_yen/tier*_arrival`、cost_daily/monthly | 実績billingと期間外挿。plan expected buy/sellと別由来。価格/締日/costmodelの計算・数値変更は今回対象外 |
| 寿命・劣化・容量 | raw coefficients `battery_usable_capacity_kwh/battery_temp_coeff_per_deg/battery_cycle_capacity_fade_per_cycle`、input cycle/temp、result effective capacity、plan cost_model | 部分値は保存されるがDTOに完整寿命説明モデル/price assumptionsの独立fieldなし。SQL SOC拡張、新金融計算、現行定数を正本と装う補完をしない |

### 主な根拠

- final hourly/日計/parts: `app.operations.domain.extract_hourly_forecast_from_plan` (19)、`extract_final_pv_totals_from_plan` (86)、`extract_final_pv_source_from_plan` (122)。[domain.py](../../../../../app/operations/domain.py#L86)。結果totalは24値、partialはNone、legacy fallbackはhourlyなし時だけ。
- selected pair/日provenance: `app.dashboard.aggregation._build_energy_daily` (166/223/227/267)。[aggregation.py](../../../../../app/dashboard/aggregation.py#L166)。hourlyを日ごと合算し、paired存在時はPV/loadを同じ経路で選ぶ。
- SOC17: `app.energy_plan.soc_cost._simulate_day` (395/418)、`simulate_daytime_soc_percent` (422)。[soc_cost.py](../../../../../app/energy_plan/soc_cost.py#L395)。23の値は22区間後のend_soc、23区間を再計算した値ではない。
- SOC保存/join: `_forecast_projection_values` (106/171)、`app.dashboard.history_reconstruction._forecast_plan_metadata_by_date` (63/96)、`_metadata_matches_forecast_row` (115)、`_with_forecast_plan_metadata` (142/158)。[history_reconstruction.py](../../../../../app/dashboard/history_reconstruction.py#L63)。known run優先、異run metadata拒否、legacy timestamp fallbackとlegacy両identityなしは従来境界。
- 診断/DTO: `app.dashboard.slice_assembler.extract_pv_forecast_diagnostics` (21/29)、`read_latest_pv_forecast_diagnostics` (32)、`app.dashboard.firestore_repository._read_latest_pv_forecast_diagnostics_from_firestore` (34)。[slice_assembler.py](../../../../../app/dashboard/slice_assembler.py#L21)。現診断にはdate以外のrun/time bindingなし。
- 旧PVrows: `app.dashboard.firestore_repository.load_firestore_slice` (788)、`app.dashboard.sqlite_repository.load_sqlite_query_snapshot` (213)、`app.dashboard.postgres_repository.load_postgres_slice` (220)。[firestore_repository.py](../../../../../app/dashboard/firestore_repository.py#L788)。`app.operations.firestore._project_model_parameter_values` (927/948) は同raw retry冪等、storage row削除をしない。
- schedule: `app.dashboard.schedule._schedule_event_candidates` (123/132)、`_build_latest_schedule_from_events` (152/179)、`app.dashboard.slice_assembler.merge_latest_plan_into_schedule` (49/57)。[schedule.py](../../../../../app/dashboard/schedule.py#L123)。表示側でこの同日契約を弱めない。

## 3. 確定gapと未検証を分離

| ID | source確認した問題/限界 | 最小追加scope |
| --- | --- | --- |
| DF01 | NEW100 trace/pack/訓練数/係数をdiagnostics extractorが供給しない | producer固定rawから小要約だけpv_modelへ。旧physicalを新訓練根拠へ転用しない |
| DF02 | diagnosticsはplan_dateのみ。モデル/係数/取得/生成/forecast runを画面の同日別runへ束ねられない | producer既存identityとsingle runをcopy。旧fallback NEWはunsupported、unknownを同版へ推測しない |
| DF03 | model_parameters全row無filter、旧PV残存/更新回数をactive NEW学習として誤用可能 | NEW100のactive診断/学習viewだけ旧PV rows suppression。persistent DELETE/update/係数学習をしない |
| DF04 | forecast source/model IDが保存されてもadapter/日集計はgeneric source主体 | latestモデルはpv_model.model_idで明示。historical日には既存forecast_plans.forecast_sourceを既存run一致結果から渡せる範囲だけ提示、欠落はunknown |
| DF05 | 日次reviewのPV/loadはselected pairだがplan.result buy/sell/target fallbackはdate-only。reviewにrun/source/issuedを渡さない | 既に読んだrow/planのexisting hash/identityを内部突合し、known mismatchのplan由来部分値だけnull＋理由、実績/PV/load/review全体を消さない |
| DF06 | SQL SOC/target/capacityはSELECTだけの脱落ではなく保存側未対応。Postgres run/issuedも保存側未対応 | schema新設なし。本scopeはpersisted unavailable/参考推定の明示とSQLite既存run維持。SQL persisted SOC実装は別残件 |
| DF07 | `_build_energy_daily` のpaired判定は日内PV/loadの存在であり24値完全性判定ではない。SQL partial rowを日予測として合算し得る | original日へ24hour/finite/single run completeness/coverageを小fieldで示す。partial量を全日予測/有効0として扱わない。Firestore upstreamのcomplete-day guardは保持 |

DF01〜05/07は供給・bindingの確定gap、DF06は既存backend能力限界。browserで何が誤表示されるかは別担当の実証と統合する。現production dataの旧PV残存実数、SQL実DBの追加独自column、校正current更新履歴、全期間の完全性、HTTP/cache/live-deviceは本監査未確認。

SQLite DDL [sqlite.py:225](../../../../../app/operations/sqlite.py#L225) とensure-columns (284) はforecast_run_id/forecast_issued_atあり、planned SOC/target/capacityとforecast_plans tableなし。queryのidentity_sqlは既存columnだけ選択する。[sqlite_repository.py:111](../../../../../app/dashboard/sqlite_repository.py#L111)。Postgres DDL [postgres.py:163](../../../../../app/operations/postgres.py#L163) とquery [postgres_repository.py:154](../../../../../app/dashboard/postgres_repository.py#L154) に上記identity/SOCなし。今回の症状を理由に未知storage schemaへ新contractを追加しない。

## 4. PLANNED compact summaryのexact契約

既存 `DashboardData.pv_forecast_diagnostics: dict[str, Any]` の任意namespace **`pv_model`** に限定する。新top-level DTO/new endpointなし。旧 `physical/forecast_correction/hourly_weather_pv_shape/overnight_load_forecast/plan_date` は保持する。

| namespace内field | 型/意味・原本source |
| --- | --- |
| `schema_version/status/reason_code` | int1、`valid/legacy/invalid/unsupported`、nullable bounded enum。statusはdisplay metadata検証結果。refresh成功/physical appliedの意味を持たない |
| `model_id/basis_version` | nullable str。原本traceの既定ID/basis。unsupported NEW100を旧物理モデルへ降格しない |
| `plan_date/generated_at` | ISO date/timezone時刻。forecast.date/既存producer generationをcopy |
| `forecast_run_id/raw_plan_sha256/control_version_id` | nullable str。既存payloadの唯一run、既存producer原本hash/PublishedPlan版をcopy。raw再serializeでhashを作り直さない |
| `forecast_sha256/calibration_sha256` | nullable SHA256。traceの予報入力hash/pack原本hash。forecast_run_id/全文原本hashとは別物 |
| `retrieved_at/as_of/provider_issued_at` | timezone時刻/nullable provider issued。取得、計算参照点、provider発行を分ける。unknown issued=Noneをrefresh失敗/成功へ変換しない |
| `training_day_count/training_sample_count/training_start_date/training_end_date` | pack.training_datesのunique昇順日数/13*N/min/max。全日リストを返さない。target当日以後のtrainingはinvalid |
| `training_as_of/created_at/usable_from` | packの知識cutoff/pack生成/利用可能時刻。採用前の校正metadataであり外部current更新成功を表さない |
| `terms/theta4/rank/condition_number` | 4term `1/x/cos_phi/sin_phi`、finite4数値、int rank4、finite condition。boolを数値/intとして認めない。thetaは式の説明値で再fit/予測へ使用しない |
| `repaired_hours` | sorted unique int0..23、表示上限24（実model repair上限を変更する意味ではない）。raw24traceは返さない |
| `totals` | nested dictの `full_day_pv_kwh/decision_day_pv_kwh/morning_pv_kwh/midday_pv_kwh/evening_pv_kwh`。原本trace.totalsのfinite非負値、順に24区間/07〜22/07〜09/10〜15/16〜22。namespace直下へ平坦化せず、計算して別正本へ置換しない |

UTF-8 compact JSONのnamespace全体上限**4096 bytes**、allow_nan=False。C02のcommon文字列最大bytesも適用し、超過値はNoneでtruncate identityを作らない。fallback自体も4096 bytesを再検査する。全training_dates、24 hourly trace/F/Q/C/source_times、座標/site原本、raw HTTP、private path、complete pack、学習観測buffer、observer state、credential、refresh偽stateを含めない。

状態別shape/finite reason enumは [追加契約C02](contracts.md#c02-単一モデル要約) を正本にする。common keysは `schema_version/status/reason_code/model_id/plan_date/generated_at/forecast_run_id/raw_plan_sha256/control_version_id`。validだけ全metadataを持ち、provider_issued_atだけNone可。legacy/invalid/unsupportedはcommon keysだけで、未確認identityはNone、theta/学習件数を推測して埋めない。valid/legacyのreason_codeはNone。invalidは `clear_ghi_trace_missing/plan_identity_mismatch/display_metadata_invalid/display_summary_too_large`、unsupportedは `unsupported_model/producer_identity_unavailable/forecast_run_unavailable` のみとする。

PV/load正本・actual・financialsを保持する。producer eventは `event="dashboard_pv_model_summary_unavailable"/stage="bootstrap_summary"/target_date/model_id/reason_code` のみ。target_dateは既存producer対象日、model_id/reason_codeは上記summaryからcopyし、任意exception/URL/bodyを代用しない。D01のstampは原本・24値/SOC検証成功後かつartifact化前。GET呼出しでbody/pack取得・再計算・ログ洪水を起こさない。

## 5. PLANNED symbol/呼出し差分

| 完全修飾symbol | exact差分 |
| --- | --- |
| **NEW** `app.dashboard.slice_assembler._build_pv_model_summary(data: dict[str, Any], *, producer_identity: dict[str, str] | None = None, forecast_run_id: str | None = None) -> dict[str, Any]` | stdlibのみで上記allowlist/型/finite/bool拒否/13*N/dates/時刻/SHA形式を軽く検証。入力metadata不正はinvalid有限reason、unsupportedは有限reason、legacyはreason_code=None。I/O/forecast/fit/NumPy import0。内部バグをcatch-all成功化しない |
| existing `app.dashboard.slice_assembler.extract_pv_forecast_diagnostics` | 同じoptional keyword2個を追加しNEW helperを呼ぶ。producer identityなしdefaultではNEWモデルstatus unsupported、旧原本はlegacy。既存4dictの意味を変更しない |
| existing `app.dashboard.snapshots.write_dashboard_snapshots` | 固定 `published_plan.raw_json` を既存local `original` として使用。既存 `_validate_publication_payload(payload,published_plan)` 成功後のbootstrapだけ同原本extractorをcallしpv_modelを入れる。取得/原本再serialize/GET DB queryを追加しない |
| existing `app.dashboard.snapshots._validate_publication_payload` **呼出のみ** | 既存PV24/load24/SOC17/capacity/runの検証に使う。本文・署名・数学・保護条件を変更しない。成功後、artifact化前のextractor/NEW helperが同producer identityをcopy・検証してsummaryを構成する |
| existing `app.dashboard.models.DashboardData` / `service.assemble_dashboard_slice` | diagnostics既存dictで運ぶ。公開forecast rowsから新private markerだけ除去し、既存date/run/issued/sourceは保持。新class/schema不要 |

現 `write_dashboard_snapshots(db_path: Path, *, storage_client: Any | None = None, update_history: bool = False, full_history_rebuild: bool = False, published_plan: PublishedPlan | None = None, source_epoch: dict[str, str] | None = None, source_client: Any | None = None) -> dict[str, dict[str, Any]]` は [snapshots.py:383](../../../../../app/dashboard/snapshots.py#L383)。既存 `original=json.loads(published_plan.raw_json)`、`publication_identity={target_date,generated_at,control_version_id,raw_plan_sha256}` を再利用。forecast_run_idはvalidated payloadの全matching rowの唯一nonempty値をcopyする。bodyのsourceとsummaryを独立local JSONから混ぜない。

provider-issued unknownをvalidに許容しつつunknownと表示。future training/naive timezone/bool/Nan/Inf/hash形式不正はinvalid。as_of/retrieved/generated/pack時刻は意味別検証し、既存packのhashを表示側でcanonicalize/hashし直さない。`app.forecasting.pv_clear_ghi`、calibration IO/store/update、observer、energy workflowをdashboard helperからimportしない（NumPy/新dependency流入0）。

### browser/非static sliceの比較対象

既存public `forecast_hourly[].date: str/forecast_run_id?: str/forecast_issued_at?: str/source?: str`、`energy_daily[].date: str/forecast_run_id: str|None/forecast_issued_at: str|None/forecast_provenance_kind: str/forecast_is_reconstructed: bool` を使う。選択日がsummary.plan_dateと一致し、全選択hourly rowが唯一nonempty runでsummary.forecast_run_idと一致した時だけ当該モデルmetadataを表示する。異日/異run/未知run/reconstructedへのsummary流用は禁止。

これは**数値forecast vintage**の一致であり、exact raw/calibration identityの保証はproducer固定raw validation済みbootstrapに限定する。SQL/legacy/後の非static sliceでraw/control比較fieldがないならunknownとして扱う。新 `meta.forecast_generations_by_date` mappingは**不採用・実在しない**。時刻同値や最新scheduleだけから同runを推測しない。

### active旧PV rowsとdaily coverage

NEW100のactive診断/学習viewはpv_modelだけを使い、legacy `pv_kwh_per_sunhour/pv_temp_coeff_per_deg/pv_array_calibration_factor/pv_array_total_capacity_kw/physical_pv_radiation_scale/physical_pv_global_bias_scale/pv_forecast_error_ratio_*` をNEW100係数・訓練数へ代替しない。payload/view suppressionだけで保存docを削除/更新しない。battery/self-consumption金融係数まで一括除外しない。

original forecast日について既存rowから24distinct hour/finite PV+load/唯一runを確認し、PLANNED optional `energy_daily.forecast_available_hour_count: int|None` だけを付ける。known countは0..24、legacyなどcoverage不明はNone。完全性はknown count<24から局所的に導出し、新しいboolean/global flagを置かない。reviewにも同じcountをcopyする。known partialはreview局所noteとPV/load chartのtooltip/status captionで未完を明示し、部分合計を全日予想として断定しない。legacy/reconstructedの既存量・source labelを消さず、精度・coverage未知を明示する。数値/global aggregation、actual completeness/48slot/有効0、金融totalの再計算は変更しない。

## 6. PLANNED daily review部分binding

`app.dashboard.firestore_repository._build_firestore_daily_reviews` (517) はenergy_dailyのPV/loadを正本にする一方、(572/573)のbuy/sellと(566)のtarget fallbackを日付だけで読んだplan.resultから取得する。[firestore_repository.py](../../../../../app/dashboard/firestore_repository.py#L517)。`forecast_date/plan_date`だけで同vintageは証明できない。

1. 既存forecast doc内の内部4identityの `raw_plan_sha256` とplan docの既存 `detail_sha256` を比較する。原本SHAは [plan_snapshot.py:59](../../../../../app/backup/plan_snapshot.py#L59) でexact rawから保存済み。既存reader `_firestore_forecast_hourly_between` のfields allowlist (601)へprivate markerを取り込むだけで追加DBread0。
2. `_build_energy_daily` の既存selected runのsubsetだけでmarkerを集約し、unique時だけ内部 `"_raw_plan_sha256"/"_control_version_id"/"_plan_generated_at"` を保持。日内の別run履歴だけでmismatchにしない。reviewは既存読取済みselected-run hourlyのknown hash集合を再確認し、複数known hashがaggregate Noneからlegacy_unknownへ消えないようmismatchedへ運ぶ。全対象rowが一known hashでplan.detail_sha256と一致時だけmatched、known+missingはlegacy_unknown。選択run不明の複数known runはmismatched。公開24row hash複製/新flag/新queryは0。
3. reviewへ既存selected pairの `forecast_run_id/forecast_issued_at/forecast_pv_source/forecast_load_source/forecast_provenance_kind` を日単位で付け、PLANNED `plan_forecast_binding: matched|mismatched|legacy_unknown` とbounded `plan_forecast_binding_reason`、同日energy_dailyのoptional `forecast_available_hour_count: int|None` を返す。count<24/Noneは局所noteで示し、数値/global集計を変えない。
4. known mismatchでは**plan.result由来** `forecast_day_buy_kwh/forecast_sell_kwh`、result target/夜間充電fallback、今後明細のplan cost_model由来fieldだけnull＋理由。actual PV/load/買売/financial/battery由来/applied target/billingやreview全体は保持。unknown legacyはPV/loadと従来量を保ちlegacy_unknownと明示し、NEW100 raw一致を主張しない。
5. `service.assemble_dashboard_slice` の公開境界で上記3private markerだけを除去。既存forecast_run/issued/dateとreview binding結果は保持。plan/body current/以前の学習係数を再fetchしない。

未来の料金/寿命明細もselected planのcost_model由来を識別してから表示する。期待買売/料金/劣化費の再計算・actual financial mutationは対象外。別CLIの24h PVと07〜22金融PVの比較範囲問題は親のQとして別scope、ここでreport生成部を変更しない。

## 7. 禁止symbol/変数境界

- `app.operations.forecast_persistence._forecast_projection_values`: `planned_target_soc_percent/planned_capacity_kwh/planned_hourly_soc_percent/forecast_run_id`、`simulate_daytime_soc_percent`と24行検証を変更しない。予測値・金融式・coef sample countをsummary都合で再計算しない。
- `app.dashboard.history_reconstruction._metadata_matches_forecast_row`: explicit run優先/異run拒否/snapshot guard、`_with_forecast_plan_metadata`のSOC17を保持。hash不明を同rawと見做すshortcut禁止。
- `app.dashboard.aggregation._build_energy_daily`: `HISTORICAL_FAILURE_LOCK`下のselected PV/load pairを別run sunshine_dailyで分割しない。missingを0、raw predicted_pvをfinal24へ代用しない。
- `app.dashboard.snapshots._upload_artifact/_read_dashboard_source_epoch/_read_history_publication`: fixed URI/schema1/ETag/cache/8KiB/source-map/3CAS/24s/同generation history/retained訂正rebuildを弱めない。4096 byte summary上限はGCS metadata8KiBとは別予算。
- `app.dashboard.schedule._schedule_event_candidates/_build_latest_schedule_from_events` のplan日/同run完了、controller 23/03/07 API/時刻/DB非依存、lease/current pointer、archive ownershipは変更対象外。[保護文書](../../../agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md)を後の実装前に適用する。
- `app.operations.firestore._project_model_parameter_values`、既存SQL DDL/forecast save、全financial/料金・寿命計算、core `fit_clear_ghi_calibration/forecast_clear_ghi`、calibration current保存/復元/observerは変更禁止。view不足を新projection/DBschema/依存導入へ拡大しない。

## 8. PLANNED検証・厳密PASS

NEW `tests/test_dashboard_new100_diagnostics.py` は未作成・未実行。D01正本のtest名に統一する。実装指示後にcode-quality-audit/現必須gateを満たして以下を実行する。現回帰のsource例は `test_dashboard_publication_generation` の正常原本24/SOC→6項目混合拒否、`test_forecast_projection_generation` のreal SDK transaction、`test_dashboard_forecast_soc_contract` の異run拒否。既存PASSをNEW表示のPASSへ転記しない。

| ID | fixture/command | PASS条件 |
| --- | --- | --- |
| DBF-01 NEW PLANNED | `python -m pytest tests/test_dashboard_new100_diagnostics.py -q`、valid NEW trace/pack＋producer identity | summary全allowlistだけ、13*N/4terms/theta/rank/time/hash/nested totalsの各範囲一致、date/run/raw/controlをcopy、JSON<=4096bytes、fulltrace/dates/coords/path/rawなし |
| DBF-02 NEW PLANNED | 同上、bool/NaN/Inf/重複future dates/naive時刻/13*N不整合/hash不正/overbudget | status invalid＋理由とproducer log、PV/load/actual/expectedfinance原本不変、pack body漏洩0、invalidを有効0/validへしない |
| DBF-03 NEW PLANNED | 同上、legacy/unknown provider/active NEW identityなしfallback/未知model | legacy/valid unknown issued None/unsupportedの区別。refresh/applied偽state0、activeNEWに旧GTIモデル・旧学習数を代替しない |
| DBF-04 NEW PLANNED | 同上、同日別run/別日/summary不在/非static/SQL未知/reconstructed | current numerical vintageと一致する時だけsummary利用、旧summary fallback/unknown一致推定0、mapping新設0 |
| DBF-05 NEW PLANNED | 同上、旧PVparameter残存＋NEW100/current battery coeff | active NEW学習viewに旧PV行0、storage delete/update0、battery/financial field不変、sample_countをtraining_samplesへ換算0 |
| DBF-06 NEW PLANNED | 同上、既存row/plan同hash/異hash/legacyなし＋applied battery値 | matched/mismatched/legacy_unknown、known mismatchのplan-derived buy/sell/target fallbackだけnull、PV/load/actual/billing/review残存、DBread増分0、public private marker0 |
| DBF-07 NEW PLANNED | 同上、24h/partial/null/valid0/SOC17/各parts/24→7–22範囲 | optional count24/<24/Noneを日/reviewへcopy、局所note/tooltip/status captionでcoverage明示、boolean/global flag増殖0・数値/global集計不変、部分日を全日と断定0、全日内部精度と4dp合計差<=0.0012kWh、SOC23は提供済みclosing値、00–06/SQL unavailableを予測として捏造0 |
| DBF-08 EXISTING＋NEW PLANNED | `python -m pytest tests/test_dashboard_server.py tests/test_dashboard_snapshots.py tests/test_dashboard_publication_generation.py tests/test_dashboard_forecast_soc_contract.py tests/test_dashboard_history_contract.py tests/test_forecast_history_contract.py -q` | bootstrap initial1/history lazy/ETag/asset DB0/31日/reader cache維持、同run SOC/完全日/old history correction/finite CAS/SQL既存run回帰、raw/gzip差とproducer read数を実測 |
| DBF-09 NEW PLANNED | helper import境界fixture＋canonical `pwsh -NoProfile -File scripts/pre_release_local.ps1 -SkipInstall` | dashboardからNumPy/forecast/fit/store/observer import0、production source保護回帰PASS、未測定live/browser/emulatorはINCONCLUSIVEとして残す |

別担当のUI/browser検証は既存DTO exact names/typeと上記scopeに統合する。既存料金/寿命条件を表示へ引き継げない場合、未知/参考を明示して最小残件にし、新しい金融モデルを設計しない。

## 9. 引継ぎ

確定gap **7件（DF01〜07、うちDF06は保存backend能力限界）**、供給棚卸し19群。重大優先はNEW100要約/診断vintage/旧訓練数混同とreviewの部分generation binding。実装可能scopeはproducer-only小要約＋既存DTO/view suppression＋既存read結果の部分identity比較。SQL schema/physics/予測/金融/制御/GET追加読取は対象外。

最小次手は親の追加packetとUI監査を統合し、**この具体scopeの後の実装指示**へ渡すこと。source欠落をruntime PASS、現品質gateを新summary PASSとして扱わない。

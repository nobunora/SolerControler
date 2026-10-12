# ダッシュボード全表示 inventory — NEW100整合性監査

文書作成・自己照合は完了。production source/testの変更は0。実装は後続指示待ち。
全表示を81項目へ分け、HTMLの37 ID・13見出し・8 chart（24 series）・7 buttonを照合した。各field・renderer・line・小さな修正案・禁止箇所・test/PASS条件の機械正本は [ui-display-inventory.json](ui-display-inventory.json)。未知モデル名とdaypartは列挙規則も記録する。

NEW100の式・学習原本は表示へ未接続。旧「7」は旧parameter保存世代数または物理PV daysであり、認定晴天17日/訓練221区間とは別。時間別PVの欠測合計と別日scheduleの流用を合成VMで再現した。一方、Firestoreの保存SOC23時終端は実際に使える。0..6の実績/参考値と07..23の保存計画は区別が必要。

## 範囲と証拠

対象は templates/dashboard.html、static/dashboard.js、dashboard_calculations.js/store.js/api.js/dates.js、直接Node3 tests。backendは担当agentのqualified source証拠を受領し、この担当では読んでいない。API/UIへの表示値は原本writerと別契約として扱う。

親から同一sourceのcanonical 1168 PASS/1 skipped と lint/type/JS/security PASSを受領。既存Node suiteを独立再実行せず、読取のみのNode VM/Chart/DOM stubで合成入力を評価した。実browser/実機/live既存計画の合格ではない。9対象fileのSHA256はJSONへ固定。

## 最小の後続差分と変更禁止

既存payload内の proposed `pv_forecast_diagnostics.pv_model` は [追加表示契約C02](contracts.md) のschemaに従う。4,096 UTF-8 bytes以下、model/basis/status/date/generated/runと原本identity、theta4/terms、日/区間件数と期間、rank/condition、各取得・訓練・作成・利用時刻、修復hour、全日/decision/各時間帯totalsだけを表示する。fulltrace/F/Q/全訓練dates/座標をHTTPへ追加せず、既存legacy fieldsは保持する。validのみNEW metadata、legacy/invalid/unsupportedはcommonfieldsだけを扱う。

選択dateの全24rowsが単一の非空forecast_run_idを持ち、pv_model.forecast_run_idと一致するときだけ有効なsummaryとする。混在/別日/SQL未知は要約を未確認として正常PV/load/実績を保つ。今回per-date summary mappingを追加しない。

有効NEW100のコピーは `x=(h+.5-12)/6`, `phi=2π(doy-1)/(365/366)`, `C=theta0+theta1*x+theta2*cos(phi)+theta3*sin(phi)`, `PV=max(0,C)*min(F,Q)`。旧GTI/PR/PV温度式は旧モデル履歴だけに説明する。負荷補正や蓄電池の温度/劣化/効率・料金・目標SOCは変更しない。

[estimateHourlyForecastSoc](../../../../../static/dashboard.js#L1095) と [forecastSocFromLatestActual](../../../../../static/dashboard_calculations.js#L59) のアルゴリズム本体、`HISTORICAL_FAILURE_LOCK`、07起点/06:30保護を変更しない。renderの薄い同date/保存SOC優先/参考推定分類/helperだけを候補とする。lockを触る必要が出れば後続レビューで追加承認scopeと回帰を明示する。SQLに保存SOCがない経路は未連携/参考とし、今回schema追加を提案しない。

generic n()/sumBucket()/period/billing/life集計を一括変更しない。欠測修正は承認されたhourly rendererの局所表示に限る。既存期間cost/life欠測・axis・spanGaps・見出し番号・警告省略はD05で保持/残件確認とし、今回修正権限へ自動追加しない。

初期31日bootstrap・history明示navigation・ETag/gzip/versioned assets・GET DB0を守る。追加request/polling/GET学習/全履歴DB読みをUI整合修正へ混ぜない。generated/adopted/device appliedを同じ成功としない。

## 全表示一覧

「field」は現行の読取元。`NEW`は後続提案で、既に接続済みとは扱わない。R番号は下記の根拠/修正境界に対応。見出し、chart legend/axis、空表示、表の動的rowを含む。

| ID | 表示/状態 | renderer・根拠 | 利用field/系列 | risk |
| --- | --- | --- | --- | --- |
| U01 | タイトル・期間操作案内 | [hero](../../../../../templates/dashboard.html#L12) | static copy | R16 |
| U02 | 読込/失敗/表示期間status | [setStatus](../../../../../static/dashboard.js#L114) | store.meta; periodState; selectedWindowBuckets | R09, R10 |
| U03 | 最大6警告severity/title/message | [renderDashboardWarnings](../../../../../static/dashboard.js#L426) | dashboard_warnings[].severity; dashboard_warnings[].title; dashboard_warnings[].code; dashboard_warnings[].message | R14 |
| U04 | 見出し/前後ボタン/対象日/48件注記 | [renderDailyReview](../../../../../static/dashboard.js#L448) | daily_review.date; daily_reviews[].date | R11 |
| U05 | 設定SOC（予想） | [renderDailyReview](../../../../../static/dashboard.js#L448) | daily_review.target_soc_percent | R11 |
| U06 | 朝7時SOC（実績） | [renderDailyReview](../../../../../static/dashboard.js#L448) | daily_review.actual_morning_soc_percent | R11 |
| U07 | 夜間充電予測/実績/差 | [renderDailyReview](../../../../../static/dashboard.js#L448) | daily_review.forecast_night_charge_kwh; daily_review.actual_night_charge_kwh | R11 |
| U08 | PV24発電予測/実績/差 | [renderDailyReview](../../../../../static/dashboard.js#L448) | daily_review.forecast_pv_kwh; daily_review.actual_pv_kwh | R11 |
| U09 | 家の消費予測/実績 | [renderDailyReview](../../../../../static/dashboard.js#L448) | daily_review.forecast_load_kwh; daily_review.actual_load_kwh | R11 |
| U10 | 日中買電予測/実績 | [renderDailyReview](../../../../../static/dashboard.js#L448) | daily_review.forecast_day_buy_kwh; daily_review.actual_day_buy_kwh | R11 |
| U11 | 売電予測/実績 | [renderDailyReview](../../../../../static/dashboard.js#L448) | daily_review.forecast_sell_kwh; daily_review.actual_sell_kwh | R11 |
| U12 | 実績SOC最小/最大 | [renderDailyReview](../../../../../static/dashboard.js#L448) | daily_review.actual_soc_min_percent; daily_review.actual_soc_max_percent | R11 |
| U13 | 夜間電力累計/請求期間 | [renderDailyReview](../../../../../static/dashboard.js#L448) | daily_review.cumulative_night_buy_kwh; daily_review.billing_period_start; daily_review.billing_period_end | R11 |
| U14 | 昼間電力累計/請求期間 | [renderDailyReview](../../../../../static/dashboard.js#L448) | daily_review.cumulative_day_buy_kwh; daily_review.billing_period_start; daily_review.billing_period_end | R11 |
| U15 | 電力量料金予想/昼夜内訳 | [renderDailyReview](../../../../../static/dashboard.js#L448) | daily_review.projected_energy_cost_yen; daily_review.projected_day_cost_yen; daily_review.projected_night_cost_yen | R11 |
| U16 | 第2/第3段階到達日と予想/到達 | [renderDailyReview](../../../../../static/dashboard.js#L448) | daily_review.tier2_arrival.date; daily_review.tier2_arrival.status; daily_review.tier3_arrival.date; daily_review.tier3_arrival.status | R11 |
| U17 | 最も近い類似日/類似度/天候/最高最低気温 | [renderDailyReview](../../../../../static/dashboard.js#L448) | daily_review.analog_date; daily_review.analog_similarity; daily_review.analog_weather; daily_review.analog_max_temp_c; daily_review.analog_min_temp_c | R11 |
| U18 | 主な乖離findings領域は空 | [renderDailyReview](../../../../../static/dashboard.js#L448) | static copy | R11 |
| U19 | 最新計画見出し/対象日/無データ案内 | [hourlyForecastDate](../../../../../static/dashboard.js#L998) | latest_schedule.plan_date; store.hourly.keys | R05, R09 |
| U20 | 予想発電kWh/h | [renderHourlyForecast](../../../../../static/dashboard.js#L1008) | forecast_pv_kwh | R04, R08 |
| U21 | PV余剰充電予測kWh/h | [renderHourlyForecast](../../../../../static/dashboard.js#L1008) | forecast_charge_kwh | R04, R08 |
| U22 | 家の消費 実績/予測kWh/h | [renderHourlyForecast](../../../../../static/dashboard.js#L1008) | actual_load_kwh\|forecast_load_kwh | R17, R08 |
| U23 | 夜間系統充電予測kWh/h | [estimateHourlyNightGridCharge](../../../../../static/dashboard.js#L1087) | planned_night_charge_kwh\|night_charge_kwh + charge_start_time/charge_end_time | R05, R04 |
| U24 | 予想SOC/実績/参考推定混在 | [estimateHourlyForecastSoc](../../../../../static/dashboard.js#L1095) | forecast_soc_percent\|actual_soc_percent\|forecast_capacity_kwh | R05, R06, R07 |
| U25 | 24時間PV/余剰充電/家消費と夜間買電/ピーク/実績範囲/更新時刻 | [renderHourlyForecast](../../../../../static/dashboard.js#L1008) | forecast_hourly[].forecast_pv_kwh; forecast_charge_kwh; actual_load_kwh; forecast_load_kwh; latest_sample_at; latest_schedule.plan_updated_at; planned_night_charge_kwh | R04, R05, R06, R09, R17 |
| U26 | hourly凡例/tooltip/左右軸 | [commonOptions/buildCharts/renderHourlyForecast](../../../../../static/dashboard.js#L763) | Chart.dataset.label; Chart.parsed.y; rows[].hour / axis energy0..5, SOC0..100 | R06, R07, R08 |
| U27 | 表示期間説明/請求締日/期間label | [updatePeriodControls](../../../../../static/dashboard.js#L602) | meta.aggregation_close_day; periodState.mode/month/year; meta.global_oldest_date; meta.global_newest_date | R09, R10 |
| U28 | 月/年/全て・前/後ボタンと無効境界 | [bindPeriodControls/updatePeriodControls](../../../../../static/dashboard.js#L1488) | periodState; meta.global_oldest_date; meta.global_newest_date | R10 |
| U29 | 遅延history/slice fallback・bootstrap・同一取得再描画 | [ensureDataForRange/main/absorbSlice](../../../../../static/dashboard.js#L661) | meta.oldest_loaded_date; meta.has_more_before; historyLoaded; loadingOlder; window.__DASHBOARD_DATA__ | R09, R10 |
| U30 | PV予測/実績/差分 | [renderWindow](../../../../../static/dashboard.js#L1281) | energy_daily.forecast_pv_kwh; energy_daily.actual_pv_kwh / series: 予測(kWh) / 実績(kWh) / 実績-予測(kWh) | R04, R08, R10, R11 |
| U31 | 消費予測/実績/差分 | [renderWindow](../../../../../static/dashboard.js#L1281) | energy_daily.forecast_load_kwh; energy_daily.actual_load_kwh / series: 予測(kWh) / 実績(kWh) / 実績-予測(kWh) | R04, R08, R10 |
| U32 | 自家消費日/週・累計・動的見出し/説明 | [renderWindow](../../../../../static/dashboard.js#L1281) | cost_daily.self_consumption_kwh; store.dates; periodState.mode / series: 日次/週次 自家消費(kWh) / 累計 自家消費(kWh) | R04, R10, R12 |
| U33 | 節約額日/週・累計・動的見出し/説明 | [renderWindow](../../../../../static/dashboard.js#L1281) | cost_daily.savings_yen; store.dates; periodState.mode / series: 日次/週次 節約額(円) / 累計 節約額(円) | R04, R08, R10, R12 |
| U34 | 月別自家消費/節約額・請求期間重なり | [renderMonthly](../../../../../static/dashboard.js#L1434) | cost_monthly.month; period_start; period_end; self_consumption_kwh; savings_yen / series: 月間 自家消費(kWh) / 月間 節約額(円) | R04, R08, R10 |
| U35 | 蓄電池設定SOC/夜間計画/実PV充電終了SOC・週平均 | [renderWindow](../../../../../static/dashboard.js#L1281) | battery_daily.setting_soc_target_percent; night_charge_kwh; pv_charge_end_soc_percent; periodState.mode / series: 設定SOC(%)/週平均 / 夜間充電計画(kWh/日/週) / 太陽光充電終了時SOC(%)/週平均 | R05, R08, R10 |
| U36 | 期間内累計充電 | [renderBatteryLifeProjection](../../../../../static/dashboard.js#L1187) | battery_flow_daily.charge_kwh | R04, R13 |
| U37 | 期間内累計放電 | [renderBatteryLifeProjection](../../../../../static/dashboard.js#L1187) | battery_flow_daily.discharge_kwh | R04, R13 |
| U38 | 期間内等価サイクル | [renderBatteryLifeProjection](../../../../../static/dashboard.js#L1187) | battery_flow_daily.discharge_kwh; model_parameters.battery_usable_capacity_kwh | R04, R13 |
| U39 | 推定容量/SOH | [renderBatteryLifeProjection](../../../../../static/dashboard.js#L1187) | model_parameters.battery_usable_capacity_kwh; battery_cycle_capacity_fade_per_cycle | R04, R13 |
| U40 | 80%到達予測年 | [renderBatteryLifeProjection](../../../../../static/dashboard.js#L1187) | monthlyCycleLoss; calendarLoss; meta.global_newest_date | R04, R13 |
| U41 | 月平均Eqサイクル | [renderBatteryLifeProjection](../../../../../static/dashboard.js#L1187) | equivalentCycles; monthSpan(selected_range) | R04, R13 |
| U42 | 容量内訳stack（月）・非制御/実績copy | [renderBatteryLifeProjection](../../../../../static/dashboard.js#L1187) | battery_flow_daily.discharge_kwh; pv_daily.actual_temp_c; model_parameters.battery_usable_capacity_kwh; battery_cycle_capacity_fade_per_cycle; battery_temp_coeff_per_deg / series: 現在推定容量(kWh) / サイクル劣化(kWh) / カレンダー劣化(kWh) / 温度容量補正(kWh) | R13, R08 |
| U43 | JST00..24軸/実行計画 夜間充電と日中放電 | [renderConstraintGantt](../../../../../static/dashboard.js#L328) | latest_schedule.charge_start_time; latest_schedule.charge_end_time; latest_schedule.day_discharge_window_start; latest_schedule.day_discharge_window_end | R05, R14 |
| U44 | 制約レイヤー 放電禁止/許可 | [renderConstraintGantt](../../../../../static/dashboard.js#L328) | latest_schedule.night_window_start; latest_schedule.night_window_end; latest_schedule.day_discharge_window_start; latest_schedule.day_discharge_window_end | R05, R14 |
| U45 | 安心/経済/充電時間帯SOC設定モード | [renderConstraintGantt](../../../../../static/dashboard.js#L328) | latest_schedule.soc_safety_mode; latest_schedule.soc_economy_mode; latest_schedule.soc_charge_mode | R05, R14 |
| U46 | 設定正常完了/未確認、更新/完了確認/slot/profile | [renderConstraintGantt](../../../../../static/dashboard.js#L328) | latest_schedule.settings_completed; latest_schedule.settings_completed_status; latest_schedule.status; latest_schedule.settings_completed_at; latest_schedule.recorded_at; latest_schedule.settings_completed_profile; latest_schedule.profile; latest_schedule.mode; latest_schedule.slot | R05, R14 |
| U47 | 有効fixed制約先頭4と開始未記録note | [renderConstraintGantt](../../../../../static/dashboard.js#L328) | latest_schedule.constraints.fixed[].enabled/id/description; latest_schedule.charge_start_time | R05, R14 |
| U48 | 変数GTI/TP/LD/NL/RM/RS/RC/NC/PS | [equation](../../../../../templates/dashboard.html#L112) | static copy | R01, R06 |
| U49 | PV旧GTI/PR/温度式 | [equation](../../../../../templates/dashboard.html#L113) | static copy | R01 |
| U50 | 朝不足07-10式 | [equation](../../../../../templates/dashboard.html#L114) | static copy | R01, R06 |
| U51 | 日中余剰10-16 推定昼負荷式 | [equation](../../../../../templates/dashboard.html#L115) | static copy | R01, R06 |
| U52 | 07時目標SOC簡易式 | [equation](../../../../../templates/dashboard.html#L116) | static copy | R01, R06 |
| U53 | 夜間充電式 | [equation](../../../../../templates/dashboard.html#L117) | static copy | R01, R06 |
| U54 | 23-07放電禁止/03job/07cutoff/00制約/条件管理 | [equation](../../../../../templates/dashboard.html#L118) | static copy | R14 |
| U55 | 表見出し 分類/パラメータ/現在値/サンプル数/意味 | [fillLearningParamsTable/addLearningParameterRow](../../../../../static/dashboard.js#L725) | model_parameters[]; pv_forecast_diagnostics | R02, R03, R15, R18 |
| U56 | 充電1kWhあたりSOC上昇率 | [fillLearningParamsTable/paramAlias](../../../../../static/dashboard.js#L725) | model_parameters[].name=soc_per_kwh_charge; model_parameters[].mean_value; model_parameters[].sample_count | R02, R15 |
| U57 | 放電1kWhあたりSOC低下率 | [fillLearningParamsTable/paramAlias](../../../../../static/dashboard.js#L725) | model_parameters[].name=soc_per_kwh_discharge; model_parameters[].mean_value; model_parameters[].sample_count | R02, R15 |
| U58 | 30分SOC自然変動 | [fillLearningParamsTable/paramAlias](../../../../../static/dashboard.js#L725) | model_parameters[].name=soc_drift_per_slot; model_parameters[].mean_value; model_parameters[].sample_count | R02, R15 |
| U59 | 往復効率 | [fillLearningParamsTable/paramAlias](../../../../../static/dashboard.js#L725) | model_parameters[].name=battery_round_trip_efficiency; model_parameters[].mean_value; model_parameters[].sample_count | R02, R15 |
| U60 | 実効蓄電容量 | [fillLearningParamsTable/paramAlias](../../../../../static/dashboard.js#L725) | model_parameters[].name=battery_usable_capacity_kwh; model_parameters[].mean_value; model_parameters[].sample_count | R02, R15 |
| U61 | 朝PV直接利用率 | [fillLearningParamsTable/paramAlias](../../../../../static/dashboard.js#L725) | model_parameters[].name=pv_direct_use_ratio; model_parameters[].mean_value; model_parameters[].sample_count | R01, R02, R15, R18 |
| U62 | 余剰PV蓄電寄与率 | [fillLearningParamsTable/paramAlias](../../../../../static/dashboard.js#L725) | model_parameters[].name=pv_to_battery_ratio; model_parameters[].mean_value; model_parameters[].sample_count | R01, R02, R15, R18 |
| U63 | PV自家消費率 | [fillLearningParamsTable/paramAlias](../../../../../static/dashboard.js#L725) | model_parameters[].name=pv_self_consumption_ratio; model_parameters[].mean_value; model_parameters[].sample_count | R01, R02, R15, R18 |
| U64 | 面別PV実績補正係数 | [fillLearningParamsTable/paramAlias](../../../../../static/dashboard.js#L725) | model_parameters[].name=pv_array_calibration_factor; model_parameters[].mean_value; model_parameters[].sample_count | R01, R02, R15, R18 |
| U65 | PV予測倍率中心（確率でない） | [fillLearningParamsTable/paramAlias](../../../../../static/dashboard.js#L725) | model_parameters[].name=pv_forecast_error_ratio_mean; model_parameters[].mean_value; model_parameters[].sample_count | R01, R02, R15, R18 |
| U66 | PV倍率ばらつき（確率でない） | [fillLearningParamsTable/paramAlias](../../../../../static/dashboard.js#L725) | model_parameters[].name=pv_forecast_error_ratio_std; model_parameters[].mean_value; model_parameters[].sample_count | R01, R02, R15, R18 |
| U67 | PV倍率分散 | [fillLearningParamsTable/paramAlias](../../../../../static/dashboard.js#L725) | model_parameters[].name=pv_forecast_error_ratio_variance; model_parameters[].mean_value; model_parameters[].sample_count | R01, R02, R15, R18 |
| U68 | PV誤差サンプル数という現在値と保存世代数列 | [fillLearningParamsTable/paramAlias](../../../../../static/dashboard.js#L725) | model_parameters[].name=pv_forecast_error_ratio_sample_count; model_parameters[].mean_value; model_parameters[].sample_count | R01, R02, R15, R18 |
| U69 | 物理放射変換係数 | [fillLearningParamsTable/paramAlias](../../../../../static/dashboard.js#L725) | model_parameters[].name=physical_pv_radiation_scale; model_parameters[].mean_value; model_parameters[].sample_count | R01, R02, R15, R18 |
| U70 | 物理全体バイアス係数 | [fillLearningParamsTable/paramAlias](../../../../../static/dashboard.js#L725) | model_parameters[].name=physical_pv_global_bias_scale; model_parameters[].mean_value; model_parameters[].sample_count | R01, R02, R15, R18 |
| U71 | 未定義aliasの任意追加モデル係数 | [fillLearningParamsTable](../../../../../static/dashboard.js#L725) | model_parameters[].name; mean_value; sample_count | R02, R15 |
| U72 | 物理PV短波放射変換係数 | [fillLearningParamsTable](../../../../../static/dashboard.js#L725) | pv_forecast_diagnostics.physical.scales.radiation_scale; pv_forecast_diagnostics.physical.scales.radiation_scale_fit.sample_count | R01, R02, R03 |
| U73 | 物理PV全体バイアス補正 | [fillLearningParamsTable](../../../../../static/dashboard.js#L725) | pv_forecast_diagnostics.physical.scales.global_bias_scale; pv_forecast_diagnostics.physical.data_quality.global_days | R01, R02, R03 |
| U74 | 時間帯補正（各daypart） | [fillLearningParamsTable](../../../../../static/dashboard.js#L725) | pv_forecast_diagnostics.physical.scales.daypart[period].scale; pv_forecast_diagnostics.physical.scales.daypart[period].count | R01, R02, R03 |
| U75 | PV比率EWMA | [fillLearningParamsTable](../../../../../static/dashboard.js#L725) | pv_forecast_diagnostics.forecast_correction.pv_ratio_ewma_raw; pv_forecast_diagnostics.forecast_correction.pv_sample_count | R01, R02, R03 |
| U76 | 負荷比率EWMA | [fillLearningParamsTable](../../../../../static/dashboard.js#L725) | pv_forecast_diagnostics.forecast_correction.load_ratio_ewma_applied; pv_forecast_diagnostics.forecast_correction.load_sample_count | R02, R03 |
| U77 | 夜間気温補正倍率 | [fillLearningParamsTable](../../../../../static/dashboard.js#L725) | pv_forecast_diagnostics.forecast_correction.evening_load_temperature.multiplier; pv_forecast_diagnostics.forecast_correction.evening_load_temperature.sample_count | R02, R03 |
| U78 | 翌朝まで予測消費 | [fillLearningParamsTable](../../../../../static/dashboard.js#L725) | pv_forecast_diagnostics.overnight_load_forecast.expected_kwh; pv_forecast_diagnostics.overnight_load_forecast.sample_count | R02, R03 |
| U79 | NEW100型/model label/a,b,c,e/晴天日数/区間数/rank/condition/訓練・usable時刻 | [not implemented in inspected frontend](../../../../../static/dashboard.js#L726) | NEW pv_forecast_diagnostics.pv_model.model_id/status/basis_version/plan_date/generated_at/forecast_run_id; NEW pv_model.raw_plan_sha256/control_version_id/calibration_sha256/forecast_sha256; NEW pv_model.theta4/terms/training_day_count/training_sample_count/training_start_date/training_end_date/rank/condition_number; NEW pv_model.created_at/training_as_of/usable_from/retrieved_at/as_of/provider_issued_at; NEW pv_model.repaired_hours/totals / not_present | R01, R02, R03, R18 |
| U80 | 表で除外されるstatic5fields（life計算の入力にも使用） | [staticParameterNames/modelParam](../../../../../static/dashboard.js#L704) | battery_cycle_capacity_fade_per_cycle; battery_temp_coeff_per_deg; pv_array_total_capacity_kw; pv_kwh_per_sunhour; pv_temp_coeff_per_deg / not_visible_in_learning_table | R13, R15 |
| U81 | 入力form/selectと独立integrations/外部連携設定 | [document](../../../../../templates/dashboard.html#L1) |  / not_present | R16 |

## リスク・小さな修正案・test/PASS条件

CONFIRMED_STATICは直接source、SYNTHETIC_RUNTIMEは合成VM、PRODUCERは担当agentのsource確認。CONDITIONALはその入力が来た場合の条件付き。修正済みではない。severityは表示誤認の影響分類で、本番発生証明ではない。D05保持項目は今回自動で実装変更へ追加しない。

| risk | severity | origin | 修正step | 保持確認 |
| --- | --- | --- | --- | --- |
| R01 | HIGH | NEW100_MODEL_EXPLANATION | D02 |  |
| R02 | HIGH | NEW100_MODEL_CALIBRATION | D01,D02 |  |
| R03 | HIGH | NEW100_MODEL_PROVENANCE | D01,D02 | D03 |
| R04 | HIGH | PRE_EXISTING_AMPLIFIED_BY_NEW100 | D03 | D05 |
| R05 | HIGH | PRE_EXISTING_DATE_BINDING | D03 |  |
| R06 | HIGH | PRE_EXISTING_PRESENTATION_WITH_NEW17_SAVED_POINTS | D03 |  |
| R07 | MEDIUM | PRE_EXISTING_DRAWING |  | D05 |
| R08 | MEDIUM | PRE_EXISTING_INPUT_BOUND_CONDITIONAL |  | D05 |
| R09 | MEDIUM | PRE_EXISTING_TIMESTAMP_MEANING_NEW100_METADATA | D02,D03 |  |
| R10 | HIGH | PRE_EXISTING_CONDITIONAL_HISTORY_MERGE |  | D05 |
| R11 | MEDIUM | PRE_EXISTING_REVIEW_AND_NEW100_DAY_WINDOW | D04 | D05 |
| R12 | LOW | PRE_EXISTING_COPY |  | D05 |
| R13 | MEDIUM | PRE_EXISTING_VISUAL_LIFE_MODEL |  | D05 |
| R14 | HIGH | PRE_EXISTING_CONDITIONAL_STATUS_WARNING |  | D05 |
| R15 | MEDIUM | PRE_EXISTING_LEGACY_PARAMETERS | D02 | D05 |
| R16 | INFO | ABSENT_UI_SCOPE |  | D05 |
| R17 | MEDIUM | PRE_EXISTING_LOAD_ACTUAL_COVERAGE | D03 |  |
| R18 | HIGH | NEW100_CALIBRATION_VS_UNCERTAINTY | D01,D02 |  |

### R01 — CONFIRMED_STATIC

有効NEW100でも静的GTI/PR/PV温度式と旧物理PV係数を表示する。

最小差分: selected_model別の説明コピーに限定。旧式/旧係数は旧モデル履歴へ明示分類。

変更禁止: PV純計算・料金/容量・旧モデル学習処理へ変更を広げない。

**UI-001 PLANNED**: NEW100はmax(0,C)*min(F,Q)、旧モデルを選ぶ履歴は旧式を表示。後続許可scopeに応じて既存Node testsへ追加し、保持だけの項目は同じ挙動を検証する（今回編集0）。

### R02 — CONFIRMED_STATIC_AND_PRODUCER

学習表は旧parameter保存世代数/物理global_days/誤差sampleを混在表示し、新係数原本の日数・区間数へ接続していない。

最小差分: 同選択計画の軽いmodel summaryへ認定日数/訓練区間数/係数/時刻を結線し、旧保存世代数を別列で命名。

変更禁止: forecast uncertainty.sample_countや旧7日lockを新認定日数へ置換しない。fulltrace追加配信/追加HTTP禁止。

**UI-002 PLANNED**: synthetic原本17日/221区間を表示。旧保存世代7と誤差sample0を別意味で保持。後続許可scopeに応じて既存Node testsへ追加し、保持だけの項目は同じ挙動を検証する（今回編集0）。

### R03 — CONFIRMED_STATIC_AND_PRODUCER

diagnosticsはplan_dateだけでrun/provider/訓練provenanceが欠け、学習表はmain時一回のglobal最新diagnosticsを読む。

最小差分: 選択date全24rowがsingle nonempty forecast_run_idを持ちpv_model.forecast_run_idと一致した場合だけsummary有効。混在/別日/SQL未知は未確認、正常PV/load/actual維持。per-date mapping追加なし。

変更禁止: 毎GET再学習、履歴原本の上書き、全trace重複bootstrapは禁止。

**UI-003 PLANNED**: 同dateでもempty/mixed runは未確認、正常PV/load/実績維持。single24同run一致だけ有効モデル説明。後続許可scopeに応じて既存Node testsへ追加し、保持だけの項目は同じ挙動を検証する（今回編集0）。

### R04 — CONFIRMED_SYNTHETIC_RUNTIME

null PVはchart gapだがhourly totalはn(null)=0。cost/self/月/lifeはmissing→0を含む。

最小差分: D03対象hourly rendererの合計/null/部分取得labelだけ修正。generic n()/sumBucket()/period/billing/life集計は変更せず、期間cost/life欠測はD05保持確認/残件。

変更禁止: 予測原本の欠測を0で埋める、既存金融/計画値を変更することは禁止。

**UI-004 PLANNED**: 有効0は0、1区間欠測の合計は完全値として表示しない。全欠測は-。後続許可scopeに応じて既存Node testsへ追加し、保持だけの項目は同じ挙動を検証する（今回編集0）。

### R05 — CONFIRMED_SYNTHETIC_RUNTIME

hourly対象日がlatest schedule日と異なる場合も最新target/night量/時刻を利用する。

最小差分: render側の薄い同date predicateでscheduleを制限し、同date batteryを優先fallbackにする。

変更禁止: estimateHourlyForecastSoc/forecastSocFromLatestActual本体やlockを変更しない。

**UI-005 PLANNED**: 別日schedule9kWhは旧日の2kWh計画へ流用しない。同日優先は維持。後続許可scopeに応じて既存Node testsへ追加し、保持だけの項目は同じ挙動を検証する（今回編集0）。

### R06 — CONFIRMED_SYNTHETIC_RUNTIME_AND_PRODUCER

保存SOC23の一律再計算疑いは再現で否定（23=99正常）。0..6実績/参考と07..23保存SOCの予想SOC線/ピークlabel混在は別に確定。

最小差分: presentation helperで保存SOC17点、実績、参考推定を分け、23時は22区間終了をtooltip説明。

変更禁止: HISTORICAL_FAILURE_LOCK、07起点/06:30保護、蓄電池効率/容量/SOC再計算本体禁止。

**UI-006 PLANNED**: 07..23の17点は保存値優先、0..6を同原本モデル予測と称さず、23終端を24時へずらさない。後続許可scopeに応じて既存Node testsへ追加し、保持だけの項目は同じ挙動を検証する（今回編集0）。

### R07 — CONFIRMED_STATIC

SOC spanGaps:trueが欠けた境界を線で結び、tensionが時間エネルギーを滑らかな曲線に見せる。

最小差分: D05で保持・境界検証のみ。追加許可時の候補: 正本境界と欠測表示をlegend/tooltipで区別。描画gap方針はpresentationのみ検討。

変更禁止: 保存PV/SOCの値を曲線補間へ書換えない。

**UI-007 PLANNED**: 欠測を存在した予測点として表示せず、定義された17境界を維持。後続許可scopeに応じて既存Node testsへ追加し、保持だけの項目は同じ挙動を検証する（今回編集0）。

### R08 — CONDITIONAL_INPUT_BOUND

hourly5kWh/h、daily PV30等の固定上限で有効なNEW100/負荷/費用ピークが見切れ得る。

最小差分: D05で保持・境界検証のみ。追加許可時の候補: overflow indicationまたは描画axisだけの拡張を検討し同左右tick数を維持。

変更禁止: PV出力/目標/金融値へのclip禁止。

**UI-008 PLANNED**: 6kWh/h fixtureがtooltipだけでなくchart領域へ現れ、0..100SOC軸は維持。後続許可scopeに応じて既存Node testsへ追加し、保持だけの項目は同じ挙動を検証する（今回編集0）。

### R09 — CONFIRMED_STATIC

refreshDashboardは主にcached store再描画で、計画更新時刻/実績最新時刻/ブラウザ取得時刻は異なる。

最小差分: 既存payloadのgeneration/plan時刻と実績時刻を説明別label。未供給時刻をnowで補造しない。

変更禁止: 自動poll/追加HTTP、GETから学習、provider freshnessをHTTP成功だけで宣言は禁止。

**UI-009 PLANNED**: 表示期間操作で計画更新時刻は進まない。JST表示とUTC原本時刻を区別。後続許可scopeに応じて既存Node testsへ追加し、保持だけの項目は同じ挙動を検証する（今回編集0）。

### R10 — CONFIRMED_STATIC_CONDITIONAL_HISTORY

historyはdaily mapを新bootstrapより優先しないがhourly mergeは無条件で同hourを置換する。static monthly/params/diagはhistoryで更新しない。

最小差分: D05で保持・境界検証のみ。追加許可時の候補: 同date/runのhistory mergeを薄いselectorで制御し、選択期間に未供給のstaticを明示。

変更禁止: lazy history/31日bootstrap/cache/ETag/gzip/GET DB0/既存bounded fallbackを破壊しない。

**UI-010 PLANNED**: stale overlapping historyはdaily/時間別とも最新原本を巻戻さず、初期history要求0。後続許可scopeに応じて既存Node testsへ追加し、保持だけの項目は同じ挙動を検証する（今回編集0）。

### R11 — CONFIRMED_STATIC_CONDITIONAL_PRODUCER

review.dateだけで48件揃った説明を出し、findingsを毎回空にする。日合計PVとdecision窓が表示上区別されない。

最小差分: producer完全性と集計窓を軽いmetadataで説明。findings未提供は隠す/未提供と表示。

変更禁止: 料金計算・実績合計・認定原本の書換え禁止。

**UI-011 PLANNED**: 全24hPVと07..22部分を混同しない。未完了fixtureに48件完成を宣言しない。後続許可scopeに応じて既存Node testsへ追加し、保持だけの項目は同じ挙動を検証する（今回編集0）。

### R12 — CONFIRMED_STATIC

renderWindowが自家消費見出し4→2、節約額5→3へ書換え、他見出し番号と重複する。

最小差分: D05で保持・境界検証のみ。追加許可時の候補: 見出し番号だけをHTMLの4/5へ合わせる。

変更禁止: 系列・集計式・ルート変更を混ぜない。

**UI-012 PLANNED**: 月/年/全てで一貫した1..10の番号と日/週単位を表示。後続許可scopeに応じて既存Node testsへ追加し、保持だけの項目は同じ挙動を検証する（今回編集0）。

### R13 — CONFIRMED_STATIC

lifeの累計は選択期間内、KPI容量はcycleのみでchartはcalendar/tempも含む。未来予測でないcopyと80%到達予測が併存する。

最小差分: D05で保持・境界検証のみ。追加許可時の候補: 期間内累計・可視化推定・KPI/stackの対象差を明示。将来年表示の推定を説明。

変更禁止: 制御有効容量/温度上昇/劣化を変更せず、寿命試算を制御へ採用しない。

**UI-013 PLANNED**: 選択期間の意味、推定と実測、将来年と観測内訳を区別し制御SOCは不変。後続許可scopeに応じて既存Node testsへ追加し、保持だけの項目は同じ挙動を検証する（今回編集0）。

### R14 — CONFIRMED_STATIC_CONDITIONAL_PRODUCER

警告は先頭6、fixed制約は先頭4だけ。settings_completed/statusから設定正常完了を出し同run/device readback検証はしない。

最小差分: D05で保持・境界検証のみ。追加許可時の候補: 既存summaryで省略件数/設定記録と実機確認を区別し、producerの証明済状態だけにgreenを結線。

変更禁止: 実機制御/制約時間/0時跨ぎ防止/開始終了lockをUI判断で変更しない。

**UI-014 PLANNED**: 警告7件/制約5件でも隠れた重要事項を説明。generated/adopted/appliedを同一視しない。後続許可scopeに応じて既存Node testsへ追加し、保持だけの項目は同じ挙動を検証する（今回編集0）。

### R15 — CONFIRMED_STATIC

任意model_parametersは未定義aliasでも表示、無効/空meanやsampleが0になり、単位/対象モデルが不明な補助係数も残る。

最小差分: unknown/historicalモデルの表示分類とfinite validationをpresentationだけで行う。

変更禁止: 旧履歴field除去、backend原本のrename/誤差sampleの校正日流用禁止。

**UI-015 PLANNED**: 不明値は-、正当0は0、unknown fieldは名前を保って過去/未分類と明示。後続許可scopeに応じて既存Node testsへ追加し、保持だけの項目は同じ挙動を検証する（今回編集0）。

### R16 — CONFIRMED_SCOPE_ABSENCE

対象HTMLにはform/select/input/連携設定や独立診断カードがなく、integration fieldを描画していない。

最小差分: D05で保持・境界検証のみ。追加許可時の候補: 不在をscopeに明記し、未存在UIを今回の修正へ追加しない。

変更禁止: 新API/フォーム/外部連携操作を提案差分へ暗黙追加しない。

**UI-016 PLANNED**: HTML inventoryは37id/13見出し/8canvas/7buttonを全てcover、不在を実装済としない。後続許可scopeに応じて既存Node testsへ追加し、保持だけの項目は同じ挙動を検証する（今回編集0）。

### R17 — CONFIRMED_STATIC_CONDITIONAL_GAPS

actual loadはforecastを上書きし、min..max時刻の連続範囲として注記するが中間欠測を検証しない。

最小差分: 実績/予測の由来と実績coverageを注記する薄いselector。

変更禁止: 負荷補正式/EWMA/comfort/safety-floorアルゴリズム変更禁止。

**UI-017 PLANNED**: actual有効0を保ち、飛び飛び実績を全時刻連続取得と称さない。後続許可scopeに応じて既存Node testsへ追加し、保持だけの項目は同じ挙動を検証する（今回編集0）。

### R18 — CONFIRMED_PRODUCER_AND_STATIC

旧PV誤差sample0・neutral uncertainty・認定学習221区間は別概念。UIは旧mean/std/variance/sample rowsにNEWモデルの意味を付けない。

最小差分: 校正原本と誤差学習を別項目にし、neutralは誤差未推定と説明する。

変更禁止: sample0を完全精度/不確実性0と扱わず、sample_countへ晴天日数をコピーしない。

**UI-018 PLANNED**: 0誤差sampleと17晴天/221訓練を同時表示でき、完璧な予測とは説明しない。後続許可scopeに応じて既存Node testsへ追加し、保持だけの項目は同じ挙動を検証する（今回編集0）。

## 実測した境界と既存testの限界

合成24時間入力はPV08時null/PV09時.2、負荷毎時.1、実績SOC00時40%、保存SOC23時99%、同日夜間計画2kWh。追加summary候補に17日/221区間、旧param保存件数7を渡した。

| 観測 | 結果 | 解釈 |
| --- | --- | --- |
| 24labels、PV08 null | 保持 | chart自体は欠測0置換しない |
| 発電summary | 0.20kWh、欠測注記なし | 部分合計を完全値として出す |
| 保存SOC23 | 99% | 23終端欠落はfrontendで一律には起きない |
| SOC00 | 実績40%が予想SOC系列 | 由来label混在 |
| cost self null | 0 | 正当0と欠測を区別できない表示 |
| 校正候補17/221 | 表示なし | old7行/空物理行だけが残る |
| 別日schedule9kWh注入 | 旧対象日へ9kWh表示 | 日binding不足を実証 |
| 初期HTTP | 0 | embedded current payloadなら追加取得しない |
| hourly軸上限 | 5固定 | 5超の有効入力は見切れ条件 |

同date保存SOC17点は数式再生成せず優先する。07..22の開始SOC＋23の22区間終了SOCは17点、PVは別の24時間配列。00..06モデルSOCを推測して埋めず、実績/参考/未連携の表示を区別する。

既存command: `node tests/test_dashboard_bootstrap.js`, `node tests/test_dashboard_calculations.js`, `node tests/test_dashboard_modules.js`。bootstrap testは疎な5hour fixtureで、初期1request/history遅延/計画優先/axis/resize/前後reviewを検証する。NEW100原本数・24hour欠測合計・別日schedule・全17境界・stale hourly overlap・active model式は既存PASSだけで証明されない。

## producerから受領した根拠

- daily forecast_pv_kwh is whole selected date hourly sum (24h); fallback total also whole24; component daypart sums need not equal daily。[_build_energy_daily](../../../../../app/dashboard/aggregation.py#L166), [_build_energy_daily](../../../../../app/dashboard/aggregation.py#L223), [extract_final_pv_totals_from_plan](../../../../../app/operations/domain.py#L86)。frontend担当によるbackend追加readは0。
- Firestore persisted SOC keys7..23,23=22interval closing; same forecast_run join;0..6 outside persisted scope. Parent also reports SQL path lacks persisted SOC columns: display as reference/unlinked without adding schema.。[_simulate_day](../../../../../app/energy_plan/soc_cost.py#L395), [_simulate_day](../../../../../app/energy_plan/soc_cost.py#L418), [_forecast_projection_values](../../../../../app/operations/forecast_persistence.py#L106), [_with_forecast_plan_metadata](../../../../../app/dashboard/history_reconstruction.py#L158)。frontend担当によるbackend追加readは0。
- current diagnostics exposes plan_date but drops NEW100 trace/run/training/provider metadata。[extract_pv_forecast_diagnostics](../../../../../app/dashboard/slice_assembler.py#L21)。frontend担当によるbackend追加readは0。
- model_parameters.sample_count counts old parameter immutable originals/generations; same raw retry increments0; not NEW100 training samples; names unfiltered。[_project_model_parameter_values](../../../../../app/operations/firestore.py#L927), [_project_model_parameter_values](../../../../../app/operations/firestore.py#L948), [load_firestore_slice](../../../../../app/dashboard/firestore_repository.py#L788)。frontend担当によるbackend追加readは0。
- backend schedule candidates and latest plan merging check matching date; frontend old hourly date/latest schedule mixing is not authorized by that contract。[_schedule_event_candidates](../../../../../app/dashboard/schedule.py#L123), [_build_latest_schedule_from_events](../../../../../app/dashboard/schedule.py#L179), [merge_latest_plan_into_schedule](../../../../../app/dashboard/slice_assembler.py#L49)。frontend担当によるbackend追加readは0。

## HTMLとの自己照合

結果: **37/37 IDs、13/13見出し、8/8 chart、24 series、13 review KPI、5 table列、15既知alias、7buttonをcover**。uncovered IDは0。form/select/input、独立integrations cardは0で、存在を作り上げていない。static5parameter除外もU80、任意alias/daypartの全name規則も記録。

| HTML id | line | inventory |
| --- | ---: | --- |
| statusMsg | 14 | U02 |
| dashboardWarnings | 17 | U03 |
| dailyReviewPrevBtn | 23 | U04 |
| dailyReviewDate | 24 | U04 |
| dailyReviewNextBtn | 25 | U04 |
| dailyReviewNote | 27 | U04 |
| dailyReviewGrid | 28 | U05, U06, U07, U08, U09, U10, U11, U12, U13, U14, U15, U16, U17 |
| dailyReviewFindings | 29 | U18 |
| hourlyForecastNote | 34 | U25 |
| hourlyForecastChart | 35 | U20, U21, U22, U23, U24, U26 |
| periodMonthBtn | 42 | U28 |
| periodYearBtn | 43 | U28 |
| periodAllBtn | 44 | U28 |
| periodPrevBtn | 45 | U28 |
| periodNextBtn | 46 | U28 |
| periodLabel | 47 | U27 |
| pvChart | 54 | U30 |
| loadChart | 60 | U31 |
| dailyKwhTitle | 64 | U32 |
| dailyKwhDesc | 65 | U32 |
| dailyKwhChart | 66 | U32 |
| dailyYenTitle | 70 | U33 |
| dailyYenDesc | 71 | U33 |
| dailyYenChart | 72 | U33 |
| monthlyCostChart | 78 | U34 |
| batteryTitle | 82 | U35 |
| batteryDesc | 83 | U35 |
| batteryChart | 84 | U35 |
| lifeTotalCharge | 92 | U36 |
| lifeTotalDischarge | 93 | U37 |
| lifeEquivalentCycles | 94 | U38 |
| lifeEstimatedCapacity | 95 | U39 |
| lifeEightyPercentYear | 96 | U40 |
| lifeMonthlyCycles | 97 | U41 |
| batteryLifeChart | 99 | U42 |
| constraintGantt | 106 | U43, U44, U45, U46, U47 |
| learningParamsTable | 122 | U55, U56, U57, U58, U59, U60, U61, U62, U63, U64, U65, U66, U67, U68, U69, U70, U71, U72, U73, U74, U75, U76, U77, U78 |

| 見出し | line | inventory |
| --- | ---: | --- |
| おうち発電ダッシュボード | 12 | U01 |
| 1. 予実レビュー（昨日まで） | 21 | U04 |
| 2. 時間別予測（最新計画） | 33 | U19 |
| 表示期間 | 39 | U27 |
| 3.1 発電量（予測と実績） | 52 | U30 |
| 3.2 消費量（予測と実績） | 58 | U31 |
| 4. 自家消費kWh（日） | 64 | U32 |
| 5. 節約額（日） | 70 | U33 |
| 6. 自家消費と節約額（月） | 76 | U34 |
| 7. 蓄電池計画値と実績（日次） | 82 | U35 |
| 8. 蓄電池容量推定（実績） | 88 | U42 |
| 9. 制約管理ガントチャート（最新設定） | 104 | U43 |
| 10. 蓄電池方程式とパラメータ | 110 | U55 |

全2成果物のlinks/JSON/risk参照/IDを再照合し、テンプレート/JS/testのhash不変を確認済み。実装はユーザーの後続指示後に、上記最小差分と未確認producer契約だけを対象に進める。

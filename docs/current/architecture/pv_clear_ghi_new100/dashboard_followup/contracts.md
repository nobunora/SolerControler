# 追加表示契約

2026-10-12のユーザー指示によりD01〜D05の実装を開始する。以下のC08/C09を追加し、固定軸と実測欠測も対応対象とする。
新方式の学習件数不足を直すために旧7日消費履歴やPV計算器を変えない。

## C01 読取経路とコスト

browser→既存HTML shell→既存bootstrap GET→既存materialized GCS/local gzip。
historyは明示操作後の既存history GET。既存slice fallbackは現在の有限読取のまま。
producerは採用済みrawとPublishedPlan、既に読んだforecast/plan recordsを使う。

- 新HTTP request/endpoint/DBcollection/env/dependency/NumPy importを追加しない。
- initial/default viewの同期全履歴読取0、assetありGETのDBread0。
- 原本のfull trace、24個のQ/F/C、全晴天日配列、座標/site fingerprint、秘密/内部pathをHTTPへ渡さない。
- 新しいモデル要約はcompact UTF-8 JSON 4,096 bytes以下。超過/不正時は要約だけ未確認とし、正常なPV/load/実績を消さない。
- staticは既存serverのcontent hash version/ETagを使う。JS修正でhashが変わることを試験し、server cache方針を編集しない。

## C02 単一モデル要約

追加候補keyは `pv_forecast_diagnostics.pv_model`。既存plan_date/physical/forecast_correction/hourly_weather_pv_shape/overnight_load_forecastを削除しない。
Summaryは同じ採用原本に対して一回だけ作る。推定・fit・観測取得・PVの再補正は行わない。

| field | 型・由来 |
| --- | --- |
| schema_version | int、1 |
| status | valid / legacy / invalid / unsupported。表示metadataの適格性だけを表す |
| reason_code | 有限の理由codeまたはNone。raw exception/URL/bodyは入れない |
| model_id / basis_version | traceの固定model/basis。未知modelを旧式へ読み替えない |
| plan_date / generated_at | 採用原本の対象日・計画生成時刻 |
| forecast_run_id / raw_plan_sha256 / control_version_id | 既存producer identity。別の再serialize hashで置換しない |
| calibration_sha256 / forecast_sha256 | 原本traceの係数版・予報入力hash。学習回数ではない |
| training_day_count / training_sample_count | len(pack.training_dates) / pack.training_samples。後者の単位は1時間区間、13×日数 |
| training_start_date / training_end_date | packの最初/最後の日。全日付配列は転送しない |
| training_as_of / created_at / usable_from | pack内の学習cutoff/係数版生成/利用可能時刻 |
| terms / theta4 | 固定[1,x,cos_phi,sin_phi]と有限signed数値4個。係数の符号を0へ丸めない |
| rank / condition_number | 保存packの値。表示時に再計算しない |
| retrieved_at / as_of / provider_issued_at | 予報取得完了/run cutoff/provider発行。provider発行時刻不明はNone |
| repaired_hours | 保存traceの修復した0..23時のsorted unique配列、最大24要素 |
| totals | full_day_pv_kwh、decision_day_pv_kwh、morning_pv_kwh、midday_pv_kwh、evening_pv_kwh。保存traceからcopy |

追加要約の適格性はstdllibの有限数値/正しい型/日付/件数/field整合/identityで検証する。boolをint/floatとして受けない。
countsは累積update回数ではない。新晴天日追加なら増え、訂正による認定退役なら減り得る。同入力retryでは増えない。
statusは観測更新成功、実機設定適用、予報accuracy、cloud server atomicityを意味しない。

状態別shapeを固定する。common keysはschema_version/status/reason_code/model_id/plan_date/generated_at/
forecast_run_id/raw_plan_sha256/control_version_id。日時/識別値が確認できなければNoneを保持する。
`valid`だけ上表のNEWmetadata全keysを持つ。provider_issued_atだけNone可、その他は定義型を必須とする。
`legacy/invalid/unsupported`はcommon keysだけとし、theta/学習件数を推測して埋めない。reason_codeはvalid/legacyではNone。
invalidはclear_ghi_trace_missing/plan_identity_mismatch/display_metadata_invalid/display_summary_too_large。
unsupportedはunsupported_model/producer_identity_unavailable/forecast_run_unavailable。
この有限enum以外のreason/bodyを転送しない。

正確なraw/calibration由来の検証はbootstrap producerのPublishedPlan.raw_json固定原本がある場合に限定する。
GET fallbackの従来呼出しはproducer_identity=Noneで、activeNEWならunsupported、旧方式ならlegacy。
browserは選択dateの全forecast rowで1個のnonempty forecast_run_idを確認し、summary.plan_date/runと一致時だけNEW係数要約を結ぶ。
未知run/同日別run/非static slice更新後の旧summaryを同じ版と推測しない。追加per-date public mappingは作らない。
raw完全一致の保証をGET/historyへ広げず、そこではdate/runの範囲だけを扱う。

識別値の最大UTF-8 bytesはmodel_id/basis_version128、forecast_run_id/control_version_id256、SHA64、ISO時刻64、plan_date10。
超過/形式不正のcommon値はNoneとし、文字列truncateで架空identityを作らない。fallback common-onlyも4,096 bytesを再検査する。
NEW validの時刻条件はUTC instantで `training_as_of<=created_at<=usable_from<=as_of`、`retrieved_at<=as_of`、
provider_issued_at既知なら`provider_issued_at<=retrieved_at`。retrieved_atとcreated_atの相互順序は要求しない。
generated_atは原本とproducerの既存値が同instantであることだけを要求し、係数版生成時刻などへ読み替えない。
NEW各key pathはtrace=`data.decision_rationale.pv_clear_ghi`、pack=`trace.calibration_pack`、totals=`trace.totals`。
model/basis/calibration/forecast hash/as_of/retrieved/provider/repairedはtrace、terms/theta/training/rank/condition/created/usableはpack。

legacyと認める現行sourceは`physical_pv_forecast`/`corrected_pv_forecast`。
physical.selected_methodの現行旧集合はexisting/physical_unscaled/physical_global/physical_daypart/physical_altitude_shortwave。
source未記録の旧資料はこの集合か既存旧diagnosticsのscales/data_qualityを根拠にlegacy資料として扱うが、採用方式やraw一致は推測しない。
NEW sourceはphysical_clear_ghi_annual1、basisはnoaa_geometric_1min_midpoint_annual1_v1。NEWとlegacyの矛盾はinvalid、未知nonempty sourceはunsupported_model。

## C03 計算説明の唯一の式

NEW100は `PV(h)=max(0,C(d,h))*min(F(h),Q(d,h))`。
`C=theta0+theta1*x+theta2*cos(phi)+theta3*sin(phi)`、`x=(h+0.5-12)/6`、
`phi=2π*(年内通算日-1)/(365または366)`。QはHaurwitz晴天水平日射、Fは予報の前1時間平均を一度だけ整列した時間積算日射。
温度は消費や蓄電池容量へ既存どおり作用するが、NEW100 PVへ旧PR/GTI/温度/EWMA/残差を二重適用しない。
旧方式が実際に選ばれたlegacy資料には旧説明を維持。model/metadata不明時にNEW式/旧式を断定しない。

## C04 値・区間・SOC

PV/loadは0..23の24開始時刻区間。decision windowは07..22、morning07..09、midday10..15、evening16..22。
3時間帯の和には05/06等が含まれないため、全日PVと同一値とは説明しない。
保存hourlyは4dp、traceは内部precision。全日sum比較の許容誤差は24×0.00005 kWhまで。値を再補正して無理に一致させない。

SOCは07開始〜23終了の17保存点。23の終点SOCと23〜24のPV/load区間は異なる意味。
同じ日/生成根拠の保存SOCを優先し、0..6や保存なしのUI推定は参考と表示する。
保存SOCはfinite numeric（bool/文字列を数値0へcoerceしない）かつ0..100の点だけ。範囲外を0/100へclipして正当な計画点にしない。
未知generation/別日latest_scheduleを新予想の根拠にしない。SQLに保存SOCがない場合は未連携/参考とし、新DBschemaを作らない。
既存estimateHourlyForecastSoc/forecastSocFromLatestActualの計算式、07起点/06:30目標判定は変更しない。
保存SOCが無くscheduleが別日/不明でも、同日実測SOCと単一の有効保存容量がある場合は既存forecastSocFromLatestActualで未来の参考点を表示する。実測点を計画線/peakに混ぜず、最新実測起点の参考SOCと明記する。

## C05 欠測・古版・旧係数

None/null/非有限/unknownを学習0・予測0・費用0へ見せない。有効な0は数値0で表示する。
欠測のある時間別予測の全日合計は「未完」。実績途中経過は「取得済み分、n/24」と明記し、全日予実差と混同しない。
generic n()やperiod/billing/battery life集計を一括変更せず、対象rendererの表示値だけを区別する。
旧model_parametersのPV係数は削除/再学習しない。NEW100のactive係数として表示しない。
model_parameters.sample_countは計画投影回数、EWMAは日別ペア数、NEWは晴天日/時間区間数と分ける。
neutral uncertaintyのsample0は晴天学習0ではない。設定した標準偏差を実績学習の精度と称しない。

## C06 日次レビュー

PV/load予実は既存energy_dailyの選択済み同日ペアが正本。料金実績/制御証拠は独立に保持。
計画由来expected buy/sell/targetは既存読取済み原本hash/runを照合する。known mismatchはその予測fieldだけNoneにし理由を表示。
identityのないlegacyを一致と推測しない。由来不明表示で正常なPV/load/実績を保持する。
private identity markerはartifact/DTOへ出さず、公開hashを24rowへ複製しない。
実績の料金・収益・資産・目標SOCの算出式を表示整合性修正のついでに変えない。
reviewの追加fieldはplan_forecast_binding=matched/mismatched/legacy_unknown、plan_forecast_binding_reason=None/identity_missing/plan_forecast_mismatch。
既存読取済みoriginal hourlyからforecast_available_hour_count:int|Noneを日単位に付加する。finite PV/load両方があるdistinct hourを数える。
legacy日計のみで区間証拠がない場合はNone、known partialは0〜23。別のforecast_complete booleanを追加せず、この件数から完全性を表示する。
reviewへ件数をcopyし、PV/load日別chartはtooltip/status captionで区間不足を示す。値/集計式や全履歴は変更しない。
日内に複数runが存在するだけで一律mismatchにしない。既存選択runがある場合はそのsubset内だけでidentityを比較する。
選択runが特定できず複数known runが使われている場合はmismatched。known一つ+unknownはlegacy_unknown。
selected-run内のknown raw hash複数はmismatched。全対象rowが同一known raw hashを持ちplan.detail_sha256へ一致した場合だけmatched。
private aggregateがNoneへ縮退しても、reviewは既存読取済みselected-run hourlyからknown hash混在を再確認する。新本番flag/queryを追加しない。

## C07 運用境界

全historyをNEW100で書き換えない。過去原本と再構成推定の由来を維持する。
警告の07切替前06:30判定、全制御時刻、lease非依存、容量/温度/料金/劣化/寿命式、backup schema/raw SHAを変更しない。
actual date/schedule read-back/forecast取得/pack作成/画面取得は別の時刻として表示する。
aware ISO時刻は表示変換のみでJSTへ整形し、元のinstant/日付を変更しない。実行host/browserの既定timezoneを使って由来を混ぜない。

## C08 グラフ軸の見切れ防止（追加承認）

全8グラフの24系列を対象とする。描画直前の純表示helperで各axisの有効数値と、stacked系列の同位置の正負累積を包含する。
正常な既存固定範囲は維持し、範囲外の有限値がある場合にだけ必要な上下限・tickを拡張する。データ値をclipしたり計算を変更しない。
SOC軸は物理的な0〜100%を維持し、範囲外SOCを有効値として捏造しない。null/bool/文字列/非有限を軸計算用0へ変換しない。
全chartのpresentation tension=0/spanGaps=falseとし、曲線補間による架空の負PV/過大peak、欠測をまたぐ連続線を作らない。保存値と計算結果は変更しない。
変更可能箇所はstatic/dashboard.jsの新ensureVisibleChartRanges、各chart.update直前の呼出し、および時間別固定kWh軸の適用部に限定する。
renderWindow/renderMonthly/renderBatteryLifeProjectionはこの呼出しだけ追加可能。料金/容量/寿命の集計・モデル、dataset数は変更禁止。
元データがunknownの点を数値0へ変換していたdataset代入は、純表示maskでnullを保つ範囲だけ変更可能。計算helper/料金/寿命の算術本体は変更しない。
固定軸保護の旧禁止文より、本追加承認の限定範囲を優先する。

## C09 元データ保存側の修復（ユーザー訂正後の正本）

「元データを修復し、ダッシュボードでの補完は行わない」という訂正を優先する。修復はoperationsのCSV同期と保存日計の再生成で行う。
Firestore監視原本monitoring_samplesはCSVの有効な原値だけを保存し、原値が同じ場合は再書込しない。保存日計dashboard_daily_metricsは取り込んだsource日を再計算し、欠落/誤集計を保存側で修復する。
日計をCSVがchangedの場合だけに限定しない。sourceがunchangedでも日計が欠けたり旧値が誤っている場合は再生成する。
欠測/非有限/負値/boolを0にしない。読み取ったsource区間のいずれかに対象field欠測があればその合計はNone。有効な実測0は0のまま保持する。
途中日の日計は取得済み区間の値で既存sample_count/first/latestを維持し、48件の完全日という主張と分離する。欠けた区間そのものは補間しない。
既存の4日windowまたは明示DATA_MONITORING_FULL_BACKFILLでCSV供給範囲を限定する。rawを変更せず日計を再生成する処理は永続mode/flagを追加しない。
dashboard/service/reader/browserで新しい補完fallback、raw再走査、CSV/API/archive取得、前値保持や補間を追加しない。保存実測の通常の時間集約・表示だけを行う。
通常時間集約は00/30両区間の有効値だけを合計し、片側欠測ならNone。保存SOCは観測点。予測無しの時刻でも保存実測を落とさず、予測Noneを維持する。
保存元にも値がない場合は欠測を残し、日付/field件数/有限reasonだけを構造化logへ記録する。復元済みという虚偽表示をしない。
assetがある通常GETのDBread0、initial bootstrap1、history lazyを維持する。既存dashboard fallbackは今回拡張しない。
予測は保存された当時の原本からreplay可能な既存回復経路だけを使用し、再現不可区間は欠測を維持する。過去の再予測や実績から当時の予測を捏造しない。
既存legacy_rolling_14d_estimateは既存の参考推計という由来を維持し、今回の修正で削除も当時原本への昇格も行わない。
spanGapsで未復元実測を連続値に見せず、復元した値だけ描く。
既存materialized historyの古い欠測は直近windowの更新だけでは回復しない。既存23時のfull_history_rebuild（または既存の明示full rebuild）で全保存期間を再生成する。
導入時はこの再生成の完了を履歴表示反映の確認条件とする。GETで自動再生成せず、新しい恒久flag/schemaを追加しない。
コード実装・merge/deployはこの文書作成の承認から推測しない。G04/G07本番性能・競合、G06経済原本、G08導入承認は別件。

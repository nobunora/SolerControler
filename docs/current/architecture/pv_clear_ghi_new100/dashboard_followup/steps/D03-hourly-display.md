# D03 時間別表示・SOC・合計

## 入口・参照範囲

[contracts C04/C05](../contracts.md)、inventory R04/R05/R06/R09/R17、D01のmodel/run binding。
既存forecast_hourlyの24row・保存SOC・actual値と既存scheduleだけを使用する。

## 変更対象

| path / symbol | 限定差分 |
| --- | --- |
| static/dashboard.js::renderHourlyForecast | 保存SOC/actual/参考推定の表示種別、合計の完全性、最大点対象・ラベル、tooltip、actual途中経過を区別 |
| 同file NEW hourlyDisplayState(rows,date,schedule) | renderer直前の純表示用object。rowsのdate/run/valid valuesとschedule.plan_dateを確認。既存SOC計算helperは呼出のみ |
| 同file NEW ensureVisibleChartRanges(chart) | C08に従い各軸の有限表示値・stack正負累積を包含。SOC0〜100を保持 |
| 全8chartのupdate直前 | 上記helperの呼出しだけ。renderWindow/renderMonthly/renderBatteryLifeProjectionの計算は変更しない |

既存Canvas/hourlyForecastSummary/hourlyForecastNoteを維持。時間別tableは現行に存在しないので作らない。
既存SOC dataset1本のlabel/dataとsummary/note/tooltipだけで根拠を区別し、dataset/axisを増やさない。
新class/API/schema/storageなし。newhelperは数値を再計算せず、値/根拠/完全性を分類する。

## 変更禁止

estimateHourlyForecastSoc、estimateHourlyNightGridCharge、maxForecastSocPoint、n、sumBucket/averageBucket、dualScales、renderWindow、renderMonthly、renderBatteryLifeProjectionの本体は変更しない。
dashboard_calculations.js::forecastSocFromLatestActual/plannedBatteryValues/SOC効率式・07起点、warningsの06:30判定を変えない。
料金・期間集計・Chart.js・capacity・night charge計算を変えない。軸は追加承認C08の範囲のみ変更可能。未復元実測のspanGapsは連続に見せない。生PV/loadの0を欠測へ変換しない。

## クラス・変数差分

hourlyDisplayStateが返す表示localsはpoints/pointSources、forecastComplete/forecastCount、actualCount、
forecastPvTotal/forecastLoadTotal/actualPvSubtotal/actualLoadSubtotal、boundSchedule、plannedPeak/referencePeak。
pointSourcesはsaved_plan / observed / reference_estimate / unavailable。これは描画根拠で、制御mode flagではない。
saved_planは同日保存forecast_soc_percent。observedは実測SOC。reference_estimateは既存UI式の出力と明記する。

## 実装手順・分岐

1. date/hourを揃えた既存rowsをそのまま使用。finite予測0を保持、null/非有限はunknown扱い。
2. forecast PV/loadの全24区間が揃わなければ「全日予想合計 未完 n/24」。部分合計を全日値として表示しない。
3. actualは取得済み区間のsubtotalとn/24を表示。「当日全日予実差」は完全な比較区間が揃うまで未確認にする。chart/summary/noteの欠測を未確認表示で維持。
4. 07..23の同根拠保存SOCを優先。保存点が一つでもある場合、既存SOC datasetはsaved_plan点だけとし他hourはnull。23保存終点は正常なので修正式を入れない。
5. 0..6のactual/参考値を計画SOC lineへ混ぜない。保存SOCが全無しのSQL/legacyだけ既存推定を「参考SOC（保存計画未連携）」と表示し、observed点はそのline/peakからも除外する。
6. schedule依存の参考推定はschedule.plan_date==dateのときだけ。別日/unknown scheduleでは既存推定helperへ不正なscheduleを渡さず、SOC/夜間grid chargeを未連携表示にする。
   ただし同日実測SOCと単一finite正の保存容量がある場合は、既存forecastSocFromLatestActualを直接呼び未来点だけを最新実測起点の参考SOCと表示する。観測点はreference peakにも混ぜず、旧scheduleは使用しない。歴史的missing-plan fallbackを保護する。
7. peakは保存点だけの「計画SOC最大」とする。17点未完なら「取得済み計画SOC最大 n/17」。保存が全無しで参考が適格なら参考pointだけの「参考SOC最大」。既存max helperへ分類後の配列を渡すだけ。
8. C04の全日24/07..22/23終了をcaptionで区別する。SQL保存SOCなしは参考/未連携と表示し、別日の計画や実測値から保存値を捏造しない。

## テスト・PASS条件

PLANNED `tests/test_dashboard_hourly_display.js`: 24完全/1欠測/全欠測/有効0、partial actual、same day/single run/mixed run、別日schedule9kWh、None schedule。
SOC fixtureは保存23=99を維持、0時actual40がforecast peakへ入らない、0..6推定の参考ラベル、stored07..23全部保持、SQL persisted無し、storedSOCのbool/文字列/NaN/-1/101を計画点にしない。
PASSは新PV/load値変更0、保存SOC差0、計画最大点は保存範囲のみ、未知を0にしない、追加request0、dataset増分0/axes変更0。
追加PASS: 全8chartの有効値とstack正負累積が軸範囲内（SOC0〜100保持）。tests/test_dashboard_axis_ranges.jsに5kWh超/負値/stack/全欠測/正常固定範囲の回帰を追加する。旧axes変更0は軸数変更0を意味し、C08の上下限拡張は許可する。
既存test_dashboard_calculations.js/forecast_soc_contract/06:30 warning回帰を維持する。

## 中断条件

古いSOC式/効率/07起点を修正しないと分類できない、SQL schema追加、storedSOCの再生成が必要なら停止して別の限定設計を報告する。

## 出口・引継ぎ

D05へpartial/legacy/SQL/別日/mixed generationを含む図・summary/noteのfixtureを渡す。

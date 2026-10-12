# D02 学習・係数・説明・時刻の表示

## 入口・参照範囲

[contracts C02/C03/C05](../contracts.md)、inventory R01/R02/R03/R09/R15/R18。
D01のpv_modelだけを使う。学習原本やAPIをbrowserから取得しない。

## 変更対象

| path / symbol | 限定差分 |
| --- | --- |
| templates/dashboard.html | 既存学習/モデル説明pane内にactive model・対象計画日/版・学習単位・時刻/修復表示用DOMを追加。既存37ID/8canvas/controls/tableのID変更なし |
| static/dashboard.js::fillLearningParamsTable | NEW validとlegacy/unknownを分岐。NEW theta4/日数/区間を表示し、旧PV由来行をactiveとして混ぜない |
| 同file::fillLearningParams | D01 summaryと選択forecast date/runの一致を判定して表示helperへ渡す |
| 同file::refreshDashboard | 既存store吸収/期間描画の後にfillLearningParamsを呼び直す。cached行の再描画だけでfetch/merge方針を変更しない |
| 同file NEW renderPvModelSummary(summary, binding) | コピー/学習期間/時刻/修復/無効理由をtextContentで表示。PV再計算なし |
| 同file NEW bindPvModelToForecast(summary,rows,date) | 当日全24rowがnonempty同一runを持ちsummaryのdate/runへ一致するときだけ有効とする。known+missing混在を一致扱いにしない。raw hash一致をbrowserで断定しない |

新class0。新DOM IDsはpvModelName/pvModelProvenance/pvModelLearningPeriod/pvModelTiming/pvModelRepair/pvModelExplanationの6個に限定。

## 変更禁止

store.params/DB model_parametersの削除・再保存・sample_count変更0。legacy physical/EWMA計算、消費補正、battery life/capacity値の変更0。
generic n()/modelParam()/staticParameterNamesの全体挙動、API/store module、Chart.js/axis/CSS frameworkを変更しない。
旧7日window/20日閾値を表示対応のために変えない。source式と説明式を別係数で推測しない。

## クラス・変数差分

locals summary/binding/rows/date、render categoryの観測単位を明記する。
NEW4係数はterms順theta0〜3、共通sample_countはtraining_sample_countで単位「1時間区間」。晴天日数は別行。
model_parameters.sample_countは「計画保存回数」、EWMA/温度は「有効日数」、NEWは「晴天日/時間区間」。
neutral mean/std/variance/ratio_sample_countはNEWでは「設定値/実績学習なし」と分類し、221から確信度を作らない。

## 実装手順・分岐

1. 同dateの全24rowがnonempty同一runかつvalid summaryではNEW名称とC03式、日数/区間/期間/版を表示する。row不足/duplicate hour/missing runも未確認扱い。
2. 既知legacyは旧物理説明/旧diagnosticsを維持。unknown/invalid/unsupported/mixed bindingは未確認理由を表示し、旧式に自動分類しない。
3. NEW時は旧PV radiation/global/daypart/array/EWMA値をactive欄へ出さない。保存された旧値は消さず、必要なら「旧方式履歴」と明示する。
4. created_atは係数版生成、training_as_ofは学習cutoff、usable_fromは利用開始、retrieved_atは予報取得完了。画面更新時刻や実測の観測時刻に読み替えない。
5. repaired_hoursを修復した区間として表示。有効0や夜間0を修復と数えない。provider-issued Noneは「不明」。
6. latest学習情報はそのplan_date/版を常に表示。過去月の閲覧だけでその月の係数だと誤認させない。
7. 初期main時の既存呼出しだけに依存しない。refreshDashboardの末尾で同じ要約を再bindし、history/slice吸収後の別run行に古いactive要約を残さない。既存main初期呼出しは保持してもよく、追加HTTPは0。

## テスト・PASS条件

PLANNED `tests/test_dashboard_model_display.js`: NEW17/221/4theta/年周期copy、legacy旧copy、missing/unknown/invalid、同日別run、旧PV leftovers、sigma設定値、provider発行None、修復empty/2interval、signedtheta/0値。
再描画fixtureは初期valid→同日別run行吸収→refreshで要約未確認、未知run→完全同runで復帰。初期だけの判定になっていないことを確認する。
HTML assertionsは旧37ID/8canvas/control保持＋追加6DOMだけ、GTI×PR×温度をNEWのactive式として表示しない。
PASSは全値単位/由来とraw field一致、no extra fetch、HTML injectionなし、legacy display保持。

## 中断条件

row run不明を一致推測する、旧PVを再適用する、公開full trace/全日付配列・秘密・座標が必要なら停止。

## 出口・引継ぎ

D03へmodel binding/未確認ラベルを渡す。D05へinventory対象IDとlegacy/NEWスクリーン再現fixtureを渡す。

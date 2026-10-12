# S06 — 交絡を分離した経済・SOC評価

## 入口・参照範囲

前提[S05](S05-plan-output.md)の出口＋[共通契約](../contracts.md)。S01〜05の全本文を読み直さず、生成された同入力・原本・traceを使う。G06を解決する検証step。曇雨天の実績を予測入力へ混ぜない。

## 変更対象

| 対象 | 許可 |
| --- | --- |
| `artifacts/analysis/` のNEW replay script/result/report | 保存入力による比較・一因子/組合せシミュレーション、hash/費用内訳/差分の保存 |
| `gates.json` G06 | 結果・局所悪化・未比較日を記録。実装導入の合否を判定 |

本stepにproduction source編集許可はない。必要なtestはS04/S05で定義済みの境界へ追加し、simulationはローカル保存データだけで行う。

## 変更禁止

| symbol/変数 | 禁止 |
| --- | --- |
| `effective_capacity_kwh`, `battery_temp_coeff_per_deg`, `battery_cycle_capacity_fade_per_cycle` | 方式ごとに容量/劣化を変えて改善率を作る |
| `soc_now_percent`, `target_soc_7_percent`の評価起点、`hourly_load_kwh` | 実際の初期SOC/負荷実績を候補ごとに変える。optimizer出力と固定入力は区別 |
| monthly料金・売電/penalty・raw CSV金融値 | 想定料金へ置換、月欠落を0、片側だけ23時残電資産評価 |
| `training_dates`, `as_of`, `forecast_issued_at` | 評価日以降の晴天/更新原本を学習へ使用 |
| 保存forecast原本 | replay計算を当時の公開予測として上書き |

## クラス・変数差分

production class追加0。NEW simulation行は `case_date`, `input_sha256`, `variant`, `pv_model`, `load_binding`, `midday_binding`, `scenario_binding`, `capacity_kwh`, `initial_soc_percent`, `target_soc_percent`, `buy_kwh`, `sell_kwh`, `end_soc_percent`, `loss_yen`, `end_asset_yen`, `loss_with_asset_yen`。入力keyは固定値、目標/費用は出力。金融評価式は既存研究/同契約を使用し、初期資産・夜間買電の二重評価を避ける。

## 実装手順・分岐

1. 共通比較可能37日/直近10日を選ぶ。未取得実績や当時入力が欠けた日は除外理由を記録し、方式ごとに対象日を変えない。
2. X1: 旧PVと新PVだけを変え、消費・容量・金融・scenarioを固定。hourly/daily MAE/biasと経済差を記録する。
3. X2: 同PV24で正しい07〜22night合計と意図的24合計を比較し、05/06/23二重計上検出fixtureを作る。
4. X3: 負荷値自体は同じ、raw daily night入力と最終補正hourly集計だけを切替える。
5. X4: 同PV/loadでdaily fractionと10〜15時hourly net余剰だけを比較する。
6. X5: 同中心PV/loadで旧pairedとnew neutral sigma×既存load scenarioを比較する。晴天日数を予報誤差sample_countへ流用しない。
7. X3/X4/X5の2³=8条件を全て比較し、相互作用/最大悪化日を報告する。単独の改善を合算して全体改善にしない。
8. X6: 同目標/容量/原本でfull precision→4dp保存→既存SOC再計算。X7: 訂正/更新/欠損/raceはS02/S07〜10の個別試験へ送る。

## テスト・PASS条件

| ID/状態 | 条件 | PASS/評価条件 |
| --- | --- | --- |
| ECON-001 PLANNED | 各評価日前だけ訓練、同forecast vintage、方式比較 | 将来混入0、日/時間集合一致、input provenanceを説明 |
| ECON-002 PLANNED | X1〜X5一因子＋8条件、共通料金/負荷/容量 | 経済/SOC変化の要因を分離、買電/売電/損失/23時資産/局所悪化を全条件表示 |
| ECON-003 PLANNED | 07起点、PV05/06/23変更、4dp保存 | SOC17点の起点と窓保持、丸め許容内、capacity/targetの違いを丸め扱いしない |
| ECON-004 PLANNED | raw CSV金融集計→生成原本→optimizer | raw非0なのにplan0となる欠落を拒否、買電↑費用↓など方向反転なし |
| ECON-005 EXISTING | [既存モデル試験](../../../../../tests/test_energy_model.py)、[runtime](../../../../../tests/test_energy_model_runtime.py) | 料金/容量/境界の現行期待値を維持 |

EXISTING実行は `python -m pytest -q tests/test_energy_model.py tests/test_energy_model_runtime.py`、planned局所testは追加後 `python -m pytest -q tests/test_pv_clear_ghi.py tests/test_energy_model_runtime.py`。simulation scriptは作成時にコマンド/入力hashを記録して同結果を再実行可能にする。合格数をMAEの改善率と混同しない。

G06の経済的採用基準は、同条件で現行より総損失/資産込み指標が改善するかを実optimizerで確認し、悪化日の条件と最大差も承認判断資料へ出すこと。過去MAE改善だけではG06をCLOSEDにしない。未検証条件は未検証と表示する。

## 中断条件

raw金融入力不一致、当時入力なし、方式間の料金/負荷/容量差、将来学習、改善を交絡から分離出来ない場合は停止してG06へ理由を記録。予報API/Drive/importや実機設定をsimulationから呼ばない。

## 出口・引継ぎ

replay/result/report/hashとX1〜X7分類、8条件/全37日・直近10日・最大日悪化をS07へ渡す。G06未解決なら新100％の本番安全/経済合格を宣言しない。next: [S07](S07-control-publication.md)。

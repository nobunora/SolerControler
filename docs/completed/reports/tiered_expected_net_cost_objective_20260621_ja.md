# 段階料金込み期待純コスト目的関数

作成日時: 2026-06-21 14:55 JST

## 目的

SOC判断の目的を「主観的な最大SOC」や「買電/売電ゼロ」ではなく、利益最大化に近い期待純コスト最小化へ寄せる。
昼間買電は固定単価ではなく、月次段階料金の増分コストで評価する。

## 評価関数

```text
score(candidate) =
  night_charge_cost
+ tiered_day_buy_increment_cost
+ export_penalty_or_value
+ peak_unmet_penalty
```

売電未契約中:

```text
export_penalty_or_value = expected_export_kwh * export_penalty_rate
```

売電契約後:

```text
export_penalty_or_value = - expected_export_kwh * sell_revenue_rate
```

昼間買電は次で評価する。

```text
tiered_day_buy_increment_cost =
  tiered_cost(monthly_day_buy_before_target + expected_day_buy_kwh)
- tiered_cost(monthly_day_buy_before_target)
```

## 変更内容

- `app/soc_cost_optimizer.py`
  - `SocCostModel.day_buy_cost_yen()` を追加。
  - `night8_tiered` の月次段階料金増分コストをSOC候補評価に使用。
- `energy_model_main.py`
  - 対象日より前の当月昼間買電kWhをCSVから集計。
  - 集計値をSOC optimizerへ渡す。
  - `soc_cost_risk` に料金段階情報と月次累積を出力。
- `.env.example`
  - `SOC_OBJECTIVE_MODE=tiered_expected_net_cost`
  - `SOC_TIERED_DAY_BUY_COST_ENABLED=true`
- `tests/test_soc_cost_optimizer.py`
  - 第1段階から第2段階をまたぐ増分コストのテストを追加。

## 代表10日シミュレーション

今回のローカルCSVでは、対象日より前の当月昼間買電が0kWhだったため、全日で第1段階単価から評価された。
そのため、固定39.10円評価より昼間買電の重みが下がり、SOCは少し下がる方向になった。

| 日付 | 固定単価SOC | 段階料金SOC | 差 | 月次昼間買電before | score |
|---|---:|---:|---:|---:|---:|
| 2026-05-03 | 49 | 48 | -1 | 0.0 | 286.6 |
| 2026-05-17 | 2 | 1 | -1 | 0.0 | 218.1 |
| 2026-05-24 | 85 | 84 | -1 | 0.0 | 92.2 |
| 2026-05-27 | 8 | 8 | 0 | 0.0 | 234.5 |
| 2026-05-28 | 68 | 68 | 0 | 0.0 | 299.8 |
| 2026-05-29 | 0 | 0 | 0 | 0.0 | 264.4 |
| 2026-05-30 | 0 | 0 | 0 | 0.0 | 318.6 |
| 2026-06-01 | 0 | 0 | 0 | 0.0 | 314.3 |
| 2026-06-05 | 40 | 40 | 0 | 0.0 | 210.1 |
| 2026-06-21 | 61 | 56 | -5 | 0.0 | 346.1 |

## 段階料金感度試験

6/21を使い、月次昼間買電beforeを仮想的に変えて比較した。

| 月次昼間買電before | SOC | 必要充電kWh | score | 期待昼間買電kWh | 昼間買電円 | 期待売電kWh | Peak unmet kWh |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 56 | 5.42 | 346.1 | 1.77 | 56.2 | 1.03 | 2.08 |
| 89 | 61 | 5.91 | 354.9 | 1.57 | 58.2 | 1.14 | 1.77 |
| 229 | 69 | 6.68 | 362.5 | 1.25 | 52.7 | 1.35 | 1.27 |

月次買電が第1段階上限や第2段階上限に近づくほど、昼間買電の限界単価が上がるため、SOCを高めに選び、昼間買電を減らす方向へ変化した。
これは狙い通り。

## 評価

- 第1段階内では、夜間充電による昼間買電回避はほぼトントンなので、SOCを低めにする判断は合理的。
- 第2/第3段階に近づくと、昼間買電回避の価値が上がるため、SOCを上げる判断に自然に変わる。
- 売電未契約中は `SOC_EXPORT_VALUE_MODE=penalty` により売電はペナルティ。
- 売電契約後は `SOC_EXPORT_VALUE_MODE=revenue` へ切り替えれば、売電収入を目的関数に入れられる。

## 出力

- `artifacts/replay/tiered_expected_net_cost_summary_20260621.csv`
- `artifacts/replay/tiered_sensitivity_20260621.csv`

## テスト

- `python -m pytest tests\test_soc_cost_optimizer.py tests\test_energy_model.py`
  - 27 passed
- `python -m compileall app\soc_cost_optimizer.py energy_model_main.py`
  - 成功

## 注意

- 本番デプロイ、KP-NET設定変更、commit/push は行っていない。
- 今回の代表10日では実績上の月次昼間買電beforeが0kWhだったため、閾値超え効果は感度試験で確認した。

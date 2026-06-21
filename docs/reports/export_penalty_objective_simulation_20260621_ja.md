# 売電未契約向け目的関数とシミュレーション

作成日時: 2026-06-21 14:05 JST

## 目的

売電契約が完了するまで、売電を収入ではなくペナルティとして扱う目的関数へ変更し、代表10日で再シミュレーションした。
売電契約後は環境変数でペナルティを外し、収入扱いへ切り替えられるようにした。

## 変更内容

- `app/soc_cost_optimizer.py`
  - `SocCostModel.export_value_mode` を追加。
  - `penalty` / `neutral` / `revenue` / `opportunity` を切り替え可能にした。
- `energy_model_main.py`
  - `SOC_EXPORT_VALUE_MODE` を読み込むようにした。
  - 既定は `penalty`。
  - `SOC_EXPORT_PENALTY_YEN_PER_KWH` 未指定時は昼間買電単価を売電ペナルティとして使う。
  - `SOC_SELL_REVENUE_YEN_PER_KWH` を追加し、将来 `revenue` モードで売電収入を反映できるようにした。
- `.env.example`
  - 売電目的関数の切替項目を追加。
- `tests/test_soc_cost_optimizer.py`
  - `penalty` / `neutral` / `revenue` の単価解釈テストを追加。

## 設定

```env
SOC_EXPORT_VALUE_MODE=penalty
SOC_EXPORT_PENALTY_YEN_PER_KWH=
SOC_SELL_REVENUE_YEN_PER_KWH=0
SOC_PEAK_UNMET_BASE_FACTOR=1.0
SOC_PEAK_UNMET_RISK_FACTOR=2.0
SOC_PEAK_UNMET_MAX_FACTOR=2.0
```

売電契約後の例:

```env
SOC_EXPORT_VALUE_MODE=revenue
SOC_SELL_REVENUE_YEN_PER_KWH=<売電単価>
```

売電を単に無視する場合:

```env
SOC_EXPORT_VALUE_MODE=neutral
```

## シミュレーション結果

比較対象は、直前のhourly予報あり・旧目的関数の結果。

| 日付 | 旧SOC | 新SOC | SOC差 | 旧売電kWh | 新売電kWh | 売電差 | 昼間買電差 | Peak unmet差 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-05-03 | 53 | 49 | -4 | 1.03 | 0.95 | -0.08 | +0.14 | +0.12 |
| 2026-05-17 | 3 | 2 | -1 | 4.36 | 4.32 | -0.05 | +0.04 | +0.02 |
| 2026-05-24 | 85 | 85 | 0 | 0.66 | 0.66 | 0.00 | 0.00 | 0.00 |
| 2026-05-27 | 8 | 8 | 0 | 1.86 | 1.86 | 0.00 | 0.00 | 0.00 |
| 2026-05-28 | 68 | 68 | 0 | 0.54 | 0.54 | 0.00 | 0.00 | 0.00 |
| 2026-05-29 | 0 | 0 | 0 | 5.06 | 5.06 | 0.00 | 0.00 | 0.00 |
| 2026-05-30 | 0 | 0 | 0 | 6.82 | 6.82 | 0.00 | 0.00 | 0.00 |
| 2026-06-01 | 0 | 0 | 0 | 7.13 | 7.13 | 0.00 | 0.00 | 0.00 |
| 2026-06-05 | 40 | 40 | 0 | 0.60 | 0.60 | 0.00 | 0.00 | 0.00 |
| 2026-06-21 | 68 | 61 | -7 | 1.33 | 1.15 | -0.19 | +0.28 | +0.43 |

## 集計

- 平均SOC差: -1.2ポイント
- 売電期待値差合計: -0.318kWh
- 昼間買電期待値差合計: +0.456kWh
- Peak unmet期待値差合計: +0.677kWh

## 評価

売電未契約の間は、売電をペナルティ方向に扱うのは経済合理性がある。
ただし、売電ペナルティを強めるとSOCはさらに下がりやすい。

特に6/21は、SOCが68%から61%へ下がり、売電期待値は0.19kWh減ったが、昼間買電とPeak unmetが増えた。
これは「売電を避けるためにSOCヘッドルームを増やす」方向なので、目的関数としては自然。
一方で、ユーザーの懸念である「SOCが低すぎる」問題は改善しない。

6/5は新目的関数でも40%のまま。
したがって6/5の低SOCは、売電ペナルティよりも、候補評価上のコスト最小点が40%付近にあることが主因。

## 次の提案

「売電は抑えるが、買電しない範囲で最大SOCを狙う」には、純粋な期待コスト最小化だけではなく、次の制約を追加するのがよい。

```env
SOC_MAXIMIZE_WITHIN_TOLERANCE_ENABLED=true
SOC_MAX_ALLOWED_DAY_BUY_KWH=0.5
SOC_MAX_ALLOWED_SELL_KWH=0.8
SOC_MAX_ALLOWED_PEAK_UNMET_KWH=2.5
```

このモードでは、許容範囲を満たす候補の中で最大SOCを選ぶ。
売電未契約中でもSOCを過度に下げにくくなり、ユーザー意図に近い。

## 実行コマンド

```powershell
python -m pytest tests\test_soc_cost_optimizer.py tests\test_energy_model.py
python -m compileall app\soc_cost_optimizer.py energy_model_main.py
```

## テスト結果

- `tests/test_soc_cost_optimizer.py tests/test_energy_model.py`: 26 passed
- `compileall`: 成功

## 注意

- 本番デプロイ、KP-NET設定変更、commit/push は行っていない。

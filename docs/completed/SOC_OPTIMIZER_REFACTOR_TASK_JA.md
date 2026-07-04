# SOC Optimizer Refactor Task

この指示書は、SOC最適化ロジックを小さく整理したうえで、直近で決めた制御変更を安全に入れるための作業範囲を固定するものです。
サブエージェントはこの範囲から外れないでください。

## 目的

- 売電契約が未完了で売電収入が0円の前提に合わせ、売電を過小評価しない。
- 日中の買電を避けつつ、太陽光充電終了時SOCを100%近辺へ寄せる。
- SOC最適化が今後の条件追加で読みにくくならないよう、最適化内部だけを小さく整理する。
- 既存の良いルール、DB項目、Firestore項目、外部連携を壊さない。

## やること

1. `app/soc_cost_optimizer.py` 内だけを中心に小リファクタする。
2. 既存の公開関数名 `optimize_soc_by_expected_cost` と `evaluate_soc_candidate` は維持する。
3. 既存の戻り値データ構造は後方互換を保つ。
4. PVと負荷を同時に扱えるシナリオ構造を追加する。
5. 既存のPV sigma bucketは維持し、PVシナリオ生成に使う。
6. 負荷シナリオを追加する。
7. 曇り・雨の日のPV上振れシナリオを追加できる設計にする。
8. 最大SOC未達ペナルティ係数は固定 `0.45` を使う。
9. 売電機会損失は `38.75円/kWh` として扱えるようにする。
10. 買電リスクと売電リスクの判定値を判断根拠に残せるようにする。
11. Firestoreに保存される既存の `decision_rationale` / `daytime_soc_optimization` の形は壊さない。
12. 6/1〜6/4の過去データで再シミュレーションし、想定SOCに近いことを確認する。
13. 既存テストとSOC最適化周辺テストを更新する。

## やらないこと

- 大規模リファクタはしない。
- `forecast_correction.py` 全体を整理しない。
- `energy_model.py` の旧ロジックと新ロジックを統合しない。
- DBカラム名、Firestoreフィールド名、環境変数名を削除・変更しない。
- 外部API、Cloud Run Job、Scheduler、KP-NET連携の契約を変えない。
- 実績による `0.45` の自動増減は入れない。
- 天気予測そのものの補正は入れない。
- 時間帯別の個別ペナルティ調整は入れない。
- 買電リスク/売電リスクによる複雑な倍率分岐は今回は入れない。

## 固定パラメータ

今回の実装で使う前提値です。既存の環境変数を壊さず、必要なら新規envを追加してください。

```text
SOC_PEAK_UNMET_BASE_FACTOR = 0.45
SOC_PEAK_UNMET_TARGET_SOC_PERCENT = 95
SELL_REVENUE_YEN_PER_KWH = 0
SELL_OPPORTUNITY_LOSS_YEN_PER_KWH = 38.75
NIGHT_BUY_RATE_YEN_PER_KWH = 31
USABLE_CHARGE_EFFICIENCY = 0.8
```

既存envがある場合は後方互換を優先してください。
既存envの意味を変える必要がある場合は、変更前に作業を止めて報告してください。

## 評価関数

SOC候補ごとの総期待損失は次で比較します。

```text
総期待損失 =
  夜間充電コスト
  + 日中買電コスト
  + 売電機会損失
  + 最大SOC未達ペナルティ
```

各項目の意味:

```text
夜間充電コスト =
  夜間充電kWh * 31円

日中買電コスト =
  日中買電kWh * 昼間買電単価

売電機会損失 =
  売電kWh * 38.75円

最大SOC未達ペナルティ =
  max(0, 95% - 日中最大SOC) に相当するkWh
  * 昼間買電単価
  * 0.45
```

## シナリオ設計

既存のPV専用 `SigmaBucket` に負荷を無理やり混ぜないでください。
人間が読める単位で、PV倍率・負荷倍率・確率・ラベルを持つシナリオにしてください。

推奨構造:

```text
ForecastScenario
  label
  probability
  pv_multiplier
  load_multiplier
```

負荷シナリオの初期値:

```text
load_low:  probability 0.2, multiplier 0.82
load_mid:  probability 0.6, multiplier 1.00
load_high: probability 0.2, multiplier 1.18
```

曇り・雨の日はPV上振れシナリオを追加します。
初期値は直近シミュレーションと同等にしてください。

```text
weather_upside_probability = 0.12
weather_upside_z = 3.5
```

確率の合計は必ず1.0へ正規化してください。

## リスク判定

今回は制御分岐には使わず、判断根拠として保存します。

```text
買電リスク =
  expected_day_buy_kwh > 0.3
  または
  worst_case_day_buy_kwh > 1.0

売電リスク =
  expected_sell_kwh > 0.3
  または
  worst_case_sell_kwh > 2.0
```

保存する情報:

- `expected_day_buy_kwh`
- `expected_sell_kwh`
- `worst_case_day_buy_kwh`
- `worst_case_sell_kwh`
- `buy_risk`
- `sell_risk`
- `peak_unmet_penalty_factor`
- `sell_opportunity_loss_yen_per_kwh`
- 使用したシナリオ数
- シナリオ生成方法

## 期待される再現結果

補正済み時間別予測と実績が揃っている直近データでは、固定 `0.45` で概ね次のSOCになる想定です。

```text
2026-06-01: SOC 0%
2026-06-02: SOC 38%
2026-06-03: SOC 95%
2026-06-04: SOC 66%
```

特に `2026-06-04` は重要です。

```text
x0.40: SOC 57%, 最大SOC 90.8%, 売電なし
x0.45: SOC 66%, 最大SOC 99.8%, 売電なし
x0.50: SOC 75%, 最大SOC 100%, 売電あり
```

実装後、`0.45` で `2026-06-04` がSOC `66%` 前後にならない場合は、原因を調べて報告してください。

## テスト要件

最低限、次を確認してください。

```text
python -m pytest tests/test_soc_cost_optimizer.py
python -m pytest tests/test_energy_model.py
python -m pytest
```

追加テストの観点:

- 負荷シナリオが確率付きで評価される。
- PVシナリオと負荷シナリオの組み合わせ数が期待通りになる。
- 売電機会損失 `38.75円/kWh` が候補選択に反映される。
- ピークSOC未達ペナルティ `0.45` が候補選択に反映される。
- 既存のPVのみ最適化テストが壊れない。
- `decision_rationale` にリスク判定情報が残る。

## 作業中に止める条件

次に該当した場合は、推測で進めずに報告してください。

- 既存のDB/Firestoreフィールド名を変えないと実装できない。
- 既存envの意味を変えないと実装できない。
- 6/1〜6/4の再現結果が大きくずれる。
- 既存テストの期待値が多数変わる。
- 旧ロジックと新ロジックのどちらを変更すべきか判断不能。

## 報告形式

作業完了時は、次を簡潔に報告してください。

```text
変更内容:
検証結果:
6/1〜6/4のSOC比較:
Firestore/decision_rationaleへの保存内容:
残るリスク:
```

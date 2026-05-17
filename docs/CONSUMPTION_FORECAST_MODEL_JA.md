# 消費電力量予測モデル仕様

この文書は、現行実装における消費電力量予測モデルの推定方程式と変数定義をまとめたものです。

対象コードは主に `app/consumption_forecast.py` と `energy_model_main.py` です。

## 重要な前提

このモデルが予測しているのは瞬間的な消費電力 `kW` ではなく、一定時間帯に積算した消費電力量 `kWh` です。

CSVの `消費電力量[kWh]` をもとに、翌日の次の2種類を別々に予測します。

| 記号 | 予測対象 | 集計時間帯 | 単位 |
|---|---|---:|---|
| `L_morning(d)` | 朝の消費電力量 | 07:00以上 10:00未満 | kWh |
| `L_daytime(d)` | 日中の消費電力量 | 07:00以上 23:00未満 | kWh |

日付 `d`、時刻付き観測 `t`、観測消費電力量 `load(t)` に対して、教師データは次のように作ります。

```text
L_morning(d) = sum(load(t) for t in d if 07:00 <= hour(t) < 10:00)

L_daytime(d) = sum(load(t) for t in d if 07:00 <= hour(t) < 23:00)
```

## 予測モデルの全体像

朝と日中で、独立した2つのモデルを学習します。

```text
pred_morning(d) = max(0, f_morning(x_morning(d)))

pred_daytime(d) = max(0, f_daytime(x_daytime(d)))
```

`f_morning` と `f_daytime` は、どちらも `HistGradientBoostingRegressor` による 0.75 分位点回帰です。

0.75分位点を使う理由は、夜間充電計画では消費をやや保守的に見積もる必要があるためです。平均値予測よりも、やや高めの消費シナリオを採用します。

## 特徴量ベクトル

各モデルの入力は、天気・暦・履歴を結合した特徴量ベクトルです。

```text
x(d) = [
  month,
  weekday,
  weather_code,
  is_weekend,
  temp,
  heating_degree,
  cooling_degree,
  sunshine_hours,
  precipitation,
  lag1,
  lag7,
  rolling_7,
  rolling_14,
  same_weekday_avg
]
```

朝モデルと日中モデルで、暦・天気の特徴量は同じです。履歴特徴量は、それぞれ `L_morning` と `L_daytime` から別々に作ります。

## 暦・天気変数

| 変数 | 定義 | 説明 |
|---|---|---|
| `month` | `month(d)` | 月。1から12。 |
| `weekday` | `weekday(d)` | 曜日。月曜=0、日曜=6。 |
| `weather_code` | `encode(weather_code(d))` | 天気コードをカテゴリ整数に変換した値。 |
| `is_weekend` | `1 if weekday(d) >= 5 else 0` | 土日なら1、平日なら0。 |
| `temp` | `T(d)` | 日平均気温。単位は degC。 |
| `heating_degree` | `max(0, 18 - T(d))` | 暖房需要の代理変数。 |
| `cooling_degree` | `max(0, T(d) - 24)` | 冷房需要の代理変数。 |
| `sunshine_hours` | `S(d)` | 日照時間。単位は hour/day。 |
| `precipitation` | `P(d)` | 日降水量。単位は mm/day。 |

気温はそのまま入れるだけでなく、暖房度日と冷房度日の形にも変換します。

```text
heating_degree(d) = max(0, 18 - temp(d))

cooling_degree(d) = max(0, temp(d) - 24)
```

これにより、寒い日と暑い日で消費が増えやすい非対称な関係を表現しやすくしています。

## 履歴変数

予測対象を `y(d)` とします。朝モデルでは `y(d) = L_morning(d)`、日中モデルでは `y(d) = L_daytime(d)` です。

対象日 `d` より前の履歴を日付順に並べたものを次のように置きます。

```text
H(d) = [(d_1, y(d_1)), (d_2, y(d_2)), ..., (d_n, y(d_n))]
where d_1 < d_2 < ... < d_n < d
```

履歴特徴量は次の式で作ります。

```text
lag1(d) = y(d_n)

lag7(d) = y(d_{n-6}) if n >= 7 else lag1(d)

rolling_7(d) = mean(last min(7, n) values of H(d))

rolling_14(d) = mean(last min(14, n) values of H(d))

same_weekday_avg(d) = mean(y(d_i) for d_i in H(d) if weekday(d_i) = weekday(d))
```

同じ曜日の履歴がない場合、`same_weekday_avg` は `rolling_7` で代替します。

履歴が全くない場合はモデル学習ができないため、フォールバック予測では `0.0 kWh` を返します。

## 推定方程式

現行モデルは線形回帰ではないため、次のような固定係数の式ではありません。

```text
y = a + b1 * temp + b2 * weekday + ...
```

実際には、0.75分位点損失を最小化する勾配ブースティング木の和として推定します。

予測対象 `r` を `morning` または `daytime` とすると、学習データは次の形です。

```text
D_r = {(x_r(d_i), y_r(d_i))}_{i=1..N}
```

損失関数は分位点損失です。

```text
rho_q(u) = u * (q - I(u < 0))

q = 0.75
```

モデルは、決定木 `h_m` を順に足し合わせる形です。

```text
F_0(x) = initial 0.75-quantile estimate

F_m(x) = F_{m-1}(x) + learning_rate * h_m(x)

f_r(x) = F_M(x)
```

現行設定は次の通りです。

| パラメータ | 値 |
|---|---:|
| `loss` | `quantile` |
| `quantile` | `0.75` |
| `learning_rate` | `0.05` |
| `max_iter` | `300` |
| `max_depth` | `6` |
| `min_samples_leaf` | `5` |
| `categorical_features` | `month`, `weekday`, `weather_code` |
| `random_state` | `42` |

最終予測値は負にならないようにクリップします。

```text
pred_r(d) = max(0, f_r(x_r(d)))
```

## 相互影響の扱い

このモデルでは、気温・月・曜日・天気を単純加算する手書き式にはしていません。

勾配ブースティング木は、木の分岐構造によって変数間の相互影響を表現します。

例として、次のような関係をモデル内部で表現できます。

| 相互影響 | 解釈例 |
|---|---|
| `temp x month` | 同じ気温でも春・夏・冬で消費の意味が違う。 |
| `temp x weekday` | 平日と休日で冷暖房や在宅時間の影響が違う。 |
| `weather_code x sunshine_hours` | 晴れ・曇り・雨と日照時間の組み合わせで消費傾向が変わる。 |
| `precipitation x is_weekend` | 雨の休日は在宅時間が伸び、消費が増える可能性がある。 |
| `lag1 x temp` | 直近消費が高い状態で気温条件が悪いと、翌日も高くなりやすい。 |
| `same_weekday_avg x weekday` | 曜日固有の生活パターンを履歴から吸収する。 |

つまり、現行モデルは「説明変数を足し算しただけの式」ではなく、条件分岐の組み合わせで非線形・相互作用を学習するモデルです。

## 学習条件

次の条件を満たす場合に `HistGradientBoostingRegressor` を使います。

| 条件 | 内容 |
|---|---|
| 天気データあり | 消費実績日と同じ日付の天気データがあること。 |
| 最小学習日数 | 既定では45日以上。 |
| scikit-learn利用可能 | `HistGradientBoostingRegressor` を import できること。 |
| 学習可能件数 | 最低2件以上。 |

最小学習日数は環境変数で変更できます。

```text
CONSUMPTION_MODEL_MIN_TRAINING_DAYS=45
```

## フォールバック式

学習データが不足している場合、または scikit-learn が利用できない場合は、履歴平均ベースのフォールバックを使います。

予測対象を `y(d)`、対象日前の履歴値を日付順に次のように置きます。

```text
V = [v_1, v_2, ..., v_n]
```

履歴がない場合は次の値です。

```text
fallback(d) = 0
```

履歴がある場合、候補値を作ります。

```text
window = min(fallback_window, n)

candidates = [
  v_n,
  mean(v_{n-window+1}, ..., v_n),
  mean(last min(7, n) values)
]
```

7件以上の履歴がある場合は、7日前相当の値も候補に加えます。

```text
candidates += [v_{n-6}] if n >= 7
```

同じ曜日の履歴がある場合は、同曜日平均も候補に加えます。

```text
candidates += [
  mean(v_i for d_i in H(d) if weekday(d_i) = weekday(d))
]
```

フォールバック予測値は候補値の平均です。

```text
fallback_raw(d) = max(0, mean(candidates))
```

`fallback_window` の既定値は14日です。

```text
CONSUMPTION_MODEL_FALLBACK_WINDOW_DAYS=14
```

### ゼロ予測時の前日実績フォールバック

データ不足時に `fallback_raw(d)` が `0.0 kWh` になる場合は、対象日前日の実績を優先して返します。

```text
previous_actual(d) = y(d - 1) if y(d - 1) exists
                   = latest y(d_i) before d otherwise

fallback(d) = previous_actual(d) if fallback_raw(d) <= 0 and previous_actual(d) exists
            = fallback_raw(d) otherwise
```

前日実績も存在しない場合は `0.0 kWh` のままです。

この経路を通った場合、出力の `source` は `fallback_previous_actual` になります。

## 充電予測への接続

消費電力量予測の結果は、夜間充電目標の計算に渡されます。

```text
morning_load_forecast_kwh = pred_morning(d)

daytime_load_forecast_kwh = pred_daytime(d)
```

夜間充電モデルでは、朝の不足量を次のように見積もります。

```text
predicted_pv_kwh = pv_kwh_per_sunhour * sun_hours_forecast * temperature_factor

predicted_morning_pv_kwh = predicted_pv_kwh * morning_pv_ratio

predicted_morning_deficit_kwh = max(0, morning_load_forecast_kwh - predicted_morning_pv_kwh)
```

この `predicted_morning_deficit_kwh` が、7時時点で最低限持っておきたい蓄電池エネルギー量の一部になります。

## 出力

`energy_model_main.py` は、予測結果を `artifacts/night_charge_plan.json` の `consumption_forecast` に保存します。

出力例の構造は次の通りです。

```json
{
  "target_date": "2026-05-18",
  "morning_load_kwh": 3.2,
  "daytime_load_kwh": 12.8,
  "source": "hist_gradient_boosting",
  "sample_count": 60,
  "features": [
    "month",
    "weekday",
    "weather_code",
    "is_weekend",
    "temp",
    "heating_degree",
    "cooling_degree",
    "sunshine_hours",
    "precipitation",
    "lag1",
    "lag7",
    "rolling_7",
    "rolling_14",
    "same_weekday_avg"
  ]
}
```

`source` が `hist_gradient_boosting` の場合は統計モデルによる予測です。

`source` が `fallback_rolling_average` の場合は、学習条件を満たさず履歴平均式で予測しています。

`source` が `fallback_previous_actual` の場合は、履歴平均式が `0.0 kWh` になったため、前日または直近の実績値を採用しています。

`source` が `fallback_no_history` の場合は、参照できる消費実績がなく、予測値は `0.0 kWh` です。

## 不在日の表現と現行実装

旅行などで年に数回だけ不在になる日は、通常日とは別の「予定イベント」として表現するのが安全です。

不在日は件数が少ないため、気温・月・曜日・天気だけから統計モデルに自然学習させると、通常日の消費パターンを壊す外れ値になりやすいです。

現行実装では、Googleスプレッドシートの `occupancy_schedule` タブに入力した日付範囲を読み取り、予測対象日が該当する場合だけ消費電力量予測を補正します。

| 変数 | 例 | 説明 |
|---|---|---|
| `start_date` | `2026-08-12` | 不在開始日。 |
| `end_date` | `2026-08-15` | 不在終了日。 |
| `occupancy_status` | `away` | 在宅状態。通常日は `normal`、不在日は `away`。 |
| `occupancy_factor` | `0.25` | 通常消費に対する係数。完全不在でも冷蔵庫などの待機消費があるため0にはしないのが基本です。 |
| `morning_load_override_kwh` | `0.8` | 必要なら朝消費を直接上書きします。 |
| `daytime_load_override_kwh` | `3.0` | 必要なら日中消費を直接上書きします。 |
| `include_in_training` | `false` | 通常日モデルの学習に含めるかどうか。原則は `false` 推奨です。 |
| `reason` | `travel` | 旅行、出張、帰省などのメモ。 |

現行実装の優先順位は次の通りです。

```text
1. 不在日の明示的な kWh override があれば、それを使う。

2. override がなければ、通常予測値に occupancy_factor を掛ける。

3. ただし最低待機消費量 standby_floor_kwh を下回らないようにする。

4. 不在日は通常日モデルの学習から除外する、または is_away=1 として別特徴量にする。
```

式で書くと次の形です。

```text
pred_away(d) = max(standby_floor_kwh, pred_normal(d) * occupancy_factor)
```

年に数回程度なら、`is_away` を学習特徴量として入れるより、予定ベースのオーバーライドとして扱う方が安定します。

入力シートの詳細は `docs/OCCUPANCY_SCHEDULE_JA.md` を参照してください。

## 現行実装上の注意

現行実装では、Open-Meteo の過去天気から `shortwave_radiation_sum_mj_m2` も取得していますが、消費電力量予測モデルの特徴量にはまだ入れていません。

また、`DailyWeatherFeatures.extras` は正規化時に保持されますが、現行の `MODEL_FEATURE_NAMES` には含まれていません。

そのため、現在の消費電力量予測に直接使われる天気系変数は、気温、天気コード、日照時間、降水量です。

# 東・南・西アレイの発電予測モデル

対象コードは主に `app/pv_array_forecast.py`, `energy_model_main.py`, `app/energy_model.py` です。

## 概要

太陽光パネルが東・南・西の複数面に分かれている場合、1つの日照時間係数にまとめると朝・昼・夕方の発電分布が崩れます。

現行実装では、`config/pv_arrays.json` に面別アレイを定義し、Open-Meteo の hourly `global_tilted_irradiance` を面ごとに取得して発電量を計算します。

```text
PV_total(t) = PV_east(t) + PV_south(t) + PV_west(t)
```

各面の予測式は次の形です。

```text
PV_i(t)
= capacity_i_kw
  * GTI_i(t) / 1000
  * performance_ratio_i
  * calibration_factor
  * shading_factor_i
  * temperature_factor_i(t)
```

## 面別設定

設定ファイルは `config/pv_arrays.json` です。

Open-Meteo の方位角は次の定義です。

| 方位 | azimuth_deg |
|---|---:|
| 東 | -90 |
| 南 | 0 |
| 西 | 90 |
| 北 | 180 または -180 |

`capacity_kw` は面別のDC容量です。現時点の初期値は東・南・西とも `1.0` の仮値です。

実際の面別kWが分かる場合は、ここを更新してください。合計値だけでなく東西南の比率が朝・夕方の予測に効きます。

## 実績補正

KP CSV の実測発電量と、過去日のOpen-Meteo GTIモデル発電量を比較し、全体の `calibration_factor` を推定します。

```text
calibration_factor
= sum(actual_pv_kwh) / sum(modeled_pv_kwh)
```

現状のCSVは発電量が全アレイ合算なので、東・南・西それぞれの `performance_ratio` を個別同定することはできません。

そのため、面別の形は `capacity_kw` の比率で表現し、実績補正は全体係数として全アレイに同じ倍率で掛けます。

## 夜間充電モデルへの接続

3面合算後、次の時間帯別に集計します。

| 指標 | 時間帯 |
|---|---|
| `morning_kwh` | 07:00-10:00 |
| `midday_kwh` | 10:00-16:00 |
| `evening_kwh` | 16:00-23:00 |
| `daytime_kwh` | 07:00-23:00 |
| `total_kwh` | 00:00-24:00 |

夜間充電計算では、旧来の固定比率ではなく、次の値を優先して使います。

```text
predicted_pv_kwh = total_kwh
predicted_morning_pv_kwh = morning_kwh
predicted_midday_surplus_kwh = max(0, midday_kwh - estimated_midday_load_kwh)
```

`estimated_midday_load_kwh` は、朝以外の日中消費予測に `PV_MIDDAY_LOAD_FRACTION` を掛けて求めます。未設定時は `6/13` です。

## 出力

`artifacts/night_charge_plan.json` に `pv_array_forecast` が追加されます。

また、Firestore/SQLite/PostgreSQL の `sunshine_daily` には次の値を保存します。

| 列 | 意味 |
|---|---|
| `forecast_pv_total_kwh` | 3面合算の予測発電量 |
| `forecast_pv_morning_kwh` | 朝の予測発電量 |
| `forecast_pv_midday_kwh` | 昼の予測発電量 |
| `forecast_pv_evening_kwh` | 夕方の予測発電量 |
| `forecast_pv_calibration_factor` | 実績補正係数 |


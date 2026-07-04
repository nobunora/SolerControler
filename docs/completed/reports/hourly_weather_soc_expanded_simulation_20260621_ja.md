# 1時間天気予報SOC制御の拡大シミュレーション

作成日時: 2026-06-21 10:05 JST

## 目的

`SOC_PEAK_UNMET_BASE_FACTOR=1.0` を固定し、Open-Meteoの1時間予報を使う場合と使わない場合を、比較可能な過去run 10日分で評価した。

## 条件

- `SOC_PEAK_UNMET_BASE_FACTOR=1.0`
- `SOC_PEAK_UNMET_RISK_FACTOR=2.0`
- `SOC_PEAK_UNMET_MAX_FACTOR=2.0`
- hourlyあり: Open-Meteo Previous Runs day-1 hourly予報を取得
- hourlyなし: 同じコードで `-DisablePreviousDay1Forecast`
- 日次sun/temp: Open-Meteo Historical Forecast API から取得
- DB投入は省略し、`night_charge_plan.json` の生成のみ実施

出力CSV:

- `artifacts/replay/hourly_weather_batch_summary_20260621.csv`

## 結果

| 日付 | sun h | baseline SOC | hourly SOC | 差 | kWh差 | day buy差 | peak unmet差 | hourly guard | 判定 |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| 2026-05-03 | 6.47 | 55 | 53 | -2 | -0.193 | -0.014 | +0.123 | applied | 小幅削減 |
| 2026-05-17 | 12.65 | 2 | 3 | +1 | 0.000 | -0.036 | -0.016 | applied | ほぼ同等 |
| 2026-05-24 | 2.07 | 85 | 85 | 0 | 0.000 | -0.169 | -0.101 | not applied | 低余剰で維持 |
| 2026-05-27 | 9.25 | 8 | 8 | 0 | 0.000 | -0.226 | -0.107 | applied | 同等 |
| 2026-05-28 | 0.99 | 69 | 68 | -1 | -0.095 | -0.047 | +0.221 | not applied | 低日照でほぼ維持 |
| 2026-05-29 | 10.86 | 2 | 0 | -2 | -0.189 | +0.075 | -0.210 | applied | 小幅削減 |
| 2026-05-30 | 12.14 | 0 | 0 | 0 | 0.000 | -0.031 | +0.007 | applied | 同等 |
| 2026-06-01 | 12.73 | 0 | 0 | 0 | 0.000 | -0.006 | +0.013 | applied | 同等 |
| 2026-06-05 | 3.39 | 50 | 40 | -10 | -0.995 | +0.131 | +0.553 | applied | 要注意 |
| 2026-06-21 | 3.98 | 77 | 68 | -9 | -0.871 | +0.189 | +0.485 | applied | 要注意 |

## 集計

- 対象日数: 10日
- SOC平均差: -2.3ポイント
- SOC削減日: 5日
- SOC同等日: 4日
- SOC増加日: 1日
- 夜間充電量差合計: -2.343kWh
- 昼間買電期待値差合計: -0.134kWh
- Peak unmet期待値差合計: +0.968kWh
- 期待コスト差平均: +16.6円

## 実績照合できた日

ローカルCSVで対象日の実績が確認できたのは一部のみ。

- 2026-06-01: 実績昼間買電0kWh、PV 17.718kWh、load 15.237kWh、SOC 1-88%
- 2026-06-05: 実績昼間買電0kWh、PV 7.949kWh、load 14.343kWh、SOC 14-100%

6/5はシミュレーション上はhourly版でSOCを10ポイント下げ、Peak unmet期待値が増えたが、実績では昼間買電0だった。
したがって「危険」と断定はできないが、低～中日照でSOCを大きく下げる条件は追加検証が必要。

## 評価

`SOC_PEAK_UNMET_BASE_FACTOR=1.0` は、1時間予報なし時の安全弁としては妥当そう。
hourly導入後も低日照日は多くの場合SOCを維持し、晴天日はSOCを下げる方向に働いた。

一方で、6/5と6/21のように sun 3-4h 程度でも日中純余剰ガードが効き、SOCを9-10ポイント下げるケースがある。
ここは制約としてやや攻めている可能性がある。

## 次の提案

1. `DAYTIME_NET_SURPLUS_HEADROOM_MIN_KWH` を 1.0 から 1.5 または 2.0 に上げて再比較する。
2. `DAYTIME_NET_SURPLUS_HEADROOM_RATIO` を 0.65 から 0.50-0.60 に下げて再比較する。
3. sun 4h未満または weather_class cloudy の日は、日中純余剰ガードのSOC削減幅に上限を設ける。
4. 6/21の実績が入った後、同じ比較を再実行する。

## 実行コマンド

```powershell
python -m pytest tests\test_energy_model.py tests\test_soc_cost_optimizer.py

# batch内で以下を10日分 x hourlyあり/なしで実行
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\replay_23h_local.ps1 `
  -RunId <run> -ForecastDate <date> -ForecastSunHours <sun> -ForecastTempC <temp> -SkipDbPipeline
```

## 注意

- ローカルADCのSheetsスコープ不足により、占有スケジュールはスキップされた。
- 本番デプロイ、KP-NET設定変更、commit/push は行っていない。

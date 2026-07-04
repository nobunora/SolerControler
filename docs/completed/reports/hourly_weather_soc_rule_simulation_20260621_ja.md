# 1時間天気予報を含むSOC判断ルール調整とシミュレーション

作成日時: 2026-06-21 09:45 JST

## 目的

説明可能なSOC候補評価型の土台に、Open-Meteoの1時間天気予報を追加し、日中充電余力と夕方SOC不足リスクの重みを調整する。
今回は本番反映ではなく、ローカルリプレイで妥当性を確認した。

## Open-Meteo確認結果

- Open-Meteoには `Previous Runs API` があり、`weather_code_previous_day1` や `shortwave_radiation_previous_day1` など、過去日のday-1予報を取得できた。
- 2026-05-03 の府中市相当地点で、24時間分のhourly予報が取得できた。
- `precipitation_probability_previous_day1` は取得できないケースがあったため、現時点では天気コード、降水量、雲量、短波放射、気温を主入力にする。
- 非商用・評価用途ならOpen Access/無料枠で試せる想定。今回のリプレイは数回のAPI呼び出しで収まった。

## 変更内容

- `energy_model_main.py`
  - Open-Meteo Previous Runs API から過去日のday-1 hourly予報を取得する処理を追加。
  - `forecast.hourly_weather_summary` を追加し、雨時間、低短波放射時間、昼間の代表天気を集計。
  - hourly短波放射を使ってPV予測の日内配分を補正。
  - PVアレイ予報が0kWh合計の場合、0で基礎PV予測を上書きしないよう修正。
  - `daytime_net_surplus_headroom_guard` を追加。
    - 7-17時の `hourly_pv_forecast - hourly_load_forecast` で純余剰を評価。
    - 純余剰が十分あり、雨・低放射でない場合だけSOC上限を下げる。
    - 低純余剰や雨寄りの日は緩和する。
- `app/forecast_correction.py`
  - `SOC_PEAK_UNMET_*` のコード上デフォルトを `.env.example` と一致させた。
  - base=1.0、risk=2.0、max=2.0。
- `scripts/replay_23h_local.ps1`
  - 過去day-1 hourly予報をデフォルトで使うようにした。
  - 出力ディレクトリ名にRunIdとミリ秒を含め、並列実行時の衝突を避けた。
- `.env.example`
  - hourly天気予報、daytime net surplus guard、Previous Runs APIの調整項目を追加。
- `tests/test_energy_model.py`
  - hourly天気集計、PV配分補正、日中純余剰ヘッドルームガードのテストを追加。

## シミュレーション結果

比較対象は、候補評価型整理直後の旧リプレイ結果。
新結果はOpen-Meteo Previous Runs day-1 hourly予報、PV日内配分補正、日中純余剰ガード、ピーク不足ペナルティ調整を含む。

| 日付相当 | 旧SOC | 新SOC | 差 | 旧必要kWh | 新必要kWh | 新期待コスト円 | 昼間買電kWh | 売電/余剰kWh | Peak unmet kWh | 判定 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 2026-05-03 | 85 | 58 | -27 | 8.089 | 5.520 | 290.2 | 0.623 | 0.959 | 1.615 | 晴れ/曇り寄りでhourly PV余力を反映し、夜間充電を削減 |
| 2026-05-24 | 85 | 85 | 0 | 0.096 | 0.096 | 99.1 | 1.269 | 0.661 | 0.627 | 低純余剰のため日中余力ガードは不適用 |
| 2026-05-17 | 2 | 3 | +1 | 0.000 | 0.000 | 199.1 | 0.426 | 4.491 | 0.854 | 大きな日中余剰があり低SOC維持 |

## 評価

- 5/3は旧85%から新58%へ大きく下がった。
  - 以前は過去日のPVアレイ予報0kWhが基礎PV予測を潰していた。
  - 修正後は日照時間ベースの総PVをhourly短波放射で配分でき、昼間充電余力を反映できた。
  - ただしPeak unmetが1.615kWh残るため、まだやや攻め気味。安全重視なら `SOC_PEAK_UNMET_BASE_FACTOR` を1.2-1.5程度へ上げる余地がある。
- 5/24は新旧同じ85%。
  - hourly予報を入れても純余剰が0.664kWhと小さく、夜間充電を減らす根拠が弱い。
  - これは期待通り。
- 5/17は2%から3%でほぼ同等。
  - 大きな日中余剰が見込めるため低SOC維持。
  - これも期待通り。

## 今後の調整候補

- `SOC_PEAK_UNMET_BASE_FACTOR`
  - 現在: 1.0。
  - 夕方SOC 0リスクをさらに避けるなら 1.2-1.5 を試す。
- `DAYTIME_NET_SURPLUS_HEADROOM_RATIO`
  - 現在: 0.65。
  - 晴れの日の夜間充電をもっと削るなら上げる。安全側なら下げる。
- `DAYTIME_NET_SURPLUS_HEADROOM_MIN_KWH`
  - 現在: 1.0kWh。
  - 低余剰日に過敏に反応しないため、今の値は妥当そう。
- 過去day-1予報の対象モデル
  - 現在: `jma_seamless`。
  - 必要なら `best_match` や他モデルとの比較を行う。

## 実行コマンド

```powershell
python -m pytest tests\test_energy_model.py tests\test_soc_cost_optimizer.py
python -m compileall energy_model_main.py app\forecast_correction.py
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\replay_23h_local.ps1 -RunId 20260502-001639 -ForecastDate 2026-05-03 -ForecastSunHours 6.2 -ForecastTempC 20
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\replay_23h_local.ps1 -RunId 20260524-163445 -ForecastDate 2026-05-24 -ForecastSunHours 1.929 -ForecastTempC 20
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\replay_23h_local.ps1 -RunId 20260517-134230 -ForecastDate 2026-05-17 -ForecastSunHours 3.0 -ForecastTempC 24
```

## テスト結果

- `python -m pytest tests\test_energy_model.py tests\test_soc_cost_optimizer.py`: 25 passed
- `python -m compileall energy_model_main.py app\forecast_correction.py`: 成功

## 既知の制約

- ローカルADCのSheetsスコープ不足により、占有スケジュールはリプレイ中にスキップされた。
- 今回はローカル検証のみ。本番デプロイ、KP-NET設定変更、Git push は行っていない。
- 過去day-1予報の無料枠は十分小さい利用量で確認したが、大量バックテスト時はAPI呼び出し数をキャッシュする設計が必要。

## 次の推奨

1. 追加で10-20日分の代表日をリプレイし、`SOC_PEAK_UNMET_BASE_FACTOR=1.0/1.2/1.5` を比較する。
2. 過去day-1 hourly予報をローカルSQLiteまたはJSONキャッシュへ保存し、再シミュレーション時のAPI呼び出しを削減する。
3. ダッシュボードに `daytime_net_surplus_headroom_guard`、候補SOC、Peak unmet、期待コストを表示する設計へつなげる。

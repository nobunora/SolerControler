# SOC判断フロー候補評価型整理と過去データシミュレーション

作成日時: 2026-06-21 09:00 JST

## 目的

1時間毎の天気予報を全面導入する前に、既存SOC設定ルールを説明可能で矛盾が少ない形へ寄せる。
今回は最小改修として、制約付き候補評価型へ移行しやすい土台を追加し、過去データのリプレイで判断内容を確認した。

## 変更内容

- `app/soc_cost_optimizer.py`
  - 選択SOCだけでなく、上位候補と却下理由を `candidate_summaries` として返すようにした。
  - 却下理由は `higher_day_buy_risk` / `higher_sell_loss` / `higher_peak_unmet_risk` / `higher_night_charge` / `higher_total_cost` / `selected`。
- `energy_model_main.py`
  - `plan_quality` を追加し、予報入力の健全性と適用可否を出力するようにした。
  - `decision_rationale` に目的関数、選択理由、有効制約、却下候補、コスト内訳を追加した。
  - `SOC_COST_RESPECT_MORNING_HEADROOM_CAP` の既定値を `true` に変更し、朝PVヘッドルーム制約をデフォルトで尊重するようにした。
  - `SOC_COST_SELL_OPPORTUNITY_LOSS_YEN_PER_KWH` は環境変数が明示された場合のみ上書きし、未指定時は `SOC_COST_SELL_VALUE_RATIO` が効くようにした。
  - `raw_target_soc_7_percent` は最終値ではなく base 値を指すように修正した。
- `tests/test_soc_cost_optimizer.py`
  - 候補サマリに選択候補と却下候補が含まれることを確認するテストを追加した。

## シミュレーション結果

ローカル同期済みの過去CSVを使い、`scripts/replay_23h_local.ps1` で3ケースを再実行した。
本番設定変更やKP-NETへの反映は行っていない。

| リプレイ | 予報日 | 日照h | Target SOC | 必要充電kWh | 総期待コスト円 | 昼間買電kWh | 売電/余剰kWh | Peak unmet kWh | 主な制約 | 代表的な却下理由 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| 20260621-085419 | 2026-05-03 | 6.20 | 85 | 8.089 | 488.1 | 5.667 | 0.000 | 0.894 | reserve, overnight, historical | 84/83/82% が昼間買電リスク増 |
| 20260621-085444 | 2026-05-24 | 1.93 | 85 | 0.096 | 74.7 | 1.445 | 0.072 | 0.726 | reserve, overnight, historical | 84% が peak unmet 増、83/82% が昼間買電リスク増 |
| 20260621-085509 | 2026-05-17 | 3.00 | 2 | 0.000 | 178.7 | 0.461 | 4.379 | 0.870 | reserve, overnight, morning_headroom, historical | 3/1% が総コスト増、4% が売電損増 |

## 分かったこと

- SOC 85% の判断は、単純に天気だけで決まっているわけではない。
- 低いSOC候補は主に昼間買電リスク、または夕方の peak unmet リスクで落ちている。
- そのため「晴れ予報なのに高SOC」というケースは、現在の評価では historical_daytime_soc_gain_guard と昼間買電リスクの重みが強く効いている可能性が高い。
- 候補サマリにより、なぜ1つ下のSOCではなく選択SOCになったかを追えるようになった。
- 朝PVヘッドルーム制約をデフォルト尊重にしたことで、過充電側の説明は以前より一貫する。

## 足りない点と提案

- `plan_quality.should_apply` は出力のみで、現時点ではKP-NET反映停止には使っていない。
  - 次段で `partial_data` / `unsafe_to_apply` / `stale_forecast` の場合は反映しないガードを追加するのがよい。
- `stale_forecast` 判定は未実装。
  - Open-Meteo取得時刻または予報生成時刻を保存し、古い予報なら安全側へ倒す設計が必要。
- `historical_daytime_soc_gain_guard` が強すぎる日がある可能性がある。
  - 次はこの guard を `Hard constraint` ではなく `Risk constraint` として扱うか、天気/純余剰/過去誤差に応じて重みを変えるシミュレーションを行うのがよい。
- リプレイ時の `weather_class` は `unknown` になっている。
  - 過去リプレイにも weather code または hourly forecast のスナップショットを渡すと、雨/晴れ条件ごとの評価精度が上がる。
- 1時間毎予報の全時間帯をSOCへ直接反映するのはまだ入れていない。
  - 先に候補評価型の入力欄として hourly forecast を受け取れる形にし、雨・低純余剰・夕方SOC 0リスクの日だけ効かせるのが低コスト。

## 実行コマンド

```powershell
python -m compileall energy_model_main.py app\soc_cost_optimizer.py
python -m pytest tests\test_soc_cost_optimizer.py tests\test_energy_model.py
scripts\replay_23h_local.ps1  # 3ケースをローカルデータで実行
```

## テスト結果

- `python -m compileall energy_model_main.py app\soc_cost_optimizer.py`: 成功
- `python -m pytest tests\test_soc_cost_optimizer.py tests\test_energy_model.py`: 22 passed

## 既知の制約

- Google Sheets の占有スケジュール取得はローカルADCスコープ不足でスキップされたが、SOCリプレイ自体は継続した。
- 今回はCloud同期、本番デプロイ、KP-NET設定変更、Git push は行っていない。
- 既存の未追跡ログ/スケジューラ関連ファイルは本変更とは別管理のまま。

## 次の推奨マイルストーン

1. `plan_quality.should_apply` を実際の反映可否に接続する。
2. `historical_daytime_soc_gain_guard` を候補評価の Risk constraint として再設計し、過去データで比較する。
3. hourly forecast は全日常時ではなく、雨・低純余剰・夕方SOC 0リスク日だけ評価に入れる。
4. ダッシュボード向けに `candidate_summaries` と `decision_rationale` を表示できる最小UI設計を切る。

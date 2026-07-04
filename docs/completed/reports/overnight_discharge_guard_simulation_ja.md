# overnight_discharge_guard シミュレーション報告

作成日: 2026-06-19

## 目的

`overnight_discharge_guard.expected_kwh` を複数値に変更した場合に、過去データ上で夜間充電量・7時SOC目標・日中買電/売電がどう変わるかを確認した。

## 入力データ

- Firestore から `artifacts/cloud_pull.db` へ同期したローカルSQLite
- Firestore の `night_charge_plans` を `artifacts/cloud_pull_night_charge_plans.json` へ退避
- 実績リプレイ対象: `2026-05-29` から `2026-06-18` までの21日
- ガード実装後の保存済み計画対象: `2026-06-13` から `2026-06-18` までの6日

## 候補値

`expected_kwh = 0, 1, 2, 3, 4, 5, 6`

## 結果概要

21日全体では、`expected_kwh=0` が最も低コストだった。

| expected_kwh | 平均目標SOC | 平均夜間充電kWh | 実績買電相当円 | 売電機会損失込み円 | 平均日中買電kWh | 平均日中売電kWh |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 52.6% | 4.19 | 178.0 | 194.9 | 1.46 | 0.44 |
| 1 | 52.0% | 4.52 | 182.3 | 202.6 | 1.32 | 0.52 |
| 2 | 51.5% | 4.78 | 186.0 | 211.1 | 1.23 | 0.65 |
| 3 | 51.2% | 4.94 | 189.9 | 217.5 | 1.21 | 0.71 |
| 4 | 51.2% | 4.96 | 190.6 | 218.2 | 1.21 | 0.71 |
| 5 | 51.2% | 4.96 | 190.6 | 218.2 | 1.21 | 0.71 |
| 6 | 51.2% | 4.96 | 190.6 | 218.2 | 1.21 | 0.71 |

## 夜間充電不足による昼間買電損失

安全側の `expected_kwh=6` を基準に、候補値を下げたことで増えた昼間買電を評価した。

| expected_kwh | 平均昼間買電kWh | 平均昼間買電円 | 6kWh比の追加昼間買電kWh合計 | 6kWh比の追加昼間買電円 | 6kWh比の夜間充電節約円 | 差引増減円 |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 1.46 | 57.0 | 5.15 | 201.5 | 465.4 | -263.9 |
| 1 | 1.32 | 51.8 | 2.33 | 91.1 | 265.1 | -174.0 |
| 2 | 1.23 | 48.0 | 0.32 | 12.6 | 109.8 | -97.2 |
| 3 | 1.21 | 47.4 | 0.00 | 0.0 | 14.0 | -14.0 |
| 4 | 1.21 | 47.4 | 0.00 | 0.0 | 0.0 | 0.0 |
| 5 | 1.21 | 47.4 | 0.00 | 0.0 | 0.0 | 0.0 |
| 6 | 1.21 | 47.4 | 0.00 | 0.0 | 0.0 | 0.0 |

解釈:

- `expected_kwh=0` は、6kWh基準より昼間買電が21日合計で `5.15kWh` 増え、昼間買電費は `201.5円` 増えた。
- ただし夜間充電を減らせた効果が `465.4円` あり、差引では `263.9円` 安かった。
- `expected_kwh=2` では昼間買電増は `0.32kWh / 12.6円` まで小さく、夜間充電節約 `109.8円` が残る。
- `expected_kwh=3` 以上では、今回の過去データ上では昼間買電抑制効果がほぼ頭打ち。

ガード実装後の6日だけでは、候補値を変えても結果は同一だった。

| expected_kwh | 平均目標SOC | 平均夜間充電kWh | 実績買電相当円 | 売電機会損失込み円 | 平均日中買電kWh | 平均日中売電kWh |
|---:|---:|---:|---:|---:|---:|---:|
| 0-6 | 42.3% | 4.11 | 161.6 | 165.7 | 1.10 | 0.11 |

## 今回の6/19判断との関係

2026-06-19 の計画では、04:30時点の `soc_now_percent` が `0.0%` だった。

この状態では、`expected_overnight_discharge_kwh` を大きくしても、投影SOCはすでに0なので、7時SOC目標そのものを押し上げる主因にはなりにくい。

実際の保存済み計画:

- `expected_overnight_discharge_kwh = 6.0`
- `target_soc_7_percent = 34.0`
- `required_night_charge_kwh = 3.2527`
- KP-NET設定では `34%` が `socUpper=40` に切り上げ

したがって、6/19の40%充電は `expected_kwh=6.0` 単独が原因ではなく、SOCが0%だったことと、SOC設定候補の40%丸めが主因。

## 推奨

現時点の過去データだけで最適値を選ぶなら、`OVERNIGHT_DISCHARGE_GUARD_CAP_KWH=0` から `2` 程度が妥当。

ただし、04:30時点でSOCが十分残っているケースでは、ガードを0にすると7時までの自然放電を過小評価する可能性がある。安全側を少し残すなら `2.0kWh`、晴天日の過充電抑制を優先するなら `1.0kWh` を推奨する。

## 制約

- `night_charge_plans` の保存開始前の古い日付では、`overnight_discharge_guard` の詳細が存在しない。
- 実績リプレイは、07:00-23:00の実績PV/負荷を使った近似評価。
- KP-NETの実際のSOC候補丸め、充電開始遅延、充電停止タイミングは完全には再現していない。

## 実行コマンド

- `python .\scripts\sync_validation_state.py --direction firestore-to-sqlite --sqlite artifacts\cloud_pull.db --project-id codrivernavi-web-20260510 --database-id "(default)"`
- Firestore `night_charge_plans` を `artifacts/cloud_pull_night_charge_plans.json` に退避
- インラインPythonで `expected_kwh = 0..6` の過去日リプレイを実行

## 生成物

- `artifacts/cloud_pull.db`
- `artifacts/cloud_pull_night_charge_plans.json`
- `artifacts/overnight_guard_simulation.json`
- `artifacts/overnight_guard_shortage_loss.json`
- `docs/completed/reports/overnight_discharge_guard_simulation_ja.md`

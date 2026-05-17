# 不在予定入力シート仕様

旅行・出張・帰省などで通常と違う消費になる日は、Googleスプレッドシートの `occupancy_schedule` タブへ入力します。

このタブは、Sheetsバックアップジョブが存在しない場合に自動作成し、ヘッダー行だけを補完します。既存の予定行は上書きしません。

## 入力例

| enabled | start_date | end_date | status | occupancy_factor | morning_load_override_kwh | daytime_load_override_kwh | standby_floor_morning_kwh | standby_floor_daytime_kwh | include_in_training | reason | note |
|---|---|---|---|---:|---:|---:|---:|---:|---|---|---|
| true | 2026-08-12 | 2026-08-15 | away | 0.25 |  |  | 0.5 | 2.5 | false | travel | 旅行で不在 |
| true | 2026-09-03 | 2026-09-03 | away |  | 0.8 | 3.0 |  |  | false | business_trip | 日帰り出張 |

## 列定義

| 列 | 必須 | 説明 |
|---|---|---|
| `enabled` | 任意 | `true` の行だけ有効。空欄も有効扱いです。 |
| `start_date` | 必須 | 予定開始日。`YYYY-MM-DD` または `YYYY/MM/DD`。 |
| `end_date` | 任意 | 予定終了日。空欄なら `start_date` と同じ1日予定。 |
| `status` | 任意 | 不在日は `away`。`normal` は補正対象外。空欄は `away`。 |
| `occupancy_factor` | 任意 | 通常予測値に掛ける係数。`away` で空欄なら `0.25`。 |
| `morning_load_override_kwh` | 任意 | 朝 07:00-10:00 の消費予測を直接指定。 |
| `daytime_load_override_kwh` | 任意 | 日中 07:00-23:00 の消費予測を直接指定。 |
| `standby_floor_morning_kwh` | 任意 | 朝の最低待機消費量。係数補正後の下限。 |
| `standby_floor_daytime_kwh` | 任意 | 日中の最低待機消費量。係数補正後の下限。 |
| `include_in_training` | 任意 | 通常モデルの学習に含めるか。不在日は `false` 推奨。空欄も `false`。 |
| `reason` | 任意 | `travel`、`business_trip` などの理由。 |
| `note` | 任意 | 人間向けメモ。 |

## 補正式

直接指定値がある場合は、それを優先します。

```text
pred_morning = morning_load_override_kwh
pred_daytime = daytime_load_override_kwh
```

直接指定値がない場合は、通常予測に係数を掛け、最低待機消費量を下回らないようにします。

```text
pred_morning = max(standby_floor_morning_kwh, normal_morning * occupancy_factor)

pred_daytime = max(standby_floor_daytime_kwh, normal_daytime * occupancy_factor)
```

最低待機消費量が空欄の場合は、下限 `0.0 kWh` として扱います。

## 適用優先順位

同じ日に複数の有効予定がある場合は、シート上で後ろにある行を優先します。

運用上は、あとから追加した予定で上書きしやすくするためです。

## 学習データからの除外

`include_in_training=false` の不在日は、消費電力量予測モデルの学習データから除外します。

これにより、旅行中の低い消費実績が通常日の消費予測を引き下げすぎることを避けます。

## 出力確認

不在予定が適用された場合、`artifacts/night_charge_plan.json` に次の情報が入ります。

```json
{
  "consumption_forecast": {
    "source": "hist_gradient_boosting+occupancy_away"
  },
  "base_consumption_forecast": {
    "source": "hist_gradient_boosting"
  },
  "occupancy_adjustment": {
    "method": "factor",
    "original_morning_load_kwh": 4.0,
    "adjusted_morning_load_kwh": 1.5
  }
}
```

`consumption_forecast` は充電計画に実際に使われる補正後の予測です。

`base_consumption_forecast` は不在補正前の通常予測です。

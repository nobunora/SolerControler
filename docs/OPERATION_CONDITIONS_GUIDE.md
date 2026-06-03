# 運用条件ファイルガイド（operation_conditions.json）

この文書は、現在の実装が `config/operation_conditions.json` から読み取るルールだけを厳密に説明します。

対象コード:

- `app/kpnet_workflow.py`
- `cloud_job_runner.py`
- `energy_model_main.py`
- `config/operation_conditions.json`

## 1. このファイルで制御できる範囲

`operation_conditions.json` が直接制御するのは、KP-NETへ送る設定プロファイル内の時刻条件です。

- 夜間プロファイルの充電終了時刻
- 夜間プロファイルの充電開始時刻の補正条件
- 日中グリーンプロファイルの充電時間帯
- 日中放電開始時刻
- 0時跨ぎ禁止と開始終了同一禁止

次の判断は `operation_conditions.json` ではなく、環境変数、夜間計画、ジョブ制御、またはエネルギーモデル側で決まります。

- 強制充電モードにするかどうか
- 23時に部分強制充電を待機させ、03系ジョブで開始時刻を逆算する制御
- SOC目標値 `target_soc_7_percent`
- 必要夜間充電量 `required_night_charge_kwh`
- 太陽光蓄電余力、買電、売電を考慮した7時SOC最適化
- 予報データ取得、CSV取得、Firestore/SQLite/PostgreSQLへの保存

## 2. 実装上の読み取り順

トップレベル構造は次の形です。

```json
{
  "version": 1,
  "description": "...",
  "priority_order": ["fixed", "variable"],
  "fixed": [],
  "variable": []
}
```

現在のコードが必須として検証するのは `fixed` と `variable` が配列であることです。

`priority_order` は現行コードでは適用順の制御には使いません。実際の処理は次の順序です。

1. `variable` から該当IDの有効ルールを探し、時刻を決定
2. 決定した充電時間帯に `fixed` を優先度順で適用
3. 補正後も不正な時間帯ならエラー停止
4. KP-NET候補値に合わせてSOCコードや運転モードコードへ丸める

各セクション内のルールは `enabled` が `false` でないものだけが有効です。省略時は有効として扱います。

同じIDが複数ある場合は、`priority` が大きいものから探索されます。

## 3. 現在の固定条件（fixed）

固定条件は `_apply_fixed_time_rules()` で充電時間帯に対して適用されます。

対象判定:

- `target: "charge"` は充電時間帯だけに適用
- `target: "all"` はすべての時間帯に適用
- その他の `target` は現在の充電時間帯処理では無視

### forbid_cross_midnight

現在値:

```json
{
  "id": "forbid_cross_midnight",
  "enabled": true,
  "priority": 1000,
  "target": "charge",
  "min_duration_minutes": 30
}
```

動作:

- 条件: `start_minute > end_minute`
- 補正: `start_minute = max(0, end_minute - min_duration_minutes)`
- 目的: KP-NETへ0時を跨ぐ充電時間帯を送らない

### forbid_same_start_end

現在値:

```json
{
  "id": "forbid_same_start_end",
  "enabled": true,
  "priority": 990,
  "target": "charge",
  "min_duration_minutes": 30
}
```

動作:

- 条件: `start_minute == end_minute`
- 補正: まず `start_minute = max(0, end_minute - min_duration_minutes)`
- それでも同一なら `end_minute = min(23:59, start_minute + min_duration_minutes)`
- 目的: 開始時刻と終了時刻が同一の充電設定を送らない

補正後も `start_minute > end_minute` または `start_minute == end_minute` の場合は `RuntimeError` で停止します。

## 4. 現在の変動条件（variable）

現行コードが参照する変動条件IDは次の4つだけです。

- `night_charge_end_time`
- `night_charge_end_by_forecast`
- `day_charge_window`
- `day_discharge_start_by_forecast`

それ以外のIDは、現行コードでは参照されません。

### night_charge_end_time

現在値:

```json
{
  "id": "night_charge_end_time",
  "enabled": true,
  "priority": 500,
  "value": "07:00"
}
```

動作:

- 夜間プロファイルの基本充電終了時刻です。
- `night_charge_end_by_forecast.sunny_end` が未指定の晴れ扱い日は、この値が使われます。
- ルール未設定時のコード上デフォルトは `06:00` です。

### night_charge_end_by_forecast

現在値:

```json
{
  "id": "night_charge_end_by_forecast",
  "enabled": true,
  "priority": 450,
  "sunny_min_sun_hours": 6.0,
  "cloudy_or_rain_end": "07:00"
}
```

動作:

- `night_charge_plan.json` の `forecast_sun_hours` で夜間充電終了時刻を切り替えます。
- `forecast_sun_hours` が欠損している場合は `night_charge_end_time.value` を使います。
- `forecast_sun_hours >= sunny_min_sun_hours` の場合は晴れ扱いです。
- 晴れ扱いで `sunny_end` がある場合は `sunny_end` を使います。
- 晴れ扱いで `sunny_end` がない場合は `night_charge_end_time.value` を使います。
- `forecast_sun_hours < sunny_min_sun_hours` の場合は曇り/雨扱いとして `cloudy_or_rain_end` を使います。
- `cloudy_or_rain_end` 未設定時のコード上デフォルトは `07:00` です。

このルール名は曇り/雨と表現していますが、現行実装では天気コードではなく日照時間しきい値で判定します。

### day_charge_window

現在値:

```json
{
  "id": "day_charge_window",
  "enabled": true,
  "priority": 400,
  "start": "00:00",
  "end": "07:00"
}
```

動作:

- 日中グリーンプロファイルで使う充電時間帯です。
- `start` 未設定時のコード上デフォルトは `00:00` です。
- `end` 未設定時のコード上デフォルトは `06:00` です。
- この時間帯にも `fixed` の0時跨ぎ禁止と開始終了同一禁止が適用されます。

### day_discharge_start_by_forecast

現在値:

```json
{
  "id": "day_discharge_start_by_forecast",
  "enabled": true,
  "priority": 350,
  "sunny_min_sun_hours": 6.0,
  "sunny_start": "06:00",
  "cloudy_start": "07:00"
}
```

動作:

- `night_charge_plan.json` の `forecast_sun_hours` で日中放電開始時刻を切り替えます。
- `forecast_sun_hours` が欠損している場合は `KP_DAY_DISCHARGE_WINDOW_START` を使います。
- `forecast_sun_hours >= sunny_min_sun_hours` の場合は `sunny_start` を使います。
- `forecast_sun_hours < sunny_min_sun_hours` の場合は `cloudy_start` を使います。
- `sunny_start` 未設定時のコード上デフォルトは `06:00` です。
- `cloudy_start` 未設定時のコード上デフォルトは `07:00` です。
- 放電終了時刻は `operation_conditions.json` ではなく `KP_DAY_DISCHARGE_WINDOW_END` で決まります。

## 5. 夜間プロファイルの現在ルール

夜間プロファイルは `_build_dynamic_forced_profile()` で作ります。

処理順:

1. `night_charge_plan.json` を読む
2. CSVから夜間充電電力を推定する
3. `required_night_charge_kwh / estimated_charge_power_kw` から必要充電時間を計算する
4. `night_charge_end_time` と `night_charge_end_by_forecast` で充電終了時刻を決める
5. 終了時刻から必要充電時間を逆算して充電開始時刻を決める
6. 必要時間が終了時刻までの同日内窓を超える場合は、開始時刻を `00:00` 側へクリップする
7. `fixed` 条件で0時跨ぎと開始終了同一を補正する
8. `day_discharge_start_by_forecast` で日中放電開始時刻を決める
9. SOC目標値以上の最小 `SocChargeMode` コードを選ぶ

強制充電モードかグリーンモードかの選択:

- `required_charge_percent >= KP_GREEN_MODE_MAX_CHARGE_PERCENT` なら強制充電寄りの運転モードを選びます。
- 現在のデプロイ値は `KP_GREEN_MODE_MAX_CHARGE_PERCENT=50` です。
- このしきい値は `operation_conditions.json` では変更しません。

## 6. SOC目標の経済最適化

7時SOC目標は `operation_conditions.json` では直接決めません。`energy_model_main.py` が
PV予測・消費予測・蓄電池容量を集め、`app/soc_cost_optimizer.py` がSOC候補を比較します。

比較するコストは次の3つです。

- 夜間充電原価: 夜間単価を充放電効率で割り戻した、実際に使える電力の原価
- 昼間買電期待額: PV下振れ時に昼間買電する期待kWhに昼間単価を掛けた値
- 売電機会損失: PV上振れ時に蓄電できず売電したkWhの機会損失

PV予測は1本に決め打ちせず、履歴の `forecast_error_distribution` から平均・分散を取り、
sigma bucket に分けて期待値計算します。履歴が不足する場合は
`PV_FORECAST_ERROR_RATIO_MEAN` と `PV_FORECAST_ERROR_RATIO_STD` を使います。

主な調整値:

- `SOC_COST_DAY_BUY_RATE_YEN_PER_KWH`
- `SOC_COST_NIGHT_RATE_YEN_PER_KWH`
- `SOC_COST_SELL_VALUE_RATIO`
- `SOC_COST_DAY_BUY_PENALTY_FACTOR`
- `PV_FORECAST_ERROR_RATIO_MEAN`
- `PV_FORECAST_ERROR_RATIO_STD`

## 7. 日中グリーンプロファイルの現在ルール

日中グリーンプロファイルは `_build_dynamic_green_profile()` で作ります。

処理順:

1. `day_charge_window.start/end` から充電時間帯を決める
2. `fixed` 条件で0時跨ぎと開始終了同一を補正する
3. `day_discharge_start_by_forecast` で日中放電開始時刻を決める
4. 放電終了時刻は `KP_DAY_DISCHARGE_WINDOW_END` を使う
5. SOC安全/経済/接点入力/充電上限は各候補値の最小コードへ寄せる
6. 運転モードはグリーンモードを使う

現在のデプロイ値では `KP_DAY_DISCHARGE_WINDOW_END=23:00` です。

## 8. 23時・03系・07時ジョブとの関係

`operation_conditions.json` は設定プロファイルの時刻ルールですが、実際の適用タイミングは `cloud_job_runner.py` が制御します。

### 23時ジョブ

- CSV取得
- 夜間計画計算
- 設定反映
- DB/ダッシュボード更新

部分強制充電が必要な場合は、強制充電モードが設定直後から充電を始める特性を避けるため、23時時点ではグリーン待機に寄せます。

### 03系ジョブ

- CSV取得
- 23時に作成した同じ対象日の計画を維持
- 最新SOCから必要充電量を再見積もり
- 07:00から逆算した時刻まで待機
- 必要なら03:10頃に同じ対象日の予報を再取得
- 計画差分がしきい値以上ならDB/ダッシュボードを更新
- 逆算時刻で強制充電モードへ切替
- SOC監視または07:00到達でグリーンへ戻す

100%目標の場合は、目標到達後も07:00まで強制モードを維持し、早朝放電を避けます。

### 07時ジョブ

- 日中グリーンプロファイルを適用します。
- `KP_FORCE_SETTINGS_PROFILE=green`
- `KP_DYNAMIC_FORCED_PROFILE=false`

## 9. 編集時の注意

- 時刻は `HH:MM` 形式で書いてください。
- JSONのコメントは使えません。
- 使わないルールは削除より `enabled: false` を推奨します。
- `description` は説明用で、判定には使われません。
- `priority` は整数として扱われ、大きいほど先に評価されます。
- 現行コードが参照しないIDを追加しても動作は変わりません。

## 10. 反映と確認

1. `config/operation_conditions.json` を編集
2. ローカルで対象シナリオを実行
3. `artifacts/<run_id>/kpnet_summary.json` を確認
4. 次の項目を確認
   - `operation_conditions`
   - `night_charge_end_rule`
   - `day_discharge_start_rule`
   - `fixed_condition_adjustments`
   - `night_charge_plan`
   - `daytime_mode_plan`
5. 問題なければ Cloud Run Jobs を再デプロイ

関連する全体条件木は [CURRENT_DECISION_TREE_JA.md](CURRENT_DECISION_TREE_JA.md) を参照してください。

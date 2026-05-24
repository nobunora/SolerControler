# 現在の判定ルール（条件木）

最終更新: 2026-05-24 (JST)  
対象コード: `cloud_job_runner.py`, `app/kpnet_workflow.py`, `energy_model_main.py`, `config/operation_conditions.json`

この文書は、現在実装されている「設定適用の判定ロジック」を条件木として整理したものです。

## 1. 実行スロット分岐（Cloud Run Job）

`CLOUD_JOB_SLOT` で実行シナリオを分岐します。

```text
ROOT: CLOUD_JOB_SLOT
├─ in {"23", "night", "night23"}
│  ├─ kpnet_main.py (KP_WORKFLOW_MODE=csv)
│  ├─ energy_model_main.py
│  ├─ kpnet_main.py (KP_WORKFLOW_MODE=settings,
│  │                KP_FORCE_SETTINGS_PROFILE=forced,
│  │                KP_DYNAMIC_FORCED_PROFILE=true,
│  │                KP_DYNAMIC_MODE_SWITCH_BY_TIME=false)
│  ├─ db_pipeline_main.py (CLOUD_JOB_SLOT=23)
│  └─ sheets_export_main.py (optional)
├─ in {"3", "03", "adjust", "adjust03"}
│  ├─ kpnet_main.py (KP_WORKFLOW_MODE=csv)
│  ├─ 23時に作成した night_charge_plan.json を維持
│  ├─ 00時台の最新SOCから必要充電kWhを再見積もり
│  ├─ 強制充電が必要(必要SOC差 >= KP_GREEN_MODE_MAX_CHARGE_PERCENT)なら:
│  │  ├─ 07:00 逆算で「強制モード開始時刻」を算出
│  │  ├─ 逆算時刻まで待機
│  │  ├─ 待機中に ADJUST03_REFRESH_HHMM を跨ぐ場合のみ同じ対象日の予報を再取得
│  │  ├─ 内容変化ありなら db_pipeline_main.py (CLOUD_JOB_SLOT=03, DATA_PREFER_NIGHT_PLAN_METRICS=true)
│  │  ├─ 逆算時刻で kpnet_main.py(settings, forced, dynamic=true) を実行
│  │  └─ cutoff(07:00)までSOC監視
│  └─ 100%目標:
│     └─ 目標到達後も07:00まで強制モードを維持し、早朝放電を避ける
└─ in {"7", "07", "day", "day07"}
   └─ kpnet_main.py (KP_WORKFLOW_MODE=settings,
                    KP_FORCE_SETTINGS_PROFILE=green,
                    KP_DYNAMIC_FORCED_PROFILE=false,
                    KP_DYNAMIC_MODE_SWITCH_BY_TIME=false)
```

## 2. 設定フェーズの大分岐（`_run_settings_phase`）

```text
ROOT: settings phase
├─ dynamic_forced_profile == true
│  ├─ forced_profile = _build_dynamic_forced_profile(...)
│  └─ green_profile  = _build_dynamic_green_profile(...)
└─ dynamic_forced_profile == false
   ├─ forced_profile = FORCED_CHARGE_PROFILE（battery_operating_modeのみ候補値に合わせる）
   └─ green_profile  = GREEN_MODE_PROFILE
```

次に、適用するプロファイル列を決定します。

```text
ROOT: profile selection
├─ force_settings_profile == "forced"
│  └─ profiles = [forced_profile]
├─ force_settings_profile == "green"
│  └─ profiles = [green_profile]
├─ dynamic_mode_switch_by_time == true
│  ├─ 現在時刻が夜間窓内 -> profiles = [forced_profile]
│  └─ それ以外           -> profiles = [green_profile]
├─ settings_sequence == "forced-only"
│  └─ profiles = [forced_profile]
└─ otherwise ("forced-then-green")
   └─ profiles = [forced_profile, green_profile]
```

## 3. 夜間プロファイル生成（`_build_dynamic_forced_profile`）

### 3-1. 充電完了時刻（夜間）

`night_charge_plan.json` の予報日照時間を使って、夜間の充電完了時刻を決定します。

```text
ROOT: charge_end_time
├─ base_end = variable rule "night_charge_end_time".value
│           (未設定時デフォルト 06:00)
├─ rule "night_charge_end_by_forecast" が存在しない
│  └─ charge_end = base_end
└─ rule "night_charge_end_by_forecast" が存在
   ├─ threshold = sunny_min_sun_hours (デフォルト 6.0)
   ├─ sunny_end = sunny_end があればその値、なければ base_end
   ├─ cloudy_or_rain_end = cloudy_or_rain_end (デフォルト 07:00)
   ├─ forecast_sun_hours が欠損
   │  └─ charge_end = base_end
   ├─ forecast_sun_hours >= threshold
   │  └─ charge_end = sunny_end
   └─ forecast_sun_hours < threshold
      └─ charge_end = cloudy_or_rain_end
```

### 3-2. 充電開始時刻

```text
ROOT: charge_start_time
├─ required_night_charge_kwh = max(0, plan.required_night_charge_kwh)
├─ estimated_charge_power_kw =
│  ├─ 夜間窓内CSVの「充電電力量[kWh]」正値の中央値 × 2.0
│  └─ データが無ければ KP_DEFAULT_CHARGE_POWER_KW
├─ duration_minutes
│  ├─ power>0 かつ required>0 -> ceil(required/power*60)
│  └─ それ以外 -> 0
├─ duration_minutes > charge_end_minute の場合は clip
└─ charge_start = max(0, charge_end - duration)
```

03側の夜間コントローラでは、23時計画の `required_night_charge_kwh` をそのまま使わず、
00時台に取り込んだ最新SOCと有効容量から必要充電kWhを再計算します。
これにより、23時計画時点のSOC推定が古い場合でも、開始時刻を7時から再逆算します。

### 3-3. 放電開始時刻（日中側境界）

```text
ROOT: day_discharge_start
├─ default = KP_DAY_DISCHARGE_WINDOW_START
├─ rule "day_discharge_start_by_forecast" が存在しない
│  └─ discharge_start = default
└─ rule が存在
   ├─ threshold = sunny_min_sun_hours (デフォルト 6.0)
   ├─ sunny_start  = sunny_start  (デフォルト 06:00)
   ├─ cloudy_start = cloudy_start (デフォルト 07:00)
   ├─ forecast_sun_hours が欠損 -> default
   ├─ forecast_sun_hours >= threshold -> sunny_start
   └─ forecast_sun_hours <  threshold -> cloudy_start
```

### 3-4. SOC系コードの選び方

```text
battery_operating_mode = BatteryOperatingMode から "green" を特定
soc_safety_mode        = SocSafetyMode の最大コード
soc_economy_mode       = SocEconomyMode の最小コード
soc_contact_input      = SocContactInput の最大コード
soc_charge_mode        = SocChargeMode の「target_soc_7_percent 以上の最小コード」
                        （候補が足りない場合は最大コード）
```

## 4. 日中グリーンプロファイル生成（`_build_dynamic_green_profile`）

```text
ROOT: green profile
├─ day_charge_window
│  ├─ start = variable rule "day_charge_window".start (default 00:00)
│  └─ end   = variable rule "day_charge_window".end   (default 06:00)
├─ discharge_start = 3-3 の day_discharge_start ルール
├─ discharge_end   = KP_DAY_DISCHARGE_WINDOW_END
└─ SOCコード
   ├─ soc_safety_mode   = SocSafetyMode 最小
   ├─ soc_economy_mode  = SocEconomyMode 最小
   ├─ soc_contact_input = SocContactInput 最小
   └─ soc_charge_mode   = SocChargeMode 最小
```

## 5. 固定条件（`fixed`）の最終補正

時刻ウィンドウ（充電時間帯）に対して、`fixed` ルールを優先順で適用します。

```text
ROOT: fixed condition adjustments
├─ forbid_cross_midnight
│  ├─ 条件: start > end
│  └─ 補正: start = max(0, end - min_duration_minutes)
├─ forbid_same_start_end
│  ├─ 条件: start == end
│  └─ 補正: 最低 min_duration_minutes を確保
└─ 補正後も
   ├─ start > end なら RuntimeError
   └─ start == end なら RuntimeError
```

## 6. `target_soc_7_percent` の算出側（`energy_model_main.py`）

夜間SOC目標の計算入力として、予備SOCを使います。

```text
reserve_soc_percent = NIGHT_RESERVE_SOC_PERCENT (現在のデフォルト 0)
```

この値は `night_charge_plan.json -> result.target_soc_7_percent` に反映され、
最終的に夜間プロファイルの `soc_charge_mode` 選択に使われます。

## 7. 参照する可変ルールID（現行）

- `night_charge_end_time`
- `night_charge_end_by_forecast`
- `day_charge_window`
- `day_discharge_start_by_forecast`

## 8. 優先順位の原則

1. `fixed`（禁止条件）
2. `variable`（予報連動/時刻設定）
3. 候補値マップへの丸め（SOCコード変換）

上ほど優先され、下位ロジックを上書きまたは制約します。

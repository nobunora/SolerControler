# S04 最終PV/loadから夜間一回・同bundle統合

ID: S04 / 前提: S03 / 状態: LOCAL_IMPLEMENTED / ゲート: G06,G09（係数結線G02前提）。

## 入口・参照範囲

[共通契約](../contracts.md)、[型と時間窓](../data-contracts.md)、[ゲート](../gates.json) を読む。
直接参照: [workflow](../../../../../app/energy_plan/workflow.py)、
[pv_selection](../../../../../app/energy_plan/pv_selection.py)、[runtime tests](../../../../../tests/test_energy_model_runtime.py)。
入力: 既存context/consumption＋S02の固定pack＋取得完了時の固定as_of。
出力: 同runの最終PV24/load24と一回のNightChargePreparation、既存optimizerのdecision。

## 変更対象

| path | qualified symbol / 許可範囲 |
| --- | --- |
| `app/energy_plan/clear_ghi_adapter.py` NEW | `_build_clear_ghi_forecast_bundle`, `_prepare_night_charge_from_hourly` |
| `app/energy_plan/workflow.py` | `PvForecastBundle` 末尾field、`build_energy_plan` 呼出し結線 |
| 同上 | `_forecast_for_date`, `_fetch_open_meteo_previous_day1_forecast` 同取得回翌00時日射tail保存（G09成立後） |
| 同上 NEW | `_radiation_provenance`、`_forecast_from_env_or_api`のraw入力引継ぎ |
| `app/energy_plan/clear_ghi_update.py` NEW | 既存storage境界の非制御更新・60秒期限・固定pack/完了cutoff |
| `tests/test_energy_model_runtime.py` | 新経路順序・呼出しspy・同bundle/24値・時間窓試験 |

PV非依存の既存容量/料金/SOC準備と夜間計算の分離だけ許可。旧helper互換は残す。

## 変更禁止

| 既存qualified symbol | 保護する実コード・変数 |
| --- | --- |
| `workflow._prepare_night_charge` 互換計算 | `expected_overnight_discharge_kwh = 0.0` / `battery_temp_c=config.battery_temp_c if config.battery_temp_c is not None else temp_c` |
| 同上金融準備 | `context.billing_rows if context.billing_rows is not None else context.rows`、月請求入力の意味不変 |
| `workflow._build_soc_constraints` | `capacity = night_charge.result.effective_capacity_kwh` / `apply_caps = not _uses_physical_pv_forecast(pv_forecast.physical_diagnostics)` |
| `workflow._run_legacy_soc_optimization` | `hourly_load_kwh=pv_forecast.hourly_load_kwh, hourly_pv_kwh=pv_forecast.hourly_pv_kwh`、07起点/既存SOC演算 |
| `pv_selection._build_selected_pv_forecast` | `selected_days = {row["dt"].date().isoformat() for row in context.rows}` と7日lock |

容量/料金/制約/headroom式、latest API SOC、context履歴選択、control時刻/leaseを変更しない。
旧PV生成後patch/recompute・`_estimate_midday_surplus_from_pv_forecast` の6/13再適用禁止。

## クラス・変数差分

`workflow.PvForecastBundle.clear_ghi_forecast: ClearGhiForecast | None = None` はNEW末尾field。
説明データ有無だけを表す。旧constructor互換、新adapter成功時は非None、mode flagにしない。
`forecast["pv_radiation_rows"]` は同API responseのraw時刻とW/m²。対象日00〜23と翌00時を一列に保持し、tailを重複保存しない。
`pv_forecast_input_sha256`はcanonical `{target_date,timezone,source_rows}` のhash。
`pv_forecast_retrieved_at`は取得・選択完了のaware UTC、`pv_provider_issued_at`は未知ならnull。
Q23=0ならtail不在を許容し日中へ拡張しない。
NEW signatures:

```python
_build_clear_ghi_forecast_bundle(context: EnergyModelContext, consumption: ConsumptionForecastBundle,
    calibration: ClearGhiCalibration, *, as_of: datetime) -> PvForecastBundle
_prepare_night_charge_from_hourly(context, consumption, pv_forecast: PvForecastBundle) -> NightChargePreparation
```

`hourly_pv_kwh/hourly_load_kwh: dict[int,float]` は最終値0..23、新adapter所有。
`selected_method/source: str = physical_clear_ghi_annual1`、`array_forecast=None`。
`hourly_weather_shape={"enabled":False,"reason":"clear_ghi_direct"}`。
`physical_diagnostics: dict[str,object]` はmodel/basis/source/hash/品質compactのみ、偽scalesなし。
`uncertainty: PvForecastUncertainty` は既存physical neutral mean/std floor、学習21日をsample_countにしない。

## 実装手順・分岐

1. context→consumptionを既存順序で生成。予報/履歴/pack取得完了時にas_ofを一回記録して固定。
   run_started_atやgenerated_atと混同せず、内部計算はnowを再読しない。
2. build_hourly_load_forecast一回→S03 load補正一回→load24/scenarios検証。
3. 同取得回hourly_weather＋tail→S01 forecast_clear_ghi一回→PV24検証、tupleをdictへ変換。
   旧sourceのhour番号からtimestampを捏造しない。HTTP追加0、温度/日付意味不変。
   previous-runsのend_dateを対象翌日に広げ、同じ一応答からnext00を保持する。気温parserは対象日24行のまま。
4. 新bundleへload scenariosだけ渡す。旧pairedを暗黙流用しない。旧PV/reshape/residual呼出し0。
5. 最終hourlyから次を集計し全overrideに有効0も渡す。Noneで旧sun_hoursへ戻さない。

| NightChargeInputs field | 最終hourlyからの値 |
| --- | --- |
| daytime_load_forecast_kwh / morning_load_forecast_kwh | Σload[7..22] / Σload[7..9] |
| predicted_pv_kwh_override / predicted_morning_pv_kwh_override | ΣPV[7..22] / ΣPV[7..9] |
| predicted_midday_surplus_kwh_override | max(0,Σ(PV−load)[10..15]) |

midday PV override fieldは存在しないのでconstructorへ追加しない。容量/温度/金融準備は旧helper同値。
6. compute_night_charge_target一回→既存constraints→legacy→current→S05 result。
   optimizer/legacy/無効/予測専用は同bundleを使用。旧選択/旧night helperは互換用に残し新経路では呼ばない。
7. G06: X1 PV式、X2時間窓、X3 raw daily→補正load、X4 daily fraction→実10..15、
   X5 paired→neutral sigma+load scenarios、X6丸め/cache、X7訂正/並行を分離する。
   X3/X4/X5は一因子後に新旧2³組合せ。中心PV同値から費用同値を推定しない。

## テスト・PASS条件

品質gate後に実施。実行済みの対応fixtureは [実装状況](../implementation-status.md) に記録する。PLANNED名は設計IDで、全条件の実証と同義ではない。
command: `python -m pytest -q tests/test_energy_model_runtime.py tests/test_energy_model.py`。

| ID / test | fixture・経路 | PASS |
| --- | --- | --- |
| PLAN-001 `test_clear_ghi_stage_order_and_single_calls` PLANNED | monkeypatch stage/callback spy、通常/optimizer無効/forecast-only | final PV/load後night1、旧PV/reshape/residual0、同bundle |
| PLAN-002 `test_clear_ghi_decision_windows` PLANNED | 05/06/07/09/10/15/16/22/23境界hour値 | PV24保存、night07..22、朝07..09、余剰10..15、SOC17 |
| PLAN-003 `test_clear_ghi_preserves_finance_and_capacity` PLANNED | 温度0/欠損/実測battery温度、billing_rows、override0 | 容量/温度/料金/SOC入力同値、0をNoneへ変換しない |
| PLAN-004 `test_radiation_tail_uses_one_response` PLANNED | fake現行/previous-runs response、翌00時/欠損tail | HTTP増0、raw温度不変、source/hash一致、Q23>0欠損は品質規則どおり失敗 |
| PLAN-005 `test_failed_bundle_never_reaches_plan_output` PLANNED | 不正load/PV/pack/日中欠損 | result/publication呼出し0、失敗段階理由ログ、旧PVfallback0 |
| PLAN-006 `test_economic_confounders_factorial` PLANNED | 同37日入力/料金/容量/初SOC、X3/4/5単独＋8組 | 買電/売電/損失/23時資産/最大日悪化と入力差を別掲、G06承認基準で判定 |

既存tests `test_build_energy_plan_coordinates_stages_without_persisting`,
`test_prepare_night_charge_preserves_zero_forecast_temperature`,
`test_compute_night_charge_target_accepts_hourly_pv_overrides` を保持。

## 中断条件

G02/G09のbinding未確定またはtail原時刻不能なら該当patchを中断。G06の実装後実optimizer検証は限定ローカル結線・fixture準備を妨げないが、未実証/経済合格基準未確定なら結線の採用PASSを中断し、gate OPENのままS05/S06で証拠を作る。
既存protected変更が必要ならG05。対策として新flag/取得API/制御依存を追加しない。

## 出口・引継ぎ

S05へ一回生成したbundle/decision/容量と同runのas_of/hashを渡す。
G06の未測定値は未測定として残す。公開順序・実adapter競合・本番導入は後続step/G07/G08。

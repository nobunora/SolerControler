# S05 計画原本・full trace一箇所・24時間保存

ID: S05 / 前提: S04 / 状態: LOCAL_IMPLEMENTED。

## 入口・参照範囲

[共通契約](../contracts.md)、[full trace唯一schema](../data-contracts.md)、[ゲート](../gates.json) を読む。
直接参照: [result_builder._build_energy_model_output](../../../../../app/energy_plan/result_builder.py)、
[runtime tests](../../../../../tests/test_energy_model_runtime.py)、[原本回帰](../../../../../tests/test_forecast_history_contract.py)。
入力はS04の同bundle/同decision/同容量。出力は既存PlanDocumentV1と説明trace。
browser/caller→HTTP→service→storageの公開/read全経路は後続stepへ渡す。ここで新endpointを作らない。

## 変更対象

| path | qualified symbol / 許可範囲 |
| --- | --- |
| `app/energy_plan/result_builder.py` | `_build_energy_model_output`、説明traceを一度追加、compact physical診断維持 |
| 同上 NEW | `_clear_ghi_trace`、共通schemaの唯一serializer |
| 同上出力path | 既存 `KP_NIGHT_PLAN_PATH` override、未指定時の既存artifact path保持 |
| `tests/test_energy_model_runtime.py` | trace一箇所/同原本/保存丸め/旧constructor回帰追加 |

PlanDocumentV1必須field/schema、旧計画reader、容量/料金/SOCfieldは既存契約を維持。

## 変更禁止

| 既存qualified symbol / source | 保護する実コード・変数 |
| --- | --- |
| `result_builder._build_energy_model_output` | `coefficients: dict[str, Any] = to_dict(context.coefficients)`、PV thetaで置換しない |
| 同上結果 | `result_payload["final_predicted_pv_kwh"] = final_pv_totals["total_kwh"]`、保存totalは24時間 |
| 同上constructor | `inputs=to_dict(night_charge.inputs)` / `daytime_soc_optimization=optimization_payload`、同採用SOC/容量 |
| 同上summary | `"legacy_result_predicted_pv_kwh": result_payload.get("predicted_pv_kwh")`、互換field名維持 |
| 同上physical係数 | `physical_scales = pv_forecast.physical_diagnostics.get("scales")`、新経路へ偽scales/array factorを作らない |

fulltraceをoptimization/physical診断/forecast_hourly24行へ複製しない。
dashboard再計算・GET学習/履歴scan・SOC fallback変更・frontend/asset cache変更は禁止。
完全日チェック/原本hash/復元条件を緩めない。backup新形式/collectionをtrace目的だけで増やさない。

## クラス・変数差分

新classなし。既存documentのoptional説明objectへNEW `pv_clear_ghi` を一箇所追加。
NEW signatureを固定する。

```python
_clear_ghi_trace(forecast: ClearGhiForecast, *, timezone: str,
    hourly_load_kwh: dict[int,float]) -> dict[str,object]
```

`forecast` はS04のimmutable説明値。timezoneはcontext値、loadは同bundle最終値。
返却objectはserializer所有、schema全keys/type/固定文字列は [data-contracts](../data-contracts.md) に集約。
`coefficients` は既存容量/学習係数、`final_pv_totals` は同hourly最終PV集計。
`legacy_result_predicted_pv_kwh` は新経路ではnight判断07..22由来、field名を変えずtraceで説明。

## 実装手順・分岐

1. S04時点でPV/load0..23の完全性/有限非負を検証済みとする。
   generatorで不完全ならhandoffへ渡さない。PublishedPlanのNULL受理へ頼らない。
2. 既存result totalsを同PVから生成。保存窓はtotal0..23、morning7..9、midday10..15、evening16..22。
   05/06/23はtotalだけ、total≠三帯合計を修正しない。
3. `_clear_ghi_trace` 一回でcalibration.raw_bytes→JSON pack、canonical再生成hashを照合。
   datetimeをtimezone付きISO、tupleをhour順24行へ明示変換、夜間未知F/sourceはnull。
   `asdict`でbytes/datetimeを汎用JSONへ流さない。
4. 修復hours、F/Q/C/最終PV、forecast/pack hash、as_of/retrieved/issued、窓集計を共通schemaどおり保存。
   source W/m²と整列F kWh/m²の意味を固定semanticsで説明。中間PV/温度・雲雨倍率は作らない。
5. 同traceを `document.decision_rationale["pv_clear_ghi"]` だけへ設定。
   existing `final_pv_forecast` summaryとcompact physical診断は維持。初期bootstrapへfulltraceを追加しない。
6. 旧互換bundleのclear_ghi_forecast=Noneは旧document経路を維持。
   新adapter成功でNoneなら欠陥として失敗、Noneを新旧mode切替flagに使わない。
7. 保存hourly4小数とfulltrace/full precisionが丸めだけの差を確認。
   同target/capacity時の既存persistence SOC再計算差を共通保守上限で検証、24点SOCを作らない。
8. 同run retryでは固定bytes/generated_atを再利用する後続保存境界へ渡す。
   immutable archive/control採用/projection順は後続公開step、ここでは書込を行わない。

## テスト・PASS条件

品質gate後に実施。実行済み対応fixtureと未実証境界は [実装状況](../implementation-status.md) に記録する。PLANNED名は設計ID。
command: `python -m pytest -q tests/test_energy_model_runtime.py tests/test_forecast_history_contract.py`。

| ID / test | fixture・経路 | PASS |
| --- | --- | --- |
| OUTPUT-001 `test_clear_ghi_trace_schema_and_unique_location` PLANNED | 正常bundle、修復/night None、raw forecast | 共通keys/type、fulltrace一箇所、compact診断だけ、hash一致 |
| OUTPUT-002 `test_clear_ghi_output_uses_final_bundle` PLANNED | 窓境界05/06/07/09/10/15/16/22/23 | total24/判断16窓、同PV/load/SOC17/容量、legacy field互換 |
| OUTPUT-003 `test_clear_ghi_rounding_preserves_soc_contract` PLANNED | full precision→4dp、同target/capacity | clip前差が共通上限内、target/capacity差を丸め扱いしない |
| OUTPUT-004 `test_legacy_bundle_keeps_document_contract` PLANNED | 旧bundle None/旧plan reader fixture | 旧必須field/coefficients/schemaを保持、trace強制なし |
| OUTPUT-005 `test_invalid_trace_never_serializes_success` PLANNED | raw pack改竄/hash不一致/不正時刻/新bundle None | 出力失敗、公開/persistence0、原因ログ、secret/raw JSON漏出なし |
| OUTPUT-006 `test_trace_is_not_repeated_in_hourly_or_bootstrap` PLANNED | plan→既存読取projection/backup fixture | fulltrace複製0、新DB取得0、埋込raw復元hash一致（実adapterは後続G07） |

既存 `test_forecast_persists_capacity_from_the_same_plan_without_control_writes`,
`test_mutable_pv_and_load_use_one_vintage_even_with_stale_daily_pv`,
`test_missing_value_with_full_weather_cannot_replace_valid_forecast` を維持。
新trace詳細HTTP endpointを作らない。request/DB reads/bytes/cache前後測定は後続公開/read stepで実施。

## 中断条件

fulltrace複製/同世代hash不一致/不完全24値/容量またはSOC契約差があれば停止。
旧reader/schema/lock変更が必要ならG05。未測定本番転送量・backup/current参照復旧をPASSにしない。

## 出口・引継ぎ

後続公開stepへ同plan原本/固定hash・24値・trace一箇所・rounding証拠を渡す。
pack入りplanの復元とcurrent係数配送は別。G02/G04/G07/G08の未解決を保存成功で閉じない。
計算パケットの完了は仕様完了であり、実装/公開/本番導入完了ではない。

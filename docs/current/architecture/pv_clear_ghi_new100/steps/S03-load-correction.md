# S03 消費補正の独立境界・旧API等価性

ID: S03 / 前提: S02 / 状態: LOCAL_VERIFIED。

## 入口・参照範囲

[共通契約](../contracts.md) → [run所有の消費型](../data-contracts.md) → [ゲート](../gates.json)。
入力は既存rows/予報履歴/weather/raw load/policy。出力は最終load24/scenarios/診断。
直接参照は [correction_model.calculate_forecast_correction](../../../../../app/forecasting/correction_model.py)、
[correction.build_forecast_correction](../../../../../app/forecasting/correction.py) と
[既存消費回帰](../../../../../tests/test_energy_model.py)。PVモデル変更をここへ混ぜない。

## 変更対象

| path | qualified symbol / 許可範囲 |
| --- | --- |
| `app/forecasting/correction_model.py` | 既存 `calculate_forecast_correction` の消費演算だけ抽出 |
| 同上 NEW | `LoadCorrectionPreparation`, `_prepare_load_correction`, `_finalize_load_correction`, `calculate_load_forecast_correction` |
| `app/forecasting/correction.py` NEW | `build_load_forecast_correction`、既存ports/policyを渡す入口 |
| `tests/test_load_correction_equivalence.py` NEW | 旧API等価性・新load専用callback/取得回数試験 |

## 変更禁止

| 既存qualified symbol | 保護する実コード・変数 |
| --- | --- |
| `correction_model._physical_vector_residual_correction` | `actual_pv = to_float(actual.get("pv"))` / `if actual_pv is None or actual_pv < 0:` とlock |
| `correction_model._correct_hourly_pv` | `if skip_pv_correction:` 以下 `physical_corrector(...)`、既存skipを停止flagへ改変しない |
| `correction_model.calculate_forecast_correction` 消費順序 | `load_summary = ewma_ratio_from_daily_pairs(...)` は一回、load_ratio演算/clip維持 |
| 同上comfort置換 | `if comfort_model.get("applied") is True and len(comfort_hourly) == 24:` / `corrected_load = comfort_hourly` / `residual_multipliers = comfort_model.pop("_residual_multipliers", [])` |
| `correction.ForecastCorrectionPolicy.from_env` | `enabled=env_bool("FORECAST_CORRECTION_ENABLED", default=True)` と全既存env/default |

`ForecastCorrectionInput` 既存field・旧公開API引数/戻りfield、熱履歴窓、PV実績集計、comfort演算を変えない。
新load入口へダミーPVゼロを入れて旧統合APIを呼ばない。新skip/fallback/pluginを作らない。

## クラス・変数差分

NEW private frozen `LoadCorrectionPreparation` のfield/type/所有/defaultは [data-contracts](../data-contracts.md)。
mutable load診断はrun専用コピー。`pop`対象のcomfort dictを旧/新run間で共有しない。
NEW `_prepare_load_correction` はload EWMA〜温度/scenario準備、`_finalize_load_correction` は
時間配分→load倍率→comfort置換→load診断を所有する。旧wrapperが同じload EWMAを再計算しない。
NEW `calculate_load_forecast_correction` は既存calculateのload入力/port/設定だけを受け、PV引数を要求しない。
NEW公開入口signature:

```python
build_load_forecast_correction(*, rows, hourly_load_forecast, target_date,
    latitude, longitude, timezone, forecast, policy) -> dict[str,object]
```

戻りkeys/typeは共通正本。`policy.enabled=False` はraw load・既存disabled診断・空scenariosを返す。
空scenariosは既存consumerの未提供fallback用であり、旧公開APIの戻りshapeは変更しない。
既存PV比率/skip fieldは新入口で読まない。内部defaultを新設定値へ置換しない。

## 実装手順・分岐

1. 既存回帰fixtureでraw24/履歴/weather/comfort等の入力を固定し旧結果を保全。
2. history_loaderとactual_hourly_totals_by_dayをrun一回だけ取得。
   history_dates交差・ADAPTIVE_LOOKBACK_HOURS・weather取得・thermal stateの順を維持。
3. load EWMA/clip→温度features/correction→scenario準備をprepareへそのまま抽出。
   `to_float(...raw_ratio) or 1.0`、temperature multiplierの既存fallbackを維持する。
4. hourly温度配分→load倍率→comfortをfinalizeへ抽出。comfort適用時は
   corrected_load24とload_scenariosの両方を既存結果で置換、追加倍率で掛け直さない。
5. 旧wrapperの順序を次に固定して既存assembler/paired結果を維持する。

```text
history/actual一回 -> 旧PV EWMA -> load prepare（一回EWMA）
-> 旧PV correct -> load finalize -> 旧paired -> 旧assembler
new load only: history/actual一回 -> load prepare -> load finalize
disabled: raw-load + 既存disabled診断（PV/学習callbackへ入らない）
```

6. 新load-only経路のPV EWMA/physical_corrector/paired呼出しは0。
   PV異常で独立load計算を旧PV補正失敗へ巻き込まない。
7. 全24load/scenarios/load関連診断の等価性と同じI/O回数を確認してS04へ渡す。
   比較のために旧期待値/丸め/comfort診断を変更しない。

## テスト・PASS条件

品質gate後に実施。load専用入口とNEW試験ファイルは実装済み。
対象command: `python -m pytest -q tests/test_load_correction_equivalence.py`。
以下のPLANNEDは試験仕様ID。実行結果は検証後に別記し、fixture定義を実行PASSと扱わない。

静的確認: 対象2sourceとNEW testのRuff check・py_compile PASS、対象2sourceのmypy PASS。

2026-10-12実行結果: 親担当のcanonical `scripts/pre_release_local.ps1 -SkipInstall` の必須品質check通過後、
`python -m pytest -q tests/test_load_correction_equivalence.py tests/test_clear_ghi_store.py` は48 PASS
（load16件、store32件）。load試験の5条件×skip有無10件では、基準commit
`d401b15de199ff8f1215935d01080a67a4c95266` の旧関数を同じoffline入力/portで実行した全resultと完全一致。
そのcanonical JSON SHA256を各条件の永続goldenとして保持し、PV/load/scenario/全診断の回帰を検出する。
`_physical_vector_residual_correction` と `_correct_hourly_pv` の本文は基準commitと完全同一。
履歴/actual集計/load EWMAは各1回、load専用のPV/physical/paired/旧assembler呼出しは0。
これは消費補正のローカル等価性であり、新PVの経済採用/G06や実クラウド/G07の合格ではない。

| ID / NEW test | fixture・経路 | PASS |
| --- | --- | --- |
| LOAD-001 `test_old_and_load_only_results_match` PLANNED | 正常/履歴不足/高温/熱履歴/月跨ぎ/欠損 | load24/scenarios/load診断が旧結果と一致、丸め/順序不変 |
| LOAD-002 `test_comfort_replaces_values_and_scenarios` PLANNED | comfort適用/不適用、24未満、不正scenario | 適用時両方置換、非適用fallback同値、pop診断を再利用しない |
| LOAD-003 `test_disabled_preserves_raw_load` PLANNED | policy.enabled=False、温度0、空履歴 | raw24/disabled診断維持、補正callback0 |
| LOAD-004 `test_legacy_callback_order_and_single_ewma` PLANNED | callback spy、旧統合API | 上記順序、load EWMA1、旧PV/paired互換 |
| LOAD-005 `test_load_only_has_no_pv_callbacks_or_extra_io` PLANNED | PV callbackをraiseに置換、weather/history spy | PV EWMA/physical/paired0、history/actual1、weather回数増加0 |

既存command: `python -m pytest -q tests/test_energy_model.py -k 'forecast_correction or temperature or thermal'`。
既存tests `test_typed_forecast_correction_boundary_preserves_disabled_result`,
`test_build_forecast_correction_uses_comfort_model_when_applied`,
`test_thermal_and_humidity_state_carries_into_next_day`,
`test_build_forecast_correction_can_skip_pv_ratio_for_physical_model` を維持。

## 中断条件

旧load/scenario/診断不一致、EWMA二回、callback順序差、I/O増、protected変更が必要なら停止。
等価抽出で直せないモデル仕様の変更はこのstepでは行わない。G05対象は承認と回帰が必要。

## 出口・引継ぎ

S04へload専用入口、等価性fixture/証拠、取得回数を渡す。
旧APIは従来PV/pairedを保持、新入口はPVを一度も補正しないことを出口条件にする。
次stepのraw daily→最終hourly接続によるSOC差はG06で別評価する。

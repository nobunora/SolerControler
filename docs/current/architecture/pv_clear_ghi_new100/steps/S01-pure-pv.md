# S01 純PV・認定済原本からの係数学習

ID: S01 / 前提: S00 / 状態: LOCAL_IMPLEMENTED_TEST_PENDING / ゲート: G01,G03。

## 入口・参照範囲

読む順: [共通契約](../contracts.md) → [型とschema](../data-contracts.md) → [ゲート](../gates.json)。
入力は対象日・timezone/site・固定as_of・同取得回の日射・完成係数、または認定済clear_days。
出口は `ClearGhiForecast` / `ClearGhiCalibration`。ここでstorage/公開を扱わない。
研究移植元は `artifacts/analysis/pv_geometry_20261010.py::solar_position/radiation/hourly`。
研究ファイルはignored、site_configはprivate planを読むためimport禁止。

## 変更対象

| path | 許可するqualified symbol |
| --- | --- |
| `app/forecasting/pv_clear_ghi.py` NEW | `ClearGhiCalibration`, `ClearGhiForecast`, `clear_ghi_hour`, `normalize_radiation_intervals`, `forecast_clear_ghi`, `fit_clear_ghi_calibration` |
| `tests/test_pv_clear_ghi.py` NEW | 下記PV試験と秘密を除いた追跡geometry fixture |

研究純関数を明示移植する。既存旧物理関数への委譲や置換は許可しない。

## 変更禁止

| 既存qualified symbol / source | 保護する実コード・変数 |
| --- | --- |
| [pv_physical](../../../../../app/forecasting/pv_physical.py) module constants | `HOURS = range(7, 23)` / `OUTPUT_HOURS = range(5, 23)` と直前 `HISTORICAL_FAILURE_LOCK` |
| [correction_model._physical_vector_residual_correction](../../../../../app/forecasting/correction_model.py) | `actual_pv = to_float(actual.get("pv"))` / `if actual_pv is None or actual_pv < 0:`、欠損を0へ戻さない |
| `pv_clear_ghi.forecast_clear_ghi` NEW | I/O・env・旧PV/残差/温度倍率・雲雨再減衰・nowcast・新mode/fallback/clip追加禁止 |

## クラス・変数差分

二つのNEW frozen dataclassの全field/type/default/所有は [data-contracts](../data-contracts.md) が正本。
`q_kwh_m2`, `f_kwh_m2`, `c_raw`, `pv_kwh`: run所有tuple24、暗黙defaultなし。
`repaired_hours`: sorted unique tuple。`theta`: a,b,c,eのtuple4。mutable dictをfieldに入れない。
計算局所値 `phi,x,q,f,c,pv: float`、`hour: int`。設備値はcontextから引数で渡す。
次のsignatureをNEWとして固定する。

```python
clear_ghi_hour(day: date, hour: int, *, latitude: float, longitude: float, timezone: str) -> float
normalize_radiation_intervals(source_rows: tuple[Mapping[str, object], ...], *, target_date: date, timezone: str) -> tuple[tuple[float | None, ...], tuple[datetime | None, ...]]
forecast_clear_ghi(*, target_date: date, as_of: datetime, latitude: float, longitude: float, timezone: str, calibration: ClearGhiCalibration, source_rows: tuple[Mapping[str, object], ...], forecast_hash: str, retrieved_at: datetime, provider_issued_at: datetime | None) -> ClearGhiForecast
fit_clear_ghi_calibration(clear_days: tuple[Mapping[str, object], ...], *, target_date: date, as_of: datetime, site: Mapping[str, object], basis_version: str) -> ClearGhiCalibration
```

## 実装手順・分岐

1. 型/有限性/bool拒否/非負/日付/区間/重複/aware時刻・hash/site/basisを検証。
2. DOYは1起点、Yは365/366、`phi=2*pi*(DOY-1)/Y`, `x=(h+0.5-12)/6`。
3. NOAA研究係数、γ=`2*pi*(DOY-1+(t-12)/24)/Y`、当日正午offset、幾何学高度・屈折なし。
   `t=h+(i+0.5)/60, i=0..59`。高度>0で `1098*sin(alpha)*exp(-0.059/sin(alpha))`、それ以外0。
   Qは60点平均÷1000。旧365固定/中央時刻/別ライブラリへ変更しない。
4. raw timestampの提供者h+1行を計算hへ一度だけ対応、F=W/m²÷1000。翌00時→前日23時。
   温度等の時点値はshiftしない。変換済入力の再shiftを拒否、元timestampを保持。
5. Q=0ならF不在でもPV=0。F=0は保持。日中欠損はrun合計≤2、各連続gap≤2、
   同取得回で両端F有効かつQ>0の場合だけ `K=min(F/Q,1)` の時間線形補間。
   端点/3時間以上/全日/多数gap/別取得回補充は失敗。修復hours/原本hashを診断へ。両端はdata-contractsの導出規則を使い新keyを加えない。
6. `C=a+b*x+c*cos(phi)+e*sin(phi)`、`PV=max(0,C)*min(F,Q)`。通常計算でQを除算しない。
   Q/C/積のoverflow・非有限も失敗。24値の検証完了後だけ成功値を返す。
7. fitは05..17の13区間すべての00/30分実績ペアが完全な認定済過去日を選ぶ。
   日認定の主要帯（高度≥10度、空集合不可）だけ日照≥55分・衛星GHI/中央Haurwitz≥0.8を検証。
   学習は全13区間を保持（低高度/Q=0も研究と同じ）。実測0と欠損を区別し、認定filterを学習行へ流用しない。
   `X=Q*[1,x,cos(phi),sin(phi)]`, y=実績kWh、既存NumPy `linalg.lstsq(...,rcond=None)`。
   同日訂正は置換、training_date<target_date、available_at≤as_of。rank4・有限条件数/係数必須。
   `y/Q`無加重回帰・逆行列・未来実績は不採用。認定供給/as_ofはG01。
8. G03局所規則は `CONDITION_NUMBER_LIMIT: float = 30.0`、rank4/有限かつcondition≤30のみ採用。
   保存21日prefixの±1%最小特異方向摂動で候補10/30/100/1e4を比較。30は8/21採用、
   全13区間で再評価し翌日最大PV差0.063071kWh/h、年間geometry外挿差0.402335。10の翌日差0.060949に近く採用数は倍。
   100は同じ翌日差でも年間感度0.542642へ増加。年間精度・historical availabilityの保証にはしない。
   証拠: `artifacts/analysis/pv-new100-implementation-20261011/g03-conditioning.safe.json`。

## テスト・PASS条件

実行前の品質gateは [共通契約](../contracts.md)。2026-10-12に親のmandatory gate PASS通知後、
`python -m pytest -q tests/test_pv_clear_ghi.py tests/test_clear_ghi_calibration_io.py` は42 PASS。
下表の広域fixture/継続供給/年間性能まで合格した意味ではない。局所source Ruff/mypyもPASS。

| ID / NEW test | fixture・経路 | PASS |
| --- | --- | --- |
| PV-001 `test_geometry_matches_research_midpoints` PLANNED | 追跡少数fixture＋local57日1368時間、研究原本hash/版 | 移植結果一致、秘密/site_config importなし、I/O0 |
| PV-002 `test_annual_basis_and_leap_year` PLANNED | 365/366年、12/31→1/1、位相0/2π | 周期連続、別日の値を同値へ固定しない |
| PV-003 `test_radiation_interval_tail_and_single_shift` PLANNED | 09時source→08時、翌00時→23時、温度、二重shift | 24区間・元timestamp一致、温度不変、二重shift拒否 |
| PV-004 `test_invalid_values_fail_before_success` PLANNED | None/bool/NaN/負/重複/overflow、Q=0、F=0 | 不正は失敗、夜間None/有効0は正当0、24値の偽装なし |
| PV-005 `test_fixed_short_gap_policy` PLANNED | 1/2内部gap・単独2gap、総3/端点/全日/別run | 許可分だけK補間と修復記録、禁止分は失敗 |
| PV-006 `test_fit_uses_only_available_certified_originals` PLANNED | rolling481時間、認定/半時間欠損・訂正・未来・rank不足 | cutoff/置換守る、rank不足/未認定失敗、G03確定閾値のみ使用 |
| PV-007 `test_nonconstant_provider_pulse_is_shifted_once_into_interval_eight` EXISTING LOCAL PASS | 原本08時100/09時200W、08区間 | F08=.2、source09時、PV08は単一式で算出。定数fixtureで隠れる無shiftを検出 |
| PV-008 `test_certification_core_does_not_filter_low_altitude_or_zero_q_training_rows` EXISTING LOCAL PASS | synthetic低高度実績変化、sun0/衛星None、Q0行 | 認定coreのみ規則検証、全13行独立lstsqとtheta一致・sample数13×日数 |

既存保護回帰: `python -m pytest -q tests/test_physical_pv_residual_quality.py`、
`test_missing_or_invalid_actual_is_not_a_zero_residual_sample` / `test_measured_zero_and_valid_negative_residual_are_preserved`。

## 中断条件

G01実供給未証明なら本番fit/採用を中断。G03規則は局所証拠から定義済み、全履歴採用率はS06で確認。
geometry原本/fixture取得不能はINCONCLUSIVE。
研究との差、input漏れ/非有限成功、旧lock変更が必要なら停止して最小差分を報告する。
誤差対策として無承認epsilon/倍率上限/dependencyを追加しない。

## 出口・引継ぎ

S02へ純型・canonical候補・テストID/実行証拠を渡す。S04へPV24/元時刻/修復記録を渡す。
pack配置と自動認定の結線はここで完了したと判断しない。full trace schemaは共通正本のみ。
認定adapterは公開 `solar_altitude` を使用する。完成bytesの共有decoderは `calibration_from_bytes`、
run検証は `validate_calibration_for_run`、canonical生成は `canonical_pack_bytes`。純核は時計を読まない。

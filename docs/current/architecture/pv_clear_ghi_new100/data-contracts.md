# 計算・原本・説明データ契約

状態: IMPLEMENTATION_PENDING。ソース基準 `d401b15de199ff8f1215935d01080a67a4c95266`。
共通禁止事項は [contracts.md](contracts.md)、未決定事項は [gates.json](gates.json) を参照する。
ここは型とfull traceの唯一の定義。各stepはこれを参照し、別schemaを作らない。

## 純計算の内部入力schema

以下はNEW内部契約であり、providerの外部schemaを変更する指示ではない。adapterで一度だけ変換する。
`source_rows: tuple[Mapping[str, object], ...]` の各行は必須 `time: str`（provider原本ISO時刻）、
`shortwave_radiation_w_m2: float | None`（有限・非負、bool拒否）。原本の時刻と同取得回hashを保持する。
naive ISOは指定timezoneで解釈し、aware ISOは同timezoneへ変換する。行番号・時刻だけから時刻を補造しない。
翌00時行も同取得回から渡す。previous-runs suffixの正規化はadapterで一度だけ行い、温度行をshiftしない。

`site: Mapping[str, object]` は必須 `latitude: float`（-90..90）、`longitude: float`（-180..180）、
`timezone: str`（有効IANA名）、`site_fingerprint: str`（呼出元で検証済み設備binding）。bool/非有限拒否。
既存contextから渡し、純関数はprivate plan/envを読み直さない。座標・設備識別子をログへ出さない。

`clear_days: tuple[Mapping[str, object], ...]` の各日を次で固定する。G01では供給元からこの内部形への
変換根拠を証明する。供給元項目名を推測して実装せず、証明不能なら本番学習を停止する。

| 必須key | 型・制約 |
| --- | --- |
| date | `date`; 対象日前の実績日 |
| actual_available_at / certification_available_at | aware `datetime`; 当時取得可能となった時刻。両方≤as_of |
| actual_sha256 / certification_sha256 | lowercase SHA256 hex、各日の原本bytesのhash |
| certification_rule_version | `str`; packと一致する認定規則版 |
| certified_clear | `bool`; 認定規則版に従って終日晴天と認定済み。自己判断でtrueにしない |
| site_fingerprint / basis_version | `str`; 今回のsite/basisと一致 |
| hours | `tuple[Mapping[str, object], ...]`; 05..17の13区間、hour重複なし |

hours行の必須keysは `hour: int`、`actual_halfhour_kwh: tuple[float | None,float | None]`、
`solar_altitude_deg: float`、`sunshine_minutes: float`（0..60）、`satellite_ghi_w_m2: float | None`、
`central_haurwitz_w_m2: float`。全数値bool/非有限拒否、実績/GHIは非負、有効0を欠損にしない。
半時間値はhourの00/30分開始、合計が時間kWh。全13区間の実績ペアが完全な認定済み日を選び、
日認定は高度≥10度の主要帯（空集合不可）で日照≥55分、分母>0かつ衛星比≥0.8を満たすことを検証する。
学習には認定済み日の全13区間を使う。低高度・Q=0の行も研究と同じ重み付き設計行として保持し、認定filterを学習filterへ流用しない。
夜明け前の低高度を晴天不成立と扱わない。不完全な日・非認定日は除外し理由を残す。衛星値を予報値で代用しない。
同日訂正は両原本のavailable_at最大が後の版を選ぶ。同時刻に異hashは競合として失敗する。
当時の供給経路・認定版・available_atが未証明ならG01 OPEN。以上の内部schemaでfixture実装は可能だが、
実データの継続学習PASSは宣言しない。新runtimeクラスを増やさず既存NumPy以外の依存も追加しない。

補間両端は `repaired_hours` の連続gap直前・直後の未修復hourとして、full traceのhourly/source_timeから
一意に導出する。原本hashと共に説明する。別の両端key/schemaを追加しない。

## 純計算のNEW型

`app.forecasting.pv_clear_ghi.ClearGhiCalibration` と `ClearGhiForecast` はNEWの `@dataclass(frozen=True)`。
全必須fieldに暗黙defaultなし。tuple/bytesを所有し、内部でnow・env・storageを読まない。
datetimeはtimezone付き。比較はUTCの同じ瞬間へ正規化し、ISO文字列の辞書順を使わない。

| ClearGhiCalibration field | 型・制約 |
| --- | --- |
| model_id | `str`; `physical_clear_ghi_annual1` 固定 |
| basis_version | `str`; `noaa_geometric_1min_midpoint_annual1_v1` 固定 |
| site_fingerprint | `str`; 呼出元のsiteと一致 |
| theta | `tuple[float,float,float,float]`; 順序a,b,c,e、全有限、bool拒否 |
| training_dates | `tuple[date,...]`; sorted unique、全日 `< target_date` |
| training_samples | `int`; 全13区間×training_dates数と厳密一致、bool拒否。旧filter生成packを再利用しない |
| rank | `int`; 成功には4、bool拒否 |
| condition_number | `float`; 有限。閾値はG03で確定、100をdefaultにしない |
| training_as_of / usable_from | `datetime`; 原本由来、使用runのas_of以下 |
| raw_bytes | `bytes`; 完成packのcanonical JSON、run内固定 |
| sha256 | `str`; raw_bytesのhash、pack外で保持 |

| ClearGhiForecast field | 型・制約 |
| --- | --- |
| target_date / as_of | `date` / `datetime`; 固定入力の対象日・cutoff |
| calibration | `ClearGhiCalibration`; 同runで選んだ一原本 |
| forecast_hash | `str`; 同取得回の予報原本hash |
| retrieved_at | `datetime`; 実際の取得時刻、as_of以下 |
| provider_issued_at | `datetime \| None`; provider発行時刻、未知ならNone |
| source_times | `tuple[datetime \| None,...]`; 原timestamp、長さ24 |
| q_kwh_m2 | `tuple[float,...]`; Q、有限非負、長さ24 |
| f_kwh_m2 | `tuple[float \| None,...]`; F、有限非負、長さ24 |
| c_raw | `tuple[float,...]`; C、有限、長さ24、負のraw値も説明用保持 |
| pv_kwh | `tuple[float,...]`; 最終PV、有限非負、長さ24 |
| repaired_hours | `tuple[int,...]`; sorted unique、修復したhourだけ |

Q=0の夜間のみF/source不在をNoneで保持しPV=0を成功とする。有効F=0は欠損ではない。
内部gap修復後のFを保存する場合でもsource_timeを捏造しない。修復hourの元timestamp不在はNoneを維持する。
`workflow.PvForecastBundle.clear_ghi_forecast: ClearGhiForecast | None = None` はNEW末尾field。
旧constructor互換のdefaultでありmodeではない。新adapter成功時は必ず非None。

## 消費専用のNEW型と戻り値

`correction_model.LoadCorrectionPreparation` はprivate frozen dataclass。
mutable診断はrun内コピーを所有する。別runやcomfortのpop処理へ同じdictを再利用しない。

| field | 型・default/所有 |
| --- | --- |
| forecast_history / actual_history | `dict[str,dict[int,dict[str,float]]]`; 取得済みrun内値、defaultなし |
| history_source | `str`; 既存loader値、defaultなし |
| load_summary | `dict[str,object]`; 既存load EWMA結果、defaultなし |
| load_ratio_raw / load_ratio | `float`; 既存演算・clip後値、fallbackは既存1.0 |
| weather_by_day / target_weather | `dict[str,dict[int,dict[str,float]]]` / `dict[int,dict[str,float]]`; run内コピー |
| target_temperatures | `dict[int,float]`; 既存temp_c欠落default24.0を保持 |
| temperature_correction | `dict[str,object]`; run内mutable診断 |
| temperature_multiplier | `float`; 既存multiplierまたは1+delta、delta欠落0.0 |
| load_scenarios | `list[dict[str,float \| str]]`; 既存adaptive戻り型、未提供時は既存adaptive初期値 |

`build_load_forecast_correction(...) -> dict[str,object]` のkeysは
`enabled: bool`, `hourly_load_kwh: dict[int,float]`, `load_scenarios: list[dict[str,float | str]]`, `rationale: dict[str,object]`。
NEW入口はdisabledでも4 keysを返す: `enabled=False`, `hourly_load_kwh=hourly_load_forecast`,
`load_scenarios=[]`, `rationale={"enabled":False,"reason":"disabled"}`。
空scenarioは未提供を表し、既存consumerのfallbackを使用する。旧公開APIの戻りkeysは変更しない。
history_sourceの実loaderは `tuple[dict[str,dict[int,dict[str,float]]],str]` を返し、
source値は `sqlite_forecast_hourly` / `firestore_forecast_hourly` / `unavailable`。このstrをそのまま保持する。
成功した最終PV/loadはhour keysが厳密に0..23、有限非負。SOCは07..22開始＋23終了の17点。

## 完成calibration pack

以下はNEW内部schemaの確定key。raw packには自己参照hashを入れない。
canonical形式はUTF-8、`sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False`。

| JSON key | 型・制約 |
| --- | --- |
| schema_version | `int`; 1、bool拒否 |
| model_id / basis_version / site_fingerprint | `str`; 上記classと同識別値 |
| terms | `array[str]`; `["1","x","cos_phi","sin_phi"]` 固定 |
| theta4 | `array[float]`; a,b,c,eの4値、class.thetaに対応 |
| training_dates | `array[str]`; sorted unique ISO date、全日target_dateより前 |
| training_samples / rank | `int`; 区間数 / 4、bool拒否 |
| condition_number | `float`; 有限、G03で固定した合格条件 |
| training_as_of / created_at / usable_from | `str`; timezone付きISO、created_atは生成証拠で訓練順比較に使わない |
| actual_source_sha256 | `str`; 使用した実績原本集合のcanonical集約hash |
| certification_source_sha256 | `str`; 使用した認定原本集合のcanonical集約hash |
| certification_rule_version | `str`; 入力認定原本の規則版、実供給版はG01で確認 |

両source hashの算法を固定する。実績/認定それぞれについて、使用したtraining_datesの昇順に
`[{"date":"YYYY-MM-DD","sha256":"当日の使用原本のSHA256"}, ...]` を作る。
このarrayを上記canonical JSON条件でUTF-8 bytes化し、SHA256の小文字hexを保存する。
文字列連結をhashしない。同日訂正は当日のsha256を置換し旧版を追加しない。
参照arrayへ原本の個人情報/秘密値を入れない。原本版hashからのみ再現可能にする。
生成identityはpack外の `pack_sha256`（class.sha256）。storage generationは有効参照/CASが所有しpack内へ入れない。
read-backとcanonical hash一致が必要。配送/current参照/updater結線/cold startはG02の未決ゲートであり、
JSON key名を実装者が変更する理由にはならない。
候補theta `[2.8025929925329236,0.0859706896750686,0.6948006392718235,0.1387449958270166]` は
2026-10-10までの学習が許され入手可能となった将来run専用。過去cutoffへ流用しない。

## full trace schema1（唯一の保存位置）

所有者NEW `result_builder._clear_ghi_trace(forecast, *, timezone, hourly_load_kwh)`。
保存先は `document.decision_rationale["pv_clear_ghi"]` の一箇所。再取得・再計算なし。

| JSON key | 型・値 |
| --- | --- |
| schema_version | `int`; 1 |
| model_id / basis_version / site_fingerprint | `str`; calibrationの識別値 |
| target_date / timezone | `str`; ISO date / context timezone |
| as_of / retrieved_at | `str`; timezone付きISO日時 |
| provider_issued_at | `str \| null`; timezone付きISO日時、未知はnull |
| forecast_sha256 / calibration_sha256 | `str`; 使用原本のhash |
| calibration_pack | `object`; `json.loads(calibration.raw_bytes)`、canonical再生成hash一致 |
| hourly | `array[object]`; hour順24行、下表のkeysだけ |
| repaired_hours | `array[int]`; sorted unique、正常0を含めない |
| totals | `object`; 下表の7 keys |
| value_precision | `str`; `internal_full_precision_persisted_hourly_4dp` 固定 |
| source_radiation_semantics | `str`; `previous_hour_mean_w_m2_to_interval_kwh_m2` 固定 |

| hourly行key | 型・値 |
| --- | --- |
| hour | `int`; 0..23 |
| interval_start / interval_end | `str`; context timezone付きISO、23時endは翌00時 |
| source_time | `str \| null`; 提供者原timestamp、未知はnull |
| q_kwh_m2 / coefficient_raw / pv_kwh | `float`; Q/C/最終PV、full precision |
| f_kwh_m2 | `float \| null`; 正規化/許可修復後F、夜間未知はnull |

| totals key | 型・集計窓（両端含む） |
| --- | --- |
| full_day_pv_kwh | `float`; ΣPV[0..23] |
| decision_day_pv_kwh | `float`; ΣPV[7..22] |
| morning_pv_kwh / midday_pv_kwh / evening_pv_kwh | `float`; ΣPV[7..9] / [10..15] / [16..22] |
| decision_day_load_kwh / midday_load_kwh | `float`; Σload[7..22] / [10..15] |

既存raw `forecast.hourly_weather` はsource W/m²、trace Fは整列済kWh/m²。
上記semanticsで二つの保存理由を明示する。fulltraceをphysical診断・optimization・24行へ複製しない。
既存保存hourlyは4小数、内部とtraceはfull precision。target/capacity同一時のclip前SOC差は
`16 * 0.0001 / effective_capacity_kwh * 100` の保守上限内。target/capacity差は丸め誤差にしない。

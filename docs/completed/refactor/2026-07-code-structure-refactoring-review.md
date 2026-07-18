# SolerControler 大規模モジュール・変数過多レビュー

作成日: 2026-07-18

## 1. 結論

現在の最重要リファクタリング対象は `energy_model_main.py` の `main()` です。

- 535行
- ローカル変数 105個
- 分岐相当 59個
- 関数呼び出し 255回
- 環境変数の読込み、履歴データ加工、需要予測、PV予測、予測補正、SOC制約、コスト最適化、結果整形、永続化を1関数で処理

これは単に「長い関数」ではなく、複数のユースケースとドメイン処理を同じスコープで管理している状態です。

次点は以下です。

1. `cloud_job_runner.py:_monitor_partial_forced_and_stop`
   - 270行、35変数、33分岐、100呼出
2. `app/dashboard_data.py` のDB別ロード関数
   - SQLite 240行
   - PostgreSQL 240行
   - Firestore 194行
3. `app/forecast_correction.py:_build_forecast_correction`
   - 200行、49変数、21分岐、108呼出、引数10個
4. `app/pv_array_forecast.py:calibrate_performance_ratio`
   - 199行、引数11個
5. `app/kpnet_workflow.py:_run_settings_phase`
   - 197行
6. `app/kpnet_workflow.py:_build_dynamic_forced_profile`
   - 173行

## 2. ファイル規模

主要な大規模ファイルは以下です。

| ファイル | 行数 |
|---|---:|
| `energy_model_main.py` | 2204 |
| `app/dashboard_data.py` | 1718 |
| `app/kpnet_workflow.py` | 1596 |
| `static/dashboard.js` | 1340 |
| `cloud_job_runner.py` | 1229 |
| `app/forecast_correction.py` | 932 |
| `app/pv_array_forecast.py` | 931 |
| `app/sheets_export.py` | 874 |
| `app/operations_db.py` | 839 |

ファイル行数だけで問題とは断定できませんが、上位ファイルでは責務の異なる処理が同居しています。

## 3. 優先度A: `energy_model_main.main()` の分割

### 現在含まれている処理

確認できた処理だけでも、次の段階があります。

1. `.env` と実行環境の読込み
2. artifacts・CSVパスの解決
3. CSV履歴読込み
4. モデル係数の学習
5. 履歴プロファイル作成
6. 緯度・経度・タイムゾーン取得
7. 翌日天気予報取得
8. 在宅・占有イベント取得
9. 消費電力学習データ作成
10. 気象履歴取得
11. 日次消費電力予測
12. 学習日数・気象結合状況の診断
13. PVアレイ予測
14. PV予測の上書き値作成
15. 現在SOC・月間買電量等の計算
16. 夜間充電入力作成
17. 夜間充電目標計算
18. 時間別負荷予測作成
19. 時間別PV予測作成
20. 物理PVモデル候補作成・選択
21. 予測補正
22. 日没時刻推定
23. 朝・昼・履歴ベースのSOC上限制約
24. 従来方式の昼間SOC最適化
25. コスト最適化用シナリオ作成
26. FirestoreからSOC意思決定事前分布取得
27. 期待コスト最適化
28. 結果payload作成・出力

### 推奨構造

`main()` は最終的に次の程度まで縮小します。

```python
def main() -> int:
    config = EnergyModelConfig.from_env()
    context = load_energy_model_context(config)
    forecasts = build_forecasts(context)
    constraints = evaluate_soc_constraints(context, forecasts)
    decision = optimize_soc_decision(context, forecasts, constraints)
    output = build_energy_model_output(context, forecasts, constraints, decision)
    persist_energy_model_output(output, config)
    return 0
```

目標は、main() を30～60行程度のオーケストレーターにすることです。

分割候補モジュール
app/energy_model/
    config.py
    context.py
    data_loading.py
    consumption_pipeline.py
    pv_pipeline.py
    correction_pipeline.py
    constraints.py
    optimization_pipeline.py
    output.py
    runner.py
config.py

環境変数の読込みを集約します。

Python
実行する
@dataclass(frozen=True)
class EnergyModelConfig:
    artifacts_dir: Path
    latitude: float
    longitude: float
    timezone: str
    consumption_min_training_days: int
    consumption_fallback_window_days: int
    reserve_soc_percent: float
    cost_optimization_enabled: bool
    cost_soc_step_percent: float
```

現状は os.getenv() が処理途中に多数存在するため、設定値の由来、デフォルト値、型変換、テスト差替えが分散しています。

context.py

実行中に共有する基礎データをまとめます。

Python
実行する
@dataclass
class EnergyModelContext:
    config: EnergyModelConfig
    csv_paths: CsvPaths
    rows: list[dict]
    coefficients: ModelCoefficients
    historical_profile: HistoricalProfile
    target_date: date
    weather_forecast: WeatherForecast
    latest_soc_percent: float
    occupancy_events: list[OccupancyEvent]
```

#### `consumption_pipeline.py`

以下をまとめます。

学習行抽出

占有イベント反映

気象履歴取得

日次需要予測

時間別負荷予測

学習データ診断

返り値は辞書ではなくデータクラスが望ましいです。

Python
実行する
@dataclass
class ConsumptionForecastBundle:
    daily: DailyConsumptionForecast
    hourly_kwh: dict[int, float]
    training_diagnostics: TrainingDiagnostics
    occupancy_adjustment: OccupancyAdjustment | None
```

#### `pv_pipeline.py`

以下を担当します。

PVアレイ予測

既存方式の時間別PV生成

物理モデル候補生成

採用方式の決定

不確実性の選択

Python
実行する
@dataclass
class PvForecastBundle:
    hourly_kwh: dict[int, float]
    total_kwh: float
    morning_kwh: float
    selected_method: str
    source: str
    uncertainty: PvUncertainty
    diagnostics: dict
```

#### `constraints.py`

現在別々に扱われている以下の制約を統合します。

morning headroom guard

daytime net surplus guard

historical SOC gain guard

reserve SOC

legacy max target SOC

cost optimizer max target SOC

Python
実行する
@dataclass
class SocConstraintSet:
    reserve_soc_percent: float
    max_target_soc_percent: float
    active_constraints: list["SocConstraint"]
    diagnostics: dict

各guardが辞書を返す設計より、共通インターフェースを持たせる方が安全です。

Python
実行する
@dataclass
class SocConstraint:
    name: str
    applied: bool
    cap_target_soc_percent: float | None
    reason: str
    evidence: dict
optimization_pipeline.py

従来最適化と期待コスト最適化の選択・実行を担当します。

Python
実行する
@dataclass
class OptimizationRequest:
    soc_now_percent: float
    capacity_kwh: float
    hourly_load_kwh: dict[int, float]
    hourly_pv_kwh: dict[int, float]
    constraints: SocConstraintSet
    cost_model: SocCostModel
    uncertainty: PvUncertainty
    prior: SocDecisionPrior | None
Python
実行する
@dataclass
class OptimizationDecision:
    target_soc_percent: float
    selected_strategy: str
    expected_cost_yen: float | None
    diagnostics: dict
4. 変数105個への対処

変数を単純に短くしたり、途中で del したりしても根本解決にはなりません。

105個の変数は、少なくとも次のデータ群が同一スコープに存在することが原因です。

設定値

生データ

学習データ

天気予報

需要予測

PV予測

物理モデル診断

予測補正

SOC制約

最適化設定

コストモデル

prior

出力payload

対策は、意味のあるライフサイクル単位でオブジェクト化することです。

推奨グループ:

config
context
consumption_bundle
pv_bundle
corrected_forecast
constraints
optimization_request
decision
output

この形にすると、main() が直接保持する変数は10～15個程度に抑えられます。

5. 優先度A: 強制充電監視処理

対象:

cloud_job_runner.py:1007
_monitor_partial_forced_and_stop

計測値:

270行

35変数

33分岐

100呼出

問題

この関数は次の責務を同時に持っている可能性が高いです。

監視ポリシー読込み

開始・終了時刻計算

SOC取得

SOC取得失敗処理

充電速度推定

完了時刻推定

再適用判定

停止判定

sleep間隔制御

状態遷移

ログ・永続化

これは時間依存の状態機械として設計した方が明確です。

推奨データ構造
Python
実行する
@dataclass(frozen=True)
class ForcedChargeMonitorPolicy:
    poll_seconds: int
    max_soc_failures: int
    confirm_before_minutes: int
    reapply_enabled: bool
    reapply_after_polls: int
    reapply_min_delta_percent: float
    allow_without_soc: bool
Python
実行する
@dataclass
class ForcedChargeMonitorState:
    started_at: datetime
    previous_soc: float | None = None
    consecutive_soc_failures: int = 0
    stagnant_polls: int = 0
    latest_soc: float | None = None
    transition: str = "monitoring"
Python
実行する
@dataclass(frozen=True)
class ForcedChargeObservation:
    observed_at: datetime
    soc_percent: float | None
    estimated_rate_percent_per_hour: float | None
    estimated_remaining_minutes: float | None
推奨関数分割
Python
実行する
def create_monitor_context(...)
def read_charge_observation(...)
def update_monitor_state(...)
def decide_monitor_action(...)
def apply_monitor_action(...)
def calculate_next_poll_seconds(...)

判定結果を明示的にします。

Python
実行する
class MonitorAction(Enum):
    CONTINUE = "continue"
    REAPPLY = "reapply"
    STOP_TARGET_REACHED = "stop_target_reached"
    STOP_CUTOFF = "stop_cutoff"
    ABORT_SOC_UNAVAILABLE = "abort_soc_unavailable"

これにより、巨大な while 内の分岐を状態遷移テストとして独立検証できます。

6. 優先度A: Dashboard DBロード処理

対象:

_load_sqlite_slice: 240行

_load_postgres_slice: 240行

_load_firestore_slice: 194行

問題

DBごとに以下の共通処理が重複している可能性があります。

対象期間の正規化

oldest/newest取得

energy daily構築

cost daily/monthly構築

battery daily構築

monitoring daily構築

forecast hourly構築

latest events取得

latest schedule構築

戻り値の同一形式への整形

ストレージ固有処理と、ダッシュボード用集約処理が混在しています。

推奨設計

ストレージから統一形式のraw dataを返します。

Python
実行する
class DashboardRepository(Protocol):
    def load_raw_slice(
        self,
        start_date: date,
        end_date: date,
    ) -> "DashboardRawSlice":
        ...
Python
実行する
@dataclass
class DashboardRawSlice:
    energy_rows: list[EnergyRow]
    cost_rows: list[CostRow]
    battery_rows: list[BatteryRow]
    forecast_rows: list[ForecastRow]
    monitoring_rows: list[MonitoringRow]
    events: list[OperationEvent]
    global_oldest: date | None
    global_newest: date | None

ストレージ実装:

app/dashboard/repositories/sqlite.py
app/dashboard/repositories/postgres.py
app/dashboard/repositories/firestore.py

共通集約:

Python
実行する
def build_dashboard_slice(raw: DashboardRawSlice) -> DashboardSlice:
    ...

DB別関数は、クエリとraw型への変換だけを担当させます。

7. 優先度B: 予測補正

対象:

app/forecast_correction.py:_build_forecast_correction

計測値:

200行

49変数

21分岐

108呼出

引数10個

問題

以下が同居しています。

履歴抽出

気象データ取得

類似日・直近日の選択

PV比率計算

負荷比率計算

温度補正

hourly floor

safety floor

peak penalty

diagnostics生成

推奨分割
Python
実行する
def build_correction_context(...)
def calculate_pv_correction(...)
def calculate_load_correction(...)
def calculate_temperature_correction(...)
def calculate_hourly_floor(...)
def apply_forecast_corrections(...)
def build_correction_diagnostics(...)
引数の整理

現状の10引数は、2つの入力オブジェクトへまとめます。

Python
実行する
@dataclass(frozen=True)
class ForecastCorrectionInput:
    target_date: date
    hourly_load_kwh: dict[int, float]
    hourly_pv_kwh: dict[int, float]
    target_forecast: WeatherForecast
Python
実行する
@dataclass(frozen=True)
class ForecastCorrectionPolicy:
    latitude: float
    longitude: float
    timezone: str
    skip_pv_correction: bool
    allow_load_safety_floor: bool

返り値も辞書ではなく型を定義します。

Python
実行する
@dataclass
class ForecastCorrectionResult:
    hourly_load_kwh: dict[int, float]
    hourly_pv_kwh: dict[int, float]
    load_scenarios: list[LoadScenario]
    peak_penalty: PeakPenalty
    diagnostics: dict
8. 優先度B: PVアレイ予測

対象:

calibrate_performance_ratio: 199行、引数11個

build_pv_array_forecast: 127行、引数10個

forecast_pv_arrays: 引数7個

forecast_pv_arrays_forecast_solar: 引数7個

推奨設定型
Python
実行する
@dataclass(frozen=True)
class PvSite:
    latitude: float
    longitude: float
    timezone: str
Python
実行する
@dataclass(frozen=True)
class PvCalibrationRequest:
    site: PvSite
    target_date: date
    arrays: tuple[PvArray, ...]
    history: tuple[PvObservation, ...]
    weather_history: WeatherHistory
    policy: PvCalibrationPolicy

関数引数が減るだけでなく、引数順序の取り違えを防止できます。

9. 優先度B: KpNet workflow

対象:

KpNetClient: 262行

KpNetConfig: 127行

_run_settings_phase: 197行

_build_dynamic_forced_profile: 173行

_build_dynamic_green_profile: 87行

_build_payload: 81行

推奨分割
app/kpnet/
    config.py
    client.py
    auth.py
    payloads.py
    profiles.py
    settings_phase.py
    workflow.py

KpNetClient はHTTP通信だけに限定し、動的プロファイル生成や運転モード選択を持たせない方がよいです。

Python
実行する
class KpNetClient:
    def authenticate(...)
    def fetch_settings(...)
    def update_settings(...)
    def start_operation(...)
    def stop_operation(...)

プロファイル生成:

Python
実行する
class ForcedProfileBuilder:
    def build(...)

class GreenProfileBuilder:
    def build(...)
10. 辞書中心設計の縮小

現在は .get() を多用した辞書が、複数の処理境界を越えて渡されている形跡があります。

例:

Python
実行する
physical_pv_diagnostics.get("enabled")
physical_pv_diagnostics.get("selected_method")
morning_headroom_guard.get("cap_target_soc_percent")
forecast_correction.get("hourly_load_kwh")
forecast_correction.get("peak_penalty")

この形式には以下の問題があります。

キー名のタイプミスを静的検出できない

必須項目と任意項目が不明

数値単位が不明

生成元と利用側が密結合

リファクタリング時に利用箇所を追跡しにくい

dict かどうかの防御コードが増える

内部処理では dataclass、外部入出力境界だけで辞書化する構成を推奨します。

Python
実行する
payload = asdict(output)
11. 実施順序
Phase 0: 安全網

コードを分割する前に、以下のcharacterization testを追加します。

同じCSV・環境変数から同じ目標SOCが得られる

同じ入力から同じ時間別PV・負荷予測が得られる

同じ入力から同じguard適用結果が得られる

同じ入力から同じpayload主要項目が得られる

強制充電監視の主要状態遷移

SQLite/PostgreSQL/FirestoreのDashboardSlice互換性

完全一致が難しい浮動小数点値は許容誤差を明示します。

Phase 1: main() 内で関数抽出

ファイル移動をせず、まず energy_model_main.py 内で以下を抽出します。

_load_execution_context
_build_consumption_forecasts
_build_selected_pv_forecast
_build_soc_constraints
_run_soc_optimization
_build_result_payload

最初から多数のファイルへ移すより、挙動を保ったまま関数境界を作る方が安全です。

Phase 2: データクラス導入

優先順:

EnergyModelConfig

ConsumptionForecastBundle

PvForecastBundle

SocConstraintSet

OptimizationDecision

EnergyModelOutput

Phase 3: モジュール移動

抽出済み関数を app/energy_model/ へ移します。

Phase 4: Dashboard repository共通化

SQLiteを基準実装としてrepository境界を作り、その後PostgreSQL、Firestoreを合わせます。

Phase 5: 監視処理の状態機械化

_monitor_partial_forced_and_stop を状態・観測・判定・副作用へ分割します。

Phase 6: 予測補正・PV校正

引数オブジェクト導入後に処理段階を分割します。

12. 最初の変更単位

最初のPRまたはコミットでは、次だけを行うのが安全です。

EnergyModelConfig を追加

main() 冒頭の環境変数読込みを移す

_load_execution_context(config) を抽出

出力値が変わらないことを既存テストで確認

snapshotまたは主要payload比較テストを追加

この時点では予測アルゴリズムやSOC計算ロジックを変更しません。

13. 目標メトリクス

厳密なルールではありませんが、本プロジェクトでは次を警告基準にできます。

項目	警告基準	目標
関数行数	80行超	20～60行
ローカル変数	25個超	15個以下
引数数	7個超	5個以下
分岐数	15個超	10個以下
モジュール行数	1000行超	300～800行
辞書による内部DTO	複数境界通過	dataclass化

例外として、単純なマッピングテーブル、シリアライズ、SQL定義等は長くても問題ありません。

14. 注意点

ファイルを分割すること自体を目的にしない

既存の計算式とリファクタリングを同時に変更しない

環境変数名を同時に変更しない

payloadキー名を同時に変更しない

Firestore・CSV・Dashboard互換性を先に固定する

dict.get(..., default) の既存挙動をデータクラス移行時に失わない

日付、タイムゾーン、時間境界を値オブジェクト化する

純粋計算と外部I/Oを分離する

ログ出力をビジネス判定の代替にしない

15. 総合優先順位
優先度	対象	理由
A1	energy_model_main.main	535行、105変数、全処理の集中点
A2	_monitor_partial_forced_and_stop	時間依存・副作用・状態遷移が集中
A3	Dashboard DBロード3系統	重複削減効果が大きい
B1	_build_forecast_correction	49変数、10引数、複数補正方式
B2	PV校正・予測関数	引数過多、モデル処理の集中
B3	KpNet workflow	通信・方針・payload生成の分離余地
C1	static/dashboard.js	1340行。Python側整理後に別途調査
C2	export・operations DB	大規模だが上記より先に境界確認が必要

最も効果が高いのは、main() のコードを機械的に小関数へ刻むことではなく、需要予測、PV予測、SOC制約、最適化、出力という5つの明確な処理結果をデータクラスで表現することです。

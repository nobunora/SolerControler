# 第6段階: EnergyModelパイプライン分割

> 対象リポジトリ: `C:\VSC\SolerControler`  
> レビュー基準HEAD: `88c11ea` (`Embed dashboard bootstrap payload in HTML`)  
> 作成日: 2026-07-15  
> 文書の性格: 実装着手前の保守性改善設計書。確認済み事実と未確認事項を分離して記載する。  
> 注意: 本文書作成時点ではソースコード変更を行っていない。

## 文書の使い方

この段階では、実装前に「現在の挙動を壊さないこと」と「次段階の設計を妨げないこと」を同時に満たす必要がある。作業者は、確認済み事実を前提にしてよいが、追加調査項目を推測で埋めてはならない。特に、外部サービス、Firestore、PostgreSQL、KP-NET実機、Cloud Jobのスケジュールに関する事項は、ローカル単体テストだけでは確定できない。

## 1. 目的

`energy_model_main.py` の巨大な `main()` を、明示的な入力、段階、結果、出力へ分解する。計算ロジックを変更せず、予測・補正・制約・最適化・payload構築の境界を定義し、将来のモデル差替えと検証を容易にする。

## 2. 調査済み状態

- ファイル全体は約2303行。
- `main()` は1782〜2299行、518行。
- CSV探索、履歴、天気、消費予測、PV予測、physical candidate、forecast correction、SOC guard、最適化、cost optimization、ML prior、payload、JSON書込を一つの関数が担当する。
- `2029-2185` 付近のcost optimizationが大きく、`2251-2295` で巨大payloadを直接構築する。
- `_historical_daytime_soc_gain_guard` など大きな補助関数もある。
- `energy_model_main.py` 内部で独自env helper、CSV reader、latest artifact探索を持つ。
- `app.energy_model`, `app.soc_cost_optimizer`, `app.pv_array_forecast`, `app.forecast_correction`, `app.pv_physical_forecast` 等の既存モジュールは既にあり、完全な単一ファイルではない。

## 3. 追加調査が必要な箇所

1. 最終payloadの各field利用者。cloud runner、DB pipeline、dashboard、Firestore archive、外部script。
2. fieldの必須/任意、旧version互換。
3. env overrideが本番でどの程度使われているか。
4. physical PV candidateと既存PV forecastの選択基準。
5. forecast correctionが失敗した場合のfallback。
6. optimizer disabled時、legacy payloadの必要性。
7. ML prior取得失敗の扱いとtimeout。
8. 同一入力で完全再現可能か。外部API、現在時刻、履歴更新による非決定性。
9. 計算途中での丸めと最終表示丸め。

## 4. 理想状態と現在のギャップ

| 観点 | 現在 | 理想 | ギャップ |
|---|---|---|---|
| entrypoint | 518行 | 30〜60行 | orchestrationと計算が混在 |
| 入力 | env、CSV、APIを直接読む | typed `EnergyPlanInputs` | 依存が暗黙 |
| stage | ローカル変数列 | 明示的stage result | 再利用・部分テスト困難 |
| payload | 巨大dict直書き | typed result + serializer | schemaが暗黙 |
| 外部I/O | main内部 | ports/adapters | 再現性が低い |
| エラー | stageごとに不統一 | stage error/fallback policy | failure理由が追いにくい |

## 5. 実装案3案

### 案A: mainを小関数へ機械的分割

処理ブロックを関数へ移し、dictを渡す。

- 長所: 最短でmainが短くなる。
- 短所: 暗黙の共有dictと順序依存が残り、型境界が弱い。

### 案B: typed pipeline context + stage functions

入力・各stage result・最終resultをdataclass化し、stageは必要な入力だけ受け取る。entrypointはsettingsとportsを組み立てる。

- 長所: 段階移行可能。テストと将来のモデル差替えに強い。
- 短所: model数が増える。初期の型設計が必要。

### 案C: DAG/ワークフローエンジン

各計算をnodeとして依存DAGを定義し、cache・並列実行・再計算を可能にする。

- 長所: 複雑なモデル実験に強い。
- 短所: 現状の規模では過剰。debugと運用が複雑になる。

## 6. 採用案

**案Bを採用する。** DAG化は、stage依存がさらに増え、部分再計算や並列実行の必要が明確になった場合に再評価する。

## 7. 推奨構成

```text
app/energy_plan/
├─ models.py
├─ settings.py
├─ inputs.py
├─ weather.py
├─ consumption.py
├─ pv.py
├─ correction.py
├─ guards.py
├─ optimization.py
├─ selection.py
├─ payload.py
├─ pipeline.py
└─ ports.py
```

既存 `app.energy_model` 等をすぐ移動する必要はない。新packageはorchestration境界として開始し、既存計算を呼び出す。

## 8. モデル設計

### 8.1 Inputs

```python
@dataclass(frozen=True)
class EnergyPlanInputs:
    target_date: date
    monitoring: Sequence[MonitoringPoint]
    weather: WeatherForecast
    occupancy: Sequence[OccupancyEvent]
    current_soc_percent: float | None
    tariff_context: TariffContext
```

### 8.2 Forecast stage

```python
@dataclass(frozen=True)
class BaseForecasts:
    hourly_load_kwh: tuple[float, ...]
    pv_array: PvArrayForecast
    sun_hours: float
    temperature_c: float
```

### 8.3 Correction/selection

```python
@dataclass(frozen=True)
class SelectedForecasts:
    hourly_load_kwh: tuple[float, ...]
    hourly_pv_kwh: tuple[float, ...]
    pv_source: str
    diagnostics: Mapping[str, object]
```

### 8.4 Constraints

```python
@dataclass(frozen=True)
class SocConstraints:
    overnight_discharge: GuardResult
    morning_headroom: GuardResult
    daytime_surplus: GuardResult
    historical_gain: GuardResult
```

### 8.5 Optimization result

```python
@dataclass(frozen=True)
class EnergyPlanDecision:
    target_soc_percent: float
    required_night_charge_kwh: float
    objective_name: str
    active_constraints: tuple[str, ...]
    rejected_candidates: tuple[RejectedCandidate, ...]
```

最終JSONはtyped resultからserializerで作る。

## 9. stage設計

1. `load_inputs(settings, ports)`
2. `build_base_forecasts(inputs, settings)`
3. `build_physical_candidate(...)`
4. `apply_forecast_correction(...)`
5. `select_forecast_candidate(...)`
6. `compute_soc_constraints(...)`
7. `run_daytime_optimization(...)`
8. `run_cost_optimization(...)`
9. `select_final_decision(...)`
10. `build_plan_document(...)`
11. `write_plan(...)`

各stageは副作用を持たないことを基本とする。外部API、Firestore prior、ファイル書込はport経由にする。

## 10. ports

```python
class WeatherProvider(Protocol): ...
class MonitoringRepository(Protocol): ...
class SocPriorProvider(Protocol): ...
class PlanWriter(Protocol): ...
class Clock(Protocol): ...
```

テストでは固定providerを使う。外部API失敗時のfallbackはpipeline policyとして明示する。

## 11. payload互換

最終payloadを一気に変更しない。

- `PlanDocumentV1` serializerを作り、現行キーを維持。
- 内部model名と外部キー名を分離。
- diagnosticsはversionを付ける。
- 新fieldはoptional追加。
- 削除予定fieldはdeprecation期間を設ける。

`cloud_job_runner`, DB ingest, dashboardで参照するfieldを一覧化し、consumer contract testを追加する。

## 12. 移行手順

### Step 1: payload builder抽出

最も末端で副作用が少ない。現行ローカル値を引数にして同一JSONを返すことを確認する。

### Step 2: input loading抽出

CSV、env、weather、prior取得をまとめるが、計算とは分ける。

### Step 3: forecast stage抽出

base forecast、physical candidate、correction、selectionを分ける。

### Step 4: guard stage抽出

各guard結果をdataclass化する。

### Step 5: optimization stage抽出

daytime/cost/legacyを整理し、candidate選択を一箇所にする。

### Step 6: pipeline導入

`main()` をsettings→ports→pipeline→writerへ縮小する。

### Step 7: 旧helper整理

重複CSV/env/time helperを第2段階の共通モジュールへ切替。

## 13. テスト

- 各stage単体
- stage間contract
- golden payload
- env override
- external provider failure
- no monitoring data
- no current SOC
- high PV / low PV
- optimizer disabled
- cost optimization disabled
- legacy compatibility
- deterministic replay
- numeric tolerance

## 14. 可観測性

各stageで以下を記録する。

- stage名
- 入力件数・date
- 実行時間
- selected source
- fallback reason
- active constraints
- decision summary

機密情報や全CSV内容をログへ出さない。

## 15. ロールバック

- `ENERGY_PLAN_PIPELINE_V2` flagで旧main経路を維持。
- 新旧を同一入力で計算し、書込は旧のみのshadow mode。
- payload差分を安定化して記録。
- consumer contractに差がある場合はV1 serializerへ戻す。

## 16. 完了条件

- `main()` が薄いentrypointになる。
- 外部I/Oと計算stageが分離される。
- 最終payloadがtyped resultから生成される。
- 代表fixtureで旧新payloadが許容差内一致する。
- 各stageが単体テスト可能。
- 新規energy_plan packageがmypy strictを通る。

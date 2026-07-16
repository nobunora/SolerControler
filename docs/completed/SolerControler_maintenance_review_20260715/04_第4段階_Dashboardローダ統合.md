# 第4段階: Dashboardローダ統合

> 対象リポジトリ: `C:\VSC\SolerControler`  
> レビュー基準HEAD: `88c11ea` (`Embed dashboard bootstrap payload in HTML`)  
> 作成日: 2026-07-15  
> 文書の性格: 実装着手前の保守性改善設計書。確認済み事実と未確認事項を分離して記載する。  
> 注意: 本文書作成時点ではソースコード変更を行っていない。

## 文書の使い方

この段階では、実装前に「現在の挙動を壊さないこと」と「次段階の設計を妨げないこと」を同時に満たす必要がある。作業者は、確認済み事実を前提にしてよいが、追加調査項目を推測で埋めてはならない。特に、外部サービス、Firestore、PostgreSQL、KP-NET実機、Cloud Jobのスケジュールに関する事項は、ローカル単体テストだけでは確定できない。

## 1. 目的

SQLite、PostgreSQL、Firestoreごとに重複しているdashboard読み込みと派生計算を、normalized source rowsと単一serviceへ統合する。backend差をquery/document変換に限定し、表示ロジックの差異を防ぐ。

## 2. 調査済み状態

`app/dashboard_data.py` には大きなloaderが3つある。

- `_load_sqlite_slice`: 約235行
- `_load_postgres_slice`: 約235行
- `_load_firestore_slice`: 約192行

各loaderはbackendからデータを読むだけでなく、次も行う。

- `_build_energy_daily`
- `_build_cost_monthly`
- `_build_latest_schedule_from_events`
- warning/review構築
- `DashboardRawData` 作成
- `_build_dashboard_slice`

Firestoreだけdaily reviewやcache関連処理が追加されている。共通domain変換を各loaderが個別に呼ぶため、処理順や欠損時fallbackがbackendごとにずれる余地がある。

`static/dashboard.js` は1449行で、日付、API、state、DOM、Chart.js、計算を混在させているが、純粋計算の一部は `dashboard_calculations.js` に分離済みである。

## 3. 追加調査が必要な箇所

1. 3backendの実データで同期間のdashboard payloadを比較する。
2. Firestore daily reviewが表示上必須か、運用診断専用か。
3. cacheのTTL、invalidate条件、backend切替時の挙動。
4. global bounds取得の性能と必要性。
5. 大量期間指定時のメモリ・query負荷。
6. settings eventの同時刻・欠損時のtie-break仕様。
7. timezone/date文字列のbackend差。
8. dashboard APIの後方互換性要件。

## 4. 理想状態と現在のギャップ

| 観点 | 現在 | 理想 | ギャップ |
|---|---|---|---|
| backend adapter | query + 派生計算 | normalized rows返却 | loaderが太い |
| service | backendごとに実質重複 | 単一build service | 処理順差の余地 |
| model | dictとdataclass混在 | typed source/result models | null意味が曖昧 |
| Firestore固有 | loader内部に混在 | capabilityとして明示 | 共通機能との境界不明 |
| API | 現行payload | versioned/stable contract | 変更影響が大きい |

## 5. 実装案3案

### 案A: loader内の共通関数呼び出しだけ整理

既存ファイル内でhelperを増やし、3loaderの共通末尾を一本化する。

- 長所: 変更量が少ない。
- 短所: query、変換、serviceが同じ巨大ファイルに残る。

### 案B: normalized source rows + adapter + service

各backend adapterは `DashboardSourceRows` を返し、単一serviceが日次/月次/警告/sliceを作る。

- 長所: backend parityが明確。将来APIやcacheを変更しやすい。
- 短所: source model設計と一時的な変換コードが必要。

### 案C: dashboard専用materialized read model

DB pipeline側でdashboard表示用データを事前集計し、dashboardは単純readのみとする。

- 長所: 表示性能が高い。queryが単純。
- 短所: write pipelineとの結合が強く、再集計・migrationが必要。現段階では大きい。

## 6. 採用案

**案Bを採用する。** 性能上必要になった場合、案Bのservice出力をmaterializeする形で案Cへ発展できる。

## 7. 推奨構成

```text
app/dashboard/
├─ models.py
├─ domain.py
├─ service.py
├─ cache.py
└─ adapters/
   ├─ sqlite.py
   ├─ postgres.py
   └─ firestore.py
```

### 7.1 source model

```python
@dataclass(frozen=True)
class DashboardSourceRows:
    pv_daily: Sequence[PvDailyRow]
    cost_daily: Sequence[CostDailyRow]
    battery_daily: Sequence[BatteryDailyRow]
    battery_flow_daily: Sequence[BatteryFlowDailyRow]
    monitoring_daily: Sequence[MonitoringDailyRow]
    forecast_hourly: Sequence[ForecastHourlyRow]
    settings_events: Sequence[SettingsEvent]
    model_parameters: Sequence[ModelParameterRow]
    global_bounds: DateRange | None
    diagnostics: DashboardDiagnostics
```

### 7.2 service

```python
class DashboardService:
    def __init__(self, source: DashboardSource):
        self.source = source

    def get_slice(self, request: DashboardRequest) -> DashboardSlice:
        rows = self.source.load(request.source_range)
        return build_dashboard_slice(rows, request)
```

serviceはbackend名を知らない。

### 7.3 adapter責務

- SQL/Firestore query
- backend row/document → typed row
- backend固有ページング
- field alias
- timestamp normalization

日次・月次計算、latest schedule選択、warning生成を行わない。

### 7.4 Firestore daily review

二案を実装前に判定する。

1. normalized rowsから作れるなら `domain.py` へ移し全backendで利用可能にする。
2. Firestore監査メタデータに依存するなら `FirestoreDashboardDiagnostics` としてadapter capabilityに明示し、一般slice生成と分離する。

暗黙的にFirestore loaderだけ結果を追加する状態を避ける。

## 8. API互換

外部payloadは当面変えない。内部modelから現行JSONへのserializerを用意する。

```python
def serialize_dashboard_slice(value: DashboardSlice) -> dict[str, object]:
    ...
```

新フィールド追加はoptionalとし、削除・renameはAPI version導入後に行う。

## 9. 移行手順

1. 現行3loaderの出力を同じfixtureで保存・比較。
2. `models.py` を追加。
3. SQLite adapterから開始し、既存loaderと同一出力を確認。
4. PostgreSQL adapterを実装しparity確認。
5. Firestore adapterを実装し固有diagnosticsを分離。
6. 単一serviceへ切替。
7. 旧loaderをfacade化後、参照消滅を確認して削除。
8. cache keyをbackend + request + data versionで明示する。

## 10. テスト

- source row parser tests
- same source rows → same slice
- latest schedule tie-break
- empty data
- single day
- month crossing
- future period
- timezone boundary
- global boundsなし
- Firestore diagnosticsあり/なし
- serializer後方互換
- backend integration parity

## 11. 性能

最初に正確性を優先し、計測後に最適化する。

計測項目:

- adapter query時間
- source row件数
- service変換時間
- JSON size
- cache hit率

必要ならsource rangeを期間ごとに狭める。巨大な全期間読み込みを前提にしない。

## 12. ロールバック

- `DASHBOARD_DATA_SERVICE_V2` flagで旧loaderへ戻せるようにする。
- 初期は同一requestを新旧で計算し、JSON差分をログへ出すshadow modeを用意する。
- API serializerを維持するため、frontend変更なしで戻せる。

## 13. 完了条件

- 3backend adapterが同一 `DashboardSourceRows` 契約を実装する。
- 日次/月次/最新schedule/warning/slice生成が単一serviceに存在する。
- 同一fixtureでbackend parityが通る。
- `app/dashboard_data.py` がfacadeまたは大幅に縮小される。
- Firestore固有機能の境界が明示される。
- 現行dashboard APIレスポンスが互換である。

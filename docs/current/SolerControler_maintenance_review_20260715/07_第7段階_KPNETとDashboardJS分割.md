# 第7段階: KP-NETとDashboard JavaScript分割

> 対象リポジトリ: `C:\VSC\SolerControler`  
> レビュー基準HEAD: `88c11ea` (`Embed dashboard bootstrap payload in HTML`)  
> 作成日: 2026-07-15  
> 文書の性格: 実装着手前の保守性改善設計書。確認済み事実と未確認事項を分離して記載する。  
> 注意: 本文書作成時点ではソースコード変更を行っていない。

## 文書の使い方

この段階では、実装前に「現在の挙動を壊さないこと」と「次段階の設計を妨げないこと」を同時に満たす必要がある。作業者は、確認済み事実を前提にしてよいが、追加調査項目を推測で埋めてはならない。特に、外部サービス、Firestore、PostgreSQL、KP-NET実機、Cloud Jobのスケジュールに関する事項は、ローカル単体テストだけでは確定できない。

## 1. 目的

バックエンド中核のdomain・pipelineが安定した後、`app/kpnet_workflow.py` と `static/dashboard.js` の責務集中を解消する。外部機器I/Oと設定判断、frontend計算とDOM描画を分離し、変更影響を限定する。

## 2. 調査済み状態

### 2.1 KP-NET

`app/kpnet_workflow.py` は約1772行。

`KpNetClient` は1076〜1334行で、概ね以下に収まる。

- HTTP GET/POST
- login/logout
- CSVページ、download
- settings page
- poll JSON
- current settings読取
- candidate map
- confirm/write

Client自体の境界は比較的良好である。一方、同ファイルに次が同居する。

- config/env
- HTML parsing/auth utilities
- operation condition/time rules
- plan loading/validation
- charge-rate estimation
- dynamic profile構築
- CSV parse
- plotting
- settings phase
- workflow orchestration

`_run_settings_phase()` は約188行で、現在設定読取、rule/profile、time policy、payload生成、write、confirm、auditを扱う。

### 2.2 Dashboard JavaScript

`static/dashboard.js` は約1449行。

大きな関数例:

- `buildCharts`: 約125行
- `renderWindow`: 約127行
- `renderBatteryLifeProjection`: 約94行
- `renderConstraintGantt`: 約87行
- `bindPeriodControls`: 約86行
- `todayIsoJst`: 約62行

日付、state、API、DOM、Chart.js、計算が混在する。`dashboard_calculations.js` とNodeテストは既にあり、純粋計算分離の足場がある。

## 3. 追加調査が必要な箇所

### KP-NET

1. HTML構造・field名が機器firmwareで変わる可能性。
2. login session、CSRF、cookie、timeout要件。
3. candidate mapとconfirmの正しい順序。
4. dry-run、read-only運用の利用状況。
5. plottingが本番workflowに必要か、開発用か。
6. CSV downloadとsettings操作を同一sessionにする必要。
7. settings intentの監査・承認要件。

### JavaScript

1. 対応ブラウザとES moduleサポート。
2. CSPで`type="module"`が許可されるか。
3. Chart.jsのload順、global依存。
4. dashboard APIのerror/loading state。
5. DOM selectorの安定性。
6. bundler導入可否、Node version管理。
7. accessibility、keyboard、responsive要件。

## 4. 理想状態と現在のギャップ

| 領域 | 現在 | 理想 | ギャップ |
|---|---|---|---|
| KP-NET client | 比較的まとまる | protocol adapter | 同ファイルの周辺責務が多い |
| settings判断 | I/O中に構築 | pure `SettingsIntent` | dry-run/テストが弱い |
| CSV/plot | workflowと同居 | artifact/analysis module | 本番責務が曖昧 |
| JS計算 | 一部分離 | DOM非依存module | dashboard.jsに残存 |
| JS state/API | global/同一file | store/api分離 | 変更影響が広い |
| JS build | script直読 | 最小ES modulesまたは明示namespace | module境界が弱い |

## 5. 実装案3案

### 案A: 最小facade分割

KP-NETはclient/configだけ別ファイル、JSはcalculationsだけ追加分離する。

- 長所: 変更が小さい。
- 短所: orchestrationとDOM集中が残る。

### 案B: package分割 + pure intent + 段階的ES modules

KP-NETをclient/config/plan/profiles/settings_intent/csv/workflowへ分ける。JSはdates/store/api/charts/renderers/mainへ段階分割し、純粋関数をNodeテストする。

- 長所: 既存技術を維持しつつ境界が明確。将来TypeScript化も可能。
- 短所: import/CSP/load順の調整が必要。

### 案C: TypeScript + bundler + frontend framework、KP-NET全面hexagonal化

Vite/TypeScript/React等を導入し、KP-NETも完全なport/adaptersへ再設計する。

- 長所: 型とfrontend構造が強い。
- 短所: build chainと運用が大きく変わり、現dashboard規模では過剰。

## 6. 採用案

**案Bを採用する。** TypeScriptは、JS module分割後に型不足が実際の障害となった時点で判断する。KP-NETは既存Clientを維持し、周辺判断とI/Oを分離する。

## 7. KP-NET推奨構成

```text
app/kpnet/
├─ client.py
├─ config.py
├─ models.py
├─ plan.py
├─ profiles.py
├─ settings_intent.py
├─ csv_export.py
├─ parsing.py
├─ plotting.py
└─ workflow.py
```

### 7.1 SettingsIntent

```python
@dataclass(frozen=True)
class SettingsIntent:
    profile_name: str
    desired_values: Mapping[str, str]
    reasons: tuple[str, ...]
    expected_changes: tuple[SettingChange, ...]
    dry_run: bool
```

純粋関数:

```python
def build_settings_intent(
    current: CurrentSettings,
    plan: NightPlan,
    policy: SettingsPolicy,
    now: datetime,
) -> SettingsIntent:
    ...
```

副作用:

```python
def apply_settings_intent(
    client: KpNetClient,
    intent: SettingsIntent,
) -> ApplySettingsResult:
    ...
```

### 7.2 Client

既存Clientの公開メソッドを大きく変えない。HTTP detailはclientへ維持し、domain modelへの変換はparserまたはadapterへ移す。

### 7.3 CSV/plot

CSV downloadは本番artifact取得、plottingは分析・デバッグ用途として分離する。runner imageにmatplotlibが不要なら依存も分離できる。

### 7.4 workflow

workflowは次だけを行う。

1. config読み込み
2. client login
3. 現状取得
4. intent構築
5. dry-runまたはapply
6. confirm
7. summary/audit保存
8. logout

## 8. JavaScript推奨構成

```text
static/dashboard/
├─ calculations.js
├─ dates.js
├─ store.js
├─ api.js
├─ charts.js
├─ period_controls.js
├─ renderers/
│  ├─ summary.js
│  ├─ forecast.js
│  ├─ battery.js
│  └─ warnings.js
└─ main.js
```

### 8.1 分割順序

1. `dates.js`: JST日付、期間range、label。
2. `api.js`: fetch、error normalization、request cancellation。
3. `store.js`: 現在期間、payload、loading/error。
4. `charts.js`: Chart.js create/update/destroy。
5. renderers。
6. `main.js`: bootstrapとevent bindingのみ。

### 8.2 module方式

対応ブラウザとCSPが問題なければnative ES modulesを使う。

```html
<script type="module" nonce="..." src="/static/dashboard/main.js"></script>
```

bootstrap payloadは現行のinline nonce scriptで先に定義する。moduleから `window.__DASHBOARD_DATA__` を読む互換期間を設け、その後明示的bootstrap moduleへ移す。

ES modulesが難しい場合は、IIFE + `window.Dashboard.*` namespaceを中間案として使う。ただしglobal追加を無秩序に行わない。

## 9. テスト

### KP-NET

- intent generation
- no-change
- forced/green profile
- time window boundary
- invalid plan
- current settings欠損
- candidate map差
- dry-run
- confirm mismatch
- HTTP timeout/retry
- logout failure

### JavaScript

- date/JST boundary
- period navigation
- calculation functions
- store transitions
- API error normalization
- renderer with minimal DOM fixture
- Chart config生成
- bootstrap order

Node標準assertで開始してよい。DOMテストが増えた場合にjsdom等を評価する。

## 10. 移行手順

### KP-NET

1. config/modelを抽出。
2. settings intent純粋関数を追加し、現行 `_run_settings_phase` 内で使用。
3. apply処理を分離。
4. CSV/plotを移動。
5. workflowを薄くする。
6. `app/kpnet_workflow.py` はcompatibility facadeにする。

### JavaScript

1. `dashboard_calculations.js` を基準に純粋関数を追加。
2. datesを分離。
3. API/storeを分離。
4. charts/renderersを分離。
5. mainを置換。
6. 旧 `dashboard.js` をentry facade化後に削除。

## 11. 全体バランスと将来性

- backendとdomainを先に整理してからUI/KP-NETを分けることで、移動先interfaceが安定する。
- KP-NET Clientを壊さず、intentをpure化するため実機リスクを抑えられる。
- native ES modulesを採用すればbundlerなしで境界を作れる。
- 将来TypeScriptへ移る場合、pure modulesから段階移行できる。
- plotting依存を本番runnerから分離できればimage軽量化にも寄与する。

## 12. ロールバック

- `KP_WORKFLOW_V2` で旧workflowへ戻す。
- intentを新旧で比較し、applyは旧のみのshadow mode。
- frontendは旧 `dashboard.js` を残し、template script切替だけで戻せるようにする。
- API contractは変更しない。

## 13. 完了条件

- `KpNetClient`、intent、workflow、CSV、plottingが別責務になる。
- settings判断がpure functionとしてテストされる。
- `app/kpnet_workflow.py` がfacadeまたは大幅縮小される。
- dashboardのdate/API/store/chart/renderが分離される。
- JavaScriptテストがpre-releaseに組み込まれる。
- CSP/bootstrap互換を維持する。
- frontend build chainを不必要に増やしていない。

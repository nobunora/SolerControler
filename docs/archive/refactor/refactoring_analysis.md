# SolerController リファクタリング分析

**作成日**: 2026-06-20
**対象コミット**: work ブランチ

## 目次
1. [現状分析](#現状分析)
2. [問題の詳細](#問題の詳細)
3. [影響範囲](#影響範囲)
4. [リスク評価](#リスク評価)

---

## 現状分析

### プロジェクト規模
- **総コード行数**: 11,862行（app/*.py）
- **ファイル数**: 24モジュール
- **平均ファイルサイズ**: 494行
- **最大モジュール**: `kpnet_workflow.py`（1770行）、`dashboard_data.py`（1405行）

### コード品質指標
| 指標 | 評価 | 備考 |
|------|------|------|
| **関数重複率** | 🔴 高 | 環境変数・型変換ユーティリティが5-10ファイルで重複 |
| **ファイル複雑度** | 🔴 高 | 1400行超のファイルが2つ、責務が5個以上混在 |
| **定数の集約度** | 🟡 中 | ハードコード値が各所に分散（100.0が11箇所） |
| **保守性スコア** | 🟡 中 | 修正時の修正漏れリスク高、テストカバレッジ不明 |

---

## 問題の詳細

### 1. ユーティリティ関数の重複

#### 1.1 環境変数読み込み関数

**出現位置**（3ファイル）:
- `app/operations_db.py`: 行16-31
- `app/kpnet_workflow.py`: 行30-46
- `app/main.py`: 同様の実装あり

**コード比較**:
```python
# operations_db.py - シンプル版
def _load_dotenv_if_present(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (len(value) >= 2 and
            ((value[0] == '"' and value[-1] == '"') or
             (value[0] == "'" and value[-1] == "'"))):
            value = value[1:-1]
        os.environ.setdefault(key, value)
```

**問題**:
- 仕様変更時に3箇所同時修正が必須
- バグ修正時に修正漏れのリスク
- テスト追加の際に3回テストが必要

#### 1.2 環境変数取得関数群

**出現位置**（複数ファイル）:

| 関数 | config.py | operations_db.py | kpnet_workflow.py | その他 | 不整合 |
|------|-----------|------------------|-------------------|--------|--------|
| `_env()` | ✓ | ✓ | ✓ | - | sig差: config.pyは必須/オプション分岐、ops_dbは全てデフォルト有 |
| `_env_bool()` | ✓ | - | ✓ | - | sig差: デフォルト値の扱い |
| `_env_int()` | ✓ | - | - | - | |
| `_env_float()` | ✓ | ✓ | - | - | |
| `_env_optional_float()` | ✓ | - | - | - | |

**具体例 - 不整合**:
```python
# config.py
def _env(name: str, default: Optional[str] = None) -> str:
    value = os.getenv(name)
    if value is None:
        if default is None:
            raise ValueError(f"Missing required environment variable: {name}")
        return default
    return value

# operations_db.py
def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)  # エラーにしない
```

#### 1.3 型変換関数の分散

**出現位置**（5ファイル）:
- `operations_db.py`: `_to_float()`, `_to_float_any()`, `_to_int_any()`
- `dashboard_data.py`: `_to_float_or_none()`
- `kpnet_workflow.py`: `_to_optional_float()`
- `forecast_correction.py`: 同様の処理あり
- `consumption_forecast.py`: 同様の処理あり

**実装の違い**:
```python
# operations_db.py
def _to_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None

# dashboard_data.py
def _to_float_or_none(value: Any) -> float | None:
    if isinstance(value, float):
        return value
    if isinstance(value, int):
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return float(s)
        except (ValueError, TypeError):
            return None
    return None

# kpnet_workflow.py
def _to_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        # _to_floatを呼ぶか直接実装か混在
```

**問題**:
- エラーハンドリング戦略が異なる（try-except vs isinstance）
- None扱いが不統一
- 型チェックの有無が不統一

---

### 2. ハードコード定数の分散

#### 2.1 SOC境界値（100.0, 0.0）

**出現位置**（11箇所）:

```python
# energy_model.py（8箇所）
line 252: reserve_soc = max(0.0, min(100.0, reserve_soc_percent))
line 253: max_target_soc = max(reserve_soc, min(100.0, max_target_soc_percent))
line 254: soc_now = max(0.0, min(100.0, soc_now_percent))
line 257: target_peak_soc = max(reserve_soc, min(100.0, target_peak_soc_percent))
line 317: target_soc_7_percent=max(0.0, min(100.0, best_target_soc))
line 322: predicted_daytime_max_soc_percent=max(0.0, min(100.0, max_soc))
line 325: predicted_sunset_soc_percent=max(0.0, min(100.0, sunset_soc))
line 381: target_soc = max(0.0, min(100.0, 100.0 * e_target / cap_eff if cap_eff > 0 else 0.0))

# kpnet_workflow.py（2箇所）
line 634: return max(0.0, target_soc - max(0.0, min(100.0, soc_now)))

# soc_cost_optimizer.py（1箇所）
line 137: return max(0.0, min(100.0, value))
```

**問題**:
- 将来SOC範囲の拡張（例：-10% ～ 110%）の場合、すべて修正必須
- 定数の意味が不明（SOCなのか他のパーセンテージなのか）
- テスト時の境界値データが複数箇所に散在

#### 2.2 その他のハードコード値

| 定数 | 値 | 出現位置 | 用途 |
|------|-----|---------|------|
| 時間単位 | 24.0 | energy_model.py, kpnet_workflow.py | 日数計算、時間単位変換 |
| パーセント | 100.0 | 各所 | SOC、効率値の正規化 |
| チャンク容量 | 1024*1024 | drive_backup.py | ファイルアップロード |

---

### 3. 責務混在による巨大ファイル

#### 3.1 kpnet_workflow.py（1770行）

**責務分類**:

| 責務 | 行数 | 関数数 | 説明 |
|------|-----|--------|------|
| HTML解析・スクレイピング | ~200 | 5 | `_extract_csrf`, `_extract_alert_message`, `_extract_title`, `_parse_har_credentials` |
| HTTP クライアント/セッション管理 | ~150 | 3 | `KpNetClient` クラス（行1104-1360） |
| 時間・スケジュール計算 | ~300 | 15 | `_parse_hhmm`, `_minutes_to_hm`, `_is_night_window_now`, `_resolve_hhmm` など |
| 充電計画・SOC推定 | ~400 | 12 | `_estimate_charge_power_kw`, `_estimate_charge_soc_rate_percent_per_hour`, `_required_charge_percent` |
| バッテリー運用ロジック | ~350 | 8 | `_apply_fixed_time_rules`, `_pick_battery_operating_mode_code`, `_build_dynamic_forced_profile` |
| CSVデータ処理・グラフ作成 | ~150 | 3 | `_parse_csv_points`, `_plot_csvs` |
| 設定管理 | ~100 | 3 | `ProfileOverrides`, `KpNetConfig` クラス |

**デメリット**:
- 単体テスト作成が困難（依存性が複雑）
- 新機能追加時の影響範囲の把握が難しい
- 関連ないバグ修正がこのファイルに集中

**推奨分割**:
```
kpnet_workflow.py (1770行)
├─ kpnet_client.py: HTTP通信・HTMLスクレイピング（200行）
├─ charging_plan.py: 充電計画・SOC推定（400行）
├─ battery_operations.py: バッテリー運用ロジック（350行）
├─ schedule_utils.py: 時間・スケジュール計算（300行）
└─ kpnet_workflow.py (main): 整理版（200行）
```

#### 3.2 dashboard_data.py（1405行）

**責務分類**:

| 責務 | 行数 | 関数数 | 説明 |
|------|-----|--------|------|
| SQLite操作 | ~250 | 5 | `_get_global_bounds_sqlite`, `_sqlite_table_exists`, `_load_sqlite_slice` |
| PostgreSQL操作 | ~180 | 3 | `_get_global_bounds_postgres`, `_load_postgres_slice` |
| Firestore操作 | ~200 | 5 | `_firestore_bounds`, `_firestore_rows_between`, `_firestore_monitoring_daily`, `_load_firestore_slice` |
| 会計期間計算 | ~150 | 8 | `_add_months`, `_month_end_day`, `_accounting_month_label`, `_accounting_period_bounds` |
| コスト集計 | ~50 | 2 | `_build_cost_monthly` |
| エネルギー予測 | ~150 | 3 | `_forecast_pv_kwh`, `_rolling_load_forecast`, `_model_param_value` |
| ダッシュボード警告 | ~100 | 1 | `_build_dashboard_warnings` |
| スケジュール管理 | ~150 | 2 | `_build_latest_schedule_from_events`, `_default_latest_schedule` |
| 日次エネルギー計算 | ~100 | 1 | `_build_energy_daily` |
| ユーティリティ | ~100 | 10 | `_parse_hhmm_minutes`, `_minutes_to_hhmm`, `_json_object_or_empty`, `_date_add_iso` など |

**デメリット**:
- DB層、ビジネスロジック、プレゼンテーションロジックが混在
- テスト時にすべてのDB（SQLite/PG/Firestore）を用意する必要
- DB切り替え時の影響が大きい

**推奨分割**:
```
dashboard_data.py (1405行)
├─ db_adapters/
│  ├─ sqlite_adapter.py: SQLite読み取り（150行）
│  ├─ postgres_adapter.py: PostgreSQL読み取り（120行）
│  └─ firestore_adapter.py: Firestore読み取り（150行）
├─ accounting.py: 会計期間・コスト計算（200行）
├─ energy_forecast.py: エネルギー予測（150行）
└─ dashboard_builder.py: ダッシュボード組立（300行）
```

#### 3.3 operations_db.py（1118行）

**責務分類**:

| 責務 | 行数 | 関数数 | 説明 |
|------|-----|--------|------|
| DB接続・CRUDカスタム操作 | ~200 | 15 | DB読み書き関連 |
| Open Meteo API呼び出し | ~100 | 3 | 気象予測データ取得 |
| CSV解析 | ~150 | 5 | CSV読み込み・変換 |
| 環境変数読み込み | ~50 | 6 | `_load_dotenv_if_present`, `_env*` 関数（重複問題） |
| 型変換ユーティリティ | ~50 | 3 | `_to_float*` 関数（重複問題） |
| その他ロジック | ~550 | - | メイン関数群 |

**デメリット**:
- 環境変数・型変換のユーティリティが本来の責務と無関係
- API呼び出し層とDB層が混在

---

### 4. インターフェース問題

#### 4.1 型の不統一

```python
# 同じデータを異なる型で扱う例

# operations_db.py では string
forecast_data = _env("FORECAST_URL", "default_url")

# kpnet_workflow.py では Union[str, int]
result: Union[str, int] = _pick_battery_operating_mode_code(mode_map)

# dashboard_data.py では dict vs list の混在
forecast_hourly: dict[str, float] | list[dict] = ...
```

#### 4.2 エラーハンドリングの不統一

```python
# config.py: 厳格（None時に例外）
def _env(name: str, default: Optional[str] = None) -> str:
    if value is None and default is None:
        raise ValueError(...)

# operations_db.py: 緩い（常にデフォルト返却）
def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)
```

**結果**: 呼び出し側で例外ハンドリングの有無が異なり、デバッグが困難

#### 4.3 データ構造の不整合

```python
# dashboard_data.py
DashboardSlice = {
    "date": str,
    "hourly_pv": list[dict[str, float]],
    "accounting_month": str | None
}

# kpnet_workflow.py
ProfileOverrides = {
    "charge_limit_percent": float,
    "mode_code": str
}

# 同じ概念でも異なる構造・命名規則
```

---

## 影響範囲

### 直接影響を受けるファイル

```
環境変数ユーティリティ重複の修正:
  ├─ app/config.py（定義元1）
  ├─ app/operations_db.py（定義元2）
  ├─ app/kpnet_workflow.py（定義元3）
  ├─ app/main.py
  ├─ app/db_sync.py（呼び出し）
  └─ app/*.py（複数ファイルが呼び出し）

型変換ユーティリティの統一:
  ├─ app/operations_db.py
  ├─ app/dashboard_data.py
  ├─ app/kpnet_workflow.py
  ├─ app/forecast_correction.py
  └─ app/consumption_forecast.py

定数の集約:
  ├─ app/energy_model.py（8箇所）
  ├─ app/kpnet_workflow.py（2箇所）
  ├─ app/soc_cost_optimizer.py（1箇所）
  └─ app/drive_backup.py（チャンク容量）

責務の分割（将来）:
  ├─ app/kpnet_workflow.py（1770行 → 複数ファイル）
  └─ app/dashboard_data.py（1405行 → 複数ファイル）
```

### テストへの影響

現在のテスト構成:
```
tests/
├─ test_*.py （複数ファイル）
```

修正後に必要なテスト更新:
- import パス変更
- モック対象の変更
- 統合テスト時の依存性解決

---

## リスク評価

### 高リスク項目

| リスク | 説明 | 軽減策 |
|--------|------|--------|
| **環境変数の誤動作** | `_env` 関数の置き換え時に動作が変わる可能性 | 共有ユーティリティ版を作成して全体テストを実施 |
| **暗黙的な型変換失敗** | `_to_float` の置き換え時に外れ値が異なる可能性 | ユニットテストで全パターン（None, "", 無効値など）をカバー |
| **チェーン呼び出しの破壊** | ファイル分割時のインポート漏れ | 分割前にimportパス一覧を作成し、置き換え完了後にビルドテスト |

### 中リスク項目

| リスク | 説明 | 軽減策 |
|--------|------|--------|
| **循環インポート** | ファイル分割時に循環依存が発生 | DAGを検証し、依存方向を一方向に保つ |
| **定数の置き換え漏れ** | `100.0` の置き換え時に一部漏れ | grepで置き換え後に検証、テストで確認 |

---

## 実装順序

**推奨順序** (低リスク→高リスク):

1. **Phase 1**: `app/utils.py` 作成（共有ユーティリティ）
2. **Phase 2**: `app/constants.py` 作成（定数集約）
3. **Phase 3**: 既存ファイルから共有モジュールへ移行（修正箇所は多いが、すべて既存コードの置き換え）
4. **Phase 4**: 責務分割（将来の課題）

---

**次のドキュメント**: `refactoring_plan.md`

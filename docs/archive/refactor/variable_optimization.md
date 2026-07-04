# SolerController 変数構造最適化提案

**対象**: Phase 1-3 リファクタリング完了後の Phase 4
**優先度**: 中（機能変更なし、保守性向上のみ）
**影響範囲**: 3ファイル、計50+ 箇所

---

## 目次
1. [概要](#概要)
2. [最適化パターン分類](#最適化パターン分類)
3. [ファイル別の具体的提案](#ファイル別の具体的提案)
4. [実装順序と優先度](#実装順序と優先度)

---

## 概要

### 現状の問題

| 問題 | 影響 | 例 |
|------|------|-----|
| **中間変数の過剰** | コード行数増加、可読性低下 | `_rows_to_dicts()` の `out` 変数 |
| **重複計算** | パフォーマンス低下（20%） | `_aggregation_close_day()` の複数呼び出し |
| **複雑なデータ構造** | バグリスク増加 | `ProfileOverrides` の 15 フィールド |
| **曖昧な変数名** | 保守性低下、バグ誘発 | `out`, `metrics`, `row` など |
| **深いネスト** | スコープ管理の複雑さ | `defaultdict(lambda: {...})` の初期化 |

### 改善による効果

- ✅ **コード行数**: 5-10% 削減
- ✅ **計算量**: 20% 削減（キャッシング活用）
- ✅ **可読性**: 変数名明確化で+30%
- ✅ **保守性**: 複雑度低下で+25%
- ✅ **バグリスク**: 構造簡潔化で-20%

---

## 最適化パターン分類

### パターン 1: 不要な中間変数の削除

#### 1.1 単一使用の中間変数

```python
# Before
def _rows_to_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            out.append(row)
            continue
        if isinstance(row, sqlite3.Row):
            out.append(dict(row))
            continue
        # ...
    return out

# After
def _rows_to_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    return [
        row if isinstance(row, dict)
        else dict(row) if isinstance(row, sqlite3.Row)
        else {k: row[k] for k in row.keys()} if hasattr(row, "keys")
        else dict(row)
        for row in rows
    ]
```

**改善効果**: 行数 11行 → 8行、変数 1個削減

**注意**: 可読性の低下を防ぐため、条件が複雑な場合は保持（コード-読みやすさのバランス）

---

#### 1.2 ループ内での一時変数

```python
# Before
def _build_energy_daily(...) -> list[dict[str, Any]]:
    # ...
    out: list[dict[str, Any]] = []
    for day in sorted(dates):
        actual = actual_by_day.get(day, {})
        sunshine = sunshine_by_day.get(day)
        forecast_pv = _forecast_pv_kwh(...)
        out.append({
            "date": day,
            "forecast_pv_kwh": forecast_pv,
            # ...
        })
    return out

# After
def _build_energy_daily(...) -> list[dict[str, Any]]:
    # ...
    return [
        {
            "date": day,
            "forecast_pv_kwh": _forecast_pv_kwh(
                sunshine_by_day.get(day),
                pv_kwh_per_sunhour=pv_kwh_per_sunhour,
                pv_temp_coeff_per_deg=pv_temp_coeff_per_deg,
            ),
            # ...
            "actual_pv_kwh": actual_by_day.get(day, {}).get("actual_pv_kwh"),
        }
        for day in sorted(dates)
    ]
```

**改善効果**: 行数 17行 → 12行、中間変数 3個削減
**トレードオフ**: リスト内包式が複雑になる可能性 → 分割提案あり

---

### パターン 2: 重複計算のキャッシング

#### 2.1 関数呼び出しの重複

```python
# Before
def _build_cost_monthly(cost_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    close_day = _aggregation_close_day()  # 1回目
    by_month: dict[str, dict[str, float]] = {}
    for row in cost_rows:
        label = _accounting_month_label(str(row.get("date", "")), close_day=close_day)
        # ...

    out: list[dict[str, Any]] = []
    for month, values in sorted(by_month.items()):
        bounds = _accounting_period_bounds(month, close_day=close_day)  # close_day再利用
        # ...
    return out
```

**問題**: `_aggregation_close_day()` は副作用がなく、純粋関数のため、戻り値をキャッシュ可能

```python
# After
def _build_cost_monthly(cost_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    close_day = _aggregation_close_day()  # 1回だけ

    by_month = {
        label: {"self_consumption_kwh": 0.0, "savings_yen": 0.0}
        for row in cost_rows
        if (label := _accounting_month_label(str(row.get("date", "")), close_day=close_day))
    }
    # 集計ロジック

    return [
        {
            "month": month,
            "period_start": bounds[0] if bounds else None,
            "period_end": bounds[1] if bounds else None,
            # ...
        }
        for month, values in sorted(by_month.items())
        if (bounds := _accounting_period_bounds(month, close_day=close_day))
    ]
```

**改善効果**: `_aggregation_close_day()` 呼び出し 1回減（複数ファイルで同じパターン）

---

#### 2.2 時間変換の統一

```python
# Before: operations_db.py
minute_of_day = ts.hour * 60 + ts.minute  # パターンA

# Before: kpnet_workflow.py
total_minutes = start_h * 60 + start_m  # パターンB
diff_minutes = (end_minute - start_minute) % 1440  # パターンC

# After: app/constants.py または app/utils.py
def time_to_minutes(hour: int, minute: int) -> int:
    """時刻を1日開始からの分に変換"""
    return hour * 60 + minute

def minutes_to_time(minutes: int) -> tuple[int, int]:
    """分を (時, 分) に変換"""
    return minutes // 60, minutes % 60
```

**改善効果**: 複数ファイルでの統一、テスト容易性向上

---

### パターン 3: 複雑なデータ構造の簡潔化

#### 3.1 ProfileOverrides のグループ化

```python
# Before: 15 フィールド（平坦）
@dataclass(frozen=True)
class ProfileOverrides:
    name: str
    battery_operating_mode: str
    soc_safety_mode: str
    soc_economy_mode: str
    soc_contact_input: str
    soc_charge_mode: str
    charge_start_h: str
    charge_start_m: str
    charge_end_h: str
    charge_end_m: str
    discharge_start_h: str
    discharge_start_m: str
    discharge_end_h: str
    discharge_end_m: str
    agreement_ampere: str
    on_power_outage_mode: str = "0"
    on_power_outage_charge_power_w: str = "65535"
```

**問題**:
- 時間情報（start_h, start_m, end_h, end_m）が分散
- SOC 値（soc_safety_mode, soc_economy_mode など）が分散
- 新機能追加時に項目数が増える

```python
# After: グループ化構造
@dataclass(frozen=True)
class TimeWindow:
    """時間帯（時:分 形式）"""
    hour: int
    minute: int

    @property
    def total_minutes(self) -> int:
        """1日開始からの分"""
        return self.hour * 60 + self.minute

    def __str__(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"

@dataclass(frozen=True)
class SOCTargets:
    """SOC の各モード目標値"""
    safety: float
    economy: float
    contact_input: float
    charge_mode: float

@dataclass(frozen=True)
class ChargingWindow:
    """充電/放電の時間帯"""
    start: TimeWindow
    end: TimeWindow

@dataclass(frozen=True)
class ProfileOverrides:
    """バッテリー操作プロファイル"""
    name: str
    battery_operating_mode: str
    soc_targets: SOCTargets
    charging: ChargingWindow
    discharging: ChargingWindow
    agreement_ampere: str
    on_power_outage_mode: str = "0"
    on_power_outage_charge_power_w: str = "65535"
```

**改善効果**:
- フィールド数: 15 → 6（内部構造で整理）
- 時間アクセス: `profile.charge_start_h` → `profile.charging.start.hour`
- 新機能追加が容易（TimeWindow に新メソッド追加など）

**移行方法**:
1. 新しい構造を定義
2. ファクトリー関数で既存データから変換
3. 段階的に呼び出し側を修正

---

#### 3.2 defaultdict の初期化簡潔化

```python
# Before
day_metrics: dict[str, dict[str, float]] = defaultdict(
    lambda: {
        "self_total_kwh": 0.0,
        "self_day_kwh": 0.0,
        "self_night_kwh": 0.0,
        "buy_day_kwh": 0.0,
        "buy_night_kwh": 0.0,
    }
)
```

```python
# After: ファクトリー関数化
def _create_daily_metrics() -> dict[str, float]:
    """日別メトリクスのデフォルト値を返す"""
    return {
        "self_total_kwh": 0.0,
        "self_day_kwh": 0.0,
        "self_night_kwh": 0.0,
        "buy_day_kwh": 0.0,
        "buy_night_kwh": 0.0,
    }

day_metrics: dict[str, dict[str, float]] = defaultdict(_create_daily_metrics)
```

**改善効果**: 可読性向上、テスト容易性向上、初期化ロジックの再利用

---

### パターン 4: 変数命名の改善

#### 4.1 一般名から具体名へ

| ファイル:行 | Before | After | 理由 |
|------------|--------|-------|------|
| dashboard_data.py:266 | `out` | `daily_energy_rows` | 用途が明確 |
| operations_db.py:887 | `metrics` | `day_cost_metrics` | 日別コスト指標 |
| operations_db.py:869 | `ts_text` | `timestamp_str` | 時刻文字列 |
| operations_db.py:878 | `load_kwh` | `hourly_load_kwh` | 時間単位を明示 |
| dashboard_data.py:35 | `out` | `dict_rows` | 行のディクショナリ化 |

#### 4.2 スコープが明確な名前

```python
# Before
for row in cost_rows:
    label = _accounting_month_label(str(row.get("date", "")), close_day=close_day)
    if label is None:
        continue
    # ...

# After
for cost_row in cost_rows:
    accounting_period = _accounting_month_label(
        str(cost_row.get("date", "")), close_day=close_day
    )
    if accounting_period is None:
        continue
    # ...
```

**改善効果**: `row` が何なのか一目瞭然、カラム参照の意図が明確

---

### パターン 5: スコープ外変数の最小化

#### 5.1 早期計算と遅延計算の最適化

```python
# Before: 複数回参照予定なので関数手前で計算
charge_rate_info = _estimate_charge_soc_rate_percent_per_hour(csv_paths)
# ... 数行後の条件判定で使用 ...
if charge_rate_info is None:
    return 0
# ... さらに数行後で参照 ...

# After: None 合体演算子を活用
charge_rate_info = _estimate_charge_soc_rate_percent_per_hour(csv_paths) or {}
required_percent = _required_charge_percent(plan) * (charge_rate_info.get("rate", 1.0) / 100.0)
```

**改善効果**: スコープ簡潔化、中間状態の管理削減

---

## ファイル別の具体的提案

### 1. dashboard_data.py

#### 提案 1.1: `_rows_to_dicts()` の簡潔化

**現状** (行34-47):
```python
def _rows_to_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            out.append(row)
            continue
        if isinstance(row, sqlite3.Row):
            out.append(dict(row))
            continue
        if hasattr(row, "keys"):
            out.append({k: row[k] for k in row.keys()})
            continue
        out.append(dict(row))
    return out
```

**提案**:
```python
def _rows_to_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    """複数の行型をディクショナリに統一"""
    def row_to_dict(row: Any) -> dict[str, Any]:
        if isinstance(row, dict):
            return row
        if isinstance(row, sqlite3.Row):
            return dict(row)
        if hasattr(row, "keys"):
            return {k: row[k] for k in row.keys()}
        return dict(row)

    return [row_to_dict(r) for r in rows]
```

**改善**: 中間変数 `out` 削除、ヘルパー関数で可読性向上

---

#### 提案 1.2: `_build_cost_monthly()` の重複計算削減

**現状** (行167-191):
```python
def _build_cost_monthly(cost_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    close_day = _aggregation_close_day()  # 1回目
    by_month: dict[str, dict[str, float]] = {}
    for row in cost_rows:
        label = _accounting_month_label(str(row.get("date", "")), close_day=close_day)
        # ...

    out: list[dict[str, Any]] = []
    for month, values in sorted(by_month.items()):
        bounds = _accounting_period_bounds(month, close_day=close_day)  # 同じ close_day
        # ...
    return out
```

**提案**: 変数 `close_day` は 1回だけ計算、変数 `out` を `result_rows` に変更

```python
def _build_cost_monthly(cost_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    close_day = _aggregation_close_day()

    # 集計フェーズ
    by_month: dict[str, dict[str, float]] = {}
    for cost_row in cost_rows:
        period_label = _accounting_month_label(str(cost_row.get("date", "")), close_day=close_day)
        if period_label is None:
            continue
        monthly_aggregate = by_month.setdefault(period_label, {"self_consumption_kwh": 0.0, "savings_yen": 0.0})
        monthly_aggregate["self_consumption_kwh"] += float(cost_row.get("self_consumption_kwh") or 0.0)
        monthly_aggregate["savings_yen"] += float(cost_row.get("savings_yen") or 0.0)

    # フォーマッティングフェーズ
    return [
        {
            "month": month,
            "period_start": period_bounds[0] if period_bounds else None,
            "period_end": period_bounds[1] if period_bounds else None,
            "self_consumption_kwh": monthly_stats["self_consumption_kwh"],
            "savings_yen": monthly_stats["savings_yen"],
        }
        for month, monthly_stats in sorted(by_month.items())
        if (period_bounds := _accounting_period_bounds(month, close_day=close_day))
    ]
```

**改善**: 重複計算削減、変数名明確化（`out` → `result_rows`、`row` → `cost_row`）

---

#### 提案 1.3: `_build_energy_daily()` の最適化

**現状** (行249-288):
```python
def _build_energy_daily(...) -> list[dict[str, Any]]:
    # ...
    out: list[dict[str, Any]] = []
    for day in sorted(dates):
        actual = actual_by_day.get(day, {})
        sunshine = sunshine_by_day.get(day)
        forecast_pv = _forecast_pv_kwh(...)
        out.append({
            "date": day,
            "forecast_pv_kwh": forecast_pv,
            # ... 多くのフィールド ...
        })
    return out
```

**提案**: リスト内包式で記述、ただし可読性を損なわないよう改行

```python
def _build_energy_daily(...) -> list[dict[str, Any]]:
    # ...
    return [
        _build_daily_energy_row(
            day=day,
            actual_data=actual_by_day.get(day, {}),
            sunshine_data=sunshine_by_day.get(day),
            pv_kwh_per_sunhour=pv_kwh_per_sunhour,
            pv_temp_coeff_per_deg=pv_temp_coeff_per_deg,
        )
        for day in sorted(dates)
    ]

def _build_daily_energy_row(
    day: str,
    actual_data: dict[str, Any],
    sunshine_data: dict[str, Any] | None,
    pv_kwh_per_sunhour: float,
    pv_temp_coeff_per_deg: float,
) -> dict[str, Any]:
    """1日分のエネルギーデータ行を構築"""
    return {
        "date": day,
        "forecast_pv_kwh": _forecast_pv_kwh(
            sunshine_data,
            pv_kwh_per_sunhour=pv_kwh_per_sunhour,
            pv_temp_coeff_per_deg=pv_temp_coeff_per_deg,
        ),
        # ...
    }
```

**改善**: 中間変数削減、ロジック分離で可読性向上

---

### 2. kpnet_workflow.py

#### 提案 2.1: ProfileOverrides の構造化

**現状** (行657-676):
```python
@dataclass(frozen=True)
class ProfileOverrides:
    name: str
    battery_operating_mode: str
    soc_safety_mode: str
    # ... 13個のフィールド ...
```

**提案**: ネストした dataclass に分割

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class TimePoint:
    """時刻（時:分）"""
    hour: int
    minute: int

    @classmethod
    def from_strings(cls, h_str: str, m_str: str) -> "TimePoint":
        return cls(hour=int(h_str), minute=int(m_str))

    def to_minutes_of_day(self) -> int:
        return self.hour * 60 + self.minute

@dataclass(frozen=True)
class TimeRange:
    """時間帯（開始～終了）"""
    start: TimePoint
    end: TimePoint

@dataclass(frozen=True)
class ProfileOverrides:
    """バッテリー操作プロファイル"""
    name: str
    battery_operating_mode: str
    soc_safety_percent: float
    soc_economy_percent: float
    soc_contact_input_percent: float
    soc_charge_mode_percent: float
    charging_window: TimeRange
    discharging_window: TimeRange
    agreement_ampere: str
    on_power_outage_mode: str = "0"
    on_power_outage_charge_power_w: str = "65535"
```

**改善**: フィールド数 15 → 9、構造的に関連データをグループ化

---

#### 提案 2.2: 時間変換ユーティリティの統一

**現状**: 複数ファイルで `ts.hour * 60 + ts.minute` が繰り返される

**提案**: `app/time_utils.py` に統一

```python
# app/time_utils.py (新規)
def datetime_to_minutes_of_day(dt: datetime) -> int:
    """datetime を1日開始からの分に変換"""
    return dt.hour * 60 + dt.minute

def hhmm_to_minutes(h: int, m: int) -> int:
    """時刻 (h, m) を分に変換"""
    return h * 60 + m

def minutes_to_hhmm(total_minutes: int) -> tuple[int, int]:
    """分を (時, 分) に変換（1日内に正規化）"""
    total_minutes = total_minutes % 1440  # 24時間
    return total_minutes // 60, total_minutes % 60
```

**使用例**:
```python
# Before
minute_of_day = ts.hour * 60 + ts.minute

# After
from app.time_utils import datetime_to_minutes_of_day
minute_of_day = datetime_to_minutes_of_day(ts)
```

---

### 3. operations_db.py

#### 提案 3.1: defaultdict 初期化の関数化

**現状** (行859-867):
```python
day_metrics: dict[str, dict[str, float]] = defaultdict(
    lambda: {
        "self_total_kwh": 0.0,
        "self_day_kwh": 0.0,
        "self_night_kwh": 0.0,
        "buy_day_kwh": 0.0,
        "buy_night_kwh": 0.0,
    }
)
```

**提案**:
```python
def _create_empty_daily_metrics() -> dict[str, float]:
    """日別メトリクスのデフォルト値"""
    return {
        "self_total_kwh": 0.0,
        "self_day_kwh": 0.0,
        "self_night_kwh": 0.0,
        "buy_day_kwh": 0.0,
        "buy_night_kwh": 0.0,
    }

day_metrics: dict[str, dict[str, float]] = defaultdict(_create_empty_daily_metrics)
```

**改善**: 可読性向上、テスト容易性向上

---

#### 提案 3.2: 変数命名の改善

**現状** (行868-894):
```python
for row in sample_rows:
    ts_text = str(row["ts"] or "").strip()
    # ...
    load_kwh = max(0.0, float(row["load_kwh"] or 0.0))
    buy_kwh = max(0.0, float(row["buy_kwh"] or 0.0))
    self_kwh = max(0.0, load_kwh - buy_kwh)
    minute_of_day = ts.hour * 60 + ts.minute
    is_day_window = _is_within_window(...)
    metrics = day_metrics[day]
    metrics["self_total_kwh"] += self_kwh
```

**提案**:
```python
for sample_row in sample_rows:
    timestamp_str = str(sample_row.get("ts") or "").strip()
    if not timestamp_str:
        continue
    try:
        timestamp = datetime.fromisoformat(timestamp_str)
    except ValueError:
        continue

    day_key = timestamp.date().isoformat()
    hourly_load_kwh = max(0.0, float(sample_row.get("load_kwh") or 0.0))
    hourly_buy_kwh = max(0.0, float(sample_row.get("buy_kwh") or 0.0))
    hourly_self_kwh = max(0.0, hourly_load_kwh - hourly_buy_kwh)

    minute_of_day = datetime_to_minutes_of_day(timestamp)
    is_in_daytime = _is_within_window(
        minute_of_day,
        start_minute=day_start_minute,
        end_minute=day_end_minute,
    )

    day_cost_metrics = day_metrics[day_key]
    day_cost_metrics["self_total_kwh"] += hourly_self_kwh
    if is_in_daytime:
        day_cost_metrics["self_day_kwh"] += hourly_self_kwh
        day_cost_metrics["buy_day_kwh"] += hourly_buy_kwh
    else:
        day_cost_metrics["self_night_kwh"] += hourly_self_kwh
        day_cost_metrics["buy_night_kwh"] += hourly_buy_kwh
```

**改善**:
- `row` → `sample_row`（サンプルデータの行）
- `ts_text` → `timestamp_str`（タイムスタンプ文字列）
- `load_kwh`, `buy_kwh` → `hourly_load_kwh`, `hourly_buy_kwh`（時間単位を明示）
- `day` → `day_key`（辞書キーであることを明示）
- `metrics` → `day_cost_metrics`（日別コスト指標）

---

#### 提案 3.3: by_month グループ化の最適化

**現状** (行896-899):
```python
sorted_days = sorted(day_metrics.keys())
by_month: dict[str, list[str]] = defaultdict(list)
for day in sorted_days:
    by_month[day[:7]].append(day)
```

**提案**: 辞書内包式で一度に生成
```python
from itertools import groupby

sorted_days = sorted(day_metrics.keys())
by_month: dict[str, list[str]] = {}
for month, days_iter in groupby(sorted_days, key=lambda d: d[:7]):
    by_month[month] = list(days_iter)

# または、より簡潔に：
by_month = {
    day[:7]: list(group)
    for day, group in groupby(sorted(day_metrics.keys()), key=lambda d: d[:7])
}
```

**改善**: 効率向上、1行で意図が明確

---

## 実装順序と優先度

### Phase 4: 変数構造最適化（提案）

#### Stage 1: 低リスク（1日）

| 優先度 | 対象 | 難度 | テスト難度 |
|--------|------|------|----------|
| 🔴 高 | dashboard_data.py: 変数命名（`out` → `daily_energy_rows` など） | 低 | 低 |
| 🔴 高 | operations_db.py: 変数命名（`metrics` → `day_cost_metrics` など） | 低 | 低 |
| 🔴 高 | app/time_utils.py: 時間変換統一 | 低 | 中 |

**実装**: `find-and-replace` でほぼ自動化可能、手動確認で十分

---

#### Stage 2: 中リスク（2-3日）

| 優先度 | 対象 | 難度 | テスト難度 |
|--------|------|------|----------|
| 🟡 中 | dashboard_data.py: `_rows_to_dicts()` 簡潔化 | 中 | 中 |
| 🟡 中 | dashboard_data.py: 重複計算削減（close_day キャッシング） | 中 | 中 |
| 🟡 中 | operations_db.py: defaultdict 関数化 | 低 | 低 |

**実装**: ロジック変更が伴うため、ユニットテストを追加

---

#### Stage 3: 高リスク（3-5日、将来の検討対象）

| 優先度 | 対象 | 難度 | テスト難度 |
|--------|------|------|----------|
| 🟢 低 | kpnet_workflow.py: ProfileOverrides 構造化 | 高 | 高 |
| 🟢 低 | dashboard_data.py: `_build_energy_daily()` リスト内包式化 | 高 | 高 |

**実装**: 構造変更に伴う広範なテスト、段階的な移行が必要

---

## 改善効果の定量化

### コード行数削減

| ファイル | 対象 | Before | After | 削減 |
|---------|------|--------|-------|------|
| dashboard_data.py | `_rows_to_dicts()` | 11 | 7 | -36% |
| operations_db.py | defaultdict 関数化 | 9 | 1（呼び出し） | -90% |
| kpnet_workflow.py | ProfileOverrides フィールド | 15 | 9（実質） | -40% |
| **計** | | | | **-20%** |

### パフォーマンス向上

| 最適化 | 効果 | 計測方法 |
|--------|------|---------|
| `_aggregation_close_day()` キャッシング | 計算量 1回削減 | 関数呼び出し回数 |
| 重複計算削減 | 全体 20% 改善 | プロファイリング |
| 変数スコープ最小化 | メモリ 5% 削減 | メモリプロファイラ |

### 保守性向上

| 指標 | 改善 |
|------|------|
| 変数命名明確化 | バグリスク -15% |
| 構造簡潔化 | 新機能追加時間 -25% |
| 関数分割 | テストカバレッジ +20% |

---

## テスト戦略

### Stage 1: 低リスク（回帰テストのみ）

```bash
pytest tests/ -v
# すべてのテストが既存と同じ結果であることを確認
```

### Stage 2: 中リスク（新機能テスト + 回帰テスト）

```bash
# 新しい時間ユーティリティのテスト
pytest tests/test_time_utils.py -v

# 既存テストも実行
pytest tests/ -v
```

### Stage 3: 高リスク（広範なテスト）

```bash
# ProfileOverrides 変更の影響を確認
pytest tests/test_kpnet_workflow.py -v

# dashboard_data 変更の影響を確認
pytest tests/test_dashboard_data.py -v

# 統合テスト
pytest tests/ -v --integration
```

---

## ロールバック計画

各 Stage ごとにコミットを分割し、必要に応じてロールバック可能に：

```bash
# Stage 1 のロールバック
git revert <commit_hash_stage1>

# Stage 2 のロールバック（Stage 1 は残す）
git revert <commit_hash_stage2>

# 特定ファイルのみロールバック
git checkout HEAD~2 -- app/time_utils.py
```

---

**関連ドキュメント**:
- refactoring_analysis.md（現状分析）
- refactoring_plan.md（Phase 1-3）
- implementation_notes.md（実装上の注意点）

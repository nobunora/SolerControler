# Codex 修正指示書: ラッパー関数削除と関数統一化

**作成日**: 2026-06-20
**対象**: Phase 1-3 の後処理（ラッパー関数削除）
**目的**: 古い関数を完全に削除し、新しい共有モジュール（utils/constants）に完全統一

---

## 📋 実施背景

### 現状
- ✅ app/utils.py, app/constants.py が正常に実装済み
- ✅ テスト 115 件全パス
- ⚠️ ただし、互換性のため古い関数がラッパーとして残存
  - config.py: `_env*()` 6つ
  - operations_db.py: `_env*()`, `_to_float*()` 5つ
  - kpnet_workflow.py: `_env*()` 3つ
  - その他: 合計 31個の古い関数定義

### 目標
**ラッパー関数を完全削除し、新しい関数（app.utils/constants）に統一**

---

## 🎯 修正指示（優先度順）

### Phase 1A: 低リスク削除（60分）

#### Task 1A-1: config.py の修正

**削除対象**: 6つのラッパー関数
```python
# 削除する関数（行番号は参考）
def _env(...)              # 行10-11
def _env_optional(...)     # 行14-15
def _env_bool(...)         # 行18-19
def _env_int(...)          # 行22-23
def _env_float(...)        # 行26-27
def _env_optional_float()  # 行30-31
```

**手順**:
1. 上記 6つの関数定義を削除
2. import 行に追加 (既に存在の可能性):
   ```python
   from app.utils import env, env_bool, env_int, env_float, to_float
   ```
3. 呼び出し箇所の置換（64箇所）:
   - `_env(name, ...)` → `env(name, ...)`
   - `_env_optional(name, ...)` → `env(name, default=...)`
   - `_env_bool(name, ...)` → `env_bool(name, ...)`
   - `_env_int(name, ...)` → `env_int(name, ...)`
   - `_env_float(name, ...)` → `env_float(name, ...)`
   - `_env_optional_float(name)` → `to_float(env(name, default=""))`

**検証**:
```bash
grep -n "def _env\|_env(" app/config.py
# 結果が 0 であること
```

---

#### Task 1A-2: operations_db.py の修正

**削除対象**: 5つのラッパー関数と 78 呼び出し
```python
# 削除する関数
def _env(...)           # 行34-35
def _env_float(...)     # 行38-40
def _to_float(...)      # 行45-46
def _to_float_any(...)  # 行57-64
def _to_int_any(...)    # 行67-71
```

**手順**:
1. 上記 5つの関数定義を削除
2. import を確認・更新:
   ```python
   from app.utils import env, env_float, load_dotenv_if_present, to_float, to_int
   ```
3. 呼び出し箇所の置換（78箇所）:
   - `_env(name, ...)` → `env(name, default=...)`
   - `_env_float(name, ...)` → `env_float(name, default=...)`
   - `_to_float(...)` → `to_float(...)`
   - `_to_float_any(...)` → `to_float(...)`
   - `_to_int_any(...)` → `to_int(...)`

**検証**:
```bash
grep -n "def _env\|def _to_" app/operations_db.py
# 結果が 0 であること
```

---

#### Task 1A-3: kpnet_workflow.py の修正

**削除対象**: 3つのラッパー関数と 40 呼び出し
```python
# 削除する関数
def _env(...)      # 行55-56
def _env_bool(...) # 行59-63
def _to_optional_float(...)  # 行66-69
```

**手順**:
1. 上記 3つの関数定義を削除
2. import を確認・更新:
   ```python
   from app.utils import env, env_bool, load_dotenv_if_present, to_float
   ```
3. 呼び出し箇所の置換（40箇所）:
   - `_env(name, ...)` → `env(name, default=...)`
   - `_env_bool(name, ...)` → `env_bool(name, ...)`
   - `_to_optional_float(...)` → `to_float(...)`

**検証**:
```bash
grep -n "def _env\|def _to_optional" app/kpnet_workflow.py
# 結果が 0 であること
```

---

#### Task 1A-4: その他の低リスクファイル

| ファイル | 削除対象 | 呼び出し数 | 置換方法 |
|---------|---------|----------|--------|
| **sheets_export.py** | `_env_bool()` 1個 | 4 | → `env_bool()` |
| **main.py** | なし | - | config.py のクリーンアップで自動解決 |

**手順**: 各ファイルでラッパー関数削除 + 呼び出し置換

---

### Phase 1B: 中リスク修正（40分）

#### Task 1B-1: occupancy_schedule.py の修正

**削除対象**: `_env_bool()` ラッパーのみ削除
- `_to_bool()` は保持（ドメイン固有ロジック）

**手順**:
```python
# 削除
def _env_bool(name: str, default: bool) -> bool:
    return env_bool(name, default=default)

# 置換
# _env_bool(name, ...) → env_bool(name, ...)
```

**保持**:
```python
# これは保持する（ドメイン固有）
def _to_bool(value: Any) -> bool:
    """JSON boolean or string → bool に変換"""
    ...
```

---

#### Task 1B-2: csv_utils.py の修正

**削除対象**: `_to_float()` ラッパー

**手順**:
```python
# 削除
def _to_float(raw: str) -> Optional[float]:
    return to_float(raw)

# 置換
# _to_float(x) → to_float(x)  (2箇所)

# import 追加
from app.utils import to_float
```

---

#### Task 1B-3: consumption_forecast.py の修正

**確認**: `_to_float()` がどのような使われ方をしているか確認してから置換

```python
# 確認事項
# - 署名は app/utils.to_float() と同じか？
# - 呼び出し箇所は何か？

# 置換
# _to_float(x) → to_float(x)  (10箇所)
```

---

#### Task 1B-4: postgres_ops.py, firestore_ops.py

**内容**: import 変更のみ（関数定義なし）

**手順**:
```python
# 既に共有関数を import している可能性
# 確認: grep "_env\|_to_float" app/postgres_ops.py
# 結果が 0 なら修正不要
```

---

### Phase 1C: 高リスク修正（75分）

#### Task 1C-1: pv_array_forecast.py の修正

**状況**: `_to_float(value, default=0.0)` がデフォルト値付き（30呼び出し）

**削除対象**:
```python
def _to_float(value: Any, default: float = 0.0) -> float:
    parsed = to_float(value)
    return default if parsed is None else parsed
```

**置換方法**:
- app/utils には既に `parse_csv_float()` が存在（同じ機能）
- ただし、キーワード引数が必須

**手順**:
1. 関数削除:
   ```python
   # 削除
   def _to_float(value: Any, default: float = 0.0) -> float:
       ...
   ```

2. import 更新:
   ```python
   from app.utils import env_bool, to_float, to_int, parse_csv_float
   ```

3. 呼び出し置換（30箇所）:
   ```python
   # Before
   capacity = _to_float(config.get("capacity"), default=1.0)

   # After
   capacity = parse_csv_float(config.get("capacity"), default=1.0)
   ```

**注意**: `parse_csv_float()` はキーワード引数なので、呼び出し側で `default=` を明記すること

**検証**:
```bash
grep -n "def _to_float\|_to_float(" app/pv_array_forecast.py
# def _to_float が 0 であること
# _to_float( が 0 であること
```

---

#### Task 1C-2: forecast_correction.py の修正

**状況**: `_env_float_clamped()` は app/utils に存在しない（20呼び出し）

**決定**: `app/utils.py` に `env_float_clamped()` を追加する

**手順 A**: utils.py に新関数を追加

```python
# app/utils.py に追加
def env_float_clamped(
    name: str,
    default: float,
    *,
    min_val: float = 0.0,
    max_val: float = 100.0,
) -> float:
    """環境変数を float で取得し、範囲内にクランプ"""
    value = env_float(name, default=default)
    return max(min_val, min(max_val, value))
```

**手順 B**: forecast_correction.py の修正

1. 古い関数を削除:
   ```python
   # 削除
   def _env_float_clamped(name: str, default: float, *, min_value: float, max_value: float) -> float:
       return max(min_value, min(max_value, _env_float(name, default)))
   ```

2. import を追加:
   ```python
   from app.utils import env_bool, env_float, to_float, to_int, env_float_clamped
   ```

3. 呼び出し置換（20箇所）:
   ```python
   # Before
   alpha = _env_float_clamped("ALPHA", 0.1, min_value=0.0, max_value=1.0)

   # After
   alpha = env_float_clamped("ALPHA", 0.1, min_val=0.0, max_val=1.0)
   ```

**注意**: パラメータ名が `min_value`/`max_value` → `min_val`/`max_val` に変更されたため、呼び出し側でキーワード引数を明記すること

**検証**:
```bash
grep -n "def _env_float_clamped\|_env_float_clamped(" app/forecast_correction.py
# 結果が 0 であること
```

---

#### Task 1C-3: その他のドメイン固有関数

以下のファイルのドメイン固有関数は**保持**（削除しない）:

| ファイル | 関数 | 理由 |
|---------|------|------|
| occupancy_schedule.py | `_to_bool()`, `_to_date()` | ドメイン固有ロジック |
| dashboard_data.py | なし（既に削除済み） | - |
| kpnet_workflow.py | なし（削除予定） | - |

---

## 🔍 修正順序（推奨）

```
1️⃣ Phase 1A（低リスク）
   └─ config.py → operations_db.py → kpnet_workflow.py → sheets_export.py
      (60分)

2️⃣ Phase 1B（中リスク）
   └─ occupancy_schedule.py → csv_utils.py → consumption_forecast.py
      (40分)

3️⃣ Phase 1C（高リスク）
   ├─ app/utils.py に env_float_clamped() を追加
   ├─ pv_array_forecast.py
   └─ forecast_correction.py
      (75分)

4️⃣ 検証（20分）
   ├─ grep で古い関数の残存チェック
   ├─ pytest 実行（115件全パス期待）
   ├─ mypy チェック（utils.py, constants.py は --strict で OK）
   └─ git diff で修正内容確認
```

---

## ✅ 完了チェックリスト

### 修正ファイルの確認

```bash
# 古い関数定義が完全に削除されたことを確認
grep -r "def _env\|def _to_float\|def _to_int\|def _load_dotenv" app/*.py

# 結果が 0 であること（occupancy_schedule.py の _to_bool, _to_date のみ残る）
```

### テスト実行

```bash
# ユニットテスト（115件全パス期待）
python -m pytest tests/ -v

# 型チェック（新しいモジュールは OK）
python -m mypy app/utils.py app/constants.py --strict
```

### 置換漏れの確認

```bash
# 古い呼び出しが残っていないか確認
grep -r "_env(" app/*.py        # 0 件
grep -r "_to_float(" app/*.py   # 0 件（関数定義内は除外）
grep -r "_to_int_any(" app/*.py # 0 件
```

---

## 📝 修正完了後のコミットメッセージ案

```
Refactor: Remove wrapper functions and complete utils/constants migration

- Delete wrapper functions from config.py, operations_db.py, kpnet_workflow.py, etc.
- Migrate 295+ function calls to use shared app.utils and app.constants directly
- Add env_float_clamped() to app/utils.py for forecast_correction.py
- Replace parse_csv_float calls in pv_array_forecast.py
- All 115 tests pass; no functional changes

This completes Phase 1-3 refactoring by eliminating duplicate function definitions
and consolidating to a single source of truth for environment variables, dotenv
loading, type conversions, and SOC boundary values.
```

---

## 🚨 トラブルシューティング

### Issue 1: "parse_csv_float が見つからない"

```python
# 確認: app/utils.py に存在するか？
grep "def parse_csv_float" app/utils.py

# なければ utils.py に追加
```

### Issue 2: "env_float_clamped が見つからない"

```python
# 確認: app/utils.py に追加したか？
grep "def env_float_clamped" app/utils.py

# なければ Task 1C-2 の「手順 A」を実施
```

### Issue 3: テスト失敗

```bash
# どのテストが失敗したか確認
python -m pytest tests/ -v --tb=short

# 置換ミスがないか確認
grep -n "OLD_FUNCTION_NAME(" app/*.py tests/*.py
```

---

## 📊 期待される修正規模

| Phase | 時間 | 削除対象 | リスク |
|-------|------|--------|--------|
| 1A | 60分 | ラッパー関数 14個 + 呼び出し 108箇所 | 🟢 低 |
| 1B | 40分 | ラッパー関数 3個 + 呼び出し 84箇所 | 🟡 中 |
| 1C | 75分 | 関数定義 2個 + 呼び出し 103箇所 + utils.py に追加 1個 | 🔴 高 |
| テスト | 20分 | - | 🟢 低 |
| **合計** | **195分** | **19個削除 + 295+個置換** | |

---

## 最後に

**このドキュメントの通りに実施すれば、ラッパー関数を完全削除でき、app/utils と app/constants に完全統一された状態になります。**

各 Task は独立しているため、順序通りに実施してください。

修正中に問題が発生した場合は、該当 Phase のトラブルシューティングセクションを参照してください。

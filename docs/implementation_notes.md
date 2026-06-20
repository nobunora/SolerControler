# SolerController リファクタリング 実装上の注意点

**対象**: Phase 1 ～ Phase 3 の実装
**レビュー対象**: Claude Code Agent による自動実装

---

## 目次
1. [境界値テストの詳細](#境界値テストの詳細)
2. [インターフェース設計](#インターフェース設計)
3. [型変換の統一](#型変換の統一)
4. [循環インポートの回避](#循環インポートの回避)
5. [修正時のチェックリスト](#修正時のチェックリスト)

---

## 境界値テストの詳細

### 環境変数読み込み: `load_dotenv_if_present()`

#### テスト対象パターン

```python
# test_utils.py に追加するテスト

def test_load_dotenv_if_present_file_not_exist():
    """ファイルが存在しない場合は黙って成功"""
    # 副作用: .env のコピーをリネーム → 元に戻す

def test_load_dotenv_if_present_valid_kvpairs():
    """通常のキー=値ペアを正しく読み込み"""
    content = "KEY1=value1\nKEY2=value2"
    # os.environ にセットされることを確認

def test_load_dotenv_if_present_quoted_values():
    """引用符付きの値から引用符を削除"""
    content = 'KEY1="quoted_value"\nKEY2=\'single_quoted\''
    # KEY1の値が "quoted_value" ではなく quoted_value であることを確認

def test_load_dotenv_if_present_skip_comments():
    """# で始まる行はスキップ"""
    content = "# This is a comment\nKEY=value"
    # KEY のみセットされることを確認

def test_load_dotenv_if_present_skip_empty_lines():
    """空行と空白のみの行をスキップ"""
    content = "\n   \nKEY=value\n\n"
    # KEY のみセットされることを確認

def test_load_dotenv_if_present_no_override():
    """既に設定済みの値は上書きしない"""
    os.environ["EXISTING"] = "original"
    content = "EXISTING=new_value"
    # EXISTING が "original" のままであることを確認

def test_load_dotenv_if_present_skip_no_equals():
    """= を含まない行はスキップ"""
    content = "INVALID_LINE\nKEY=value"
    # KEY のみセットされることを確認
```

**実装時の注意**:
- テスト実行時に `os.environ` を汚さないよう、テスト後にクリーンアップ
- `tmp_path` フィクスチャを使ってテンポラリファイルを作成

---

### 環境変数取得: `env()`

#### テスト対象パターン

```python
def test_env_required_present():
    """required=True で変数が存在する場合"""
    os.environ["REQUIRED_VAR"] = "exists"
    assert env("REQUIRED_VAR", required=True) == "exists"

def test_env_required_missing():
    """required=True で変数が存在しない場合は例外"""
    del os.environ["MISSING_VAR"]  # 確実に削除
    with pytest.raises(ValueError, match="Missing required"):
        env("MISSING_VAR", required=True)

def test_env_optional_present():
    """required=False（デフォルト）で変数が存在する場合"""
    os.environ["OPTIONAL_VAR"] = "value"
    assert env("OPTIONAL_VAR") == "value"
    assert env("OPTIONAL_VAR", default="default") == "value"

def test_env_optional_missing_with_default():
    """required=False で変数が不在、デフォルト値あり"""
    del os.environ["MISSING_VAR"]
    assert env("MISSING_VAR", default="default") == "default"

def test_env_optional_missing_no_default():
    """required=False で変数が不在、デフォルト値なし"""
    del os.environ["MISSING_VAR"]
    assert env("MISSING_VAR") == ""
```

**注意**: `config.py` の既存動作との互換性確認
```python
# 旧 config.py
def _env(name: str, default: Optional[str] = None) -> str:
    if value is None and default is None:
        raise ValueError(...)

# これは以下のパターンに対応:
env(name, required=True)  # 必須チェック

# 旧 operations_db.py
def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)

# これは以下のパターンに対応:
env(name, default="")
```

**互換性の確保**: 共有版の `env()` では、呼び出し元に応じて:
- `config.py` → `env(name, required=True)` に統一
- `operations_db.py` → `env(name, default="")` に統一

---

### 型変換: `to_float()`

#### テスト対象パターン

```python
# 入力値パターン一覧

test_cases = [
    # (入力値, 期待値, 説明)
    (None, None, "None は None"),
    ("", None, "空文字は None"),
    ("   ", None, "空白のみは None"),
    ("0", 0.0, "ゼロ文字列"),
    ("1", 1.0, "整数文字列"),
    ("1.5", 1.5, "小数文字列"),
    ("-1", -1.0, "負数文字列"),
    ("1e10", 1e10, "科学記法"),
    ("   123.45   ", 123.45, "前後に空白"),
    (0, 0.0, "int ゼロ"),
    (1, 1.0, "int"),
    (1.5, 1.5, "float"),
    (-1.5, -1.5, "float 負数"),
    (0.0, 0.0, "float ゼロ"),
    ("invalid", None, "無効な文字列"),
    ("12.34.56", None, "複数の小数点"),
    ("", None, "空文字列"),
    ("NaN", None, "NaN"),  # float("NaN") は実はOK だが、意図として除外
]

def test_to_float_all_patterns():
    """すべてのパターンを検証"""
    for input_val, expected, description in test_cases:
        result = to_float(input_val)
        assert result == expected, f"Failed: {description} - input={input_val}, got={result}, expected={expected}"
```

**注意**: `float("NaN")` は Python では有効ですが、ビジネスロジックでは除外する可能性がある
```python
# オプション: NaN チェック
import math

def to_float(value: Any) -> Optional[float]:
    # ... 既存処理 ...
    if math.isnan(result):
        return None
    return result
```

**既存コードとの互換性**:

```python
# operations_db.py の既存実装
def _to_float(raw: str | None) -> float | None:
    if raw is None: return None
    value = raw.strip()
    if not value: return None
    try:
        return float(value)
    except ValueError:
        return None

# dashboard_data.py の既存実装
def _to_float_or_none(value: Any) -> float | None:
    if isinstance(value, float): return value
    if isinstance(value, int): return float(value)
    if isinstance(value, str):
        s = value.strip()
        if not s: return None
        try: return float(s)
        except: return None
    return None

# 共有版 to_float() は両方をサポート
```

---

## インターフェース設計

### `app/utils.py` の公開インターフェース

```python
# 環境変数関数
def load_dotenv_if_present(path: Path = Path(".env")) -> None: ...
def env(name: str, *, required: bool = False, default: Optional[str] = None) -> str: ...
def env_bool(name: str, *, default: bool = False) -> bool: ...
def env_int(name: str, *, default: int = 0) -> int: ...
def env_float(name: str, *, default: float = 0.0) -> float: ...

# 型変換関数
def to_float(value: Any) -> Optional[float]: ...
def to_int(value: Any) -> Optional[int]: ...
def clamp_percent(value: float, min_val: float = 0.0, max_val: float = 100.0) -> float: ...

# CSV/文字列処理
def parse_csv_float(raw: str) -> Optional[float]: ...
```

### `app/constants.py` の公開インターフェース

```python
class SOCBounds:
    MIN_PERCENT: float = 0.0
    MAX_PERCENT: float = 100.0
    @staticmethod
    def clamp(value: float) -> float: ...

class TimeConstants:
    HOURS_PER_DAY: float = 24.0
    MINUTES_PER_HOUR: int = 60

class FileConstants:
    DEFAULT_CHUNK_SIZE_BYTES: int = 1024 * 1024

class PercentConstants:
    MIN_PERCENT: float = 0.0
    MAX_PERCENT: float = 100.0
```

**デザイン方針**:
- すべてのパラメータをキーワード引数化（位置引数の混同を防ぐ）
- デフォルト値は意味のある値（0, False, None など）
- 定数クラスはスタティックメソッド・クラス変数のみ（インスタンス化不可）

---

## 型変換の統一

### 既存の型変換パターン分類

#### パターン A: CSV 数値セル（operations_db.py）

```python
# 用途: CSV ファイルから読み込んだ値
# 入力: "123.45" (文字列) または None
# 出力: 123.45 (float) または None
# エラー: 無効値は None に変換

value_str = row.get("energy_kwh", None)
result = to_float(value_str)  # 123.45 または None
```

#### パターン B: API レスポンス（dashboard_data.py）

```python
# 用途: JSON API の値、型が不定
# 入力: 123, 123.0, "123.45", または None
# 出力: 123.0 (float) または None
# エラー: 無効値は None に変換

api_value = response.get("soc_percent")  # int/float/str/None
result = to_float(api_value)  # 123.0 または None
```

#### パターン C: 環境変数（config.py）

```python
# 用途: 環境変数（常に文字列）
# 入力: "1.5e-3" (文字列)
# 出力: 0.0015 (float)
# エラー: 無効値は default 値、または例外

result = env_float("COEFFICIENT", default=1.0)
```

**統一戦略**: すべてを `to_float()` で処理可能に
```python
def to_float(value: Any) -> Optional[float]:
    """
    パターン A, B, C すべてに対応
    """
    # 実装: None チェック → 型別処理 → エラーハンドリング
```

---

## 循環インポートの回避

### 既存の import 構造を確認

```python
# app/config.py
from pathlib import Path
# （他のインポートなし、自己完結）

# app/operations_db.py
import csv, json, os, sqlite3
import requests
# （config.py をインポート していない）

# app/main.py
from app.config import AppConfig  # config をインポート
```

### リファクタリング後のインポート計画

#### 推奨: utils と constants は最小限の依存性

```python
# app/utils.py (新規)
import os, json
from pathlib import Path
from typing import Any, Optional

# 他のモジュールをインポート しない（依存性ゼロ）
```

```python
# app/constants.py (新規)
from dataclasses import dataclass

# 他のモジュールをインポート しない（依存性ゼロ）
```

#### 既存モジュールは utils / constants をインポート

```python
# app/config.py (修正後)
from app.utils import env, env_bool, env_float  # ✓ OK

# app/operations_db.py (修正後)
from app.utils import load_dotenv_if_present, to_float  # ✓ OK

# app/energy_model.py (修正後)
from app.constants import SOCBounds  # ✓ OK
```

**チェック**: 以下の循環import が発生していないことを確認
```bash
# 実装後に実行
python -m py_compile app/utils.py app/constants.py
python -c "from app import config, operations_db, energy_model"
```

---

## 修正時のチェックリスト

### Phase 1: `app/utils.py` 作成時

- [ ] **ファイル作成**: `app/utils.py` が新規作成されている
- [ ] **関数群**: 以下の関数が定義されている
  - [ ] `load_dotenv_if_present()`
  - [ ] `env()`
  - [ ] `env_bool()`, `env_int()`, `env_float()`
  - [ ] `to_float()`, `to_int()`
  - [ ] `clamp_percent()`
- [ ] **型ヒント**: すべての関数に型アノテーション
- [ ] **docstring**: 各関数に docstring（引数、戻り値、例外）
- [ ] **テスト**: `tests/test_utils.py` に以下があるか確認
  - [ ] 環境変数読み込みテスト（5+ パターン）
  - [ ] 環境変数取得テスト（4+ パターン）
  - [ ] 型変換テスト（20+ パターン）

### Phase 2: `app/constants.py` 作成時

- [ ] **ファイル作成**: `app/constants.py` が新規作成されている
- [ ] **定数クラス**: 以下のクラスが定義されている
  - [ ] `SOCBounds` (MIN_PERCENT, MAX_PERCENT, clamp メソッド)
  - [ ] `TimeConstants` (HOURS_PER_DAY, MINUTES_PER_HOUR)
  - [ ] `FileConstants` (DEFAULT_CHUNK_SIZE_BYTES)
  - [ ] `PercentConstants` (MIN_PERCENT, MAX_PERCENT)
- [ ] **値の正確性**: 定数値が既存コードと一致
- [ ] **clamp メソッド**: `SOCBounds.clamp()` がテスト済み

### Phase 3: 既存ファイル修正時（各ファイル毎）

#### `config.py` 修正時
- [ ] **インポート追加**: `from app.utils import env, env_bool, env_int, env_float`
- [ ] **関数削除**: `_env*()` 関数群が削除されている
- [ ] **呼び出し変更**: 以下の置換が実施済み
  - `_env(name, default=None)` → `env(name, required=True)` または `env(name, default=default)`
  - `_env_bool(name, default)` → `env_bool(name, default=default)`
- [ ] **テスト**: `AppConfig` が正常に構築される

#### `operations_db.py` 修正時
- [ ] **インポート追加**: `from app.utils import load_dotenv_if_present, env, env_float, to_float, to_int`
- [ ] **関数削除**: `_env*()`, `_to_float*()`, `_load_dotenv_if_present()` が削除されている
- [ ] **呼び出し変更**:
  - `_env_float(name, default)` → `env_float(name, default=default)`
  - `_to_float(value)` → `to_float(value)`
  - `_to_float_any(value)` → `to_float(value)`
  - `_to_int_any(value)` → `to_int(value)`
- [ ] **テスト**: DB操作が正常に機能

#### `energy_model.py` 修正時
- [ ] **インポート追加**: `from app.constants import SOCBounds`
- [ ] **置換**: `max(0.0, min(100.0, value))` → `SOCBounds.clamp(value)` (8箇所すべて)
- [ ] **確認**: grep で `min(100` が残存しないことを確認

#### `dashboard_data.py` 修正時
- [ ] **インポート追加**: `from app.utils import to_float` and/or `from app.constants import SOCBounds`
- [ ] **関数削除**: `_to_float_or_none()` が削除されている
- [ ] **置換**: `_to_float_or_none(value)` → `to_float(value)`
- [ ] **テスト**: ダッシュボード読み込みが正常

---

## 修正後の検証スクリプト

### スクリプト 1: インポートパス確認

```bash
#!/bin/bash
echo "=== Checking import paths ==="
grep -r "from app.utils import" app/ | wc -l
grep -r "from app.constants import" app/ | wc -l

echo "=== Checking for deleted functions ==="
grep -r "_env_float\b" app/ && echo "ERROR: _env_float still exists" || echo "OK: _env_float removed"
grep -r "_to_float_or_none" app/ && echo "ERROR: _to_float_or_none still exists" || echo "OK: _to_float_or_none removed"
grep -r "min(100\\.0" app/ | wc -l  # 0 であることを期待
```

### スクリプト 2: 実行可能性確認

```bash
#!/bin/bash
echo "=== Type checking ==="
python -m py_compile app/utils.py app/constants.py

echo "=== Import check ==="
python -c "from app.utils import *; from app.constants import *; print('OK')"

echo "=== Running tests ==="
pytest tests/test_utils.py -v
```

### スクリプト 3: 機能確認

```bash
#!/bin/bash
echo "=== Functional test ==="
python -c "
from app.utils import to_float, env_float, load_dotenv_if_present
from app.constants import SOCBounds

# テスト
assert to_float('123.45') == 123.45
assert SOCBounds.clamp(150.0) == 100.0
print('OK: Basic functionality works')
"
```

---

## 境界値テスト用データ

### CSV データサンプル

```csv
# test_energy_data.csv
timestamp,soc_percent,power_kw,efficiency
2026-06-20T08:00:00,50.5,3.2,0.95
2026-06-20T09:00:00,,2.1,0.94
2026-06-20T10:00:00,75.0,-1.5,0.96
2026-06-20T11:00:00,0.0,0.0,null
```

### API レスポンスサンプル

```json
{
  "soc_percent": 50,
  "soc_percent_float": 50.5,
  "soc_percent_string": "50.5",
  "power_kw": null,
  "efficiency": 0.95,
  "efficiency_string": "95%"
}
```

### 環境変数サンプル

```bash
# .env.test
CHARGE_LIMIT_PERCENT=80
EFFICIENCY_FACTOR=0.95
TIMEOUT_MS=5000
DEBUG_MODE=true
OPTIONAL_PARAM=
MALFORMED_JSON={invalid json}
```

---

## 実装完了後のコミットメッセージ

### コミット 1: ユーティリティモジュール追加
```
Refactor: Consolidate shared utility functions into app/utils.py

- Centralize environment variable reading (_env, _env_bool, _env_float, etc.)
- Unify type conversion functions (_to_float, _to_float_any, etc.)
- Consolidate .env loading (_load_dotenv_if_present)
- Add comprehensive parameter validation with keyword-only arguments
- Reduce code duplication across 8+ modules
- All functions include docstrings and type hints

This change eliminates 40+ lines of duplicated utility code and improves
maintainability for future environment/config changes.
```

### コミット 2: 定数モジュール追加
```
Refactor: Extract magic numbers into app/constants.py

- Define SOCBounds (0-100%) with clamp() helper
- Define TimeConstants and FileConstants
- Replace 11+ hardcoded min(100.0, ...) patterns with SOCBounds.clamp()
- Centralize configuration values for easier modification

This enables future SOC range expansion or adjustment without code scatter.
```

### コミット 3: ファイル別修正
```
Refactor: Migrate config.py to use shared utilities

- Remove duplicate _env* functions
- Import from app.utils instead
- Update function calls to match new signatures
- All tests passing
```

（各ファイルの修正毎に類似のコミット）

---

## 質問・疑問への回答テンプレート

### Q: `to_float()` で NaN を返す場合は？

**A**: NaN は `float("NaN")` で有効ですが、以下の理由から None を返すべき:
- ビジネスロジック（SOC値）として NaN は意味がない
- 計算エラーの早期発見ができない
- オプション: `math.isnan()` チェックを追加

### Q: 循環 import の回避方法は？

**A**: `utils.py` と `constants.py` は最小限の依存性を保つ:
- 他のモジュールをインポート しない
- 他のモジュールは utils/constants をインポート する（一方向）

### Q: 置換漏れが心配。確認方法は？

**A**: 3段階で確認:
1. 置換前に `grep` で全箇所を抽出
2. sed で一括置換
3. 置換後に同じ `grep` で残存確認（0件を期待）

---

**作成者**: Claude Code Agent
**レビュー対象**: Human (コード実装完了後)

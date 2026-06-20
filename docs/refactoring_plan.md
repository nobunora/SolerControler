# SolerController リファクタリング実装計画

**対象ブランチ**: work
**実装担当**: Claude Code Agent
**テスト戦略**: ユニットテスト + 統合テスト

---

## Phase 1: 共有ユーティリティモジュールの作成

### ファイル: `app/utils.py`（新規作成）

#### 目的
環境変数読み込み、型変換、文字列処理など、複数ファイルで重複している関数を集約。

#### 実装内容

##### 1.1 環境変数読み込み関数

```python
# 完全な実装については implementation_notes.md 参照

def load_dotenv_if_present(path: Path = Path(".env")) -> None:
    """
    .env ファイルから環境変数を読み込む。

    引数:
        path: .env ファイルのパス

    仕様:
    - ファイルが存在しない場合は黙って成功
    - 既に設定済みの変数は上書きしない (setdefault)
    - 引用符（シングル・ダブル）を自動削除
    - # で始まる行とキーなし行はスキップ
    """

def env(name: str, *, required: bool = False, default: Optional[str] = None) -> str:
    """
    環境変数を取得。

    引数:
        name: 環境変数名
        required: True の場合、不在時に ValueError を発生
        default: 不在時のデフォルト値（required=False の場合のみ使用）

    戻り値:
        環境変数の値、またはデフォルト値

    例外:
        ValueError: 不在で required=True の場合
    """

def env_bool(name: str, *, default: bool = False) -> bool:
    """bool型の環境変数取得。"""

def env_int(name: str, *, default: int = 0) -> int:
    """int型の環境変数取得。"""

def env_float(name: str, *, default: float = 0.0) -> float:
    """float型の環境変数取得。"""
```

**互換性の考慮**:
- 既存コードの `_env(name, default)` を `env(name, default=default)` に変換
- 既存コードの `_env_bool(name, default)` を `env_bool(name, default=default)` に変換
- 既存コードの必須チェック（config.py）を `env(name, required=True)` に変換

##### 1.2 型変換関数

```python
def to_float(value: Any) -> Optional[float]:
    """
    任意の値を float に変換。

    処理:
    1. None → None
    2. float/int → float(value)
    3. str → strip して "" → None、そうでなければ float()
    4. その他 → str() 経由で試行
    5. ValueError → None

    境界テスト対象値:
    - None, "", "   ", "0", "1.5", "-1", "1e10", "invalid"
    """

def to_int(value: Any) -> Optional[int]:
    """float経由でint化（小数を扱う場合用）"""

def clamp_percent(value: float, min_val: float = 0.0, max_val: float = 100.0) -> float:
    """パーセンテージ値をクランプ"""
```

**注意**:
- 既存の `_to_float`, `_to_float_any`, `_to_int_any` をすべて統合
- エラーハンドリングは一貫性を保つ（すべて None 返却）
- 境界値テストで全パターンをカバー

##### 1.3 CSVユーティリティ

```python
def parse_csv_float(raw: str) -> Optional[float]:
    """CSV セルの値を float に変換（汎用）"""

def parse_csv_row_as_dict(headers: list[str], values: list[str]) -> dict[str, str]:
    """CSV 行をディクショナリに変換"""
```

---

### Phase 1 の修正対象ファイル

#### 修正対象ファイル一覧

```
直接インポート追加予定ファイル: 12個
├─ app/config.py: _env* 関数を削除、utils から import
├─ app/operations_db.py: _env*, _to_float*, _load_dotenv_if_present を削除
├─ app/kpnet_workflow.py: _env*, _to_optional_float, _load_dotenv_if_present を削除
├─ app/main.py: _load_dotenv_if_present を削除
├─ app/dashboard_data.py: _to_float_or_none を削除
├─ app/forecast_correction.py: 型変換関数を削除
├─ app/consumption_forecast.py: 型変換関数を削除
├─ app/db_sync.py: 必要に応じて import
├─ app/sheets_export.py: 必要に応じて import
├─ app/pv_array_forecast.py: 必要に応じて import
├─ app/occupancy_schedule.py: 必要に応じて import
└─ app/history_store.py: 必要に応じて import

テスト更新予定: tests/test_*.py
├─ utils をテスト対象に追加
└─ 既存テスト内で環境変数関数使用箇所の更新
```

---

## Phase 2: 定数モジュールの作成

### ファイル: `app/constants.py`（新規作成）

#### 実装内容

```python
from dataclasses import dataclass

# SOC（State of Charge）定数
class SOCBounds:
    """バッテリーのSOC範囲を定義"""
    MIN_PERCENT = 0.0
    MAX_PERCENT = 100.0

    # クランプ関数
    @staticmethod
    def clamp(value: float) -> float:
        """値をSOC範囲内にクランプ"""
        return max(SOCBounds.MIN_PERCENT, min(SOCBounds.MAX_PERCENT, value))

class TimeConstants:
    """時間関連の定数"""
    HOURS_PER_DAY = 24.0
    MINUTES_PER_HOUR = 60

class FileConstants:
    """ファイル操作関連"""
    DEFAULT_CHUNK_SIZE_BYTES = 1024 * 1024  # 1MB

class PercentConstants:
    """パーセンテージ定数（一般）"""
    MIN_PERCENT = 0.0
    MAX_PERCENT = 100.0
```

#### Phase 2 の修正対象ファイル

| ファイル | 修正数 | 概要 |
|---------|--------|------|
| energy_model.py | 8 | `min(100.0, ...)` → `SOCBounds.clamp(...)` |
| kpnet_workflow.py | 2 | `min(100.0, ...)` → `SOCBounds.clamp(...)` |
| soc_cost_optimizer.py | 1 | `min(100.0, ...)` → `SOCBounds.clamp(...)` |
| drive_backup.py | 1 | `1024*1024` → `FileConstants.DEFAULT_CHUNK_SIZE_BYTES` |
| **計** | **12** | 定数置換 |

---

## Phase 3: 既存ファイルの修正

### 修正ステップ（順序重要）

#### ステップ 3.1: `config.py` の修正

**削除対象**:
- `_env()` 関数定義（行9-15）
- `_env_optional()` 関数定義（行18-19）
- `_env_bool()` 関数定義（行22-26）
- `_env_int()` 関数定義（行29-33）
- `_env_float()` 関数定義（行36-40）
- `_env_optional_float()` 関数定義（行43-50）

**追加対象**:
- インポート: `from app.utils import env, env_bool, env_int, env_float, load_dotenv_if_present`

**変更対象**:
- `AppConfig` の初期化時に `_env()` → `env()` に変更
- ただし必須チェックは `env(name, required=True)` に

**検証**:
- `AppConfig` 構築時に必須フィールドがすべて指定されているか確認

---

#### ステップ 3.2: `operations_db.py` の修正

**削除対象** (行16-71):
- `_load_dotenv_if_present()`
- `_env()`
- `_env_float()`
- `_to_float()`
- `_to_float_any()`
- `_to_int_any()`

**追加対象**:
- インポート: `from app.utils import load_dotenv_if_present, env, env_float, to_float, to_int`

**変更対象**:
```python
# Before
_load_dotenv_if_present()
value = _to_float_any(raw_value)
float_val = _env_float("SOME_VAR", 0.0)

# After
load_dotenv_if_present()
value = to_float(raw_value)
float_val = env_float("SOME_VAR", default=0.0)
```

**影響関数** (確認必須):
- `_extract_hourly_forecast_from_plan()` (行74-124)
- `load_operation_history_rows()` 以下の全関数

---

#### ステップ 3.3: `kpnet_workflow.py` の修正

**削除対象** (行30-74):
- `_load_dotenv_if_present()`
- `_env()`
- `_env_bool()`
- `_to_optional_float()`

**追加対象**:
- インポート: `from app.utils import load_dotenv_if_present, env, env_bool, to_float`
- インポート: `from app.constants import SOCBounds, TimeConstants`

**変更対象**:
```python
# Before
line 59: _load_dotenv_if_present()
line 634: return max(0.0, target_soc - max(0.0, min(100.0, soc_now)))

# After
line 59: load_dotenv_if_present()
line 634: return SOCBounds.clamp(target_soc - SOCBounds.clamp(soc_now))
```

---

#### ステップ 3.4: `main.py` の修正

**変更対象**:
- `_load_dotenv_if_present()` → `load_dotenv_if_present()`

---

#### ステップ 3.5: `dashboard_data.py` の修正

**削除対象**:
- `_to_float_or_none()` (行95-108)

**追加対象**:
- インポート: `from app.utils import to_float`
- インポート: `from app.constants import SOCBounds`

**変更対象**:
```python
# Before
line 95: def _to_float_or_none(value: Any) -> float | None:
line 194: return _to_float_or_none(...)

# After
# 削除してから、to_float() を使用

# Before
# 暗黙的な100.0のクランプロジック
return min(100.0, max(0.0, value))

# After
return SOCBounds.clamp(value)
```

---

#### ステップ 3.6: `energy_model.py` の修正

**変更対象** (全8箇所):

```python
# Before
reserve_soc = max(0.0, min(100.0, reserve_soc_percent))

# After
from app.constants import SOCBounds
reserve_soc = SOCBounds.clamp(reserve_soc_percent)
```

**対象行の詳細**:
- 行252-257: SOC初期化
- 行317: target_soc_7_percent 計算
- 行322: predicted_daytime_max_soc_percent 計算
- 行325: predicted_sunset_soc_percent 計算
- 行381: target_soc 計算

---

#### ステップ 3.7: その他ファイルの修正

**forecast_correction.py**:
- 型変換関数を `to_float()` に統一

**consumption_forecast.py**:
- 型変換関数を `to_float()` に統一

**soc_cost_optimizer.py** (行137):
```python
# Before
return max(0.0, min(100.0, value))

# After
from app.constants import SOCBounds
return SOCBounds.clamp(value)
```

**drive_backup.py** (チャンク容量参照箇所):
```python
# Before
chunk_size = 1024 * 1024

# After
from app.constants import FileConstants
chunk_size = FileConstants.DEFAULT_CHUNK_SIZE_BYTES
```

---

## 修正完了後の検証

### 検証ステップ

#### 1. インポートパスの確認

```bash
# app/utils.py と app/constants.py が存在することを確認
# すべてのインポート文が正しいことを確認
grep -r "from app.utils import" app/
grep -r "from app.constants import" app/
```

#### 2. 型チェック（mypy 実行）

```bash
mypy app/ --strict
```

#### 3. ユニットテスト実行

```bash
pytest tests/ -v
```

#### 4. 統合テスト実行（本番環境シミュレーション）

主要な end-to-end フローが正常に動作することを確認：
- ダッシュボードデータの読み込み
- KPNet ワークフロー実行
- エネルギー計算

#### 5. 置換漏れの確認

```bash
# 古い関数名がコード内に残っていないことを確認
grep -r "_to_float_or_none" app/
grep -r "_env_float\b" app/  # 単語単位で検索
grep -r "min(100" app/       # 残存する100のハードコード
```

---

## リスク軽減策

### 高リスク: 環境変数読み込みの置き換え

**リスク**: `config.py` と `operations_db.py` の `_env` 関数が仕様が異なり、置き換え時に動作変化

**軽減策**:
1. 共有版 `env()` で両方のユースケースをサポート（required パラメータ）
2. 置換前後で環境変数の読み込み結果を検証
3. テスト: 必須 / オプション / デフォルト値の全パターンをカバー

### 中リスク: 型変換の置き換え

**リスク**: `_to_float*` 系関数の処理が微妙に異なり、置き換え時に外れ値の扱いが変わる

**軽減策**:
1. 共有版 `to_float()` で全パターンをサポート（None、空文字、無効値など）
2. 既存の全パターンを吸収する実装にする
3. テスト: CSV実データを用いたテスト、エッジケース網羅

### 中リスク: 定数置換の漏れ

**リスク**: `min(100, ...)` の置き換え時に一部漏れる可能性

**軽減策**:
1. 置換前に grep で確認し、すべてを把握
2. sed / エディタで一括置換（確認あり）
3. 置換後に grep で確認（置換漏れなし）

---

## ロールバック計画

### 各 Phase 毎のロールバック方法

```bash
# Phase 1 のロールバック
git reset --soft HEAD~N  # N はコミット数
git restore app/*.py
git clean -fd app/utils.py

# Phase 2 のロールバック
git restore app/constants.py app/*.py

# Phase 3 のロールバック
git restore app/*.py
```

### 部分的なロールバック

特定ファイルのみロールバック:
```bash
git checkout HEAD -- app/energy_model.py
```

---

**次のドキュメント**: `implementation_notes.md`

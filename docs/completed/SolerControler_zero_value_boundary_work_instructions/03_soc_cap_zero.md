# SolerControler 修正作業指示書

> 対象: `C:\VSC\SolerControler`
> 基準HEAD: `95eb45c`
> 原則: この指示書の問題だけを修正し、無関係な変更を混ぜない。

## 03. SOC制約上限 `0%` を `100%` に置換してガードを無効化する

### 優先度

**P1 / 最優先**

### 対象

- `energy_model_main.py`
- SOC制約の集約処理
- `cap_target_soc_percent`
- `max_target_soc_percent`
- 朝PVヘッドルームガード
- 日中PVヘッドルームガード
- 履歴PVヘッドルームガード
- コスト最適化入力生成
- `tests/test_energy_model_runtime.py`
- `tests/test_soc_cost_optimizer.py`
- `tests/test_energy_model.py`

### 調査結果

現行コードでは、ガードが返したSOC上限を次の形式で読み込んでいる。

```python
_to_optional_float(guard.get("cap_target_soc_percent")) or 100.0
```

同型の式が複数箇所に存在する。

代表位置:

- `energy_model_main.py` 約2298行
- `energy_model_main.py` 約2390行
- `energy_model_main.py` 約2397行

Pythonでは `0.0` は偽として評価されるため、`cap_target_soc_percent=0.0` は `100.0` に置換される。

### 0%が到達可能である根拠

`cap_target_soc_percent` はPVヘッドルーム確保のための上限であり、計算結果は0から100へクランプされる。

次の条件では0%が正当な上限になり得る。

- 予約SOCが0%
- 翌日のPV余剰が実効容量以上
- 朝または日中の発電を受け入れるため、夜間充電を完全に抑制する必要がある
- 複数ガードの最小値が0%

設定上も `NIGHT_RESERVE_SOC_PERCENT=0` が既定例として存在するため、0%は異常値ではない。

### 再現結果

朝PVヘッドルームガードを、予約SOC0%、十分なPV発電、実効容量1 kWhで実行した。

結果:

```text
applied = True
cap_target_soc_percent = 0.0
current_fallback_expression = 100.0
```

ガード自体は正しく適用されているが、集約時に上限が100%へ反転する。

### 根本原因

1. 欠損値判定にPythonのtruthinessを使用している。
2. 数値0を有効値として扱う型契約がコードへ反映されていない。
3. `None` と `0.0` を区別する共通ヘルパーがない。
4. 制約集約処理が複数箇所へ重複している。
5. テストが80%、85%など正の上限だけを使用している。
6. ガード単体テストと最終最適化入力テストの間に境界テストがない。

### 影響

- 夜間充電を0%まで抑えるべき日に、上限100%として最適化される。
- PV受入れ用の空き容量を確保できない。
- 昼間の余剰売電または出力抑制が増える。
- バッテリーの不要な満充電が増える。
- 価格最適化がPVヘッドルーム制約を無視する。
- ガードの `applied=True` と実際の最適化入力が矛盾する。
- 説明ペイロード上は制約適用に見えても、実際の意思決定へ反映されない可能性がある。

### 値の契約

| 値 | 意味 | 処理 |
|---|---|---|
| `None` | ガードが上限を提供していない | 制約なし相当の100% |
| `0.0` | 有効な上限0% | 0%を適用 |
| 0より大きく100以下 | 有効な上限 | そのまま適用 |
| 0未満 | 不正 | エラーまたはガード異常 |
| 100超 | 不正またはクランプ前 | ガード側契約を確認 |

集約側で不正値を黙って100%へ置換しない。

### 修正方針

#### 最小修正

truthinessではなく `is None` を使用する。

```python
cap = _to_optional_float(guard.get("cap_target_soc_percent"))
if cap is None:
    cap = 100.0
max_target_soc_percent = min(max_target_soc_percent, cap)
```

または次のような小さなヘルパーを使用する。

```python
def _optional_cap_or_unbounded(value: object) -> float:
    parsed = _to_optional_float(value)
    return 100.0 if parsed is None else parsed
```

ただし、ヘルパー名は「0を保持する」ことが読み取れるものにする。

#### 推奨改善

制約を辞書ではなく型で保持する。

```python
@dataclass(frozen=True)
class SocCapConstraint:
    applied: bool
    cap_percent: float | None
    reason: str
    source: str
```

集約は一つの純粋関数にまとめる。

```python
def aggregate_soc_cap(constraints: Sequence[SocCapConstraint]) -> float:
    ...
```

P1修正時は大規模移行を避け、まず3箇所すべての0保持を修正する。

### 修正箇所

#### 制約構築経路

- 朝PVヘッドルームガード
- 日中PVヘッドルームガード
- 履歴PVヘッドルームガード
- その他 `cap_target_soc_percent` を返すガード

ガード側が0を返すことは許容する。

#### 制約集約経路

- `_build_soc_constraints()` 相当の処理
- `max_target_soc_percent` の最小値計算
- ガード辞書から上限を読む全箇所

#### コスト最適化入力

- `constraints.morning_headroom`
- その他制約辞書
- `max_target_soc_percent` の生成

最終的にコスト最適化へ0%が渡ることを確認する。

### 実装手順

1. 現行コードの3箇所を特定する。
2. 0%ガードが適用される失敗テストを追加する。
3. 制約集約結果が100%になることを再現する。
4. `or 100.0` を明示的なNone判定へ変更する。
5. 同型の全箇所を修正する。
6. ガードの説明値と最適化入力値が一致することをテストする。
7. 従来最適化経路とコスト最適化経路を両方テストする。
8. 正の上限値とNoneの既存挙動を確認する。
9. 対象テストを実行する。
10. この問題だけをコミットする。

### 必須テスト

#### 境界値

- `cap_target_soc_percent=None` → 100%
- `cap_target_soc_percent=0.0` → 0%
- `cap_target_soc_percent=0.1` → 0.1%
- `cap_target_soc_percent=100.0` → 100%
- 負値の契約
- 100超の契約

#### 複数制約

- 90%、80%、0% → 最終0%
- None、80% → 最終80%
- Noneのみ → 最終100%
- 適用されていないガードと0%ガードの混在

#### 最適化経路

- 従来最適化へ0%が渡る。
- コスト最適化へ0%が渡る。
- 出力ペイロードへ0%が保持される。
- 説明理由と最適化上限が一致する。

#### 回帰

- 80%、85%など既存の正値テストが維持される。
- PVガード未適用時の挙動が変わらない。
- 予約SOCが正の場合も正しく下限・上限が処理される。

### 完了条件

- 有効なSOC上限0%が100%へ置換されない。
- ガードの適用結果が最終最適化入力へ反映される。
- 3箇所の同型truthiness処理が解消される。
- Noneと0の契約がテストで固定される。
- 出力説明と実計算が一致する。
- 無関係な最適化式やガード式を変更しない。

### テスト

```powershell
python -m pytest -q tests/test_energy_model.py
python -m pytest -q tests/test_energy_model_runtime.py
python -m pytest -q tests/test_soc_cost_optimizer.py
python -m pytest -q -m "not external"
python scripts/security_check.py
git diff --check
```

### 互換性への影響

これまで誤って100%として扱われていた一部条件で、最終SOC上限が0%または低い値になる。

これは既存ガードの意図を正しく反映する不具合修正である。

外部APIのキー名は変更しない。説明値に0が出力されることを前提に、ダッシュボードが0を欠損表示しないことも確認する。

### ロールバック

この問題だけのコミットを `git revert` する。

ロールバックするとPVヘッドルーム上限0%が無効化される既知不具合が復活するため、運用上は手動で夜間充電を抑制する必要がある。

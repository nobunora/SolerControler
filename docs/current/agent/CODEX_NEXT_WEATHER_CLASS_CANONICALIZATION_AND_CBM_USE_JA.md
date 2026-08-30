# 次作業指示: weather classification canonicalization と CodebaseMemory 共有グラフ活用

## 位置づけ

これは次の実装作業に対する必須仕様である。設計案ではなく、変更境界・禁止事項・検証条件を固定する。

前提:

- CodebaseMemory triage / report hygiene のルール整備は完了済み。
- verified unused private helper 4件は削除済み。
- backup script の dotenv loader は `app.configuration.environment.load_dotenv_if_present` へ canonicalize 済み。
- `.codebase-memory/graph.db.zst` と artifact metadata が repository に追加済み。
- shared graph の恒久運用は `docs/current/agent/codebase_memory_shared_graph_usage_ja.md` に従う。

この次作業では **weather-code classification の重複だけを最小単位で解消する**。低信頼 CALLS 全件調査、compatibility wrapper 廃止、Phase 1 policy 変更、forecast algorithm 変更を混ぜない。

---

## 1. 調査で確定した3つの weather classification

### 1.1 operational: Energy Plan

`app/energy_plan/weather_history.py::weather_class`

現在の semantics:

- `None -> unknown`
- `0 -> clear`
- `1..3 -> cloudy`
- `45,48 -> fog`
- `51..67,80..82 -> rain`
- `71..77,85..86 -> snow`
- `95..99 -> storm`
- otherwise `other`

### 1.2 operational: PV array calibration

`app/forecasting/pv_array_calibration.py::_weather_class_from_code`

これは Energy Plan の operational classification と同一ロジックである。

この2実装は将来片側だけ変更される semantic drift を防ぐため canonicalize する。

### 1.3 frozen validation: Phase 1 shadow gate

`app/operations/shadow_gate.py::weather_class`

これは operational classification と **同じではない**。

例:

- `1..3 -> clear`
- `80..99 -> shower`

さらにこの関数は Phase 1 shadow validation の frozen diagnostic parity に使われている。

**この関数を operational canonical function へ置換してはならない。**

類似度・関数名・CodebaseMemory の semantic relation を理由に統合しない。

---

## 2. 実装する canonical owner

新規 pure domain module を作る。

推奨 path:

`app/domain/weather.py`

canonical function 名:

```python
open_meteo_weather_class(weather_code: int | None) -> str
```

generic な `weather_class` という名前にしない。

理由:

- Phase 1 frozen classification と semantic domain を名前で区別するため。
- Open-Meteo code mapping であることを明示するため。
- 将来別 provider / frozen policy mapping が存在しても誤統合しにくくするため。

`app.domain` は upper layer へ依存してはいけない既存 import-linter contract があるため、この module は pure function のみとし、`forecasting` / `energy_plan` / `operations` を import しない。

---

## 3. Energy Plan 側の変更

対象:

`app/energy_plan/weather_history.py`

実装:

1. `app.domain.weather.open_meteo_weather_class` を import する。
2. 現在の `weather_class` の classification body は削除する。
3. `weather_class` 自体は当面 compatibility wrapper として残す。

形状:

```python
def weather_class(weather_code: int | None) -> str:
    return open_meteo_weather_class(weather_code)
```

理由:

- 現在 `weather_history` 内部の複数箇所が `weather_class` を使用している。
- test / local consumer が module-private ではない `weather_class` を import している可能性を不用意に壊さない。
- 今回は classification table の single-owner 化が目的であり、public-ish symbol cleanup を混ぜない。

wrapper 上または直前には、必要なら短い ownership comment を置く。

例:

```python
# Compatibility name; classification ownership lives in app.domain.weather.
```

---

## 4. PV array calibration 側の変更

対象:

`app/forecasting/pv_array_calibration.py`

実装:

1. `app.domain.weather.open_meteo_weather_class` を import する。
2. `_weather_class_from_code` の classification table は削除する。
3. private helper は最初の変更では薄い wrapper として残してよい。

推奨:

```python
def _weather_class_from_code(weather_code: int | None) -> str:
    return open_meteo_weather_class(weather_code)
```

理由:

- private import / monkeypatch の repository 内確認を行ったうえで、必要なら後続 cleanup で削除できる。
- 今回は一度に「table ownership移動」と「symbol廃止」を混ぜない。

CodebaseMemory 再 index 後にこの wrapper が明確な pass-through unused candidate になり、直接検索・tests・historyでも互換用途がないことが確認できた場合のみ、別 logical patch で削除を検討する。

---

## 5. Phase 1 shadow gate の保護

対象:

`app/operations/shadow_gate.py::weather_class`

挙動は変更しない。

関数直前または docstring に、誤統合防止 comment を追加する。

推奨 comment:

```python
# codebase-memory: keep-separate — frozen Phase 1 diagnostic classification;
# intentionally differs from app.domain.weather.open_meteo_weather_class.
```

既存 docstring の「frozen diagnostic's coarse classes」も残す。

この comment は CodebaseMemory のために production semantics を変えるものではなく、将来の human/agent refactor が similarity だけで統合しないための ownership marker である。

---

## 6. 必須 regression tests

### 6.1 canonical operational classification

新規 test file の例:

`tests/test_domain_weather.py`

少なくとも以下を table-driven test で固定する。

```text
None -> unknown
0    -> clear
1    -> cloudy
3    -> cloudy
45   -> fog
48   -> fog
51   -> rain
67   -> rain
80   -> rain
82   -> rain
71   -> snow
77   -> snow
85   -> snow
86   -> snow
95   -> storm
99   -> storm
4    -> other
44   -> other
50   -> other
100  -> other
```

### 6.2 Energy Plan compatibility

`app.energy_plan.weather_history.weather_class` が canonical function と全代表値で同じ結果になることを確認する。

最低限:

```python
assert weather_history.weather_class(code) == open_meteo_weather_class(code)
```

### 6.3 PV calibration compatibility

`_weather_class_from_code` を wrapper として残す場合、canonical function と全代表値で同じ結果になることを確認する。

### 6.4 frozen classification 非同一性の regression

これは必須。

operational と Phase 1 frozen mapping が意図的に異なることを test で固定する。

最低限:

```text
code 1:
  operational -> cloudy
  shadow      -> clear

code 80:
  operational -> rain
  shadow      -> shower
```

この test により、将来「同じ weather code mapping だから統合しよう」という refactor を明確に失敗させる。

既存 `tests/test_shadow_gate.py` の frozen diagnostic parity test は削除・緩和しない。

---

## 7. CodebaseMemory をこの実装でどう使うか

### 7.1 実装前

1. `.codebase-memory/artifact.json` の indexed commit を確認する。
2. artifact 以降に source-bearing change があるか確認する。
3. `index_status=ready` を確認する。
4. graph で以下を検索する。
   - `weather_history.weather_class`
   - `pv_array_calibration._weather_class_from_code`
   - `shadow_gate.weather_class`
5. caller / test relation を確認する。
6. source を直接読み、3つの semantics が上記どおりであることを再確認する。

共有 artifact が source-bearing state に対して fresh なら、ゼロから repository 全走査は行わない。

### 7.2 実装中

変更対象を以下へ限定する。

- `app/domain/weather.py`
- `app/energy_plan/weather_history.py`
- `app/forecasting/pv_array_calibration.py`
- `app/operations/shadow_gate.py` の誤統合防止 comment のみ
- weather regression tests

unrelated cleanup を行わない。

### 7.3 実装後

1. `detect_changes` で影響範囲を確認する。
2. operational callers が canonical functionへ収束していることを確認する。
3. shadow gate が canonical function を CALL / IMPORT していないことを確認する。
4. focused quality checks / tests を実行する。
5. source-bearing commit を作る。
6. explicit `index_repository` を **1回だけ**実行して共有 artifact を生成する。
7. generated `.codebase-memory/**` を最後の commit として追加する。
8. artifact commit 自身を graph に含めるための二度目の refresh はしない。

PR description に indexed source commit、nodes、edges、status、主要 impact 所見を記録する。

---

## 8. 必須 quality / test scope

repository rule に従い、test 前に code-quality-audit を実行する。

最低限の focused checks:

```text
python -m ruff check app/domain/weather.py app/energy_plan/weather_history.py app/forecasting/pv_array_calibration.py app/operations/shadow_gate.py tests/test_domain_weather.py tests/test_shadow_gate.py
```

focused pytest:

```text
python -m pytest tests/test_domain_weather.py tests/test_energy_model.py tests/test_pv_array_forecast.py tests/test_forecasting_pv_array_compatibility.py tests/test_shadow_gate.py -q
```

import-linter contract も確認し、`app.domain` から upper layer への依存が増えていないことを証明する。

full suite / repository-required quality workflow も最終確認する。

---

## 9. 非変更範囲

このPRで変更してはいけない。

- Phase 1 `POLICY_NAME`
- Phase 1 `POLICY_VERSION`
- frozen selector semantics
- candidate formulas
- shadow evidence classification
- production PV forecast formula
- weather calibration factor formula
- SOC optimizer
- battery command / device control
- `_archive_weather_rows`
- `_run_optional`
- low-confidence CALLS を減らすためだけの production refactor

---

## 10. 次フェーズ

weather classification canonicalization が完了した後、次を別作業にする。

1. `_archive_weather_rows` の history / compatibility evidence を確定し、削除または明示 compatibility seam 化。
2. `_run_optional` の historical use と operator contract を確定し、削除または維持。
3. low-confidence app-local CALLS を edge 単位で保存し、`source-fix / graph-false-positive / dynamic-external-api / test-fake / builtins-runtime` に分類。
4. source-fix と判定された edge だけを個別 patch にする。

ここでも「CodebaseMemory の graph をきれいにすること」自体を目的にしない。目的は source correctness、boundary clarity、reviewability の改善である。

---

## Stop condition

次のすべてを満たしたらこの実装を止める。

- operational Open-Meteo mapping の classification table が1箇所だけになっている
- Energy Plan / PV calibration の出力 semantics が不変
- Phase 1 frozen mapping は挙動不変かつ誤統合防止 test/commentで保護されている
- import boundary が維持されている
- focused tests と repository-required quality workflow が通る
- shared graph artifact が source-bearing commit を表している
- artifact-only commit loopを作っていない

それ以上の cleanup を同じPRへ追加しない。

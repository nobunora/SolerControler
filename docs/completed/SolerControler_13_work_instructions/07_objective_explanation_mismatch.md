# SolerControler 修正作業指示書

> 対象: `C:\VSC\SolerControler`  
> 基準HEAD: `5430f40`  
> 原則: この指示書の問題だけを修正し、無関係な変更を混ぜない。


## 07. 目的関数の説明と実計算が一致しない

### 優先度
**P1**

### 対象
- `energy_model_main.py`
- `_decision_cost_breakdown()`
- `objective_name`
- `app/soc_cost_optimizer.py`

### 調査結果
出力上の目的関数は次のみを表す。
- night charge cost
- expected day buy cost
- expected sell opportunity loss

実計算にはさらに次が含まれる。
- expected peak unmet cost
- monthly tier landing penalty
- decision prior cost

`_decision_cost_breakdown()` にmonthly tierとdecision priorがない。

### 根本原因
optimizerへ新しい罰則を追加した後、説明用JSONと不採用理由を同期していない。目的関数の定義が複数箇所へ重複している。

### 影響
- JSONだけで選択結果を再現できない。
- 内訳合計とtotalが一致しない。
- 履歴priorや月間料金段階が判断を変えたことが見えない。
- QA・上司へ説明できない。

### 修正方針
目的関数の構成要素を1箇所へ集約し、説明出力も同じ定義から生成する。

推奨:
```python
OBJECTIVE_COMPONENT_KEYS = (
    "night_charge_cost_yen",
    "expected_day_buy_cost_yen",
    "expected_sell_opportunity_cost_yen",
    "expected_peak_unmet_cost_yen",
    "monthly_tier_landing_penalty_yen",
    "decision_prior_cost_yen",
)
```

### 実装手順
1. optimizerが返す全コストキーを列挙する。
2. `_decision_cost_breakdown()` に全項目を追加する。
3. `objective_name` を実計算と一致する名称へ変更する。
4. 内訳合計と `total_expected_cost_yen` の差を検証する。
5. candidate rejection reasonへ以下を追加する。
   - historical regret / decision prior
   - monthly tier landing penalty
6. schema互換性が必要なら `schema_version` を追加する。
7. 旧キーを残す場合はdeprecatedであることを明示する。

### 必須テスト
- 全内訳合計がtotalと一致。
- priorが選択を変えた場合、理由が表示される。
- monthly tierが原因の場合、理由が表示される。
- objective説明に全要素が含まれる。
- legacy optimizer時はlegacy説明。

### 完了条件
- JSONだけで選択理由を説明・再計算できる。
- 表示説明と実計算が一致する。
- generic `higher_total_cost` だけで重要理由を隠さない。

### テスト
```powershell
python -m pytest -q tests/test_energy_model.py tests/test_soc_cost_optimizer.py tests/test_soc_decision_feedback.py
```

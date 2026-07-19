# SolerControler 修正作業指示書

> 対象: `C:\VSC\SolerControler`
> 基準HEAD: `95eb45c`
> 原則: この指示書の問題だけを修正し、無関係な変更を混ぜない。

## 02. PVアレイ予測 `0 kWh` を欠損扱いして夜間充電量を過小化する

### 優先度

**P1 / 最優先**

### 対象

- `energy_model_main.py`
- PVアレイ予測結果の読込・正規化部分
- `predicted_pv_total_raw`
- `predicted_pv_override`
- `predicted_morning_pv_override`
- `predicted_midday_surplus_override`
- `app/energy_model.py`
- `compute_night_charge_target()`
- `forecast_pv_energy_kwh()`
- `tests/test_energy_model_runtime.py`
- `tests/test_energy_model.py`
- `tests/test_pv_array_forecast.py`

### 調査結果

現行コードはPVアレイ予測の値を `_to_optional_float()` で数値化した後、`0` 以下を `None` へ変換している。

代表的な現行処理:

```python
predicted_pv_total_raw = _to_optional_float(pv_totals.get("total_kwh"))
predicted_pv_override = predicted_pv_total_raw
if predicted_pv_override is not None and predicted_pv_override <= 0:
    predicted_pv_override = None
```

同じ考え方が朝PV予測や日中余剰予測にも適用されている。

しかし、現在のPVアレイ予測仕様では `total_kwh` は00:00から24:00までの予測発電量の合計であり、`0.0` は次のような状況を表す有効な値である。

- 終日ほぼ発電しない天候
- 降雪または強い遮蔽
- 発電設備の停止
- 日射予測が全時間帯で0
- 予測対象時間が残っていない
- 配列モデルが正常に計算した結果としての0

`0.0` は「取得失敗」「キー欠損」「解析不能」と同義ではない。

### 再現結果

純粋計算関数へ同一条件を与え、PV予測だけを `0` と `None` で比較した。

条件の要点:

- 現在SOC: 20%
- 日中負荷予測: 8 kWh
- 朝負荷予測: 2 kWh
- 日照時間予測: 5時間
- 予測PV上書き以外は同一
- 有効容量: 10 kWh
- 充電効率: 0.9

結果:

```text
zero_pv_override_required_kwh = 6.666666666666666
fallback_required_kwh = 0.0
fallback_predicted_pv_kwh = 10.525
```

正しい `0 kWh` を採用した場合は約6.67 kWhの夜間充電が必要である。

現行経路のように `0` を `None` へ変換すると、旧来のPVモデルが10.525 kWhの発電を予測し、必要夜間充電量が0 kWhへ反転する。

### 根本原因

1. 数値の妥当性判定と欠損判定が同じ条件式へ混在している。
2. 「0以下は予測不能」という過去の仮定が、現在のPVアレイ予測契約へ残っている。
3. 値だけを `float | None` で扱い、値の状態を保持していない。
4. フォールバックを適用する条件が「欠損」ではなく「0以下」になっている。
5. 既存テストが正のPV値を中心に構成され、境界値0を固定していない。
6. 予測ソースの成功・失敗状態と予測値が別々に検証されていない。

### 影響

- PV発電ゼロの日に夜間充電が不足する。
- 朝または日中にSOC不足が発生する。
- 高価格時間帯の買電が増える。
- 期待した最低SOCを維持できない可能性がある。
- ダッシュボード上の予測理由と実際の計算経路が不一致になる。
- 「PVアレイ予測を使用」と表示されても、内部では旧来モデルへ切り替わる可能性がある。
- 温度0℃の欠損化問題と組み合わさると、フォールバック予測の誤差がさらに増える。

### 値の契約

修正後は次の契約を明示する。

| 入力状態 | 意味 | 処理 |
|---|---|---|
| `None` | キー欠損、取得失敗、解析不能 | フォールバック候補 |
| `0.0` | 有効な発電量0 kWh | そのまま採用 |
| 正の有限値 | 有効な予測値 | そのまま採用 |
| 負の有限値 | 物理的に不正 | エラーまたは明示的な無効値処理 |
| `NaN` / `inf` | 数値として不正 | エラーまたは明示的な無効値処理 |

負値を0へ黙って丸めるか、予測失敗として扱うかは既存仕様を確認して決定する。ただし、負値と0を同じ分岐へ入れない。

### 修正方針

#### 最小修正

`0` を保持し、`None` の場合だけフォールバックする。

```python
predicted_pv_override = _to_optional_float(pv_totals.get("total_kwh"))
```

`predicted_pv_override <= 0` を理由に `None` へ変換しない。

負値を拒否する必要がある場合は別条件にする。

```python
if predicted_pv_override is not None and predicted_pv_override < 0:
    # 既存契約に従ってエラー、記録、または無効化
```

#### 推奨改善

予測値と状態を型で保持する。

```python
@dataclass(frozen=True)
class ForecastValue:
    value_kwh: float | None
    status: str
    source: str
    error: str | None = None
```

候補となる `status`:

- `available`
- `missing`
- `invalid`
- `disabled`
- `error`

ただし、P1修正時に大規模なデータモデル移行を同時実施しない。まず0を保持する最小修正と回帰テストを完了する。

### 修正箇所

#### `energy_model_main.py`

確認対象:

- `predicted_pv_total_raw` の生成
- `predicted_pv_override` の決定
- `predicted_morning_pv_override` の決定
- `predicted_midday_surplus_override` の決定
- 出力ペイロードへ記録する予測値と予測ソース

修正後は次を区別する。

- `total_kwh=0.0`
- `total_kwh` 欠損
- PVアレイ予測無効
- PVアレイ予測処理失敗
- 予測値が負または非有限

#### `app/energy_model.py`

原則として純粋計算関数側の `None` 契約は維持する。

- `predicted_pv_kwh_override is None`: フォールバックを使用
- `predicted_pv_kwh_override == 0.0`: 0 kWhを使用

既にこの区別が可能なら、不要な変更を加えない。

### 実装手順

1. 現行HEADで再現テストを追加し、失敗することを確認する。
2. PVアレイ予測の成功状態と `total_kwh=0.0` を含む入力を作る。
3. `energy_model_main.py` が0を `None` に変換しないよう修正する。
4. 朝PVと日中余剰についても0を保持する。
5. 負値と非有限値の扱いを既存仕様に基づいて明示する。
6. 出力ペイロードに0が保持されることを確認する。
7. フォールバック理由が欠損時だけ記録されることを確認する。
8. 対象テストを実行する。
9. 全エネルギーモデル関連テストを実行する。
10. 差分を確認し、この問題だけをコミットする。

### 必須テスト

#### PV総量

- `total_kwh=0.0` を有効値として保持する。
- `total_kwh=None` の場合だけフォールバックする。
- `total_kwh>0` をそのまま保持する。
- `total_kwh<0` の契約を固定する。
- `NaN` と `inf` の契約を固定する。

#### 計算結果

- `predicted_pv_kwh_override=0.0` で必要夜間充電量が正しく増える。
- `predicted_pv_kwh_override=None` で旧来モデルへフォールバックする。
- 0とNoneで異なる結果になる再現ケースを固定する。
- 出力の `predicted_pv_kwh` が0のまま残る。
- 使用した予測ソースがPVアレイ予測のまま残る。

#### 朝・日中

- `morning_kwh=0.0`
- `midday_surplus_kwh=0.0`
- 総PVは正、朝PVだけ0
- 総PVは0、朝PVも0
- 余剰0を欠損扱いしない

#### フォールバック

- キー欠損
- 予測無効
- 予測処理例外
- 解析不能文字列
- 負値または非有限値

### 推奨テスト配置

- `tests/test_energy_model_runtime.py`
  - `energy_model_main.py` の正規化と統合経路
- `tests/test_energy_model.py`
  - 0とNoneを区別する純粋計算
- `tests/test_pv_array_forecast.py`
  - 予測配列の合計0が正常出力になること

### 完了条件

- 正常なPVアレイ予測 `0.0 kWh` が `None` に変換されない。
- 夜間充電計算が0 kWhのPV予測をそのまま使用する。
- 欠損時だけフォールバックする。
- 予測ソースと計算値が一致する。
- 0、None、負値、非有限値の契約がテストで固定される。
- 既存の正のPV予測ケースを壊さない。
- 既存の外部APIや保存ペイロードの互換性を不要に変更しない。

### テスト

```powershell
python -m pytest -q tests/test_energy_model.py
python -m pytest -q tests/test_energy_model_runtime.py
python -m pytest -q tests/test_pv_array_forecast.py
python -m pytest -q -m "not external"
python scripts/security_check.py
git diff --check
```

### 互換性への影響

計算結果は意図的に変化する。

特に、これまで0 kWhが欠損扱いされていた日は夜間充電量が増える可能性がある。これは仕様変更ではなく、PVアレイ予測契約に沿った不具合修正である。

APIキー名や保存スキーマは原則として変更しない。状態情報を追加する場合は後方互換な追加フィールドとする。

### ロールバック

この問題だけのコミットを `git revert` する。

ただし、ロールバックするとPV予測0 kWhを欠損扱いする既知不具合が復活するため、本番運用でのロールバック時はPVゼロ日を手動確認すること。

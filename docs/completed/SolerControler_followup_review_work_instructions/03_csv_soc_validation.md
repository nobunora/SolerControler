# SolerControler 追加修正作業指示書

> 対象: `C:\VSC\SolerControler`  
> 基準HEAD: `7681fe2`  
> 原則: この指示書の問題だけを修正し、無関係な変更を混ぜない。

## 03. CSV fallback SOCに有限値・範囲検証がない

### 優先度
**P1 / 高**

### 対象
- `cloud_job_runner.py`
- `_latest_csv_soc_reading()`
- `_read_soc_with_fallback()`
- `app/kpnet_workflow.py` のSOC検証規則

### 調査結果
CSV SOCは現在、次で直接数値化される。

```python
soc = float(soc_text)
```

日時の鮮度は確認されるが、値自体について次を確認していない。

- 有限値か
- 0%以上か
- 100%以下か

### 受理され得る異常値
- `-1`
- `101`
- `780`
- `NaN`
- `Infinity`
- `-Infinity`

`float("NaN")`や`float("Infinity")`は例外にならないため、現在の`except ValueError`では除外されない。

### 既存の正しい実装
HTML由来のリアルタイムSOCには次がある。

```python
def _validate_external_soc_percent(value: float, *, raw: str) -> float:
    if not math.isfinite(value):
        raise ValueError(...)
    if value < 0.0 or value > 100.0:
        raise ValueError(...)
    return value
```

CSV経路だけ検証規則が不一致になっている。

### 影響
- 101%以上の値で目標到達と誤判定し、早期にstandbyへ切り替わる。
- 負値で必要充電量を過大評価する。
- `NaN`が比較や推定処理へ流れ、判定が不定になる。
- SOC取得元によって同じ値の扱いが異なる。

### 修正方針
SOC検証を共通関数化し、HTMLとCSVの両方から使用するのが望ましい。
共通化が大きすぎる場合は、CSV側で最低限次を実施する。

```python
soc = float(soc_text)
if not math.isfinite(soc) or not 0.0 <= soc <= 100.0:
    continue
```

### 仕様上の選択
異常行の扱いは次のどちらかを明示する。

1. 異常行をスキップして次に新しい正常行を探す。
2. 最新行が異常ならCSV全体をunavailableとする。

推奨は1。ただし、異常値をログまたは`SocReading.error`へ残し、データ品質問題を見えなくしない。

### タイムゾーン
CSV日時は`TIMEZONE`を付与して鮮度判定しており、現行の方向性は妥当。
`SocReading.observed_at`を返す際は、unavailable時にも可能ならtimezone-aware値へ統一する。

### 必須テスト
- 正常値0、38、100を受理する。
- `-1`、`101`、`780`を拒否する。
- `NaN`、`Infinity`、`-Infinity`を拒否する。
- 最新行が異常、1つ前が正常の場合の仕様をテストする。
- 正常だが古いCSVは引き続きstaleとして拒否する。
- realtime失敗、CSV正常・新鮮ならCSVを返す。

### 完了条件
- すべてのSOC取得経路で有限値かつ0〜100のみを有効とする。
- 異常値をclampして正常扱いしない。
- 異常値の存在をログまたはエラー情報で追跡できる。

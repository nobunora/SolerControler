# SolerControler 修正作業指示書

> 対象: `C:\VSC\SolerControler`  
> 基準HEAD: `5430f40`  
> 原則: この指示書の問題だけを修正し、無関係な変更を混ぜない。


## 06. SOC異常値をclampして正常値へ変換する

### 優先度
**P1**

### 対象
- `app/kpnet_workflow.py`
- `_extract_simple_visualization_soc_percent()`
- `SOCBounds.clamp`

### 調査結果
外部HTMLから解析したSOCへ `SOCBounds.clamp()` を適用している。

例:
- `780` → `100`
- `-5` → `0`

### 根本原因
内部計算値の範囲調整と、外部測定値の妥当性検証を同じ処理で扱っている。

### 影響
- HTML誤解析を検出できない。
- 異常値を本物の0%/100%として扱う。
- 100%へ丸めた場合、目標到達として誤停止する可能性がある。
- データ異常の証拠が消える。

### 修正方針
外部入力はclampせずvalidateする。

推奨:
```python
value = float(parsed)
if not math.isfinite(value):
    raise ValueError("SOC is not finite")
if value < 0.0 or value > 100.0:
    raise ValueError(f"SOC out of range: {value}")
return value
```

### 実装手順
1. 外部SOC専用validate関数を作る。
2. parser内の `SOCBounds.clamp` を削除する。
3. 範囲外・非有限値を取得失敗として扱う。
4. 上位で02のfallbackへ進める。
5. ログへ元の文字列と解析値を残す。
6. 表示丸めはvalidate後に別処理で行う。

### 必須テスト
- 0
- 100
- 78.5
- -0.1
- 100.1
- 780
- 数値なし
- 数値が複数
- 非有限値相当

### 完了条件
- 範囲外SOCが0または100へ変換されない。
- 異常入力をSOC取得失敗として追跡できる。

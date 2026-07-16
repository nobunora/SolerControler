# SolerControler 修正作業指示書

> 対象: `C:\VSC\SolerControler`  
> 基準HEAD: `5430f40`  
> 原則: この指示書の問題だけを修正し、無関係な変更を混ぜない。


## 05. `fa-battery-quarter` 未対応

### 優先度
**P1**

### 対象
- `app/kpnet_workflow.py`
- `_extract_simple_visualization_soc_percent()`
- `tests/test_kpnet_workflow.py`

### 調査結果
構造化HTML解析で認識しているアイコン:
- `fa-battery-full`
- `fa-battery-three-quarters`
- `fa-battery-half`
- `fa-battery-empty`

`fa-battery-quarter` がない。

### 根本原因
バッテリーアイコン候補を手動列挙した際に25%用クラスが抜けた。また、アイコンと最初の `td.rb_cell` だけで対象値を決めており、列見出しとの対応を確認していない。

### 影響
- KP-NETが25%付近でquarterアイコンを出すと構造化解析が失敗する。
- 正規表現fallbackで偶然取れる場合があっても仕様上保証されない。
- 複数の `rb_cell` があるHTMLでは別の数値を拾う可能性がある。

### 修正方針
最小修正ではquarter selectorを追加する。推奨修正では、アイコン名だけでなく「蓄電残量」見出しと対応セルを確認する。

### 実装手順
1. selectorに `.fa-battery-quarter` を追加する。
2. 対象tableに蓄電池関連アイコンがあることを確認する。
3. table見出しから「蓄電残量」に対応する列indexを特定する。
4. 同じ行の対応 `td` から値を読む。
5. 単純に最初の `td.rb_cell` を採用しない。
6. regex fallbackも蓄電池table付近へ範囲を限定する。
7. HTML構造不一致は `None` または明示的解析失敗とする。

### 必須テスト
- full
- three-quarters
- half
- quarter
- empty
- 複数table
- バッテリー以外の `rb_cell`
- 複数数値セル
- 見出し順変更

### 完了条件
- quarterアイコンでSOCを取得できる。
- 無関係なセルをSOCとして扱わない。
- 正常系だけでなく構造差分テストがある。

### テスト
```powershell
python -m pytest -q tests/test_kpnet_workflow.py
```

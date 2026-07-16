# SolerControler 修正作業指示書

> 対象: `C:\VSC\SolerControler`  
> 基準HEAD: `5430f40`  
> 原則: この指示書の問題だけを修正し、無関係な変更を混ぜない。


## 12. `dashboard_server._html()` 約1826行の巨大関数

### 優先度
**P2 / 構造改善**

### 対象
- `dashboard_server.py`
- `_html()`

### 調査結果
1関数に以下が混在する。
- HTML
- CSS
- JavaScript
- グラフ生成
- SOC推定
- スケジュール描画
- APIデータ処理
- イベントハンドラ
- 表示整形

### 根本原因
単一ファイル・単一文字列へ機能を継ぎ足してきた。表示と計算を分離する境界がない。

### 影響
- 小変更で全体を壊しやすい。
- JavaScript計算を単体テストしにくい。
- diffが巨大でレビュー不能になりやすい。
- 古い処理や重複処理が残りやすい。
- 低能力エージェントが変更箇所を誤る。

### 修正方針
動作を変えず、段階的に分離する。全面書換えは禁止。

### 目標構造
- `templates/dashboard.html`
- `static/dashboard.css`
- `static/dashboard.js`
- `static/dashboard_charts.js`
- `static/dashboard_schedule.js`
- `app/dashboard_view_model.py`

### 実装順序
1. 現行HTMLの重要要素ID一覧をテスト化する。
2. CSSだけ外部ファイルへ移す。
3. JavaScriptをそのまま外部ファイルへ移す。
4. CSP nonce・static配信を確認する。
5. グラフ初期化処理を別モジュールへ分ける。
6. SOC・時間按分などの計算を純粋関数へ分ける。
7. Python側で生成できるview modelはPythonへ移す。
8. 最後に未使用コードを削除する。

### 禁止事項
- 分離と同時にUIデザインを変えない。
- 計算式を変えない。
- API payload schemaを変えない。
- 大量renameを混ぜない。
- minifyしない。

### 必須テスト
- 主要DOM IDが存在。
- API endpointが同じ。
- CSP nonceが維持。
- static assetが配信される。
- 主要グラフが初期化される。
- 既存payloadを読み込める。
- 分離前後の重要表示値が一致。

### 完了条件
- `_html()` がテンプレート読込み中心になり、数百行以下。
- 計算ロジックを独立テストできる。
- 見た目と動作を変えない。

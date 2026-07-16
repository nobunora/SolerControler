# SolerControler 修正作業指示書

> 対象: `C:\VSC\SolerControler`  
> 基準HEAD: `5430f40`  
> 原則: この指示書の問題だけを修正し、無関係な変更を混ぜない。


## 10. Firestore daily reviewでレビュー日と予測日が一致しない

### 優先度
**P1**

### 対象
- `app/dashboard_data.py`
- `_load_firestore_slice()`
- `_build_firestore_daily_review()`

### 調査結果
`forecast_hourly` は `end_date_iso` の1日分だけ取得する。

一方、レビュー対象日は `actual_pv_kwh` が存在する最新日として後から決める。

例:
- `end_date_iso=2026-07-13`
- 実績が揃う最新日=`2026-07-12`
- forecast_hourly取得日=`2026-07-13`
- review_date=`2026-07-12`
- 7/12のhourly予測は空

その結果、本来の当日予測ではなくrolling forecast等へフォールバックする。

### 根本原因
表示対象日の時間別予測と、レビュー対象日の時間別予測を同じ変数で兼用している。レビュー日決定が予測取得より後にある。

### 影響
- 実際に当日使った予測と実績を比較できない。
- 予測誤差・モデル評価を誤る。
- daily reviewの数値出所が不明確になる。

### 修正方針
レビュー日を先に決め、その日付のforecast_hourlyとplanを取得する。

### 実装手順
1. monitoring/energyデータから `review_date` を決める。
2. 画面表示用 `end_date_iso` のhourlyと、レビュー用 `review_date` のhourlyを分離する。
3. review用hourlyを `review_date` 条件で取得する。
4. `_build_firestore_daily_review()` へreview専用hourlyを渡す。
5. plan documentも `review_date` を使用する。
6. review用hourly欠損時は黙って別予測へ置換しない。
7. 代替値を使う場合は `forecast_load_source` を明示する。
8. `review_date`, `forecast_date`, `plan_date` を出力し、同一性を検証できるようにする。

### 必須テスト
- review_dateとend_dateが同日。
- review_dateが前日。
- review日hourlyあり。
- review日hourlyなし。
- plan documentなし。
- actualだけあり。
- forecast sourceが明示される。

### 完了条件
- review対象の予測日・実績日・plan日が一致する。
- 欠損時の代替元が明示される。

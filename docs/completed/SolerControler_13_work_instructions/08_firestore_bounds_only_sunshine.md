# SolerControler 修正作業指示書

> 対象: `C:\VSC\SolerControler`  
> 基準HEAD: `5430f40`  
> 原則: この指示書の問題だけを修正し、無関係な変更を混ぜない。


## 08. Firestore版だけ表示期間を`sunshine_daily`単独で決める

### 優先度
**P1**

### 対象
- `app/dashboard_data.py`
- `_load_firestore_slice()`
- `_firestore_bounds()`

### 調査結果
Firestore版:
```python
global_oldest, global_newest = _firestore_bounds(client, "sunshine_daily")
```

SQLite/PostgreSQL版は複数データ源を総合している。
- sunshine_daily
- cost_daily
- battery_daily_metrics
- forecast_hourly
- monitoring_samples

### 根本原因
Firestore高速化時に境界取得を単一コレクションへ簡略化した。バックエンド共通仕様がない。

### 影響
- sunshineが空なら、monitoringがあっても全体を空として返す。
- battery/cost/forecastの最新日がsunshineより新しくても表示できない。
- 古いmonitoring履歴がsunshine開始前なら遡れない。
- 同じ論理データでもbackendで表示期間が変わる。

### 修正方針
Firestoreでも全データ源のmin/maxを集約する。

### 実装手順
1. `_get_global_bounds_firestore(client)` を作る。
2. dateフィールドのあるcollection:
   - sunshine_daily
   - cost_daily
   - battery_daily_metrics
   - forecast_hourly
3. monitoring_samplesは `ts` の先頭10文字またはTimestampを日付化する。
4. 空collectionは無視する。
5. 一部query失敗でも、取得できた境界を使用する。
6. `_pick_min_max_dates()` で全候補を集約する。
7. query数が大きい場合は境界metadata documentを別途管理する。ただし最初の修正で最適化を混ぜない。

### 必須テスト
- sunshine空・monitoringあり。
- sunshineよりbatteryが新しい。
- costだけ古い履歴がある。
- 一部collection query失敗。
- 全collection空。
- SQLite/Postgres相当の境界になる。

### 完了条件
- sunshineがなくても他データを表示できる。
- 3backendの境界決定規則が同じ。

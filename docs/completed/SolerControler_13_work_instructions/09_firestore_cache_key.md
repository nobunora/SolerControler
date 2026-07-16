# SolerControler 修正作業指示書

> 対象: `C:\VSC\SolerControler`  
> 基準HEAD: `5430f40`  
> 原則: この指示書の問題だけを修正し、無関係な変更を混ぜない。


## 09. Firestoreキャッシュキーに接続先が含まれない

### 優先度
**P1**

### 対象
- `app/dashboard_data.py`
- `_FIRESTORE_CLIENTS`
- `_FIRESTORE_SLICE_CACHE`
- `_open_dashboard_firestore_client()`
- `load_dashboard_slice()`

### 調査結果
client cache key:
```python
(project_id, database_id)
```

slice cache key:
```python
(end_date, days, include_static)
```

project/databaseがslice cache keyにない。

### 根本原因
クライアントキャッシュとデータキャッシュを別設計し、接続先識別子を共有していない。

### 影響
同一プロセス中に接続先を変更すると、別project/databaseの結果が最大120秒返る可能性がある。

発生し得る場面:
- 自動テスト
- 開発/本番切替
- 複数databaseを扱う管理プロセス
- 環境変数の動的変更

### 修正方針
slice cache keyへ `project_id` と `database_id` を含める。

推奨:
```python
(project_id, database_id, end_date, days, include_static)
```

### 実装手順
1. Firestore接続設定を返す共通関数を作る。
2. client作成とslice cacheの両方で同じ設定値を使う。
3. cache key型を更新する。
4. `clear_dashboard_cache()` を追加する。
5. テスト終了時や設定変更時にclear可能にする。
6. TTL経過後は再取得する。
7. cache書込み時刻は取得開始ではなく、取得完了後を推奨する。

### 必須テスト
- 同project/database・同条件はcache hit。
- 別projectはcache miss。
- 別databaseはcache miss。
- TTL超過でreload。
- clear後にreload。
- 取得例外時に不完全な値をcacheしない。

### 完了条件
- 別接続先のデータが混ざらない。
- cacheの隔離をテストで確認できる。

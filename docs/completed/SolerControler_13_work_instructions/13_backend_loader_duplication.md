# SolerControler 修正作業指示書

> 対象: `C:\VSC\SolerControler`  
> 基準HEAD: `5430f40`  
> 原則: この指示書の問題だけを修正し、無関係な変更を混ぜない。


## 13. バックエンド別ダッシュボードローダーの巨大重複

### 優先度
**P2 / 構造改善**

### 対象
- `app/dashboard_data.py`
- `_load_sqlite_slice()` 約248行
- `_load_postgres_slice()` 約248行
- `_load_firestore_slice()` 約204行

### 調査結果
同じ `DashboardData` を作る処理が3実装へ分散し、取得・正規化・集計・警告生成が重複している。

既に確認された非対称性:
- Firestoreだけglobal boundsがsunshine単独。
- Firestoreだけforecast_hourlyがend_dateの1日分。
- static取得、latest schedule、review作成の方法がbackendごとに異なる。

### 根本原因
backend追加時に既存ローダーを複製し、その後それぞれへ機能追加した。共通のraw schemaと組立関数がない。

### 影響
- 同じ論理データでもbackendで表示が変わる。
- 修正を3箇所へ反映する必要がある。
- 一方だけ修正される。
- parity testがない。
- 仕様が各SQL/Firestore queryへ埋没する。

### 修正方針
backend層は生データ取得だけにし、正規化・集計・警告・schedule組立は共通関数へ移す。

### 目標データ構造
```python
@dataclass
class DashboardRawData:
    pv_daily: list[dict]
    cost_daily: list[dict]
    battery_daily: list[dict]
    monitoring_daily: list[dict]
    forecast_hourly: list[dict]
    settings_events: list[dict]
    model_parameters: list[dict]
    global_oldest: str | None
    global_newest: str | None
```

共通:
```python
build_dashboard_slice(raw, end_date, window_days, include_static)
```

backend別:
- `fetch_sqlite_dashboard_raw()`
- `fetch_postgres_dashboard_raw()`
- `fetch_firestore_dashboard_raw()`

### 実装手順
1. 3ローダーの取得フィールド一覧を比較表にする。
2. 共通RawData dataclassを作る。
3. SQLiteだけ先にRawData化する。
4. 共通build関数を作り、SQLiteテストを通す。
5. PostgreSQLを移行する。
6. Firestoreを移行する。
7. backend別関数から集計・警告処理を削除する。
8. 旧重複コードを削除する。
9. parity testを追加する。

### Parity testで比較する項目
- `energy_daily`
- `latest_schedule`
- `dashboard_warnings`
- `meta`
- `daily_review`
- `cost_monthly`
- `forecast_hourly`
- 欠損値の扱い

### 禁止事項
- 構造分離とSQL最適化を同時に行わない。
- payloadフィールド名を変更しない。
- backend固有の値を共通値と混同しない。
- 3backendを一度に書き換えず、1backendずつ移行する。

### 完了条件
- 集計・警告・schedule組立ロジックが1箇所。
- backend差は取得方式だけ。
- 3backend parity testがある。

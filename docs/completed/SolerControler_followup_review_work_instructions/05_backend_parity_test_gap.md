# SolerControler 追加修正作業指示書

> 対象: `C:\VSC\SolerControler`  
> 基準HEAD: `7681fe2`  
> 原則: この指示書の問題だけを修正し、無関係な変更を混ぜない。

## 05. backend parityテストが実際のbackendを通していない

### 優先度
**P2 / 中**

### 対象
- `tests/test_dashboard_data.py`
- `test_dashboard_raw_data_build_has_backend_parity()`
- SQLite/PostgreSQL/Firestore各ローダー
- `DashboardRawData`
- `_build_dashboard_slice()`

### 調査結果
現在のテストは次の形になっている。

```python
@pytest.mark.parametrize("backend", ["sqlite", "postgres", "firestore"])
def test_dashboard_raw_data_build_has_backend_parity(backend: str) -> None:
    raw = DashboardRawData(...)
    sliced = _build_dashboard_slice(raw, ...)
```

`backend`は実際のローダー選択やデータ取得に使われず、assert失敗時のメッセージにしか使われていない。

### 現在保証できていること
- `_build_dashboard_slice()`が与えられたRawDataを概ね保持する。

### 現在保証できていないこと
- SQLite、PostgreSQL、Firestoreが同じ論理データから同じ`DashboardRawData`を生成する。
- backend間で欠損値、日付、集計、最新イベント選択が一致する。

### 影響
テスト名からはbackend parityを保証しているように見えるが、実際のadapter差異を検知できない。
特に次がbackend別にずれる可能性がある。

- `cost_monthly`
- `latest_schedule`
- monitoringの日次集約
- timestamp・日付の正規化
- 欠損値の扱い
- 並び順
- Firestore固有のdocument ID fallback

### 修正方針
最低限、現在のテスト名を実態に合わせて変更する。

例:

```text
test_build_dashboard_slice_preserves_raw_fields
```

その上で、可能な範囲で実adapter parityテストを追加する。

### 推奨テスト構成
1. 同じ論理fixtureを定義する。
2. SQLiteは一時DBへ投入して`load_dashboard_slice()`を通す。
3. PostgreSQLはcursor/connectionをモックし、SQL結果を同fixtureから返す。
4. Firestoreはcollection/query/documentをモックし、同fixtureを返す。
5. 各backendの最終`DashboardSlice`または正規化済み`DashboardRawData`を比較する。

### 比較対象
- `pv_daily`
- `cost_daily`
- `cost_monthly`
- `battery_daily`
- `battery_flow_daily`
- `energy_daily`
- `forecast_hourly`
- `latest_schedule`
- `global_oldest_date`
- `global_newest_date`
- `daily_review`

### 必須ケース
- 同一日の新旧settings event。
- monitoringのみ存在しsunshineがない期間。
- battery dateがplan dateと異なる。
- forecast実績最新日が表示end dateより前。
- null、空文字、0を区別するデータ。

### 完了条件
- テスト名と実際の保証範囲が一致する。
- 少なくとも主要3backendの正規化結果差を検知できる。
- 共通ビルダーのテストとadapterテストを分離する。

# SolerControler 追加修正作業指示書

> 対象: `C:\VSC\SolerControler`  
> 基準HEAD: `7681fe2`  
> 原則: この指示書の問題だけを修正し、無関係な変更を混ぜない。

## 06. 最新スケジュール選択が入力順へ依存する

### 優先度
**P2 / 中**

### 対象
- `app/dashboard_data.py`
- `_build_latest_schedule_from_events()`
- `tests/test_dashboard_data.py`

### 調査結果
候補選択は現在次で行われる。

```python
schedule_row = min(candidates, key=_schedule_priority, default=None)
```

優先度は次の通り。

1. `03-monitor`
2. `03-no-charge`
3. その他

同じ優先度のイベントが複数ある場合、`min()`は最初の候補を採用する。
`recorded_at`は比較していない。

完了イベントも次の`next()`で最初の一致行を採用するため、同一run内の複数状態では入力順へ依存する。

### 現在直ちに障害化しにくい理由
現行呼び出し元はすべて新しい順を渡している。

- SQLite: `ORDER BY recorded_at DESC, event_id DESC`
- PostgreSQL: `ORDER BY recorded_at DESC, event_id DESC`
- Firestore: 昇順tail取得後に`reversed()`

したがって現時点の実運用では最新が先頭になる。

### 残る問題
共通関数の契約として「入力は必ず新しい順」が明示・検証されていない。
呼び出し元の変更、単体利用、モック順序の変更で古いイベントを選ぶ。

### 修正方針
関数内で優先度と時刻を明示的に比較する。

推奨概念:

```python
(priority, recorded_at, source_doc_id)
```

- 優先度は小さい方を優先。
- 同優先度では`recorded_at`の新しい方を優先。
- 同時刻では安定したdocument/event IDで決定する。

日時文字列がISO 8601でない場合のfallback規則も決める。

### 完了イベント
`chosen_run_id`に一致する完了候補から、最新`recorded_at`を選ぶ。
単なる`next()`にしない。

### 必須テスト
- 新イベントを先頭にした場合。
- 新イベントを末尾にした場合。
- event_rowsをランダム順にした場合。
- 同一優先度・異なるrecorded_at。
- 同時刻・異なるevent ID。
- 同一runに複数完了イベントがある場合。
- `03-monitor`が古く、通常イベントが新しくてもmonitor優先を維持する場合。
- monitorが複数ある場合は最新monitorを選ぶ。

### 完了条件
- 入力順を変えても結果が変わらない。
- 優先順位と新旧判定を関数単体で説明できる。
- 予定イベントと完了イベントが同一runで、各々最新になる。

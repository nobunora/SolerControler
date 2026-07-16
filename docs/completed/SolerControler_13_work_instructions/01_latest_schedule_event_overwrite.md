# SolerControler 修正作業指示書

> 対象: `C:\VSC\SolerControler`  
> 基準HEAD: `5430f40`  
> 原則: この指示書の問題だけを修正し、無関係な変更を混ぜない。


## 01. 同一日の古いイベントが最新スケジュールを上書きする

### 優先度
**P0 / 最優先**

### 対象
- `app/dashboard_data.py`
- `_build_latest_schedule_from_events()`
- SQLite/PostgreSQL/Firestoreの各ローダー

### 調査結果
`settings_events` は新しい順で渡されるが、関数は全件を走査し、古い行でも `schedule[key] = value` を実行する。

再現済み条件:
- 同一 `plan_date`
- 両方 `schedule_source="03-monitor"`
- 新イベント: `charge_end_time="07:00"`
- 旧イベント: `charge_end_time="06:00"`

再現結果:
- `charge_end_time="06:00"`
- `recorded_at` は旧イベント
- `settings_completed_run_id` は新run

予定値と完了情報が別runから合成される。

### 根本原因
- 採用イベントを先に1件へ固定していない。
- `chosen_row` は更新判定にしか使われず、古い値の書込みを防いでいない。
- `schedule_source_locked` はmonitor以外を除外するだけで、複数monitor間の競合を防がない。
- 完了イベントと予定イベントを別々に選び、run整合性を保証していない。

### 影響
- 最新の開始・終了時刻、SOC目標、運転モードが古い値へ戻る。
- 表示上の完了runと予定runが一致しない。
- 障害調査や運用判断を誤らせる。
- 全バックエンド共通で発生する。

### 修正方針
1. `plan_date` が一致する行だけを候補にする。
2. 候補から採用する予定イベントを1件だけ決める。
3. 優先順位:
   1. 最新 `03-monitor`
   2. 最新 `03-no-charge`
   3. 最新のその他イベント
4. 予定項目は採用した1イベントの `detail_json` だけからコピーする。
5. `settings_completed_*` は原則、同じ `run_id` の完了イベントから取得する。
6. 同runの完了イベントがない場合だけ、同日battery metricを補助情報として使う。
7. 異なるrunの値を寄せ集めない。

### 実装手順
1. `event_rows` から `detail_json` を解析した候補リストを作る。
2. `detail.plan_date` 欠損または不一致を除外する。
3. 優先順位で `schedule_row` を決める。
4. 既存の全件上書きループを削除する。
5. `schedule_row` から値を一度だけ設定する。
6. `completed_row` は `schedule_row.run_id` と一致する行から選ぶ。
7. battery fallbackにも日付一致を必須とする。

### 必須テスト
- 新07:00・旧06:00で結果が07:00。
- `recorded_at` と `settings_completed_run_id` が同じ最新run。
- 異なるrunの完了情報を混ぜない。
- monitorを通常イベントより優先。
- monitorがない場合だけno-charge/通常へフォールバック。

### 完了条件
- 古いイベントが最新値を上書きしない。
- 予定値・status・recorded_at・完了runを同一runとして説明できる。
- `tests/test_dashboard_data.py` が成功する。

### テスト
```powershell
python -m pytest -q tests/test_dashboard_data.py
```

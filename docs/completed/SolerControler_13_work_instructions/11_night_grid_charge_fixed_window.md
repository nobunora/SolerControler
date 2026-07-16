# SolerControler 修正作業指示書

> 対象: `C:\VSC\SolerControler`  
> 基準HEAD: `5430f40`  
> 原則: この指示書の問題だけを修正し、無関係な変更を混ぜない。


## 11. 夜間系統充電を04:00～07:00へ固定配分する

### 優先度
**P1**

### 対象
- `dashboard_server.py`
- `estimateHourlyNightGridCharge()`
- `estimateHourlyForecastSoc()`
- `latestSchedule.charge_start_time`
- `latestSchedule.charge_end_time`

### 調査結果
現在の配分条件:
```javascript
hour >= 4 && hour < 7
```

実運転予定は可変の `charge_start_time` / `charge_end_time` を持つ。

例:
- 実予定: 02:43～07:00
- グラフ: 04:00～07:00へ均等配分

予定バーとSOC/充電グラフが矛盾する。

### 根本原因
最初の表示実装で固定時刻を仮定し、その後の動的開始時刻へ追従していない。総量だけbattery rowから取り、時間形状を別ロジックで仮定している。

### 影響
- 04:00以前の充電とSOC上昇が表示されない。
- 時間別電力・SOCが誤る。
- 端数開始時刻を無視する。
- 同一画面内の予定バーとグラフが食い違う。

### 修正方針
実スケジュール区間と各1時間区間の重なり分数で按分する。

### 実装手順
1. scheduleのstart/endを分へ変換する。
2. 各hourを `[h*60, (h+1)*60)` とする。
3. 充電区間との重複分数を計算する。
4. 全重複分数の合計で比率化する。
5. `night_charge_kwh * overlap_minutes / total_overlap_minutes` を配分する。
6. 例: 02:43～07:00
   - 02時: 17分
   - 03～06時: 各60分
7. start/end欠損時だけ明示的fallbackを使う。
8. start==end、逆転、0時跨ぎは既存制約に従って異常扱いまたは明示対応する。
9. 配分合計が総量と一致することをassert/testする。
10. SOC推定も同じ配列を使う。

### 推奨
計算を巨大HTML文字列内のJavaScriptへ残さず、独立した純粋関数またはPython側へ移す。

### 必須テスト
- 04:00～07:00。
- 02:43～07:00。
- 06:30～07:00。
- start/end欠損。
- start==end。
- 総量0。
- 配分合計が元総量と一致。
- 予定バーの時刻と配分開始が一致。

### 完了条件
- 実時刻に沿って配分される。
- グラフと予定バーが一致する。

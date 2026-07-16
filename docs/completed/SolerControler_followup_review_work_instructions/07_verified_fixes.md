# SolerControler 再レビューで修正確認済みの項目

> 再レビュー時HEAD: `7681fe2`  
> このファイルは追加修正指示ではなく、前回指摘に対して確認できた改善点の記録である。

## 修正確認済み

### 1. 同日内の古いスケジュールによる上書き対策
- plan date一致候補を抽出する構造へ変更された。
- `03-monitor`、`03-no-charge`、その他の優先順位が追加された。
- 選択runと異なる完了イベントの混入を防ぐ方向へ改善された。
- battery fallbackはplan date一致が必要になった。

残課題は`06_latest_schedule_order_dependency.md`に記載。

### 2. リアルタイムSOC取得の再試行とCSV fallback
- retry回数とdelayを環境変数化。
- realtime失敗時に新鮮なCSVへfallback。
- CSVの最大鮮度を設定可能。
- 古いCSVはunavailableになる。

残課題はCSV値検証と初回unavailable時の開始方針。

### 3. logout失敗で取得済みSOCを失わない
- `read_realtime_soc_percent()`の戻り値を保持したままlogout例外をログ化。
- logout失敗で正常SOCが例外へ置き換わらない。
- 対応テストあり。

### 4. 監視中SOC連続失敗時のfail-safe
- 連続失敗回数をカウント。
- 上限到達時にstandbyを適用。
- stop reasonとして`soc_unavailable_fail_safe`を永続化。
- timer timeout時にもstandbyとstop reasonを記録。

### 5. バッテリーquarterアイコン対応
- `.fa-battery-quarter`がSOC表示テーブル判定へ追加された。

### 6. HTML由来SOCの異常値拒否
- `math.isfinite()`を使用。
- 0〜100外を`ValueError`にする。
- 外部SOCをsilent clampしない。

### 7. 目的関数説明とoptimizerの整合
- 表示説明が実装している目的関数へ合わせて修正された。

### 8. Firestore全体期間の取得
- `sunshine_daily`だけでなく、`cost_daily`、`battery_daily_metrics`、`forecast_hourly`、`monitoring_samples`を統合してboundsを決定する。

### 9. Firestoreキャッシュの分離
- cache keyに`project_id`と`database_id`が含まれる。
- client cacheも同じ構成で分離される。

### 10. Firestore日次レビューの日付整合
- 実績PVが存在する最新日を`review_date`として再選択。
- review dateに合わせてforecast hourlyとnight charge planを取得。

### 11. 夜間充電量を実スケジュール区間へ配分する実装
- 開始・終了時刻の分単位overlapで配分するコードが追加された。
- 跨日区間にも対応。
- 配分合計で総量を保存する構造。

ただし正規表現の二重エスケープにより現状は実時刻解析が失敗する。`02_dashboard_time_regex_double_escape.md`を参照。

### 12. Dashboard HTML/CSS/JS分離
- 巨大な`_html`から次へ分離された。
  - `templates/dashboard.html`
  - `static/dashboard.css`
  - `static/dashboard.js`
- サーバー側は許可リスト形式で静的資産を配信する。
- パスは`Path(__file__).parent`基準。

ただしDockerfileへCOPYされていない。`01_dashboard_container_missing_assets.md`を参照。

### 13. Dashboard slice共通組立て
- `DashboardRawData`と`_build_dashboard_slice()`が追加された。
- meta、warnings、DashboardData組立てを共通化。

残課題はbackend loader自体の正規化・集約差と、実adapter parityテスト。

## 確認結果
- 全テスト: `199 passed in 8.78s`
- 差分検査: `git diff --check 5430f40..HEAD` は異常なし
- この再レビューではコード変更なし

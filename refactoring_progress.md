# リファクタリング進捗ログ

会話履歴とは別に、各リファクタリング単位の変更内容、検証結果、コミットを記録する。

## 2026-08-01 — 基準コミットと最初の安全な抽出

- 基準コミット: `6b18485 docs: establish readable code audit baseline`
- リファクタリングコミット: `8a7fdeb refactor: split dashboard and forecast preparation helpers`

実施内容:

1. `cloud_job_runner.py`
   - 初期SOCを取得できない場合の安全停止を `_keep_standby_when_initial_soc_is_unavailable` に抽出。
   - プロファイル適用と停止理由保存を一つの `try/finally` 境界に保持。
2. `app/dashboard_data.py`
   - `_empty_dashboard_slice` を抽出し、SQLiteのDB不存在・データなし・日付不正時に共通利用。
   - 既存レスポンスのスキーマとメタデータ値を保持。
3. `app/forecast_correction.py`
   - 予測内の天候、天候API、日次気温フォールバックを `_target_weather_from_forecast` に抽出。
   - 元のフォールバック優先順位を保持。

検証（変更前）:

- Python: `394 passed, 1 skipped`
- JavaScript: `3 passed`

検証（変更後）:

- Python: `394 passed, 1 skipped`
- JavaScript: `3 passed`
- `python -m compileall -q app cloud_job_runner.py`: 成功
- `git diff --check`: 成功

## 2026-08-01 — 予測天候ヘルパーの契約テスト

- コミット: `2a36914 test: cover forecast weather helper priority`
- `tests/test_energy_model.py` に `test_target_weather_from_forecast_prefers_valid_hourly_payload` を追加。
- 予測内に有効な時間別天候がある場合、外部天候APIへフォールバックしないことを確認。
- 個別検証: `python -m pytest -q tests/test_energy_model.py -k 'target_weather_from_forecast or build_forecast_correction'`
- 結果: `4 passed, 45 deselected`

## 次の単位

- `app/dashboard_data.py` の空レスポンス生成を、PostgreSQLとFirestoreにも安全に共通化できるかを確認する。既存テストを先に確認し、共通化する場合は専用の回帰テストを追加する。

## 2026-08-01 — ダッシュボード空レスポンスの全バックエンド共通化

- コミット: `e346e43 refactor: share dashboard empty responses`
- `app/dashboard_data.py` の `_empty_dashboard_slice` をPostgreSQLおよびFirestoreの空データ経路にも適用。
- 既存のSQLite経路と同じメタデータ形式を共有し、空レスポンス定義の重複を削減。
- `tests/test_dashboard_data.py` に `test_empty_dashboard_slice_preserves_requested_window_and_global_bounds` を追加。
- 個別検証: `python -m pytest -q tests/test_dashboard_data.py`
- 結果: `34 passed`

## 2026-08-01 — 初期SOC欠損時の安全停止順序テスト

- コミット: `1bf9ef0 test: cover forced charge standby helper`
- `tests/test_cloud_job_runner.py` に `test_initial_soc_unavailable_helper_applies_standby_before_persisting` を追加。
- standbyプロファイルを適用してから停止理由を保存する順序を、抽出したヘルパー単体で保証。
- 個別検証: `python -m pytest -q tests/test_cloud_job_runner.py -k 'initial_soc_unavailable or monitor_keeps_standby'`
- 結果: `2 passed, 42 deselected`

## 2026-08-01 — 安全な抽出単位の統合確認

- 対象: `tests/test_energy_model.py`、`tests/test_dashboard_data.py`、`tests/test_cloud_job_runner.py`
- 検証: `python -m pytest -q tests/test_energy_model.py tests/test_dashboard_data.py tests/test_cloud_job_runner.py`
- 結果: `127 passed`
- `git diff --check`: 成功
- この時点で、対象3領域の安全な抽出と、それぞれの抽出ヘルパーの個別契約テストを完了。

## 2026-08-01 — PV補正分岐の抽出

- コミット: `1cac52b refactor: isolate hourly pv correction`
- `app/forecast_correction.py` から `_correct_hourly_pv` を抽出。
- 比率補正、物理PV時の残差補正、診断情報の初期値を一つの責務へ集約。
- `tests/test_energy_model.py` に `test_correct_hourly_pv_uses_ratio_without_physical_residual` を追加。
- 物理PVではない経路で残差補正が呼ばれないことを確認。
- 個別検証: `python -m pytest -q tests/test_energy_model.py -k 'correct_hourly_pv or build_forecast_correction'`
- 結果: `4 passed, 46 deselected`

## 2026-08-01 — 低リスク段階の統合確認

- 対象: `tests/test_energy_model.py`、`tests/test_dashboard_data.py`、`tests/test_cloud_job_runner.py`
- 検証: `python -m pytest -q tests/test_energy_model.py tests/test_dashboard_data.py tests/test_cloud_job_runner.py`
- 結果: `128 passed`
- `git diff --check`: 成功
- 次の段階: 永続化や外部API呼び出しを伴わない、読み取り専用データ整形の抽出を優先する。

## 2026-08-01 — スケジュール表示イベント選択の分離（低リスク）

- コミット: `86e9bba refactor: isolate schedule event selection`
- 対象: `app/dashboard_data.py`、`tests/test_dashboard_data.py`
- 目的: ダッシュボード向けのスケジュール選択には、候補の絞り込み、優先順位付け、同順位の最新化という三つの判断があった。これらをメインのレスポンス組立てから分離し、判断規則を単体で読めて検証できるようにする。

変更前:

- `_build_latest_schedule_from_events` の内部にローカル関数と候補走査が混在していた。
- 表示値のコピー、完了状態の関連付け、候補選択が同じ関数にあり、優先順位の変更時に回帰範囲を判断しにくかった。

変更内容:

1. `_schedule_event_candidates` に、JSON詳細が有効で、要求した計画日と一致するイベントだけを残す規則を移動した。
2. `_schedule_event_priority` に、`03-monitor` を最優先、`03-no-charge` を次点、その他を最後にする規則を移動した。
3. `_event_recency_key` に、日時として解釈できるイベントを優先し、時刻、安定IDの順で比較する規則を移動した。
4. `_select_schedule_event` に、最高優先度の候補から最新イベントを選ぶ処理を移動した。
5. 完了状態の判定は、抽出前と同じく「計画日に一致した候補」だけを対象にした。別日イベントを同一計画の完了として混ぜない契約は変更していない。

不変条件:

- 監視イベントは、より新しい通常イベントより優先して表示する。
- 同じ優先度では、より新しい `recorded_at` を選ぶ。
- 日付が異なる、日付を持たない、またはJSON詳細を読めないイベントは、計画日を指定した表示には使わない。
- 完了状態は、選ばれたイベントと同じ `run_id` の完了イベントだけから導く。
- データベース書込み、外部API呼出し、返却JSONのキー・値の形式は変更していない。

個別テスト:

- 追加: `test_select_schedule_event_uses_source_precedence_then_newest_event`
  - 通常イベントの時刻がより新しくても、監視イベントを優先することを確認。
  - 複数の監視イベントでは、新しいイベントを選ぶことを確認。
- 既存回帰: 計画日混在、イベント並び順、同一runの完了状態、非充電判断の優先順位を含むテストを実行。
- 実行コマンド: `python -m pytest -q tests/test_dashboard_data.py -k 'select_schedule_event or latest_schedule'`
- 結果: `9 passed, 26 deselected in 0.46s`
- 形式検査: `git diff --check` 成功。

残るリスクと次の段階:

- この変更は読み取り専用の整形処理であり、副作用はない。一方、後続の候補には予測補正や計画計算のように数値結果へ影響するものがある。
- 次は中リスクとして、数値計算の一部分を先に純粋関数へ分離し、境界値・フォールバック値を単体テストで固定してから呼出し側を置き換える。
- 高リスクの永続化・外部装置操作は、最後に「失敗時の順序」「書込み対象」「再実行時の冪等性」を先にテスト化し、置換範囲を一分岐ずつに限定して進める。

## 2026-08-01 — 昼間目標SOCの候補比較を分離（中リスク）

- コミット: `41671fd refactor: isolate daytime target scoring`
- 対象: `app/energy_model.py`、`tests/test_energy_model.py`
- 目的: `optimize_target_soc_for_daytime` は各開始SOC候補をシミュレーションし、複数の運用目的を辞書順で比較する。比較式をループ本体から分け、優先順位が変更されないことを直接テスト可能にする。

変更前:

- 買電量、売電量、ピークSOCとの差、開始電力量を比較する式がシミュレーションループ内に埋め込まれていた。
- どの条件を先に満たすべきかはコメントを読んでループを追う必要があり、個別に検証できなかった。

変更内容:

1. `_daytime_target_score` を追加し、一候補の比較キーだけを返す純粋関数にした。
2. 比較順序は変更していない。買電の許容超過、売電の許容超過、目標ピークSOCの不足、目標からの差、実測の買電・売電量、開始電力量の順で比較する。
3. 既存の探索範囲、SOC刻み幅、充電効率、最終結果の丸め・クランプ、返却型は変更していない。

不変条件:

- 目標ピークSOCに近い候補であっても、買電許容値を超える候補より、買電を避けられる候補を優先する。
- 買電・売電・ピークSOCの比較が同点のときだけ、低い開始電力量を優先する。
- 許容値以下の買電・売電は、候補の優先度を下げない。
- シミュレーション、設定保存、外部API、入力・出力データ形式には副作用を追加していない。

個別テスト:

- 追加: `test_daytime_target_score_prioritizes_buy_before_peak_soc_and_start_energy`
  - 買電を回避する候補が、ピークSOCがより目標に近くても買電する候補より優先されることを確認。
  - それ以外が同点なら、開始電力量の低い候補を選ぶことを確認。
- 既存回帰: 目標SOC探索の優先順位と最大目標SOC制限のテストを同時に実行。
- 実行コマンド: `python -m pytest -q tests/test_energy_model.py -k 'daytime_target_score or optimize_target_soc_for_daytime'`
- 結果: `4 passed, 47 deselected in 2.44s`
- 形式検査: `git diff --check` 成功。

残るリスクと次の段階:

- 比較関数そのものは純粋だが、採用されるSOCは充電計画へ影響するため中リスクと分類した。既存の探索結果テストを同時に実行している。
- 次は高リスクの前段として、永続化処理から「入力JSONの解析・妥当性判定」だけを純粋関数へ分ける。書込み処理とトランザクション境界は変更しない。

## 2026-08-01 — 予報日次値のプラン解析を分離（高リスク領域・第1段階）

- コミット: `47b6131 refactor: isolate forecast daily plan values`
- 対象: `app/operations_db.py`、`tests/test_operations_db.py`
- リスク分類: この機能はSQLiteの `sunshine_daily` と `forecast_hourly` を更新するため高リスク領域とする。ただし本段階で変更したのはJSONから日次保存値を集める純粋関数だけであり、SQL、書込み対象、コミット、実績天候APIの呼出し順は変更していない。

変更前:

- `ingest_sunshine_from_night_plan` が、ファイル読込み、予報日次値の抽出、SQL更新、時間別行更新、実績天候取得、コミットを一つで行っていた。
- どのプラン項目が日次テーブルへ渡るかを、SQLの引数列と突き合わせて確認する必要があった。

変更内容:

1. `_forecast_daily_values_from_plan` を追加し、予報日、日照時間、気温、天候、降水、短波放射、最終PV集計、校正係数、最終予報出所を一つの辞書へ集約した。
2. `ingest_sunshine_from_night_plan` はこの辞書をSQLパラメータに変換するだけにした。
3. PV合計は既存の `_extract_final_pv_totals_from_plan` を引き続き使う。時間別PVから導いた最終値を優先する既存契約を変更していない。
4. 校正係数は従来どおり `effective_factor` を優先し、無い場合だけ `factor` を使う。

不変条件:

- 対象ファイルが無い場合は書込みをせずに戻る。
- `sunshine_daily` のUPSERT列、`forecast_hourly` のDELETE/UPSERT順序、最後の `conn.commit()` は変更していない。
- 実績天候取得の例外は従来どおり空データとして扱い、予報の書込みを失敗させない。
- PV集計には `total_kwh` 以外に `peak_kw` と `source` が含まれる場合がある。この補助フィールドは削除・正規化せず、日次SQLで使用する4区分だけを読む。

個別テスト:

- 追加: `test_forecast_daily_values_from_plan_prefers_final_pv_contract`
  - 計画日の前後空白を除去することを確認。
  - 時間別PVに基づく最終PV集計と、最終予報出所を使うことを確認。
  - `effective_factor` が `factor` より優先されることを確認。
- 既存回帰: SQLiteへ日次・時間別予報を保存する経路を同時に実行。
- 実行コマンド: `python -m pytest -q tests/test_operations_db.py -k 'forecast_daily_values or ingest_sunshine_from_night_plan'`
- 結果: `2 passed, 16 deselected in 0.38s`
- 途中検出と対処: 初回の完全辞書比較で、PV集計にはピーク値・出所も含まれることが判明した。実装は変更せず、保存対象の4区分を検証するテストへ修正した。
- 形式検査: `git diff --check` 成功。

次の高リスク手順:

1. SQL文そのものを動かさず、日次UPSERTに渡す値を名前付きパラメータへ置き換えられるか、現行テストで書込み列を固定して確認する。
2. 時間別行の削除・再作成は再実行時の冪等性に関わるため、同じプランを二回取り込む個別テストを追加してから変更する。
3. 実績天候APIの失敗・成功は予報書込みと独立であることをテスト化し、例外処理を変更する場合もその境界を越えない。

## 2026-08-01 — 今回の変更領域の統合回帰

- 対象: `tests/test_energy_model.py`、`tests/test_dashboard_data.py`、`tests/test_operations_db.py`、`tests/test_cloud_job_runner.py`
- 目的: 低リスクの表示処理、中リスクのSOC計算、高リスク領域のSQLite取込前段を同時に読み込み、モジュール間の偶発的な影響がないことを確認する。
- 実行コマンド: `python -m pytest -q tests/test_energy_model.py tests/test_dashboard_data.py tests/test_operations_db.py tests/test_cloud_job_runner.py`
- 結果: `148 passed in 14.83s`
- 確認した不変条件: ダッシュボード選択、SOC候補順位、予報日次・時間別保存、初期SOC欠損時の安全停止が同じテスト実行で全て成功した。

## 2026-08-01 — 時間別予報の保存行を分離（高リスク領域・第2段階）

- コミット: `93929f4 refactor: isolate hourly forecast persistence rows`
- 対象: `app/operations_db.py`、`tests/test_operations_db.py`
- 目的: 時間別予報の抽出結果に保存用の出所と更新時刻を付加する処理を、SQL実行部から分ける。

変更前後と不変条件:

- 変更前は、`executemany` の引数内包表記で、各行へ `source` と `updated_at` を加えていた。
- 変更後は `_hourly_forecast_rows_from_plan` が同じ行リストを作り、SQL文にはその行リストを渡す。
- `source` は全行で `night-charge-plan-hourly`、`updated_at` は呼出し側の取込時刻である契約を明示した。
- `forecast_hourly` の対象日DELETE、同じ主キーへのUPSERT、SQL列、コミット、例外処理は変更していない。従って再実行時の置換方式も変わらない。

個別テスト:

- 追加: `test_hourly_forecast_rows_from_plan_adds_persistence_metadata`
  - 時間別PV・負荷から導かれる充電量と、未指定の天候項目が既存どおりであることを確認。
  - 全行へ固定出所と取込時刻が追加されることを確認。
- 既存回帰: 日次値解析とSQLiteへの時間別保存を同時に実行。
- 実行コマンド: `python -m pytest -q tests/test_operations_db.py -k 'hourly_forecast_rows or forecast_daily_values or ingest_sunshine_from_night_plan'`
- 結果: `3 passed, 16 deselected in 0.40s`
- 形式検査: `git diff --check` 成功。

高リスク領域の残作業:

1. 同一プランを二回取り込む冪等性テストを追加し、DELETE後の再作成結果と行数が不変であることを固定する。
2. 実績天候APIが成功した場合と例外の場合を分け、予報書込みがどちらでも完了することを固定する。
3. その二つのテストが先に成功した場合だけ、日次UPSERTと実績UPSERTをそれぞれ小さな永続化ヘルパーへ抽出する。トランザクションの境界は呼出し側に残す。

## 2026-08-01 — 温度補正の学習サンプル選別を分離

- コミット: `53b1402 refactor: isolate temperature training samples`
- 対象: `app/forecast_correction.py`、`tests/test_energy_model.py`
- 目的: 温度補正の中にあった履歴日の積算、妥当性確認、残差作成を `_temperature_training_samples` として明示する。
- 不変条件: 予報負荷が0以下の日、実績/予報比が0以下または有限値でない日は学習対象から除外する。補正係数、回帰、フォールバック、環境変数、返却データには変更を加えていない。
- 追加テスト: `test_temperature_training_samples_excludes_missing_or_nonpositive_forecasts`。有効日の残差だけが学習データになることを確認。
- 実行コマンド: `python -m pytest -q tests/test_energy_model.py -k 'temperature_training_samples or temperature_correction'`
- 結果: `3 passed, 49 deselected in 2.53s`
- 形式検査: `git diff --check` 成功。

## 2026-08-01 — 再監査候補の処置と例外記録

- コミット: `6c3992f docs: record audited structural exceptions`
- 対象: `energy_model_main.py`、`app/soc_cost_optimizer.py`、`app/kpnet_workflow.py`、`app/comfort_load_forecast.py`、`app/dashboard_data.py`、`app/firestore_ops.py`、`app/operations/cost_daily.py`、`app/energy_model.py`、3つの予報取込アダプター。
- 判断: 温度補正の履歴選別は分離して修正した。一方、残った候補は同一スナップショットからの診断値生成、金額の区間丸め、外部装置操作の失敗順序、またはストレージ固有の書込み契約を一つの境界として保持する必要がある。
- 処置: 各関数の直前に `readable-code-audit: skip` を追加し、適用しない規則IDと具体的な契約理由を記録した。抽象的な「意図的」だけのスキップは使用していない。
- 確認: `python -m pytest -q tests/test_energy_model.py tests/test_operations_db.py tests/test_dashboard_data.py tests/test_comfort_load_forecast.py tests/test_soc_cost_optimizer.py tests/test_kpnet_workflow.py`
- 結果: `166 passed in 8.04s`
- 構文検査: `python -m compileall -q app energy_model_main.py` 成功。
- 形式検査: `git diff --check` 成功。

## 2026-08-01 — 修正後の全ソース再監査

- 使用ルール: `readable-code-audit`。テスト・生成物・キャッシュを除くアプリケーション、CLI、フロントエンド、設定の115ファイルを対象にした。
- 修正確認: 温度補正の履歴選別は実装として分離済み。残る先行監査の構造候補は、直近の具体的なスキップ理由を追加して再確認可能にした。
- 有効な例外: `readable-code-audit: skip` は31件。外部装置操作、金額区間計算、ストレージ別書込み、同一スナップショットの診断値生成について、適用しない理由が関数直近にある。
- AST候補: サイズ70行以上または分岐複雑度18以上の未スキップ関数は38件。これは機械的な候補であり、状態遷移表、CLIの直線的パイプライン、公開設定読込み、永続化アダプターを含む。今回、具体的な規則違反と確認できないものを違反としては数えず、機械的スキップも追加していない。
- コメント検査: `TODO`、`FIXME`、`XXX` は対象ソースで検出なし。
- 全回帰: `python -m pytest -q` を実行し、`403 passed, 1 skipped in 20.97s`。
- 構文検査: `python -m compileall -q app scripts cloud_job_runner.py dashboard_server.py db_pipeline_main.py energy_model_main.py kpnet_main.py main.py sheets_export_main.py` 成功。
- 形式検査: `git diff --check` 成功。

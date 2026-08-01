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

## 2026-08-01 — 未処置候補ゼロを完了条件とした監査サイクル

- コミット: `8b23a9b docs: record remaining audit exceptions`
- 完了条件: 監査対象の長大・高分岐関数について、未処置候補を0件にする。
- 処置: 分離済みの温度補正以外の候補は、状態遷移、外部装置操作、金額区間集計、バックエンド固有の永続化、または同一スナップショットでの診断値組立てであった。各関数直近に、適用しない規則ID（`STRUCT-04` または `DUP-01`）と具体的な安全・契約理由を追記した。
- 再監査: ASTでサイズ70行以上または分岐複雑度18以上の全Python関数を確認。直近の有効なスキップなしに残った候補は `0`。
- 全回帰: `python -m pytest -q` を実行し、`403 passed, 1 skipped in 19.89s`。
- 構文検査: 全Pythonアプリ・スクリプトを `compileall` で確認し成功。
- 形式検査: `git diff --check` 成功。

## 2026-08-01 — 追加記事に基づく条件式ルールの適用

- 追加ルール: 条件演算子は単純な値選択だけに使い、連鎖条件演算子や制御フローを隠す使い方をしない。
- 修正コミット: `7468d61 refactor: expand dashboard conditional selections`
- 対象: `static/dashboard.js`
- 変更: 軸目盛りの連鎖条件演算子を明示的な優先順の分岐へ、実績・予測負荷の入れ子条件演算子を早期return形式のマッピング関数へ置換。
- 不変条件: 目盛りは1/2/5/10の同じ系列を返す。負荷は実績を優先し、無ければ予測、両方無ければnullを返す。
- 個別検証: `node --test tests/test_dashboard_bootstrap.js tests/test_dashboard_calculations.js tests/test_dashboard_modules.js`
- 結果: `3 passed`。
- 追加監査: `tmp` と `retval` の未処置代入は0件。

## 2026-08-01 — 「プログラマが知るべき97のこと」読了後の再監査

- 読了範囲: 指定された索引の一次リンクを全件確認した後、「プログラマが知るべき97のこと」の一覧に掲載された107本をすべて読了した。
- Skills更新: `STRUCT-06` に呼出し側が扱う失敗条件の明示を追加した。`TEST-05`（不具合再現テストを先に失敗させてから修正する）と `TOOL-01`（警告は原因修正または限定的な根拠付き抑制を行う）を追加した。Skillsの構文検証は成功した。
- 再監査方法: アプリケーションとスクリプトのPython関数をASTで検査し、70行以上または分岐複雑度18以上の候補をすべて再確認した。`result` は外部JSONの契約キーやテスト対象であるため、単語だけでは命名違反としなかった。
- 判定した候補: `app/firestore_ops.py` の2関数、`app/operations_db.py`、`app/postgres_ops.py`、`app/pv_array_forecast.py`、`app/operations/domain.py`、`scripts/kpnet_soc_gap_report.py` の計7箇所。
- 判断: いずれも外部ストレージへの同一スナップショット書込み、外部API形式の正規化、または独立した診断条件を並べた決定表である。小さく分割すると、トランザクション境界・入力スナップショット・診断条件の全体像が読みにくくなるため、構造分割は行わない。
- 処置: 全7箇所の直前に `readable-code-audit: skip STRUCT-04` を追加した。理由は、混在スナップショットを防ぐ永続化境界、プロバイダー契約の正規化、または診断決定表であることを中学生程度の英語で明記した。既存の `DUP-01` 例外とは別に、今回の構造規則をスキップする理由を明確にした。
- 個別テスト: `python -m pytest -q tests/test_firestore_operations.py tests/test_operations_db.py tests/test_postgres_operations.py tests/test_pv_array_forecast.py tests/test_operations_domain.py tests/test_kpnet_soc_gap_report.py`
- 個別テスト結果: `37 passed in 1.99s`。
- 構文検査: `python -m compileall -q app scripts main.py dashboard_server.py db_pipeline_main.py energy_model_main.py kpnet_main.py sheets_export_main.py` 成功。
- 形式検査: `git diff --check` 成功。

## 2026-08-01 — 型抑制の根拠を追跡可能にする監査サイクル

- 対象ルール: `TOOL-01`。コンパイラ、型チェッカー、リンターの抑制は、根拠なく新しい問題を隠してはいけない。
- 検出: `type: ignore` は6行だけで、`app/consumption_forecast.py`、`app/comfort_load_forecast.py`、`app/night_plan_archive.py`、`app/kpnet_workflow.py` にあった。
- 確認結果: 2件は任意のscikit-learn依存がない実行環境のフォールバック、2件はGoogle Cloudの任意実行時importに対する型スタブ不足、2件はdatetime配列を表せないmatplotlib型スタブに対する抑制だった。抑制が実行時例外を握りつぶすためのものではないことを確認した。
- 処置: 各抑制の直前に `readable-code-audit: skip TOOL-01` を付け、型チェッカーが理解できない契約と、実行時の代替経路を簡単な英語で記録した。
- 個別テスト: `python -m pytest -q tests/test_consumption_forecast.py tests/test_comfort_load_forecast.py tests/test_night_plan_archive.py tests/test_kpnet_workflow.py`
- 個別テスト結果: `50 passed in 7.64s`。
- 構文検査: `python -m compileall -q app scripts` 成功。
- 形式検査: `git diff --check` 成功。

## 2026-08-01 — 例外フォールバックの契約を再確認

- 対象ルール: `STRUCT-06`、`REVIEW-03`。例外時に空値・代替値・失敗状態へ移る処理が、呼出し側に成功したように見えないかを確認した。
- 監査範囲: `app` と `scripts` のPython、およびダッシュボードJavaScriptの全ての `except Exception` / `catch` を抽出した。大半は外部API・設定・診断データの欠損に対して、空の値、理由付き結果、または画面のエラー表示へ明示的に移行していた。
- 確認した例外: `app/weekly_backup.py` のカーソルcloseだけが `pass` を使っていた。読み取り後のclose失敗は、既に組み立てたバックアップpayloadを変更できず、書込み結果を失敗扱いにすると利用可能なバックアップまで捨てるため、非致命として保持する。
- 処置: `except Exception: pass` の直前に、後始末エラーを無視してよい具体的な理由を中学生程度の英語で追加した。実行時の例外処理順・戻り値・バックアップJSONは変更していない。
- ログ訂正: 直前の型抑制サイクルにある `type: ignore` の検出件数を5行から実数の6行へ訂正した。scikit-learn 2件、Google Cloud 2件、matplotlib 2件の内訳と一致する。
- 個別テスト: `python -m pytest -q tests/test_weekly_backup.py`
- 個別テスト結果: `1 passed in 0.47s`。
- 構文検査: `python -m compileall -q app/weekly_backup.py` 成功。
- 形式検査: `git diff --check` 成功。

## 2026-08-01 — PowerShellの例外継続理由を明文化

- 対象ルール: `COMMENT-02`、`STRUCT-06`。PowerShellの例外処理で、継続・空の収集結果を返す理由がコードだけでは判断しにくい箇所を確認した。
- 確認した候補: `scripts/get_gcp_actual_cost.ps1` は同一の請求情報を異なる文面で問い合わせるリトライ列、`scripts/export_source_bundle_to_c.ps1` は収集不能なパスを除外して既に列挙済みのソース一覧を返す再帰処理だった。
- 処置: 両方の `catch` に、継続してよい不変条件を中学生程度の英語で追記した。前者は一つの問い合わせ失敗で安全な代替文面を止めないこと、後者は読めないパスからはソース本文を取得できないことを記録している。
- 不変条件: 外部API呼出し、例外時の `continue` / 返却値、生成されるファイル一覧、終了コードには変更を加えていない。
- 個別検証: `System.Management.Automation.Language.Parser::ParseFile` により両PowerShellスクリプトを構文解析した。
- 個別検証結果: `PowerShell parse check passed.`。
- 形式検査: `git diff --check` 成功。

## 2026-08-01 — 例外処理を含む更新Skillsの全体再検査

- 使用Skills: `readable-code-audit`。対象はテスト・生成物・キャッシュを除く `app`、`scripts`、ダッシュボードJavaScript、PowerShellスクリプトである。
- 例外処理検査: ASTで全Pythonの `except Exception` を確認した。`pass` だけを持つ広域例外は、理由を追記した週次バックアップのclose処理以外に存在せず、未説明候補は `0`。
- 解析フォールバックの確認: 4件の空に見える候補は、`ValueError` / `TypeError` を限定して捕捉し、数値・日付・複数入力形式を順に試すための通常制御フローだった。広域例外の握りつぶしではないため、不要なコメントやスキップは追加していない。
- 構造検査: サイズ70行以上または分岐複雑度18以上のPython関数について、直近の有効な `STRUCT-04` / `DUP-01` 例外なしに残る候補は `0`。
- コメント検査: `TODO`、`FIXME`、`XXX` は対象ソースで `0`。JavaScriptの文字列展開内にある単純な値選択は確認したが、連鎖条件演算子や制御フローを隠す三項演算子はなかった。
- 全回帰: `python -m pytest -q`
- 全回帰結果: `403 passed, 1 skipped in 19.93s`。
- Python構文検査: `python -m compileall -q app scripts cloud_job_runner.py dashboard_server.py db_pipeline_main.py energy_model_main.py kpnet_main.py main.py sheets_export_main.py` 成功。
- PowerShell構文検査: `System.Management.Automation.Language.Parser::ParseFile` により `scripts` 配下22ファイルを確認し成功。
- 形式検査: `git diff --check` 成功。

## 2026-08-01 — 機能別フォルダ構成の再判断とプロジェクトテンプレート化

- 対象ルール: `STRUCT-03`。関連する振る舞いを機能・ドメイン単位で配置し、見つけやすくする規則を再確認した。
- 現状確認: `app` には `energy_plan`、`dashboard`、`operations`、`forced_charge`、`kpnet`、`settings` の機能別パッケージが既にある。ルート直下の予測系モジュールも、PV配列予測、物理PV予測、負荷予測、補正という異なる公開境界を持つ。
- 判断: 予測系を一括移動すると、テスト、CLI、外部利用を含む既存の `app.<module>` import と、文字列指定のモンキーパッチを維持するために互換ラッパーが必要になる。現時点では発見性の改善より互換リスクと間接参照の増加が大きく、物理移動は実施しない。これは未処置違反ではなく、規則どおりに契約を確認した判断である。
- 今後の方針: 新規の複数モジュール機能は機能別パッケージを優先する。既存モジュールを移動する場合は、import、エントリーポイント、外部契約、動的参照、テストのモンキーパッチを列挙し、互換層の削除条件を先に定める。
- 再利用化: 個人用Skill `readable-code-project-starter` を `C:\Users\nobun\.codex\skills\readable-code-project-starter` に作成した。新規リポジトリへ、読みやすいコードの `AGENTS.md`、プロジェクト作業規則、Skillガバナンス規則を安全にコピーまたはマージする手順を提供する。
- 同梱知見: `readable-code-audit` の使用、`readable-code-audit: skip RULE-ID — concrete reason` の例外記法、根拠に基づく変更サイクル、機能別分割判断、英語コメントの文字コード配慮、回帰テスト先行、Skill作成後の検証をテンプレートへ組み込んだ。
- 検証: `quick_validate.py` によるSkill構造検証は `Skill is valid!`。テンプレート内の全リソースリンクは `TEMPLATE_RESOURCE_LINKS_MISSING=0`。

## 2026-08-01 — プロジェクトテンプレートへ個人用Skillを同梱

- 要求: 現在の個人用Skillsを、別環境でも使えるプロジェクトテンプレートへコピーする。
- 処置: `readable-code-project-starter` の `assets/.codex/skills/readable-code-audit/` に、個人用 `C:\Users\nobun\.codex\skills\readable-code-audit/` の全ファイルを完全コピーした。テンプレート実行時は、このフォルダを対象プロジェクトの `.codex/skills/` へ既存Skillを上書きせずにコピーする。
- 携帯性: スターターSkill自身はテンプレートのルートフォルダをコピーすることで持ち出せる。監査Skillはプロジェクトローカルにも配置されるため、個人用Codexホームに事前導入されていない別環境でも同じ監査規則を使える。
- 照合: 相対パス一覧とSHA-256を比較し、`AUDIT_SKILL_MISSING_FILES=0`、`AUDIT_SKILL_DIFFERENT_FILES=0` を確認した。
- 構造検証: `quick_validate.py` は `Skill is valid!`。テンプレート内リソースリンクの欠落は `0`。

## 2026-08-01 — ルート直下ソースの機能別分割を再監査

- 使用Skill: `readable-code-audit`。対象ルールは主に `STRUCT-03`、`STRUCT-04`、`REVIEW-02`、`REVIEW-04`。
- 対象: `app` 直下の34モジュールを、太陽光・蓄電池制御における責務、依存先、CLI、テスト、公開importで全件分類した。
- 分類結果: 運用永続化（`operations_db`、`firestore_ops`、`postgres_ops`、`db_sync`）4件、予測（PV、負荷、補正、在宅予定）6件、KP-NET 1件、ダッシュボード1件、SOC計画2件、バックアップ・アーカイブ4件、共有・入口15件、中核計算 `energy_model` 1件。
- 最優先候補: `app/operations/` に既にドメイン・費用計算があるため、3つのストレージアダプターと同期を同パッケージへ寄せる。ストレージ固有の永続化境界を集約でき、太陽光実績・予報・費用の取込先を発見しやすくする。
- 次点候補: 予測系を `app/forecasting/`、SOC最適化と中核計算を `app/planning/` または明確なSOC計画パッケージ、バックアップ群を `app/backup/` へ整理する。ダッシュボードは既存 `app/dashboard/`、KP-NETは既存 `app/kpnet/` に寄せる候補である。
- リスク確認: `forecast_correction`、`dashboard_data`、`kpnet_workflow` はテストやスクリプトが私的ヘルパーを直接import／文字列指定モンキーパッチしている。これらを物理移動する前に、テストを公開契約中心へ寄せるか、短期間の互換レイヤーと削除条件を定める必要がある。
- 方針: まず永続化アダプターを低リスクな移行単位として扱い、旧importを維持する互換モジュールと個別テストで安全を固定する。高リスクの予測・KP-NET・ダッシュボードは、その前にテスト境界を明確化する。
- 変更: この監査サイクルではソースの物理移動は行わない。監査時点で作業ツリーはクリーン、`git diff --check` も成功した。

## 2026-08-01 — 理想構成・ギャップ・段階移行計画を文書化

- 要求: ソーラーコントローラーの理想的な構成、現状とのギャップ、AIが単独で実施できる詳細なリファクタリング計画を作成する。
- 文書: `docs/current/architecture/SOLAR_CONTROLLER_MODULE_REFACTORING_PLAN_JA.md` を追加した。
- 内容: 計画・予測・運用永続化・KP-NET・ダッシュボード・バックアップの理想的な機能境界、34個のルートモジュールのギャップ、互換import、テスト境界、停止条件、フェーズ別の対象テスト、最終検証、AI向け実行制約を記録した。
- 重要判断: まず低リスクな運用永続化を `app/operations/` へ寄せる。予測、KP-NET、ダッシュボードは私的ヘルパーへのテスト依存を先に整理してから移動する。金額、SOC、安全制御、外部機器操作、外部書込みの仕様変更は構成移動と分ける。
- 変更範囲: 本サイクルは計画文書と進捗ログのみであり、アプリケーション動作、外部サービス、実機操作、保存データを変更していない。

## 2026-08-01 — モジュール再編計画の監査と実行精度改善

- 監査対象: `docs/current/architecture/SOLAR_CONTROLLER_MODULE_REFACTORING_PLAN_JA.md`。`readable-code-audit` の責務、機能配置、公開契約、テスト、例外判断の規則で再確認した。
- 検出1: 既存の `energy_plan/` と新設案 `planning/` が同じSOC計画責務を持ち、探索先が競合していた。新設 `planning/` を廃止し、既存 `energy_plan/` を計画全体の所有者として拡張する案へ修正した。
- 検出2: `app/` 直下だけを主対象にしており、ルートのPython、Dockerfile、Cloud Build YAML、mypy、pytest、requirements、ignore設定の追随が不足していた。ルートPython7件と全構成ファイルの追随台帳を追加した。
- 検出3: `energy_model_main.py` 2863行、`cloud_job_runner.py` 1485行、`db_pipeline_main.py` 426行、`dashboard_server.py` 357行は薄い入口ではなかった。対応する機能パッケージへ実装を移し、ルートを起動専用にするフェーズを追加した。
- 検出4: PowerShellで失敗する裸の `*.py` 引数、モジュール同一性・例外型・文字列モンキーパッチ・循環importの検証不足があった。PowerShell互換の `rg --glob` と追加停止条件へ修正した。
- 実行精度: Luna低インテリジェンス実行担当向けに、開始手順、許可変更、禁止変更、互換モジュール作成、終了手順、Phase 0から7と完了監査の個別カードを追加した。各カードに固定の移動元・移動先、変更前後テスト、コミット名、停止条件を指定した。
- 文書検証: Markdownコードフェンスは18個で対応、必須セクション欠落0、`git diff --check` 成功。アプリケーションコードと本番環境は変更していない。

## 2026-08-01 — モジュール再編 P0-1: 変更前基準を固定

- 実行計画: `docs/current/architecture/SOLAR_CONTROLLER_MODULE_REFACTORING_PLAN_JA.md` の P0-1。後続の構成移動で振る舞いが変わらないことを比較するため、変更前の全回帰結果と復元可能なGit基準点を記録する。
- 開始時確認: `git status --short` は出力なしで、作業ツリーはクリーンだった。基準コミットは `ad8f0c08a4e837f0671093c761226251085f068c`。
- 変更前全回帰: `python -m pytest -q` を実行した。
- 結果: `403 passed, 1 skipped in 21.12s`。失敗はなく、以降の各カードはこの結果を保持すべき動作の基準にする。
- 安全性: このカードではアプリケーションコード、設定、外部サービス、本番環境を変更していない。記録ファイルだけを追加更新する。

## 2026-08-01 — モジュール再編 P0-2: import・私的参照の移行台帳を固定

- 実行計画: モジュール移動前に、`app`、`scripts`、`tests`、ルートPythonの `app.*` import、私的helper import、文字列指定モンキーパッチ、動的importを検索した。
- 検査手順の訂正: 初回の検索は裸の `*.py` をPowerShellへ渡したため、ルートPythonのパス解釈エラーを出した。結果を採用せず、`Get-ChildItem -File -Filter '*.py'` で実在するルートPythonを明示列挙して再実行した。訂正後の検索は終了コード0で完了した。
- 動的import: `import_module` と `__import__` の使用は検出0。移動時に動的解決先を変更する必要はない。
- 私的import: `forecast_correction` の `_fetch_hourly_weather`、`_load_forecast_hourly_history_from_firestore`、`_add_thermal_states` と、`comfort_load_forecast` の `_feature_map` がテストまたは分析スクリプトから直接参照される。P2では、先に公開境界へ置換するか明示的な互換契約を作らない限り物理移動しない。
- 文字列モンキーパッチ: `forecast_correction` は7件、`dashboard_data` は7件、`kpnet_workflow` は4件、`operations.domain` は1件を確認した。`forecast_correction`、`dashboard_data`、`kpnet_workflow` の移動はテスト境界の整理カードを先行させる必要がある。一方、`operations_db`、`firestore_ops`、`postgres_ops`、`db_sync` 自体への文字列モンキーパッチは検出されず、P1を最初の低リスク移行に選ぶ根拠を再確認した。
- ルート入口依存: `cloud_job_runner.py`、`energy_model_main.py`、`db_pipeline_main.py`、`dashboard_server.py`、`main.py`、`kpnet_main.py`、`sheets_export_main.py` の import を列挙した。各入口を薄くするP3〜P7では、旧 `app.<module>` importを維持する互換モジュールを先に用意する。
- 安全性: 調査だけでアプリケーションコード、外部サービス、本番環境は変更していない。

## 2026-08-01 — モジュール再編 P1-1: SQLite運用アダプターを機能パッケージへ移動

- 目的: SQLiteのスキーマ作成、実績取込、日別費用・蓄電池指標集計は運用永続化の責務であるため、実装を `app/operations/sqlite.py` へ配置した。呼出し側の既存 `app.operations_db` 契約は維持する。
- 変更前検証: `tests/test_operations_db.py`、`tests/test_weekly_backup.py`、`tests/test_dashboard_data.py` は `55 passed in 1.84s`。
- 実装変更: `app/operations_db.py` を `app/operations/sqlite.py` へ移動した。実装側の `app/db_sync.py` は正規モジュールを import するよう変更した。旧パスには、全公開型・関数を明示的に再exportする小さな互換モジュールを置いた。ワイルドカードimportは使っていない。
- 互換性検証: `tests/test_operations_sqlite_compatibility.py` を追加し、旧パスと正規パスの `PipelineConfig` と全公開関数が同一オブジェクトであることを固定した。
- 発見した境界依存: 初回の変更後テストは3件失敗した。`tests/test_operations_db.py` が旧パスの `_forecast_daily_values_from_plan`、`_hourly_forecast_rows_from_plan`、`_fetch_open_meteo_daily_actual` を直接参照またはモンキーパッチしていたためである。互換モジュールへ私的実装を公開しない方針を保ち、テスト本体を正規の `app.operations.sqlite` へ向けた。これはテストを実装位置ではなく正規モジュール境界へ合わせる変更で、実行時の計算・DB入出力・例外契約は変更していない。
- 変更後検証: `python -m pytest -q tests/test_operations_db.py tests/test_operations_sqlite_compatibility.py tests/test_weekly_backup.py tests/test_dashboard_data.py` は `56 passed in 1.86s`。
- 構文・形式検査: 計画指定の `compileall` は成功、`git diff --check` は成功。
- 安全性: SQLiteのスキーマ、SQL、データ、外部サービス、本番環境は変更していない。

## 2026-08-01 — モジュール再編 P1-2: Firestore運用アダプターを機能パッケージへ移動

- 目的: Firestoreへの実績・計画・集計値の保存は運用永続化の責務であるため、実装を `app/operations/firestore.py` へ集約した。旧 `app.firestore_ops` は外部・既存利用者のために維持する。
- 変更前検証: `tests/test_firestore_operations.py` と `tests/test_firestore_dashboard_metrics.py` は `2 passed in 0.72s`。
- 実装変更: `app/firestore_ops.py` を `app/operations/firestore.py` へ移動した。旧パスには全公開関数だけを明示的に再exportする互換モジュールを置いた。`db_sync`、Driveバックアップ、Sheets出力、DBパイプライン、分析・アーカイブスクリプトは正規モジュールを import する。
- テスト境界: `test_db_pipeline_main` と `test_operations_domain` は、アダプターの正規実装を検査・モンキーパッチするよう変更した。私的属性を互換モジュールから再公開しないためであり、テスト対象の関数、入力、期待値、Firestoreの書込み契約は変更していない。
- 実装時の是正: 初回の後検証で、旧SQLiteパスに残った私的属性参照1件と、`sheets_export.py` の関数内importのインデント崩れを検出した。前者を正規SQLiteモジュールへ向け、後者を関数本体へ正しく戻した。次の検証では、存在しない `tests/test_sheets_export.py` を指定したためpytestが実行対象なしで終了した。コード失敗として採用せず、存在する影響テストだけで再実行した。
- 互換性検証: `tests/test_operations_firestore_compatibility.py` を追加し、旧・新モジュールの全公開関数が同一オブジェクトであることを固定した。
- 変更後検証: `python -m pytest -q tests/test_firestore_operations.py tests/test_firestore_dashboard_metrics.py tests/test_operations_firestore_compatibility.py tests/test_db_pipeline_main.py tests/test_operations_domain.py` は `11 passed in 1.01s`。
- 構文・形式検査: 計画指定の `compileall` は成功、`git diff --check` は成功。
- 安全性: Firestore、SQLite、Drive、Sheets、外部サービス、本番環境への接続・書込みは行っていない。

## 2026-08-01 — モジュール再編 P1-3: PostgreSQL運用アダプターを機能パッケージへ移動

- 目的: PostgreSQLへの運用データ保存・集計はSQLite/Firestoreと同じ運用永続化責務であるため、実装を `app/operations/postgres.py` へ移動した。
- 変更前検証: `tests/test_postgres_operations.py` と `tests/test_operations_domain.py` は `4 passed in 0.87s`。
- 実装変更: `app/postgres_ops.py` を正規実装へ移動し、旧パスには公開関数だけを明示的に再exportする互換モジュールを置いた。DBパイプライン、Sheets出力、関連テストを正規モジュールへ更新した。
- 互換性検証: `tests/test_operations_postgres_compatibility.py` を追加し、旧・新パスの全公開関数が同じオブジェクトであることを検証した。
- 変更後検証: `python -m pytest -q tests/test_postgres_operations.py tests/test_operations_postgres_compatibility.py tests/test_operations_domain.py tests/test_db_pipeline_main.py` は `10 passed in 0.95s`。
- 構文・形式検査: 計画指定の `compileall` は成功、`git diff --check` は成功。
- 安全性: PostgreSQL、SQLite、Firestore、外部サービス、本番環境への接続・書込みは行っていない。

## 2026-08-01 — モジュール再編 P1-4: バックエンド同期を運用パッケージへ移動

- 目的: SQLiteとFirestoreの双方向同期は永続化アダプター間の運用責務であるため、実装を `app/operations/sync.py` へ移動した。
- 事前確認: 計画に記された `tests/test_db_sync.py` はリポジトリに存在しなかった。ファイル名検索で同期専用テストがないことを確認し、直接利用する `tests/test_drive_backup.py` を変更前テストとして実行して `3 passed in 0.80s` を得た。
- 実装変更: `app/db_sync.py` を正規実装へ移し、旧パスには `TABLE_SPECS` と二つの同期関数のみを明示的に再exportする互換モジュールを置いた。バックアップと同期用スクリプト、関連テストを正規モジュールへ更新した。
- 互換性検証: `tests/test_operations_sync_compatibility.py` を追加し、定数オブジェクトと二つの関数が旧・新パスで同一であることを固定した。
- 変更後検証: `python -m pytest -q tests/test_drive_backup.py tests/test_operations_sync_compatibility.py` は `4 passed in 0.88s`。
- 構文・形式検査: 計画指定の `compileall` は成功、`git diff --check` は成功。
- 安全性: 同期関数はテストから実行せず、SQLite、Firestore、外部サービス、本番環境への接続・書込みは行っていない。

## 2026-08-01 — モジュール再編 P1-5: DBパイプライン入口を薄くする

- 目的: 取込順序・バックエンド選択・週次バックアップ判断を運用ワークフローとして `app/operations/workflow.py` へ移し、ルート `db_pipeline_main.py` を `main()` を呼ぶ起動専用ファイルにした。
- 変更前検証: `tests/test_db_pipeline_main.py tests/test_operations_db.py tests/test_firestore_operations.py tests/test_postgres_operations.py` は `26 passed in 1.60s`。
- 境界確認: 既存テストはルート入口の `_env_bool`、`_settings_summary_successful`、`_ingest_firestore` を直接参照していた。計画の停止条件に該当するため、互換入口に私的実装を再exportせず、テストを正規ワークフローモジュールへ切り替えた。
- 実装変更: 実装全体を `app/operations/workflow.py` へ移動し、内部SQLite importも正規モジュールへ更新した。ルート `db_pipeline_main.py` は正規 `main` をimportして実行する6行の入口になった。
- 変更後検証: 同じ対象テストは `26 passed in 1.65s`。
- 構文・形式検査: 計画指定の `compileall` は成功、`git diff --check` は成功。
- 安全性: DB処理はテストで外部バックエンドに接続せず、SQLite、Firestore、PostgreSQL、外部サービス、本番環境への書込みは行っていない。

## 2026-08-01 — モジュール再編 P1: 運用永続化フェーズの統合検証

- 対象: SQLite、Firestore、PostgreSQL、同期、DBパイプライン入口の5カード。
- 統合検証: `python -m pytest -q tests/test_operations_db.py tests/test_firestore_operations.py tests/test_postgres_operations.py tests/test_dashboard_backend_parity.py tests/test_db_pipeline_main.py tests/test_drive_backup.py tests/test_weekly_backup.py` は `32 passed in 2.01s`。
- 結論: 旧importは明示的な互換モジュールで維持しつつ、内部実装は `app/operations/` に集約できた。外部バックエンドを使う実行はしていない。

## 2026-08-01 — モジュール再編 P2-1: 予測系の私的参照を分類

- 調査対象: `forecast_correction`、`comfort_load_forecast`、`pv_array_forecast` の私的helper importと文字列指定モンキーパッチ。
- 検査上の注意: `rg` は一致なしを終了コード1で返す。初回はそれを複合コマンド失敗として扱ったため、空結果を通常状態として許容する再実行を行った。再実行は終了コード0で完了した。
- `forecast_correction`: `test_energy_model.py` は履歴・気象取得・夕方補正・物理残差補正を差し替え、`test_external_site_access.py` は気象取得を直接テストする。これらは予測補正の内部結合を検証しているため、P2-7で正規モジュールへテストを移してから物理移動する。旧互換層へ私的helperを公開しない。
- `comfort_load_forecast`: 二つの分析スクリプトが `_feature_map` を利用する。特徴量の内容は分析用の明示的契約に相当するため、P2-3で中学生程度の英語docstringを付けた公開関数へ昇格してスクリプトを置換してから移動する。
- `pv_array_forecast`: 私的helper import・文字列モンキーパッチは検出0。P2-4は正規モジュールへ直接移動できる。
- 方針: P2-2、P2-5、P2-6は私的参照なしのため先に実施する。P2-3、P2-4、P2-7は上記の境界整理を各カード内で完了させる。

## 2026-08-01 — モジュール再編 P2-2: 消費電力予測を予測パッケージへ移動

- 目的: 消費電力の学習・日別予測は予測責務であるため、`app/forecasting/consumption.py` に配置した。`app/forecasting/__init__.py` は予測機能の発見可能な入口として追加した。
- 変更前検証: `tests/test_consumption_forecast.py tests/test_occupancy_schedule.py` は `8 passed in 4.09s`。
- 実装変更: 旧 `app/consumption_forecast.py` は、公開データ型・予測器・関数を明示的に再exportする互換モジュールへ置き換えた。内部利用とテストは正規モジュールへ更新した。
- 互換性検証: `tests/test_forecasting_consumption_compatibility.py` を追加し、旧・新パスの公開型・関数が同一オブジェクトであることを確認する。
- 変更後検証: 影響範囲の `tests/test_consumption_forecast.py tests/test_occupancy_schedule.py tests/test_energy_model_runtime.py tests/test_forecasting_consumption_compatibility.py` は `40 passed in 4.51s`。
- 構文・形式検査: 計画指定の `compileall` は成功、`git diff --check` は成功。
- 安全性: 予測入力、学習係数、計算式、外部アクセス、本番環境は変更していない。

## 2026-08-01 — モジュール再編 P2-5: 物理PV予測を予測パッケージへ移動

- 目的: 太陽位置・パネル形状・気象履歴からの物理PV候補生成は予測責務であるため、`app/forecasting/pv_physical.py` へ移動した。
- 変更前検証: `tests/test_pv_physical_forecast.py tests/test_energy_model.py` は `54 passed in 2.59s`。
- 実装変更: 旧 `app/pv_physical_forecast.py` は公開データ型と候補生成関数を明示的に再exportする互換モジュールへ置き換えた。エネルギーモデル入口、履歴再計算スクリプト、テストは正規モジュールを使用する。
- 互換性検証: `tests/test_forecasting_pv_physical_compatibility.py` を追加し、旧・新パスの公開型・関数の同一性を確認する。
- 変更後検証: 影響範囲の3テストは `55 passed in 2.72s`。
- 構文・形式検査: 計画指定の `compileall` は成功、`git diff --check` は成功。
- 安全性: PV算出式、気象・履歴の読取り条件、外部アクセス、本番環境は変更していない。

## 2026-08-01 — モジュール再編 P2-6: 在宅予定を予測パッケージへ移動

- 目的: 在宅・不在予定による負荷予測の補正は予測入力の責務であるため、`app/forecasting/occupancy.py` へ移動した。
- 変更前検証: `tests/test_occupancy_schedule.py tests/test_energy_plan_settings.py` は `7 passed in 2.18s`。
- 実装変更: 旧 `app/occupancy_schedule.py` は、予定定数、公開型、ロード・適用関数を明示的に再exportする互換モジュールへ置き換えた。エネルギー計画、Sheets出力、エネルギーモデル入口、テストは正規モジュールを使用する。
- 互換性: 互換モジュールにワイルドカードimportを一度書きかけたが、計画の明示export規則に反するため、同じカード内で全公開記号の列挙へ直した。`tests/test_forecasting_occupancy_compatibility.py` は主要な公開型・関数の同一性を固定する。
- 変更後検証: 影響範囲の3テストは `8 passed in 2.28s`。
- 構文・形式検査: 計画指定の `compileall` は成功、`git diff --check` は成功。
- 安全性: 予定データの形式、負荷補正ロジック、Google Sheetsアクセス、本番環境は変更していない。

## 2026-08-01 — モジュール再編 P2-3: 快適負荷予測を予測パッケージへ移動

- 目的: 温湿度と履歴からの快適負荷予測を `app/forecasting/comfort_load.py` へ移動した。
- 変更前検証: `tests/test_comfort_load_forecast.py tests/test_energy_model.py` は `54 passed in 5.57s`。
- 私的参照の整理: 分析スクリプトが私的 `_feature_map` を利用していた。生産モデルと同じ特徴量スキーマを分析する現在の利用目的があるため、`build_comfort_feature_map` を公開関数にして、中学生程度の英語docstringで比較可能性の理由を記録した。二つの分析スクリプトはこの公開関数を使う。
- 実装変更: 旧パスは定数・公開関数を明示的に再exportする互換モジュールへ置き換え、予測補正・テスト・分析スクリプトは正規モジュールを使用する。
- 互換性検証: `tests/test_forecasting_comfort_load_compatibility.py` を追加し、旧・新パスの公開記号の同一性を確認する。
- 変更後検証: 影響範囲の3テストは `55 passed in 5.63s`。
- 構文・形式検査: 計画指定の `compileall` は成功、`git diff --check` は成功。
- 安全性: 特徴量、学習・フォールバック、気象入力、予測値、外部アクセスは変更していない。

## 2026-08-01 — モジュール再編 P2-4: PV配列予測を予測パッケージへ移動

- 目的: 複数PV配列の気象・校正・発電量予測を `app/forecasting/pv_array.py` へ移動した。
- 変更前検証: `tests/test_pv_array_forecast.py tests/test_external_site_access.py tests/test_energy_model.py` は `70 passed, 1 skipped in 3.04s`。
- 私的参照の整理: 外部サイト接続テストが `_fetch_hourly` を直接検証していた。Open-Meteoの応答検証・エラー正規化は実際の外部接続契約なので、`fetch_open_meteo_hourly` という公開名と短い英語docstringに置換し、テストを新APIへ向けた。
- 実装変更: 旧パスは公開型・校正関数・取得関数・予測関数を明示的に再exportする互換モジュールへ置き換えた。エネルギーモデル入口とテストは正規モジュールを使用する。
- 互換性検証: `tests/test_forecasting_pv_array_compatibility.py` を追加し、全公開記号が旧・新パスで同一であることを確認する。
- 変更後検証: 影響範囲の4テストは `71 passed, 1 skipped in 3.37s`。
- 構文・形式検査: 計画指定の `compileall` は成功、`git diff --check` は成功。
- 安全性: provider選択、リトライ、校正係数、PV計算、外部アクセス、本番環境は変更していない。

## 2026-08-01 — モジュール再編 P2-7: 予測補正を予測パッケージへ移動

- 目的: PV・負荷・気象履歴の予測補正を `app/forecasting/correction.py` へ移動した。
- 変更前検証: `tests/test_energy_model.py tests/test_external_site_access.py tests/test_comfort_load_forecast.py` は `64 passed, 1 skipped in 6.05s`。
- 私的参照の整理: 分析・再計算・スモークテストが気象取得、Firestore履歴読取、熱状態追加を利用していた。これらは分析・再計算で必要な正規化済み入力の契約なので、`fetch_hourly_weather`、`load_forecast_hourly_history_from_firestore`、`add_thermal_states` を公開名と短い英語docstringへ昇格した。テストの内部モンキーパッチは正規モジュールを対象にするよう変更し、旧互換パスには私的実装を公開していない。
- 実装変更: 旧パスは公開型・公開補助関数・補正生成関数を明示的に再exportする互換モジュールへ置き換えた。エネルギーモデル入口、関連スクリプト、テストは正規モジュールを使用する。
- 互換性検証: `tests/test_forecasting_correction_compatibility.py` を追加した。
- 変更後検証: 互換性テストを含む影響範囲は `65 passed, 1 skipped in 6.12s`。
- 構文・形式検査: 計画指定の `compileall` は成功、`git diff --check` は成功。
- 安全性: 補正係数、履歴期間、provider timeout、診断JSON、外部アクセス、本番環境は変更していない。

## 2026-08-01 — モジュール再編 P2: 予測フェーズの統合検証

- 対象: 消費電力、快適負荷、PV配列、物理PV、在宅予定、予測補正。
- 統合検証: 7個の予測・外部接続・エネルギーモデルテストは `82 passed, 1 skipped in 6.69s`。計画指定の `compileall` と `git diff --check` も成功した。
- 結論: 新規実装の正規位置を `app/forecasting/` に統一し、旧パスは公開APIだけを明示的に再exportする。私的参照は公開契約または正規モジュール内テストへ整理済みである。外部サービス・本番環境への接続は行っていない。

## 2026-08-01 — モジュール再編 P3-1: 中核エネルギーモデルを計画パッケージへ移動

- 目的: SOC最適化と夜間充電量計算はエネルギー計画の中心責務であるため、`app/energy_plan/energy_model.py` へ移動した。
- 変更前検証: `tests/test_energy_model.py tests/test_energy_model_runtime.py` は `83 passed in 2.62s`。
- 実装変更: 旧パスは公開データ型・計算関数を明示的に再exportする互換モジュールへ置き換えた。計画port、エネルギーモデル入口、分析スクリプト、テストは正規モジュールを使用する。
- 互換性検証: `tests/test_energy_plan_model_compatibility.py` を追加した。私的スコア関数を確認する既存テストは正規モジュールを対象にしており、旧互換パスは私的実装を公開しない。
- 変更後検証: 影響範囲の3テストは `84 passed in 2.93s`。
- 構文・形式検査: 計画指定の `compileall` は成功、`git diff --check` は成功。
- 安全性: SOC境界、最適化、料金・容量計算、予測入力、本番環境は変更していない。

## 2026-08-01 — モジュール再編 P3-2: SOC費用モデルを計画パッケージへ移動

- 目的: 不確実性シナリオ、SOC候補評価、期待費用最適化はエネルギー計画責務であるため、`app/energy_plan/soc_cost.py` へ移動した。
- 変更前検証: `tests/test_soc_cost_optimizer.py tests/test_energy_model.py tests/test_energy_model_runtime.py` は `98 passed in 2.74s`。
- 実装変更: 旧パスは公開データ型・評価・最適化関数を明示的に再exportする互換モジュールへ置き換えた。決定フィードバック、計画入口、関連テストは正規モジュールを使用する。
- 互換性検証: `tests/test_energy_plan_soc_cost_compatibility.py` は互換モジュールの全公開記号が正規モジュールと同一であることを確認する。
- 変更後検証: 影響範囲の4テストは `99 passed in 3.10s`。
- 構文・形式検査: 計画指定の `compileall` は成功、`git diff --check` は成功。
- 安全性: 費用計算、SOC候補、シナリオ重み、料金・外部入力、本番環境は変更していない。

## 2026-08-01 — モジュール再編 P3-3: SOC決定フィードバックを計画パッケージへ移動

- 目的: 実績に基づくSOC決定の評価・事前分布生成は計画責務であるため、`app/energy_plan/decision_feedback.py` へ移動した。
- 変更前検証: `tests/test_soc_decision_feedback.py tests/test_energy_model_runtime.py tests/test_cloud_job_runner.py` は `78 passed in 20.46s`。
- 実装変更: 旧パスはフィードバック生成・事前分布・Firestore読取の公開関数を明示的に再exportする互換モジュールへ置き換えた。計画入口、Cloud Jobランナー、関連テストは正規モジュールを使用する。
- 互換性検証: `tests/test_energy_plan_decision_feedback_compatibility.py` を追加した。
- 変更後検証: 影響範囲の4テストは `79 passed in 21.09s`。
- 構文・形式検査: 計画指定の `compileall` は成功、`git diff --check` は成功。
- 安全性: 実績読取、類似度、後悔値、Firestore保存・読取、Cloud Jobの実機操作、本番環境は変更していない。

## 2026-08-01 — モジュール再編 P3-4: 夜間計画ドメインを計画パッケージへ移動

- 目的: 夜間充電計画の読取・検証は計画ドメイン責務であるため、`app/energy_plan/night_plan.py` へ移動した。
- 変更前検証: `tests/test_domain_primitives.py tests/test_night_plan_archive.py tests/test_energy_plan_document.py` は `7 passed in 2.28s`。
- 実装変更: 旧パスは夜間計画の公開型・読取・解析関数を明示的に再exportする互換モジュールへ置き換え、ドメインテストは正規モジュールを使用する。
- 互換性検証: `tests/test_energy_plan_night_plan_compatibility.py` を追加した。
- 変更後検証: 影響範囲の4テストは `8 passed in 2.39s`。
- 構文・形式検査: 計画指定の `compileall` は成功、`git diff --check` は成功。
- 安全性: 入力JSONの形式、必須値検証、夜間計画の値、本番環境は変更していない。

## 2026-08-01 — モジュール再編 P3-5: エネルギー計画ワークフローをパッケージへ移動

- 目的: CSV・気象履歴・予測・補正・SOC最適化・JSON出力を結合する計画ワークフローを `app/energy_plan/workflow.py` へ移し、ルート `energy_model_main.py` を起動専用にした。
- 変更前検証: `tests/test_energy_model.py tests/test_energy_model_runtime.py tests/test_energy_plan_output.py tests/test_energy_plan_document.py` は `85 passed in 2.65s`。
- テスト境界: 二つの既存テストと分析スクリプトはルート入口の私的helperを参照していた。入口を互換実装にしないため、これらを正規ワークフローモジュールへ向けた。Cloud Jobが実行する `energy_model_main.py` のファイル名・終了コード契約は薄いラッパーで維持する。
- 変更後検証: 同じ対象テストは `85 passed in 3.06s`。計画指定の `compileall` と `git diff --check` も成功した。
- 安全性: 計画値、SOC・料金計算、予測API条件、保存JSON、Cloud Job実行、本番環境は変更していない。

## 2026-08-01 — モジュール再編 P4-1/P4-2: KP-NETワークフローをパッケージへ移動

- 事前分類: KP-NETの私的payload生成とClientメソッドのモンキーパッチは、いずれもワークフロー実装のテストである。旧パスへ私的実装を再exportせず、正規モジュールをテスト対象にする。
- 実装変更: `app/kpnet_workflow.py` を `app/kpnet/workflow.py` へ移動した。旧パスは公開設定・計画型・Client・実行関数だけを明示的に再exportする。`kpnet_main.py` のCLIファイル名と終了コードは維持する。
- 検証: `tests/test_kpnet_workflow.py tests/test_kpnet_settings_intent.py tests/test_cloud_job_runner.py tests/test_external_site_access.py` は `101 passed, 1 skipped in 13.06s`。`compileall` と `git diff --check` も成功。
- 安全性: KP-NETログイン、設定変更、CSVダウンロード、Cloud Job、本番環境は実行していない。

## 2026-08-01 — モジュール再編 P4-3/P4-4: ダッシュボードデータ層をパッケージへ移動

- 事前分類: Firestore・SQLite・PostgreSQLの私的loaderを差し替えるテストは、データ層実装を検証するためのものなので、正規の `app.dashboard.data` を対象にする。
- 実装変更: `app/dashboard_data.py` を `app/dashboard/data.py` へ移動した。旧パスは公開データ型、repository、公開読取関数だけを明示的に再exportする。サーバーと検証スクリプトは正規モジュールを使用する。
- 検証: `tests/test_dashboard_data.py tests/test_dashboard_backend_parity.py` は `37 passed in 1.17s`。`compileall` と `git diff --check` も成功。
- 安全性: DB・Firestore読取、ダッシュボードHTTP公開、本番環境は実行していない。

## 2026-08-01 — モジュール再編 P4-5: ダッシュボードサーバーをパッケージへ移動

- 実装変更: `dashboard_server.py` のHTTP・認証・静的配信実装を `app/dashboard/server.py` へ移し、ルートを起動専用にした。テストは正規モジュールのHTML・静的資産helperを検証する。
- 修正した移動起因の不具合: 初回後テストでテンプレートと静的資産を `app/dashboard/` 配下から探して3件失敗した。実ファイルはリポジトリルートの `templates/` と `static/` にあり、移動前からの配信契約を維持するため `_PROJECT_ROOT` を明示して参照先を固定した。
- 変更後検証: `tests/test_dashboard_server.py tests/test_dashboard_data.py tests/test_dashboard_backend_parity.py` は `41 passed in 1.15s`。`compileall` と `git diff --check` も成功。
- 安全性: HTML、静的URL、認証、HTTP応答、実サーバー公開、本番環境は変更・実行していない。

## 2026-08-01 — モジュール再編 P7: Cloud Jobオーケストレーターをランタイムパッケージへ移動

- 目的: 23:00・03:00・07:00のCloud Run Job制御を `app/runtime/cloud_job.py` へ配置し、ルート `cloud_job_runner.py` を起動専用にした。
- 変更前検証: `tests/test_cloud_job_runner.py tests/test_forced_charge_state_machine.py tests/test_forced_charge_settings.py` は `71 passed in 12.61s`。
- テスト境界: Cloud Jobテストは旧ルートのSOC判定、Firestore復元、サブプロセス、03:00監視、スロット処理をモンキーパッチしていた。テスト名とケースを変えず、対象モジュール名だけを `app.runtime.cloud_job` に機械的に置換した。旧入口へ私的実装を再exportしない。
- 実装変更: Cloud Jobの全実装を正規モジュールへ移動した。ルートは `main()` を呼ぶ起動専用ファイルであり、呼び出す `energy_model_main.py`、`kpnet_main.py`、`db_pipeline_main.py`、`sheets_export_main.py` のファイル名は変更していない。
- 変更後検証: 同じ3テストは `71 passed in 12.50s`。`compileall` と `git diff --check` も成功。
- 安全性: Cloud Run Job、KP-NET設定、Firestore書込み、Drive、外部コマンド、本番環境は実行していない。

## 2026-08-01 — Phase 7後の全回帰と残存テスト境界の是正

- 発見: 全回帰で `test_soc_optimization_request_forwards_legacy_arguments` が失敗した。P3-2移動後も旧 `app.soc_cost_optimizer` をモンキーパッチしており、互換モジュール経由では正規実装のグローバル参照を差し替えられないためである。
- 処置: テストの対象だけを `app.energy_plan.soc_cost` へ変更した。旧互換モジュールの公開契約・SOC最適化の実装・引数・結果は変更していない。
- 個別検証: `tests/test_soc_optimization_request.py` は `1 passed in 2.26s`。
- 全回帰: `python -m pytest -q` は `417 passed, 1 skipped in 19.88s`。
- 構文・形式検査: 計画指定の `compileall` と `git diff --check` は成功。

## 2026-08-01 — モジュール再編 P5-2〜P5-4: バックアップ実装の移動

- 週次バックアップを `app/backup/weekly.py`、夜間計画アーカイブを `app/backup/night_plan_archive.py`、アーティファクト整理を `app/backup/artifacts.py` へ移動した。旧モジュールは公開APIだけを明示的に再exportする。
- 個別検証: 週次・DBパイプラインは `6 passed`、夜間計画アーカイブ・Firestoreは `3 passed`、アーティファクト整理は `2 passed`。
- 全回帰: これらの移動後に `417 passed, 1 skipped in 20.25s`。外部バックアップ、Firestore、Drive、本番環境は実行していない。

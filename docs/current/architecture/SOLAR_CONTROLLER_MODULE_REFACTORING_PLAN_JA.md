# Solar Controller モジュール再編計画

最終更新: 2026-08-01 (JST)

## 1. 目的と完了条件

この計画は、太陽光発電・蓄電池の予測、SOC計画、実績取込、KP-NET機器操作、ダッシュボード、バックアップを扱う本リポジトリの `app/`、ルートPythonエントリポイント、ビルド・型検査・テスト設定を、機能単位で発見しやすく安全に再編するための実行手順である。

完了条件は次のすべてを満たすこと。

- 新規参加者が、機能名から実装の置き場所を予測できる。
- 計画計算、機器操作、永続化、表示、外部出力の責務境界が明確である。
- 現在のCLI、Cloud Runエントリポイント、環境変数、保存形式、外部JSON、既存の `app.<module>` import を壊さない。
- 変更ごとに対象テストが成功し、最終的に全回帰、Python構文検査、差分検査が成功する。
- 互換モジュールを残す場合は、対象、理由、削除条件が文書化される。
- ルートのPythonは起動と終了コード変換だけを担い、業務ロジックや外部I/Oの手順を持たない。
- Docker、Cloud Build、mypy、pytest、依存定義が新しいimport・配置を認識する。

この計画は可読性監査Skillの `STRUCT-01`、`STRUCT-03`、`STRUCT-06`、`REVIEW-02`、`REVIEW-04` に従う。ディレクトリを整えること自体は目的ではない。責務の発見性が上がり、かつ互換性を確認できる場合だけ移動する。

## 2. 現状の分析

`app/` には既に次の機能別パッケージがある。

- `energy_plan/`: 予報入力、履歴、出力文書、設定。
- `operations/`: 運用ドメイン、日次費用計算。
- `dashboard/`: ダッシュボードのモデル、リポジトリ契約、サービス。
- `forced_charge/`: 強制充電の状態遷移とポート。
- `kpnet/`: 設定変更意図。
- `settings/`: 強制充電設定。

一方、ルート直下には、同じ機能に属する大きな実装が残っている。

| 機能 | 現在の主なモジュール | 監査上の課題 |
| --- | --- | --- |
| 運用データの永続化 | `operations_db.py`, `firestore_ops.py`, `postgres_ops.py`, `db_sync.py` | `operations/` のドメイン実装と物理的に離れている。 |
| 予測 | `consumption_forecast.py`, `comfort_load_forecast.py`, `forecast_correction.py`, `pv_array_forecast.py`, `pv_physical_forecast.py`, `occupancy_schedule.py` | 予測モデル、補正、予測入力がルートに散在する。 |
| SOC計画 | `energy_model.py`, `soc_cost_optimizer.py`, `soc_decision_feedback.py` | 翌朝SOC計画の中核計算が分散する。 |
| KP-NET | `kpnet_workflow.py` | 既存の `kpnet/` パッケージと分かれている。 |
| ダッシュボード | `dashboard_data.py` | 既存の `dashboard/` モデル・サービスと分かれている。 |
| バックアップとアーカイブ | `drive_backup.py`, `weekly_backup.py`, `artifact_cleanup.py`, `night_plan_archive.py` | 復旧・保管処理の発見性が低い。 |

移動リスクも確認済みである。`forecast_correction.py`、`dashboard_data.py`、`kpnet_workflow.py` はテストおよび分析スクリプトから私的ヘルパーを直接importまたは文字列指定でモンキーパッチされる。これらはファイルを先に移動してはいけない。先にテスト境界を明確化する。

ルートPythonにも実装が残る。`energy_model_main.py` は2863行、`cloud_job_runner.py` は1485行、`db_pipeline_main.py` は426行、`dashboard_server.py` は357行であり、現状では「薄いエントリポイント」という設計方針を満たさない。`sheets_export_main.py` 20行、`kpnet_main.py` 6行、`main.py` 5行は既に薄い入口である。

## 3. 理想構成

以下を目標構成とする。ルートの実行ファイル名はDocker、Cloud Run、運用スクリプトから直接参照されるため維持するが、中身は対応する `app` の `main()` を呼ぶ薄いラッパーにする。

```text
app/
  domain/                 # 外部I/Oに依存しない太陽光・蓄電池の共通概念
    constants.py
    time_windows.py
    tariff.py
    monitoring.py
  configuration/          # dotenvと環境変数の読取境界
    environment.py
  parsing/                # 外部値を内部の有限数へ変換する境界
    numbers.py
  forecasting/            # PV・負荷予測と予測補正
    consumption.py
    comfort_load.py
    pv_array.py
    pv_physical.py
    correction.py
    occupancy.py
  energy_plan/            # 計画文書、SOC評価、最適化、夜間充電計算
    models.py
    ports.py
    settings.py
    historical.py
    forecast.py
    output.py
    energy_model.py
    soc_cost.py
    decision_feedback.py
  operations/             # 実績取込、日次集計、DBバックエンド、同期
    domain.py
    cost_daily.py
    monitoring_csv.py
    csv_merge.py
    sqlite.py
    firestore.py
    postgres.py
    sync.py
  kpnet/                  # KP-NET契約、設定意図、機器ワークフロー
    settings_intent.py
    workflow.py
  dashboard/              # 表示モデル、問い合わせ、集計、ロード入口
    models.py
    repositories.py
    service.py
    data.py
  backup/                 # Drive、週次差分、アーティファクト、計画アーカイブ
    drive.py
    weekly.py
    artifacts.py
    night_plan_archive.py
  exports/                # 外部出力
    sheets.py
  local_control/          # ローカル実行用の旧来コントローラー境界
    workflow.py
    browser.py
    config.py
    csv_input.py
    decision.py
    history.py
    models.py
  forced_charge/
  settings/
  runtime/
    cloud_job.py          # 23:00 / 03:00 / 07:00のCloud Runオーケストレーション
  utils.py                # 分割完了までの互換入口。所有先が決まった関数から減らす

cloud_job_runner.py       # app.runtime.cloud_job.main() を呼ぶだけ
dashboard_server.py       # app.dashboard.server.main() を呼ぶだけ
db_pipeline_main.py       # app.operations.workflow.main() を呼ぶだけ
energy_model_main.py      # app.energy_plan.workflow.main() を呼ぶだけ
kpnet_main.py             # 現状どおり薄い入口
main.py                   # 現状どおり薄い入口
sheets_export_main.py     # 現状どおり薄い入口
```

これは最終的な所有境界であり、全モジュールを一度に移す指示ではない。汎用的な `shared/` は作らない。太陽光・蓄電池の意味を持つ共通値だけを `domain/` に置き、文字列変換や環境変数読込みは利用機能へ寄せる。既存の `energy_plan/` をSOC計画全体の所有者として拡張し、意味の近い `planning/` を別に作らない。

## 4. ギャップ分析

| ギャップ | 影響 | 優先度 | 埋め方 |
| --- | --- | --- | --- |
| `operations/` にアダプターがない | SQLite / Firestore / PostgreSQLの取込先を見つけにくい | 高 | 互換モジュールを残して3アダプターと同期を段階移動する。 |
| 予測機能がルートに散在 | 予測モデル変更の影響範囲を把握しにくい | 高 | 先にテストとスクリプトの私的ヘルパー依存を解消し、その後に移動する。 |
| SOC計画の中核が分散 | 経済最適化・フィードバック・基本計算の境界が曖昧 | 中 | 公開契約を固定し、既存の `energy_plan/` へ集約する。 |
| KP-NET / dashboard の実装が既存パッケージ外 | 機能名と配置が一致しない | 中 | 高結合テストの境界整理後に移動する。 |
| バックアップ群が散在 | 復旧手順と保管実装を探しにくい | 中 | 運用スクリプトとの依存を確認後、独立パッケージへ移す。 |
| 共通モジュールがルートに残る | 所有者が不明な関数が `utils.py` に増えやすい | 低 | 太陽光・蓄電池の共通概念だけを `domain/` に置き、汎用変換は利用機能へ段階移動する。 |
| ルートPythonに大きな実装がある | Docker/Cloud Runの入口と業務ロジックが密結合 | 高 | 各機能移行と同時に実装を `app` へ移し、ルートを薄いラッパーにする。 |
| ビルド・検査設定が旧配置を参照する | ローカルテストは通ってもコンテナや型検査で失敗する | 高 | 各フェーズでDockerfile、Cloud Build、mypy、pytest、requirementsを追随確認する。 |

## 5. 共通の安全ルール

各フェーズで必ず次を行う。

1. `git status --short` が意図しない変更を含まないことを確認する。
2. 移動対象のimport、CLI、テスト、文字列指定のモンキーパッチ、環境変数、保存フィールドを `rg` で一覧化する。
3. 変更前に最小の対象テストを実行し、結果を記録する。
4. 一つのパッケージまたは一つの互換境界だけを変更する。
5. 旧公開パスを維持する場合、旧モジュールには移動先と互換性の理由を簡潔な英語コメントで書く。削除予定が未定なら、削除しない。
6. 変更後に対象テスト、`python -m compileall -q app scripts cloud_job_runner.py dashboard_server.py db_pipeline_main.py energy_model_main.py kpnet_main.py main.py sheets_export_main.py`、`git diff --check` を実行する。
7. 合格後に一つの論理的なコミットを作り、`refactoring_progress.md` に日本語で対象、判断、不変条件、実行コマンド、結果、残リスクを書く。
8. 金額、SOC、安全制御、外部機器操作、Firestore/DB書込みの意味を変える変更は、構成移動とは別コミットにする。
9. Pythonモジュールの `__module__`、クラス同一性、例外型、モンキーパッチ先、循環importも互換性に含める。値が同じだけでは合格としない。
10. 各作業開始前に直前の合格コミットIDを記録する。失敗時は未コミット差分を闇雲に消さず、差分を保存して原因を記録し、そのコミットから作業単位を再設計する。

## 6. 実行フェーズ

### Phase 0: ベースラインと移行台帳

目的: 移動前の契約を固定し、以後の差分を判断可能にする。

手順:

1. `python -m pytest -q` を実行し、基準結果を `refactoring_progress.md` に記録する。
2. 各候補について `rg -n "app\.(旧モジュール名)" app tests scripts . --glob '*.py' --glob '*.ps1' --glob '*.sh'` を実行する。PowerShellでは裸の `*.py` パス引数を使わない。
3. 次の台帳を計画文書または進捗ログへ記録する: 旧import、入口、テスト、私的ヘルパー参照、外部契約、対象テスト、互換モジュールの要否。
4. `cloud_job_runner.py`、`db_pipeline_main.py`、`energy_model_main.py`、`kpnet_main.py`、`dashboard_server.py` のimportと責務を再確認する。
5. `main.py` と `sheets_export_main.py`、Dockerfile 2件、`cloudbuild.dashboard.yaml`、`pyproject.toml`、`pytest.ini`、requirements 4件、ignoreファイルを構成台帳へ登録する。

合格条件:

- 基準テストが成功する。
- 次フェーズの各モジュールについて、旧importの参照先を把握している。

### Phase 1: 運用永続化を `app/operations/` へ移す

目的: 実績取込・日次費用・バッテリー指標・モデル指標のストレージ実装を、既存の運用ドメイン近くに集める。

対象順序:

1. `operations_db.py` → `operations/sqlite.py`
2. `firestore_ops.py` → `operations/firestore.py`
3. `postgres_ops.py` → `operations/postgres.py`
4. `db_sync.py` → `operations/sync.py`

各モジュールの手順:

1. 旧モジュールの公開関数・データクラスを一覧化する。
2. 対応する新モジュールへ実装を移す。ストレージ名は `sqlite`、`firestore`、`postgres` とし、汎用的な `data` や `adapter` は使わない。
3. 旧ファイルを、同じ公開記号を再公開する最小の互換モジュールにする。外部importを一括置換しない。
4. 第一者コードのimportだけを新パスへ更新する。テストには旧importを残し、互換性を検証する。
5. 既存の `tests/test_operations_db.py`、`tests/test_firestore_operations.py`、`tests/test_postgres_operations.py`、`tests/test_dashboard_backend_parity.py`、`tests/test_db_pipeline_main.py` を実行する。
6. SQLite・Firestore・PostgreSQLそれぞれで、取込、費用再計算、計画由来予報、バッテリー日次指標の既存テストを通す。
7. アダプター移行完了後、`db_pipeline_main.py` の実装を `operations/workflow.py` へ移し、ルートには同じ終了コードを返す薄い入口を残す。

追加テスト:

- 旧パスと新パスの代表関数が同一の結果を返すことを確認する小さな互換性テストを追加する。
- SQLite / Firestore / PostgreSQLの共通契約が同じ入力で同じ日次意味を保持することを既存パリティテストで確認する。
- 新旧パスからimportした公開クラスと例外型の同一性を確認する。文字列モンキーパッチがある場合は、パッチが実装側へ届くことも確認する。
- `python -c "import app.operations.sqlite, app.operations.firestore, app.operations.postgres, app.operations.sync"` を実行し、循環importがないことを確認する。

停止条件:

- 旧パスと新パスで関数オブジェクト、例外、戻り値、トランザクション境界の意味を同じにできない場合は停止し、互換層で吸収できない具体例を記録する。

### Phase 2: 予測機能のテスト境界を先に整理する

目的: 予測系を移動可能にする。ただし予測値の計算式・補正・フォールバックは変えない。

対象:

- `forecast_correction.py`
- `consumption_forecast.py`
- `comfort_load_forecast.py`
- `pv_array_forecast.py`
- `pv_physical_forecast.py`
- `occupancy_schedule.py`

手順:

1. `tests/test_energy_model.py`、`tests/test_external_site_access.py`、分析スクリプトにある私的ヘルパー参照を一覧化する。
2. 各私的ヘルパーを次のどちらかへ分類する。
   - 数学・入力正規化など単独で契約を持つもの: 移動先の小モジュールに置き、テストもその正規のモジュールをimportする。
   - 公開APIの内部詳細であるもの: 公開APIの入力・出力・診断値を使うテストへ置換する。
3. ネットワーク取得は注入可能なHTTP関数または既存のモック境界でテストし、移動後も外部通信をしない。
4. 私的ヘルパーの直接参照がなくなった単位から、`app/forecasting/` へ一つずつ移す。
5. 旧パスはPhase 1と同じく互換モジュールとして残す。

対象テスト:

- `tests/test_consumption_forecast.py`
- `tests/test_comfort_load_forecast.py`
- `tests/test_pv_array_forecast.py`
- `tests/test_pv_physical_forecast.py`
- `tests/test_energy_model.py`
- `tests/test_external_site_access.py`
- `tests/test_occupancy_schedule.py`

停止条件:

- 予測値、補正係数、取得タイムアウト、フォールバック理由、保存される診断JSONのどれかが変わる場合は、構成移動と分けて仕様変更として扱う。

### Phase 3: SOC計画を既存の `app/energy_plan/` へ整理する

目的: 翌朝SOC目標、日中SOC最適化、予測不確実性、過去決定フィードバックを一つの機能境界にまとめる。

対象順序:

1. `energy_model.py` → `energy_plan/energy_model.py`
2. `soc_cost_optimizer.py` → `energy_plan/soc_cost.py`
3. `soc_decision_feedback.py` → `energy_plan/decision_feedback.py`

手順:

1. `energy_model_main.py` が消費予測、PV予測、補正、SOC最適化を組み立てる契約をテストで固定する。
2. 金額・SOC・売買電・ペナルティの不変条件を対象テストで明示する。
3. 最初は旧importを保持し、`energy_model_main.py` だけを新パスへ移行する。
4. 金額計算式、SOC境界、環境変数、計画JSONキーは変更しない。
5. `energy_model_main.py` のオーケストレーションを `energy_plan/workflow.py` へ移し、ルートには `main()` 呼出しだけを残す。2863行を一度に移さず、設定型、入力取得、予測組立て、計画作成、出力保存の順に責務単位で抽出する。

対象テスト:

- `tests/test_energy_model.py`
- `tests/test_energy_model_runtime.py`
- `tests/test_soc_cost_optimizer.py`
- `tests/test_soc_decision_feedback.py`
- `tests/test_energy_plan_document.py`

追加確認:

- 金額に関わるCSV項目は、既存の本番CSV読込から下流計算まで通るfixtureテストを維持する。
- 「購入量は費用を減らさない」「認識した売電収益は費用を増やさない」などの方向不変条件を壊さない。

### Phase 4: KP-NETとダッシュボードの高リスク移行

目的: 機器操作と表示を、既存パッケージ内へ置く。ただし安全制御と表示契約を変えない。

#### KP-NET

1. `tests/test_kpnet_workflow.py` の私的ヘルパー参照を、設定意図、プロファイル、HTTPペイロード、ワークフロー結果という明示的な契約単位に分ける。
2. 設定値の選択、夜間窓、強制充電監視、ログアウト、失敗時の終了コードを対象テストで固定する。
3. `kpnet_workflow.py` を `kpnet/workflow.py` へ移し、旧パスを互換モジュールにする。
4. 実機へのログイン・設定変更は行わない。モック、fixture、既存の単体テストだけで確認する。

#### Dashboard

1. `dashboard_data.py` のテストを、データ整形、SQLite、PostgreSQL、Firestore、日次レビュー、最終ロード入口に分ける。
2. `dashboard/data.py` または責務ごとの既存 `dashboard/repositories.py` / `dashboard/service.py` へ段階的に移す。
3. `dashboard_server.py` が使う `load_dashboard_slice` と `DashboardSlice` のimport契約を維持する。
4. SQLite・Firestore・PostgreSQLの表示値パリティとJavaScriptの既存テストを実行する。
5. `dashboard_server.py` のHTTP・認証・静的ファイル配信を `dashboard/server.py` へ移し、ルートを薄い入口にする。APIパス、Cookie、認証失敗応答、JSON形状は変更しない。

停止条件:

- KP-NETの設定順序、充電停止条件、07:00への復帰、ダッシュボードAPI応答形状、認証処理のいずれかが変わる場合は停止する。

### Phase 5: バックアップ・アーカイブ

目的: 復旧機能の発見性を上げる。ただし運用スクリプト、Drive、Firestore、復旧ファイル形式を変えない。

手順:

1. `drive_backup.py`、`weekly_backup.py`、`artifact_cleanup.py`、`night_plan_archive.py` を `backup/` へ一件ずつ移す。
2. `tests/test_drive_backup.py`、`tests/test_weekly_backup.py`、`tests/test_artifact_cleanup.py`、`tests/test_night_plan_archive.py` を毎回実行する。
3. Drive API、Firestore、ローカルZIPの失敗契約と、生成ファイル名・JSON形式・ハッシュ値が変わらないことを確認する。

### Phase 6: 外部出力、ローカル制御、共通ドメイン

目的: 残ったルートモジュールに所有者を与え、巨大な共通フォルダを作らず再編を完了する。

手順:

1. `sheets_export.py` を `exports/sheets.py` へ移し、`tests/test_occupancy_schedule.py` とSheets出力の既存テスト・モックを実行する。
2. `app/main.py`、`browser_automation.py`、`config.py`、`csv_utils.py`、`decision.py`、`history_store.py`、`models.py` を `local_control/` の候補として一件ずつ監査する。ローカル実行フロー以外にも使われる記号は先に所有者を分離する。
3. `constants.py`、`time_windows.py`、`tariff.py`、`monitoring_csv.py` のうち、外部I/Oに依存せず複数機能が使う太陽光・蓄電池概念を `domain/` へ移す。
4. `csv_merge.py` は監視実績の取込処理なら `operations/csv_merge.py`、単独CLI専用なら `scripts/` 側に維持する。利用箇所を確認して決める。
5. `utils.py` の各関数について利用箇所を一覧化し、環境変数、数値変換、CSV変換などの所有機能へ移す。全関数を一括移動しない。

合格条件:

- `app/` 直下には `__init__.py` と、互換性のため意図して残した薄い旧モジュールだけがある。
- すべての残存旧モジュールに移動先、維持理由、削除条件がある。
- `local_control/` と `domain/` が互いの実装詳細へ依存せず、循環importがない。

### Phase 7: Cloud Runオーケストレーターを薄くする

目的: 最も高リスクな `cloud_job_runner.py` から、23:00 / 03:00 / 07:00の安全制御を `app/runtime/cloud_job.py` へ移し、ルートを起動専用にする。

前提:

- Phase 1から6が完了し、新しい正規importが安定している。
- `tests/test_cloud_job_runner.py` が、サブプロセス順序、環境変数、SOC監視、待機・強制充電・日中復帰、失敗時終了を十分に固定している。

手順:

1. `cloud_job_runner.py` の関数・クラス・遅延import・サブプロセス文字列を台帳化する。
2. 純粋なSOC読取・時間計算・状態選択を先に `runtime/cloud_job.py` へ移す。
3. Firestore保存、計画復元、サブプロセス起動を次の単位として移す。
4. 23時、03時、07時の入口を最後に移す。
5. ルート `cloud_job_runner.py` は `app.runtime.cloud_job.main` を呼び、その終了コードを返すだけにする。
6. 実機、Firestore、Drive、Cloud Run Jobは呼ばず、既存モックテストだけで検証する。

停止条件:

- サブプロセスの順序、設定プロファイル、SOC停止条件、07:00復帰、失敗時のfail-safe、終了コードが変わる場合は停止する。

## 7. 互換モジュールの規約

互換モジュールは一時的な移行手段であり、ロジックを持たせない。

```python
# Compatibility import for callers that still use app.operations_db.
# The implementation moved to app.operations.sqlite; remove this module only after all supported callers migrate.
from app.operations.sqlite import PipelineConfig, ensure_schema, ingest_monitoring_csvs, open_db
```

- 互換モジュールには、全公開記号を明示的に再公開する。
- `import *` は使用しない。
- 古いパスを外部利用者が使う可能性がある間は削除しない。
- 削除は、リポジトリ内の `rg`、公開配布形態、運用スクリプト、利用者移行通知を確認した別タスクで行う。
- モンキーパッチ対象は、互換モジュールと実装モジュールが別オブジェクトになる問題を確認する。必要なら、移動より先にテストを公開契約へ変更する。

## 8. 最終検証

全フェーズ完了後に次を実行する。

```powershell
python -m pytest -q
python -m compileall -q app scripts cloud_job_runner.py dashboard_server.py db_pipeline_main.py energy_model_main.py kpnet_main.py main.py sheets_export_main.py
git diff --check
```

追加で次を確認する。

- `rg -n "(?:from|import) app\.(operations_db|firestore_ops|postgres_ops|db_sync|forecast_correction|kpnet_workflow|dashboard_data)" app tests scripts . --glob '*.py' --glob '*.sh'` の残りは、意図した互換性テストまたは未移行の外部契約だけである。
- `readable-code-audit` を全ソースへ再適用し、未処置の `STRUCT-03`、`STRUCT-01`、`STRUCT-04` 候補がない。
- `readable-code-audit: skip` はすべて近接し、具体的な理由が現在のコードと一致する。
- 本番デプロイ、実機KP-NET操作、Firestore/Driveへの書込みは、このリファクタリング計画の検証では行わない。
- `python -m mypy` の実行方法が環境で利用可能なら実行し、`pyproject.toml` のstrict対象へ新パッケージを追加する。利用不能なら未検証として記録する。
- Dockerfileの `COPY` と `ENTRYPOINT`、`cloudbuild.dashboard.yaml` のDockerfile指定、requirementsの依存が新構成を含むことを静的に確認する。

## 9. AIへの実行指示

この計画を実行するAIは、次を守る。

1. 一度に一フェーズ、一モジュールだけを扱う。
2. ファイル移動の前にimport台帳と対象テストを示す。
3. 構成移動と仕様変更を混ぜない。
4. 新しいテストは、移動先の正規契約または旧パス互換契約のどちらを検証するかを明確にする。
5. テスト失敗、外部契約不明、モンキーパッチ互換性の不明があれば停止し、推測で互換性を壊さない。
6. 各コミット後に進捗ログへ、日本語で判断・不変条件・コマンド・結果・残リスクを残す。
7. Python importだけでなく、`subprocess` に渡す `*_main.py` のファイル名、Dockerの `COPY` / `ENTRYPOINT`、Cloud Build引数、mypy対象も検索する。
8. Lunaなど低インテリジェンスの実行担当は、後述の作業カードを上から一枚ずつ実行する。移動先、命名、テストを独自判断で変更しない。

## 10. ルート直下34モジュールの移行台帳

この表を各フェーズで更新する。`維持` は無期限の放置ではなく、現時点で移動利益が確認できないことを示す。

| 現在のモジュール | 目標所有先 | フェーズ | 初期判断 |
| --- | --- | --- | --- |
| `operations_db.py` | `operations/sqlite.py` | 1 | 移動 |
| `firestore_ops.py` | `operations/firestore.py` | 1 | 移動 |
| `postgres_ops.py` | `operations/postgres.py` | 1 | 移動 |
| `db_sync.py` | `operations/sync.py` | 1 | 移動 |
| `consumption_forecast.py` | `forecasting/consumption.py` | 2 | 移動 |
| `comfort_load_forecast.py` | `forecasting/comfort_load.py` | 2 | 移動 |
| `forecast_correction.py` | `forecasting/correction.py` | 2 | テスト境界整理後に移動 |
| `pv_array_forecast.py` | `forecasting/pv_array.py` | 2 | 移動 |
| `pv_physical_forecast.py` | `forecasting/pv_physical.py` | 2 | 移動 |
| `occupancy_schedule.py` | `forecasting/occupancy.py` | 2 | 予測入力契約を確認後に移動 |
| `energy_model.py` | `energy_plan/energy_model.py` | 3 | 移動 |
| `soc_cost_optimizer.py` | `energy_plan/soc_cost.py` | 3 | 金額不変条件を固定後に移動 |
| `soc_decision_feedback.py` | `energy_plan/decision_feedback.py` | 3 | 移動 |
| `kpnet_workflow.py` | `kpnet/workflow.py` | 4 | 高リスク、契約テスト後に移動 |
| `dashboard_data.py` | `dashboard/data.py` ほか | 4 | 責務分割後に移動 |
| `drive_backup.py` | `backup/drive.py` | 5 | 移動 |
| `weekly_backup.py` | `backup/weekly.py` | 5 | 移動 |
| `artifact_cleanup.py` | `backup/artifacts.py` | 5 | 移動 |
| `night_plan_archive.py` | `backup/night_plan_archive.py` | 5 | 計画文書との境界確認後に移動 |
| `sheets_export.py` | `exports/sheets.py` | 6 | 移動 |
| `app/main.py` | `local_control/workflow.py` | 6 | ルート `main.py` からの入口を維持して移動 |
| `browser_automation.py` | `local_control/browser.py` | 6 | 移動 |
| `config.py` | `local_control/config.py` | 6 | ローカル制御専用と確認済み。P6-10で移動 |
| `csv_utils.py` | `local_control/csv_input.py` | 6 | 移動 |
| `decision.py` | `local_control/decision.py` | 6 | 移動 |
| `history_store.py` | `local_control/history.py` | 6 | 移動 |
| `models.py` | `local_control/models.py` | 6 | 4データクラスがローカル制御専用と確認済み。P6-11で移動 |
| `constants.py` | `domain/constants.py` | 6 | 移動候補 |
| `time_windows.py` | `domain/time_windows.py` | 6 | 移動候補 |
| `tariff.py` | `domain/tariff.py` | 6 | 金額契約を固定後に移動 |
| `monitoring_csv.py` | `domain/monitoring.py` + `operations/monitoring_csv.py` | 6 | ドメイン値とCSV I/OをP6-15・P6-16で分離 |
| `csv_merge.py` | `operations/csv_merge.py` | 6 | 運用データ前処理としてP6-17で移動 |
| `utils.py` | `configuration/environment.py` + `parsing/numbers.py` + `domain/constants.py` | 6 | P6-12〜P6-14で関数単位に縮小し、旧パスは互換入口にする |
| `night_plan.py` | `energy_plan/night_plan.py` | 3または5 | 読込契約とアーカイブ責務を分けて移動 |

## 11. ルートファイルの追随台帳

| ルートファイル | 役割 | 計画上の扱い |
| --- | --- | --- |
| `energy_model_main.py` | 計画生成の実装とCLI | Phase 3で実装を `energy_plan/workflow.py` へ移し、薄い入口にする。 |
| `db_pipeline_main.py` | DB取込・集計の実装とCLI | Phase 1で `operations/workflow.py` へ移し、薄い入口にする。 |
| `dashboard_server.py` | HTTP、認証、静的配信 | Phase 4で `dashboard/server.py` へ移し、薄い入口にする。 |
| `cloud_job_runner.py` | Cloud Runスロット制御 | Phase 7で `runtime/cloud_job.py` へ最後に移す。 |
| `kpnet_main.py` | KP-NET CLI入口 | ファイル名と終了コードを維持する。既に薄い。 |
| `main.py` | ローカル制御入口 | ファイル名と終了コードを維持する。既に薄い。 |
| `sheets_export_main.py` | Sheets出力入口 | Phase 6の実装移動後も薄い入口として維持する。 |
| `Dockerfile` | Cloud Run Jobイメージ | `COPY app`、ルート入口、requirementsを各フェーズで確認する。 |
| `Dockerfile.dashboard` | Dashboardイメージ | `dashboard_server.py` の薄い入口と `app/dashboard` が含まれることを確認する。 |
| `cloudbuild.dashboard.yaml` | Dashboardビルド | Dockerfile名、ビルドコンテキスト、置換変数を維持する。 |
| `pyproject.toml` | mypy設定 | `forecasting.*`、`domain.*`、`backup.*`、`exports.*`、`local_control.*`、`runtime.*` をstrict対象へ追加する。旧対象は互換モジュールがある間維持する。 |
| `pytest.ini` | テスト探索・marker | テスト配置を変える場合だけ更新し、`external` markerを維持する。 |
| `requirements*.txt` | 実行環境別依存 | import移動だけでは変更しない。依存追加が必要なら別タスクとして承認を得る。 |
| `.dockerignore` | Jobビルド除外 | 新パッケージが除外されないこと、`.env` と成果物が引き続き除外されることを確認する。 |
| `.gcloudignore-dashboard` | Dashboardアップロード除外 | `app/`、`templates/`、`static/`、薄い入口が除外されないことを確認する。 |
| `.gitignore` | Git追跡除外 | 新パッケージが誤って除外されず、`.env`、キャッシュ、成果物が引き続き除外されることを確認する。 |
| `.env.example` | 公開設定契約 | キー名・初期値を構成移動では変更しない。実 `.env` は読まず、追跡・ステージしない。 |
| `README.md`, `AGENTS.md` | 利用方法・作業規約 | 正規importや構成説明が変わったフェーズで追随更新する。 |

## 12. Luna低インテリジェンス実行手順

この節は実装担当への直接指示である。上位節を要約したり、複数カードを同時に処理したりしない。各カードの完了後に必ずコミットし、次のカードはクリーンな作業ツリーから開始する。

### 12.1 全カード共通の開始手順

次を記載順に実行する。

```powershell
git status --short
git rev-parse HEAD
```

- `git status --short` に出力があれば停止する。既存変更を削除、退避、上書きしない。
- `git rev-parse HEAD` の値を `refactoring_progress.md` のカード開始記録へ書く。この値がロールバック基準である。
- 対象カードに書かれた「変更前テスト」を実行する。1件でも失敗またはエラーなら、ソースを変更せず停止する。
- `.env` を表示、編集、ステージしない。本番サービス、KP-NET実機、Firestore、Drive、Cloud Run Jobを実行しない。

### 12.2 一つの移動カードで許可される変更

1. 対象の旧実装ファイルを、カードに指定された新パスへ移動する。
2. 旧パスに互換モジュールを新規作成する。
3. `app/` とルートエントリポイントの第一者importを新パスへ変更する。
4. カード指定の互換性テストを追加する。
5. `pyproject.toml` のmypy strict対象に新パッケージがなければ追加する。
6. `refactoring_progress.md` に日本語で結果を追記する。

次は禁止する。

- 関数本体、計算式、SQL、HTTP、環境変数名、JSONキー、例外型、ログ文言の変更。
- 関係ないformat、rename、コメント整理。
- 依存パッケージの追加・更新。
- 旧互換モジュールの削除。
- カードにないファイルの移動。

### 12.3 互換モジュールの作成手順

1. 移動前に `rg -n "^(class|def) " <旧ファイル>` でトップレベル記号を列挙する。
2. 先頭が `_` でない記号と、第一者コードが実際にimportする記号を一覧にする。
3. 旧ファイルには、移動先から必要な記号を明示的にimportするコードだけを置く。`import *` を使わない。
4. 旧パスと新パスから代表的なクラス・関数をimportし、`old_symbol is new_symbol` をassertするテストを追加する。
5. 旧パスに対する文字列モンキーパッチが検出された場合、そのカードは実行せず停止する。「テスト境界整理カードが必要」と進捗ログへ書く。

### 12.4 全カード共通の終了手順

カード指定の変更後テストに加えて次を実行する。

```powershell
python -m compileall -q app scripts cloud_job_runner.py dashboard_server.py db_pipeline_main.py energy_model_main.py kpnet_main.py main.py sheets_export_main.py
git diff --check
git status --short
```

- テスト、構文検査、差分検査のどれかが失敗したらコミットしない。失敗コマンド、最初の失敗、変更ファイルを進捗ログへ記録して停止する。
- 成功した場合だけ、カードに指定されたコミットメッセージでコミットする。
- コミット直後に `git status --short` を実行し、出力があれば次のカードへ進まない。

### 12.5 Phase 0 カード

#### P0-1: 全回帰ベースライン

- 変更前テスト: `python -m pytest -q`
- 変更: `refactoring_progress.md` に日時、GitコミットID、テスト件数、skip件数、所要時間を記録するだけ。
- 変更後検査: `git diff --check`
- コミット: `docs: record module migration baseline`
- 停止条件: テスト失敗、`.env` がステージ済み、作業ツリーが開始時点でdirty。

#### P0-2: import・設定台帳

- 実行:

```powershell
rg -n "(?:from|import) app\." app tests scripts . --glob '*.py' --glob '*.sh'
rg -n "monkeypatch\.setattr|patch\(" tests --glob '*.py'
rg -n "COPY |ENTRYPOINT|CMD|python .*_main\.py|cloud_job_runner\.py|dashboard_server\.py" Dockerfile Dockerfile.dashboard cloudbuild.dashboard.yaml scripts --glob '*.ps1' --glob '*.sh'
```

- 変更: 検出した旧パス、文字列モンキーパッチ、ルート実行ファイル参照を `refactoring_progress.md` に記録するだけ。
- コミット: `docs: record module import migration inventory`
- 停止条件: コマンド自体がエラーになる場合。検索結果が0件であることはエラーではない。

### 12.6 Phase 1 カード: operations

カードは次の順で実行する。

| カード | 旧パス → 新パス | 変更前後に同じコマンドを実行 | コミット |
| --- | --- | --- | --- |
| P1-1 | `app/operations_db.py` → `app/operations/sqlite.py` | `python -m pytest -q tests/test_operations_db.py tests/test_weekly_backup.py tests/test_db_pipeline_main.py` | `refactor: move sqlite operations into package` |
| P1-2 | `app/firestore_ops.py` → `app/operations/firestore.py` | `python -m pytest -q tests/test_firestore_operations.py tests/test_firestore_dashboard_metrics.py tests/test_db_pipeline_main.py` | `refactor: move firestore operations into package` |
| P1-3 | `app/postgres_ops.py` → `app/operations/postgres.py` | `python -m pytest -q tests/test_postgres_operations.py tests/test_dashboard_backend_parity.py` | `refactor: move postgres operations into package` |
| P1-4 | `app/db_sync.py` → `app/operations/sync.py` | `python -m pytest -q tests/test_drive_backup.py tests/test_db_pipeline_main.py` | `refactor: move database sync into operations` |
| P1-5 | `db_pipeline_main.py` の実装 → `app/operations/workflow.py` | `python -m pytest -q tests/test_db_pipeline_main.py tests/test_operations_db.py tests/test_firestore_operations.py tests/test_postgres_operations.py` | `refactor: isolate operations pipeline workflow` |

P1-5では、ルート `db_pipeline_main.py` を次の形にする。ただし現在の実装が `main()` 以外の公開記号をテストから参照している場合は停止し、参照一覧を記録する。

```python
from app.operations.workflow import main

if __name__ == "__main__":
    raise SystemExit(main())
```

Phase 1完了後に次を実行する。

```powershell
python -m pytest -q tests/test_operations_db.py tests/test_firestore_operations.py tests/test_postgres_operations.py tests/test_dashboard_backend_parity.py tests/test_db_pipeline_main.py tests/test_drive_backup.py tests/test_weekly_backup.py
```

### 12.7 Phase 2 カード: forecasting

P2-1は移動を行わない。先に私的参照を整理する。

#### P2-1: 私的参照の分類

- 実行: `rg -n "from app\.(forecast_correction|comfort_load_forecast|pv_array_forecast) import _|monkeypatch\.setattr\(\"app\.(forecast_correction|comfort_load_forecast|pv_array_forecast)" tests scripts --glob '*.py'`
- 各検出を「独立契約として新モジュールで直接テスト」または「公開APIテストへ置換」に分類して進捗ログへ記録する。
- このカードではテスト・実装を変更しない。
- コミット: `docs: classify forecasting private test dependencies`

移動カード:

| カード | 旧パス → 新パス | 変更前後テスト | コミット |
| --- | --- | --- | --- |
| P2-2 | `app/consumption_forecast.py` → `app/forecasting/consumption.py` | `python -m pytest -q tests/test_consumption_forecast.py tests/test_energy_model_runtime.py tests/test_occupancy_schedule.py` | `refactor: move consumption forecast into package` |
| P2-3 | `app/comfort_load_forecast.py` → `app/forecasting/comfort_load.py` | `python -m pytest -q tests/test_comfort_load_forecast.py tests/test_energy_model.py` | `refactor: move comfort forecast into package` |
| P2-4 | `app/pv_array_forecast.py` → `app/forecasting/pv_array.py` | `python -m pytest -q tests/test_pv_array_forecast.py tests/test_external_site_access.py tests/test_energy_model.py` | `refactor: move pv array forecast into package` |
| P2-5 | `app/pv_physical_forecast.py` → `app/forecasting/pv_physical.py` | `python -m pytest -q tests/test_pv_physical_forecast.py tests/test_energy_model.py` | `refactor: move physical pv forecast into package` |
| P2-6 | `app/occupancy_schedule.py` → `app/forecasting/occupancy.py` | `python -m pytest -q tests/test_occupancy_schedule.py tests/test_consumption_forecast.py tests/test_energy_model.py` | `refactor: move occupancy forecast input into package` |
| P2-7 | `app/forecast_correction.py` → `app/forecasting/correction.py` | `python -m pytest -q tests/test_energy_model.py tests/test_external_site_access.py tests/test_comfort_load_forecast.py` | `refactor: move forecast correction into package` |

- P2-3、P2-4、P2-7は、旧パスへの私的importまたは文字列モンキーパッチが1件でも残る間は実行しない。
- 予測値、補正係数、provider timeout、診断JSONが変更前後で異なれば停止する。

### 12.8 Phase 3 カード: energy_plan

| カード | 作業 | 変更前後テスト | コミット |
| --- | --- | --- | --- |
| P3-1 | `app/energy_model.py` → `app/energy_plan/energy_model.py` | `python -m pytest -q tests/test_energy_model.py tests/test_energy_model_runtime.py` | `refactor: move energy model into energy plan` |
| P3-2 | `app/soc_cost_optimizer.py` → `app/energy_plan/soc_cost.py` | `python -m pytest -q tests/test_soc_cost_optimizer.py tests/test_energy_model.py tests/test_energy_model_runtime.py` | `refactor: move soc cost model into energy plan` |
| P3-3 | `app/soc_decision_feedback.py` → `app/energy_plan/decision_feedback.py` | `python -m pytest -q tests/test_soc_decision_feedback.py tests/test_energy_model_runtime.py tests/test_cloud_job_runner.py` | `refactor: move decision feedback into energy plan` |
| P3-4 | `app/night_plan.py` → `app/energy_plan/night_plan.py` | `python -m pytest -q tests/test_domain_primitives.py tests/test_night_plan_archive.py tests/test_energy_plan_document.py` | `refactor: move night plan into energy plan` |
| P3-5 | `energy_model_main.py` の実装を責務単位で `app/energy_plan/workflow.py` へ抽出 | `python -m pytest -q tests/test_energy_model.py tests/test_energy_model_runtime.py tests/test_energy_plan_output.py tests/test_energy_plan_document.py` | `refactor: isolate energy plan workflow` |

P3-5は一コミットに2863行を移してはならない。次の5サブカードを順に使う。それぞれ同じ対象テストを実行し、一コミットにする。

1. P3-5a: 設定データクラスと設定読込。
2. P3-5b: 履歴・予報入力取得。
3. P3-5c: 消費・PV・補正モデルの組立て。
4. P3-5d: SOC計画計算と診断値組立て。
5. P3-5e: JSON保存と `main()`。最後にルートを薄い入口へ置換。

### 12.9 Phase 4 カード: kpnet と dashboard

| カード | 作業 | 変更前後テスト | コミット |
| --- | --- | --- | --- |
| P4-1 | KP-NET私的テスト依存を分類する。移動しない。 | `python -m pytest -q tests/test_kpnet_workflow.py tests/test_kpnet_settings_intent.py` | `docs: classify kpnet private test dependencies` |
| P4-2 | `app/kpnet_workflow.py` → `app/kpnet/workflow.py` | `python -m pytest -q tests/test_kpnet_workflow.py tests/test_kpnet_settings_intent.py tests/test_cloud_job_runner.py` | `refactor: move kpnet workflow into package` |
| P4-3 | Dashboard私的テスト依存を分類する。移動しない。 | `python -m pytest -q tests/test_dashboard_data.py tests/test_dashboard_backend_parity.py tests/test_dashboard_server.py` | `docs: classify dashboard private test dependencies` |
| P4-4 | `app/dashboard_data.py` のデータロードを `app/dashboard/data.py` へ移す | `python -m pytest -q tests/test_dashboard_data.py tests/test_dashboard_backend_parity.py tests/test_firestore_dashboard_metrics.py` | `refactor: move dashboard data into package` |
| P4-5 | `dashboard_server.py` の実装を `app/dashboard/server.py` へ移す | `python -m pytest -q tests/test_dashboard_server.py tests/test_dashboard_data.py` | `refactor: isolate dashboard server` |

- P4-2とP4-4は旧パスへの私的参照が残る間は実行しない。
- P4-2では実機を呼ばない。P4-4とP4-5では外部DBやHTTPサーバーを起動しない。

### 12.10 Phase 5・6カード: backup、exports、local_control、domain

| カード | 旧パス → 新パス | 変更前後テスト | コミット |
| --- | --- | --- | --- |
| P5-1 | `app/drive_backup.py` → `app/backup/drive.py` | `python -m pytest -q tests/test_drive_backup.py` | `refactor: move drive backup into package` |
| P5-2 | `app/weekly_backup.py` → `app/backup/weekly.py` | `python -m pytest -q tests/test_weekly_backup.py` | `refactor: move weekly backup into package` |
| P5-3 | `app/artifact_cleanup.py` → `app/backup/artifacts.py` | `python -m pytest -q tests/test_artifact_cleanup.py` | `refactor: move artifact cleanup into package` |
| P5-4 | `app/night_plan_archive.py` → `app/backup/night_plan_archive.py` | `python -m pytest -q tests/test_night_plan_archive.py tests/test_firestore_operations.py` | `refactor: move night plan archive into package` |
| P6-1 | `app/sheets_export.py` → `app/exports/sheets.py` | `python -m pytest -q tests/test_occupancy_schedule.py tests/test_cloud_job_runner.py` | `refactor: move sheets export into package` |
| P6-2 | `app/browser_automation.py` → `app/local_control/browser.py` | `python -m pytest -q tests/test_decision.py tests/test_domain_primitives.py` | `refactor: move local browser automation into package` |
| P6-3 | `app/csv_utils.py` → `app/local_control/csv_input.py` | `python -m pytest -q tests/test_decision.py tests/test_domain_primitives.py` | `refactor: move local csv input into package` |
| P6-4 | `app/decision.py` → `app/local_control/decision.py` | `python -m pytest -q tests/test_decision.py` | `refactor: move local decision into package` |
| P6-5 | `app/history_store.py` → `app/local_control/history.py` | `python -m pytest -q tests/test_decision.py` | `refactor: move local history into package` |
| P6-6 | `app/main.py` → `app/local_control/workflow.py` | `python -m pytest -q tests/test_decision.py tests/test_domain_primitives.py` | `refactor: isolate local controller workflow` |
| P6-7 | `app/constants.py` → `app/domain/constants.py` | `python -m pytest -q tests/test_domain_primitives.py tests/test_energy_model.py tests/test_cloud_job_runner.py` | `refactor: move battery constants into domain` |
| P6-8 | `app/time_windows.py` → `app/domain/time_windows.py` | `python -m pytest -q tests/test_domain_primitives.py tests/test_operations_domain.py tests/test_forced_charge_settings.py` | `refactor: move time windows into domain` |
| P6-9 | `app/tariff.py` → `app/domain/tariff.py` | `python -m pytest -q tests/test_domain_primitives.py tests/test_operations_cost_daily.py tests/test_soc_cost_optimizer.py` | `refactor: move tariff rules into domain` |

- `config.py`、`models.py`、`monitoring_csv.py`、`csv_merge.py`、`utils.py` の所有先は、12.10.1の決定を正とする。Lunaは別の所有先を考案してはならない。
- P6-2、P6-3、P6-5は直接テストが不足している可能性がある。対象記号を実行する既存テストが見つからなければ、移動前に最小の契約テストを追加する別カードを作り、そのカードだけ実行する。

#### 12.10.1 保留モジュールの所有先決定

この表は実装前の責務・利用元監査で確定した。実行時に再判断しない。

| 旧モジュール・記号 | 正規の所有先 | 理由 |
| --- | --- | --- |
| `app/config.py` の `AppConfig` | `app/local_control/config.py` | ブラウザ操作、ローカルCSV、ローカル判断、ローカル履歴だけが利用する。 |
| `app/models.py` の4データクラス | `app/local_control/models.py` | `ForecastResult`、`MonitoringMetrics`、`DesiredBatterySetting`、`ApplyResult` はローカル制御専用である。 |
| `MonitoringPoint`、`validated_soc_percent` | `app/domain/monitoring.py` | 外部I/Oを持たない実績値とSOC境界のドメイン契約である。 |
| `iter_monitoring_points` | `app/operations/monitoring_csv.py` | CSVファイル、文字コード、列名、日時形式を扱う運用I/Oである。 |
| `app/csv_merge.py` | `app/operations/csv_merge.py` | CSV探索、検証、重複排除、出力は運用データの前処理である。`scripts/merge_csvs.py` はCLI入口として残す。 |
| dotenvと環境変数読取 | `app/configuration/environment.py` | 複数機能が共有する実行時設定境界であり、予測・運用・KP-NETのどれか一つには属さない。 |
| 外部値の数値解析 | `app/parsing/numbers.py` | `to_float`、`to_int`、`parse_csv_float` は外部値を有限数へ正規化する境界である。 |
| `clamp_percent` | `app/domain/constants.py` | SOC・パーセント境界と同じドメイン規則である。 |
| `app/utils.py` | 互換モジュールとして維持 | 実装は持たず、移動済み公開記号を明示的に再公開する。 |

禁止事項:

- `app/common/`、`app/shared/`、`app/helpers/` を新設しない。
- `utils.py` を一括で別名の汎用モジュールへ移さない。
- 公開関数名、引数、デフォルト値、例外型、環境変数名、CSV列名を変更しない。
- 互換モジュールで `import *` を使わない。
- 構成移動とロジック改善、名称改善、フォーマット全面変更を同じカードで行わない。

#### 12.10.2 Luna低レベル共通実行手順

P6-10からP6-17は必ず番号順に一枚ずつ実行する。複数カードを一コミットにまとめない。

各カードの開始時:

1. `git status --short` を実行する。
2. 出力が1行でもあれば、そのカードを開始せず停止する。
3. `git rev-parse HEAD` を実行し、ハッシュを `refactoring_progress.md` のカード開始記録へ書く。
4. カード指定の変更前テストを実行する。
5. テストが1件でも失敗したら、ソースを変更せず停止する。

各カードの変更中:

1. 「許可変更」に列挙されたファイルだけを変更する。
2. 移動は `git mv <旧パス> <新パス>` を使う。
3. 旧パスには公開記号を明示的にimportする互換モジュールを置く。
4. 新規実装側とリポジトリ内部の利用側は正規パスをimportする。
5. 互換性テストでは `legacy.Symbol is canonical.Symbol` を公開記号ごとに確認する。
6. 英語コメントを追加する場合は中学生程度の短い英語にする。移動理由を説明するコメントは互換モジュールのdocstringだけでよい。

各カードの終了時:

1. カード指定の変更後テストを実行する。
2. 次を実行する。

```powershell
python -m compileall -q app scripts cloud_job_runner.py dashboard_server.py db_pipeline_main.py energy_model_main.py kpnet_main.py main.py sheets_export_main.py
git diff --check
git status --short
```

3. 変更前後のテスト件数、失敗修正、互換記号、構文検査、差分検査を `refactoring_progress.md` に日本語で記録する。
4. カード指定のメッセージでコミットする。
5. `git status --short` が空であることを確認してから次へ進む。

共通停止条件:

- 循環importが発生した。
- 旧パスと新パスで公開オブジェクトが同一にならない。
- 文字列モンキーパッチが旧パスを指し、正規パスへ変更するとテスト内容まで変わる。
- 数値変換結果、環境変数の既定値、CSV出力、SOC検証、例外型が変わる。
- 新しい依存ライブラリが必要になる。

#### P6-10: ローカル制御設定

- 旧パス → 新パス: `app/config.py` → `app/local_control/config.py`
- 公開記号: `AppConfig`
- 許可変更: 上記2ファイル、`app/local_control/browser.py`、`decision.py`、`history.py`、`csv_input.py`、`workflow.py`、`tests/test_local_control_config_compatibility.py`、進捗ログ。
- 変更前テスト: `python -m pytest -q tests/test_decision.py tests/test_utils.py`
- 作業:
  1. `AppConfig`実装を新パスへ移す。
  2. `app.local_control.*` の `from app.config import AppConfig` を `from app.local_control.config import AppConfig` へ変更する。
  3. 旧 `app/config.py` は `AppConfig`だけを明示的に再公開する。
  4. 互換性テストで旧・新の `AppConfig` が同一であることを確認する。
- 変更後テスト: 変更前テスト + `tests/test_local_control_config_compatibility.py`
- コミット: `refactor: move local controller config into package`

#### P6-11: ローカル制御モデル

- 旧パス → 新パス: `app/models.py` → `app/local_control/models.py`
- 公開記号: `ForecastResult`、`MonitoringMetrics`、`DesiredBatterySetting`、`ApplyResult`
- 許可変更: 上記2ファイル、`app/local_control/browser.py`、`decision.py`、`csv_input.py`、`workflow.py`、`tests/test_decision.py`、`tests/test_local_control_models_compatibility.py`、進捗ログ。
- 変更前テスト: `python -m pytest -q tests/test_decision.py`
- 作業: 4クラスを新パスへ移し、ローカル制御とテストのimportを正規パスへ変更し、旧パスで4クラスを明示的に再公開する。
- 変更後テスト: 変更前テスト + `tests/test_local_control_models_compatibility.py`
- コミット: `refactor: move local controller models into package`

#### P6-12: 環境変数境界

- 新規正規パス: `app/configuration/__init__.py`、`app/configuration/environment.py`
- 移動記号: `load_dotenv_if_present`、`env`、`env_bool`、`env_int`、`env_float`、`env_float_clamped`
- 私的定数: `TRUE_VALUES`、`FALSE_VALUES` は新モジュールで `_TRUE_VALUES`、`_FALSE_VALUES` とする。値は変更しない。
- 許可変更: 新規2ファイル、`app/utils.py`、上記関数をimportする既存Python、`tests/test_utils.py`、`tests/test_configuration_environment.py`、進捗ログ。
- 変更前テスト: `python -m pytest -q tests/test_utils.py tests/test_forced_charge_settings.py tests/test_operations_db.py`
- 作業:
  1. 6関数の本体をそのまま新モジュールへ移す。
  2. 内部利用側を `app.configuration.environment` へ変更する。
  3. `app/utils.py` から6関数の本体を削除し、新パスから明示的にimportする。
  4. 互換テストで6関数の同一性を確認する。
- 停止条件: 真偽値文字列、空文字、必須値エラー、数値変換エラー、clamp結果が変わる。
- 変更後テスト: 変更前テスト + `tests/test_configuration_environment.py`
- コミット: `refactor: extract environment configuration helpers`

#### P6-13: 外部数値の解析

- 新規正規パス: `app/parsing/__init__.py`、`app/parsing/numbers.py`
- 移動記号: `to_float`、`to_int`、`parse_csv_float`
- 私的定数: `CSV_NUMBER_PATTERN` は `_CSV_NUMBER_PATTERN` とする。正規表現は変更しない。
- 許可変更: 新規2ファイル、`app/utils.py`、3関数をimportする既存Python、`tests/test_utils.py`、`tests/test_parsing_numbers.py`、進捗ログ。
- 変更前テスト: `python -m pytest -q tests/test_utils.py tests/test_consumption_forecast.py tests/test_pv_array_forecast.py tests/test_operations_db.py`
- 作業: 3関数と必要なoverloadを移し、内部importを正規パスへ変更し、`app/utils.py` は3関数を明示的に再公開する。
- 停止条件: `None`、bool、NaN、無限大、カンマ付き数値、単位付き数値、不正文字列の結果が変わる。
- 変更後テスト: 変更前テスト + `tests/test_parsing_numbers.py`
- コミット: `refactor: extract external number parsing helpers`

#### P6-14: パーセント境界

- 旧記号 → 新所有先: `app.utils.clamp_percent` → `app.domain.constants.clamp_percent`
- 許可変更: `app/utils.py`、`app/domain/constants.py`、`tests/test_utils.py`、`tests/test_domain_primitives.py`、進捗ログ。
- 変更前テスト: `python -m pytest -q tests/test_utils.py tests/test_domain_primitives.py`
- 作業: 関数本体を新所有先へ移し、旧 `app.utils` から明示的に再公開する。引数名とデフォルト値を変えない。
- 変更後テスト: 変更前テスト。追加テストで旧・新関数の同一性も確認する。
- コミット: `refactor: move percent boundary into domain`

#### P6-15: 監視実績ドメイン

- 旧記号 → 新所有先: `MonitoringPoint`、`validated_soc_percent` → `app/domain/monitoring.py`
- 許可変更: `app/monitoring_csv.py`、新規 `app/domain/monitoring.py`、`tests/test_domain_primitives.py`、`tests/test_monitoring_domain_compatibility.py`、進捗ログ。
- 変更前テスト: `python -m pytest -q tests/test_domain_primitives.py tests/test_operations_domain.py`
- 作業:
  1. データクラスとSOC検証関数だけを移す。
  2. `iter_monitoring_points` はまだ旧ファイルに残し、新ドメイン型をimportさせる。
  3. 旧ファイルから2記号を明示的に再公開する。
- 停止条件: `as_storage_row()` のキー、日時文字列、SOC 0〜100境界、NaN処理が変わる。
- 変更後テスト: 変更前テスト + `tests/test_monitoring_domain_compatibility.py`
- コミット: `refactor: extract monitoring domain values`

#### P6-16: 監視CSV入力

- 旧記号 → 新所有先: `iter_monitoring_points` → `app/operations/monitoring_csv.py`
- 許可変更: `app/monitoring_csv.py`、新規 `app/operations/monitoring_csv.py`、`app/operations/domain.py`、`tests/test_domain_primitives.py`、`tests/test_operations_monitoring_csv_compatibility.py`、進捗ログ。
- 変更前テスト: `python -m pytest -q tests/test_domain_primitives.py tests/test_operations_domain.py tests/test_operations_db.py`
- 作業: CSV読取関数と必要な標準ライブラリimportを移し、`app.operations.domain` を正規パスへ変更する。旧ファイルは3公開記号だけを明示的に再公開する互換モジュールにする。
- 停止条件: `utf-8-sig`、日本語列名、`%Y/%m/%d %H:%M`、不正行をスキップする条件が変わる。
- 変更後テスト: 変更前テスト + `tests/test_operations_monitoring_csv_compatibility.py`
- コミット: `refactor: move monitoring csv input into operations`

#### P6-17: CSV統合

- 旧パス → 新パス: `app/csv_merge.py` → `app/operations/csv_merge.py`
- 公開記号: `DEFAULT_EXCLUDED_DIR_NAMES`、`CsvMergeResult`、`discover_csv_files`、`merge_csv_files`
- 許可変更: 上記2ファイル、`scripts/merge_csvs.py`、`tests/test_csv_merge.py`、`tests/test_operations_csv_merge_compatibility.py`、進捗ログ。
- 変更前テスト: `python -m pytest -q tests/test_csv_merge.py`
- 作業: 実装を新パスへ移し、CLIとテストを正規パスへ変更し、旧パスで4公開記号を明示的に再公開する。
- 停止条件: 除外ディレクトリ、探索順、ヘッダー不一致例外、重複判定、`source_file`、改行、UTF-8 BOM処理が変わる。
- 変更後テスト: 変更前テスト + `tests/test_operations_csv_merge_compatibility.py`
- コミット: `refactor: move csv merge into operations`

#### P6-18: 保留モジュール完了検査

次を順に実行する。

```powershell
python -m pytest -q tests/test_decision.py tests/test_utils.py tests/test_domain_primitives.py tests/test_csv_merge.py tests/test_operations_domain.py tests/test_operations_db.py
rg -n --glob '*.py' "from app\.(config|models|monitoring_csv|csv_merge|utils) import" app scripts tests
python -m compileall -q app scripts cloud_job_runner.py dashboard_server.py db_pipeline_main.py energy_model_main.py kpnet_main.py main.py sheets_export_main.py
git diff --check
git status --short
```

- `rg` の残りは互換性テストまたは外部CLI互換だけでなければならない。アプリケーション実装が旧パスをimportしていたら停止して正規パスへ直す。
- `app/config.py`、`models.py`、`monitoring_csv.py`、`csv_merge.py`、`utils.py` がロジックを持っていたら停止する。
- 循環import検査として、新旧両パスを同一Pythonプロセスでimportする契約テストを実行する。
- コミット: `test: verify remaining module ownership migration`

### 12.11 Phase 7カード: cloud_job_runner

P7は最後に実行する。次のサブカードを一つずつ処理し、毎回 `python -m pytest -q tests/test_cloud_job_runner.py tests/test_forced_charge_state_machine.py tests/test_forced_charge_settings.py` を変更前後に実行する。

1. P7-1: `SocReading` と純粋な時刻・SOC判定を `app/runtime/cloud_job.py` へ移す。
2. P7-2: Firestoreへの結果保存・計画復元を移す。
3. P7-3: `_run` とサブプロセス組立てを移す。呼び出すルートファイル名は変更しない。
4. P7-4: 23:00処理を移す。
5. P7-5: 03:00監視処理を移す。
6. P7-6: 07:00処理と `main()` を移し、ルートを薄い入口にする。

各サブカードのコミットは `refactor: extract cloud job <対象責務>` とする。テストのモック先が旧ルートを指している場合は、モック先変更だけを先行カードにし、実装移動と同じコミットに混ぜない。

### 12.12 全フェーズ完了カード

#### PF-1: 全検証

```powershell
python -m pytest -q
python -m compileall -q app scripts cloud_job_runner.py dashboard_server.py db_pipeline_main.py energy_model_main.py kpnet_main.py main.py sheets_export_main.py
git diff --check
git status --short
```

- mypyが導入済みなら、`python -m mypy` に続けて実際の対象パスを指定する。コマンドが不明なら推測せず、`pyproject.toml` と既存CI／スクリプトを検索して停止する。
- 全て成功した場合だけ、更新済みの構成図、残存互換モジュール、未移動モジュール、全テスト結果を進捗ログへ記録する。
- コミット: `docs: record completed module migration validation`

#### PF-2: 可読性再監査

- `readable-code-audit` を全ソースへ適用する。
- 未処置の `STRUCT-01`、`STRUCT-03`、`STRUCT-04` を全件報告する。
- 正当な例外には近接した `readable-code-audit: skip RULE-ID — concrete reason` があることを確認する。
- 未処置件数が0で、全回帰が成功した場合だけ計画を完了扱いにする。

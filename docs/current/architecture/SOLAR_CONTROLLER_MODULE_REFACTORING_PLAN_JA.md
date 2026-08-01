# Solar Controller モジュール再編計画

最終更新: 2026-08-01 (JST)

## 1. 目的と完了条件

この計画は、太陽光発電・蓄電池の予測、SOC計画、実績取込、KP-NET機器操作、ダッシュボード、バックアップを扱う本リポジトリの `app/` を、機能単位で発見しやすく安全に再編するための実行手順である。

完了条件は次のすべてを満たすこと。

- 新規参加者が、機能名から実装の置き場所を予測できる。
- 計画計算、機器操作、永続化、表示、外部出力の責務境界が明確である。
- 現在のCLI、Cloud Runエントリポイント、環境変数、保存形式、外部JSON、既存の `app.<module>` import を壊さない。
- 変更ごとに対象テストが成功し、最終的に全回帰、Python構文検査、差分検査が成功する。
- 互換モジュールを残す場合は、対象、理由、削除条件が文書化される。

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

## 3. 理想構成

以下を目標構成とする。`*_main.py`、`cloud_job_runner.py`、`dashboard_server.py` は実行環境・Docker・Cloud Runから直接参照されるため、リポジトリルートに維持する。

```text
app/
  shared/                 # 依存の薄い共通値・変換・時刻・料金・監視CSV
    config.py
    constants.py
    models.py
    utils.py
    time_windows.py
    tariff.py
    monitoring_csv.py
  forecasting/            # PV・負荷予測と予測補正
    consumption.py
    comfort_load.py
    pv_array.py
    pv_physical.py
    correction.py
    occupancy.py
  planning/               # SOCの評価、最適化、夜間充電計画
    energy_model.py
    soc_cost.py
    decision_feedback.py
  operations/             # 実績取込、日次集計、DBバックエンド、同期
    domain.py
    cost_daily.py
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
  forced_charge/
  settings/
```

これは最終的な所有境界であり、全モジュールを一度に移す指示ではない。`shared/` は巨大な便利箱にしてはならない。二つ以上の機能で利用され、かつ業務ポリシーを持たないものだけを置く。`energy_plan/` は計画文書の入出力モデルとして維持し、`planning/` はその入力からSOCを決定する計算を持つ。

## 4. ギャップ分析

| ギャップ | 影響 | 優先度 | 埋め方 |
| --- | --- | --- | --- |
| `operations/` にアダプターがない | SQLite / Firestore / PostgreSQLの取込先を見つけにくい | 高 | 互換モジュールを残して3アダプターと同期を段階移動する。 |
| 予測機能がルートに散在 | 予測モデル変更の影響範囲を把握しにくい | 高 | 先にテストとスクリプトの私的ヘルパー依存を解消し、その後に移動する。 |
| SOC計画の中核が分散 | 経済最適化・フィードバック・基本計算の境界が曖昧 | 中 | まず公開契約を固定してから `planning/` を作る。 |
| KP-NET / dashboard の実装が既存パッケージ外 | 機能名と配置が一致しない | 中 | 高結合テストの境界整理後に移動する。 |
| バックアップ群が散在 | 復旧手順と保管実装を探しにくい | 中 | 運用スクリプトとの依存を確認後、独立パッケージへ移す。 |
| 共通モジュールがルートに残る | 一見散在に見える | 低 | 値型・変換・料金・時刻などは移動メリットを確認してから扱う。無理に `shared/` を導入しない。 |

## 5. 共通の安全ルール

各フェーズで必ず次を行う。

1. `git status --short` が意図しない変更を含まないことを確認する。
2. 移動対象のimport、CLI、テスト、文字列指定のモンキーパッチ、環境変数、保存フィールドを `rg` で一覧化する。
3. 変更前に最小の対象テストを実行し、結果を記録する。
4. 一つのパッケージまたは一つの互換境界だけを変更する。
5. 旧公開パスを維持する場合、旧モジュールには移動先と互換性の理由を簡潔な英語コメントで書く。削除予定が未定なら、削除しない。
6. 変更後に対象テスト、`python -m compileall -q ...`、`git diff --check` を実行する。
7. 合格後に一つの論理的なコミットを作り、`refactoring_progress.md` に日本語で対象、判断、不変条件、実行コマンド、結果、残リスクを書く。
8. 金額、SOC、安全制御、外部機器操作、Firestore/DB書込みの意味を変える変更は、構成移動とは別コミットにする。

## 6. 実行フェーズ

### Phase 0: ベースラインと移行台帳

目的: 移動前の契約を固定し、以後の差分を判断可能にする。

手順:

1. `python -m pytest -q` を実行し、基準結果を `refactoring_progress.md` に記録する。
2. 各候補について `rg -n "app\.(旧モジュール名)" app tests scripts *.py` を実行する。
3. 次の台帳を計画文書または進捗ログへ記録する: 旧import、入口、テスト、私的ヘルパー参照、外部契約、対象テスト、互換モジュールの要否。
4. `cloud_job_runner.py`、`db_pipeline_main.py`、`energy_model_main.py`、`kpnet_main.py`、`dashboard_server.py` のimportと責務を再確認する。

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

追加テスト:

- 旧パスと新パスの代表関数が同一の結果を返すことを確認する小さな互換性テストを追加する。
- SQLite / Firestore / PostgreSQLの共通契約が同じ入力で同じ日次意味を保持することを既存パリティテストで確認する。

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

### Phase 3: SOC計画を `app/planning/` へ整理する

目的: 翌朝SOC目標、日中SOC最適化、予測不確実性、過去決定フィードバックを一つの機能境界にまとめる。

対象順序:

1. `energy_model.py` → `planning/energy_model.py`
2. `soc_cost_optimizer.py` → `planning/soc_cost.py`
3. `soc_decision_feedback.py` → `planning/decision_feedback.py`

手順:

1. `energy_model_main.py` が消費予測、PV予測、補正、SOC最適化を組み立てる契約をテストで固定する。
2. 金額・SOC・売買電・ペナルティの不変条件を対象テストで明示する。
3. 最初は旧importを保持し、`energy_model_main.py` だけを新パスへ移行する。
4. 金額計算式、SOC境界、環境変数、計画JSONキーは変更しない。

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

停止条件:

- KP-NETの設定順序、充電停止条件、07:00への復帰、ダッシュボードAPI応答形状、認証処理のいずれかが変わる場合は停止する。

### Phase 5: バックアップ・アーカイブと共有モジュール

目的: 復旧機能の発見性を上げる。ただし運用スクリプト、Drive、Firestore、復旧ファイル形式を変えない。

手順:

1. `drive_backup.py`、`weekly_backup.py`、`artifact_cleanup.py`、`night_plan_archive.py` を `backup/` へ一件ずつ移す。
2. `tests/test_drive_backup.py`、`tests/test_weekly_backup.py`、`tests/test_artifact_cleanup.py`、`tests/test_night_plan_archive.py` を毎回実行する。
3. `utils.py`、`tariff.py`、`time_windows.py`、`monitoring_csv.py` などは、実際に二つ以上の機能から利用されるものだけを `shared/` へ移す。名前だけで移さない。

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

- `rg -n "from app\.(operations_db|firestore_ops|postgres_ops|forecast_correction|kpnet_workflow|dashboard_data)" app tests scripts` の残りは、意図した互換性テストまたは未移行の外部契約だけである。
- `readable-code-audit` を全ソースへ再適用し、未処置の `STRUCT-03`、`STRUCT-01`、`STRUCT-04` 候補がない。
- `readable-code-audit: skip` はすべて近接し、具体的な理由が現在のコードと一致する。
- 本番デプロイ、実機KP-NET操作、Firestore/Driveへの書込みは、このリファクタリング計画の検証では行わない。

## 9. AIへの実行指示

この計画を実行するAIは、次を守る。

1. 一度に一フェーズ、一モジュールだけを扱う。
2. ファイル移動の前にimport台帳と対象テストを示す。
3. 構成移動と仕様変更を混ぜない。
4. 新しいテストは、移動先の正規契約または旧パス互換契約のどちらを検証するかを明確にする。
5. テスト失敗、外部契約不明、モンキーパッチ互換性の不明があれば停止し、推測で互換性を壊さない。
6. 各コミット後に進捗ログへ、日本語で判断・不変条件・コマンド・結果・残リスクを残す。

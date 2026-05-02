# Solar Controller Automation (Cloud Run Jobs)

23:00 と 07:00（JST）に以下を自動実行する Python 実装です。

1. ブラウザで 12 時間先の太陽日射時間を取得  
2. モニタリングサービスにログインして CSV を取得  
3. CSV と予報値から蓄電池設定を判定  
4. 蓄電池設定（充電上限/モード）を更新

実行基盤は **Cloud Run Jobs + Cloud Scheduler** を想定しています。

## 公開向け説明文と画面イメージ

- 説明文（日本語）: [docs/PUBLIC_DESCRIPTION_JA.md](docs/PUBLIC_DESCRIPTION_JA.md)
- Google Cloud運用前提: [docs/GCP_OPERATION_JA.md](docs/GCP_OPERATION_JA.md)
- ダッシュボードPNG: [docs/images/dashboard.png](docs/images/dashboard.png)

## データ保存（段階運用）

- 現在: `artifacts/` に実行ごとの `summary.json` とCSVを保存
- 追加: `artifacts/history.csv`（可読）と `artifacts/history.db`（SQLite）へ履歴を追記
- 本番推奨: `DATA_BACKEND=postgres` で Compute Engine 上 PostgreSQL に永続化

注意: Cloud Run のコンテナファイルシステムはインメモリで、インスタンス停止時に永続化されません。  
クラウド本番で履歴を残す場合は PostgreSQL などのDB連携を使ってください。  

## 1. 事前準備

- Python 3.12+
- `pip install -r requirements.txt`
- `playwright install chromium`
- `.env.example` をコピーして `.env` を作成し、URL/セレクタ/認証情報を埋める
  - 予報サイトは東京都府中市向け Open-Meteo 設定を初期値として同梱済み

PowerShell:

```powershell
Copy-Item .env.example .env
```

## 2. ローカル実行

`.env` を読み込んで実行します（シェルでの `export` は不要）。

```powershell
python main.py
```

初期値の `.env.example` は `LOCAL_DEV_MODE=true` です。  
この場合は監視サイトへログインせず、`LOCAL_MONITOR_CSV_PATH` のCSVを使って判定・履歴保存まで完走します。

- `LOCAL_FORECAST_HOURS_OVERRIDE` が空: Open-Meteoへアクセスして翌日予報を取得  
- `LOCAL_FORECAST_HOURS_OVERRIDE=4.5` のように設定: 固定値で予報処理を疑似実行

初期値では `DRY_RUN=true` のため、設定変更は実行されません（ローカルでは疑似更新扱い）。  
本番サイトまで含めて動かすときは `LOCAL_DEV_MODE=false` にして、`MONITOR_*` とセレクタを実値に設定してください。

## 3. KP-NET実機フロー（ログイン→CSV→設定変更→ログアウト）

HAR解析ベースで、以下の実フローを自動実行するエントリを追加しています。

1. ログイン
2. CSVダウンロード（指定月 + 最新月）
3. 強制充電プロファイル確認/登録（既定）
4. （任意）グリーンモードプロファイル確認/登録
5. ログアウト
6. CSVグラフ生成（`kpi_plot.png`）

実行:

```powershell
python kpnet_main.py
```

実行モード（分離）:

- `KP_WORKFLOW_MODE=csv` : ログイン→CSV取得→グラフ→ログアウト
- `KP_WORKFLOW_MODE=settings` : ログイン→設定変更→ログアウト
- `KP_WORKFLOW_MODE=all` : 両方実行（既定）
- `KP_SETTINGS_SEQUENCE=forced-only` : 強制充電のみ（既定）
- `KP_SETTINGS_SEQUENCE=forced-then-green` : 強制充電後にグリーンモードも適用
- `KP_DYNAMIC_FORCED_PROFILE=true` : `artifacts/night_charge_plan.json` から
  - 必要充電量(kWh)
  - 7時目標SOC
  - 夜間実測充電レート(kW推定)
  を使って、強制充電の `SOC下限` / `SOC上限` / `充電開始時刻` を自動算出
- `KP_DYNAMIC_MODE_SWITCH_BY_TIME=true` : 現在時刻で設定先を自動選択
  - 夜間(23:00-07:00): 強制充電 + SOC下限(安心)=最大値
  - 日中(07:00-23:00): グリーンモード + SOC下限(経済/グリーン)=0%
  - この設定が `true` のときは `KP_SETTINGS_SEQUENCE` より時刻判定を優先

主な環境変数（`.env`）:

- `KP_MONITOR_USERNAME`, `KP_MONITOR_PASSWORD`
- `KP_ENFORCE_HTTPS=true` : `KP_BASE_URL` を https に制限
- `KP_ALLOWED_HOSTS=ctrl.kp-net.com` : 接続先ホストを許可リスト化
- `KP_WORKFLOW_MODE=csv|settings|all`
- `KP_SETTINGS_SEQUENCE=forced-only|forced-then-green`
- `KP_FORCE_SETTINGS_PROFILE=auto|forced|green`
- `KP_DYNAMIC_FORCED_PROFILE=true|false`
- `KP_DYNAMIC_MODE_SWITCH_BY_TIME=true|false`
- `KP_NIGHT_PLAN_PATH=artifacts/night_charge_plan.json`
- `KP_NIGHT_CHARGE_WINDOW_START=23:00`
- `KP_NIGHT_CHARGE_WINDOW_END=07:00`
- `KP_DAY_DISCHARGE_WINDOW_START=07:00`
- `KP_DAY_DISCHARGE_WINDOW_END=23:00`
- `KP_DEFAULT_CHARGE_POWER_KW=1.8`（夜間実測が取れない場合のフォールバック）
- `KP_CSV_TARGET_MONTHS=2026-04,2026-05`
- `KP_DOWNLOAD_LATEST_MONTH=true`
- `KP_CSV_OUTPUT_FORMAT=太陽光発電＋蓄電池`
- `KP_CSV_AGGR_TYPE=30分データ`
- `COST_TARIFF_MODE=night8_tiered`（`flat` も可）
- `NIGHT8_DAY_START_HHMM=07:00`
- `NIGHT8_DAY_END_HHMM=23:00`
- `NIGHT8_DAY_TIER1_UPPER_KWH=90`
- `NIGHT8_DAY_TIER2_UPPER_KWH=230`
- `NIGHT8_DAY_RATE_TIER1_YEN=31.80`
- `NIGHT8_DAY_RATE_TIER2_YEN=39.10`
- `NIGHT8_DAY_RATE_TIER3_YEN=43.62`
- `NIGHT8_NIGHT_RATE_YEN=28.85`
- `DAY_RATE_YEN_PER_KWH=31`（`COST_TARIFF_MODE=flat` 時に使用）
- `DATA_DB_PATH=artifacts/solar_monitor.db`
- `DATA_BACKEND=sqlite|postgres`
- `DATA_DB_SYNC_ENABLED=false`（既定。逐次Cloud Storage同期は無効化）
- `DATA_DB_WRITE_ONLY_23=true`（DB永続化は23時のみ）
- `DATA_WEEKLY_BACKUP_ENABLED=true`（週1回だけ差分バックアップ）
- `DATA_WEEKLY_BACKUP_WEEKDAY=5`（土曜）
- `DATA_WEEKLY_BACKUP_DIR=artifacts/backups/weekly`
- `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`（`DATA_BACKEND=postgres` 時）
- `DRY_RUN=true` の間は設定登録は行わず、確認画面到達までを検証

翌日予測から自動で設定登録する手順（ローカル実行）:

```powershell
# 1) 最新CSV取得
$env:KP_WORKFLOW_MODE='csv'
python kpnet_main.py

# 2) 翌日予測と実績から必要夜間充電量を算出
python energy_model_main.py

# 3) 設定実登録（時刻で自動切替: 夜間=強制充電 / 日中=グリーン）
$env:KP_WORKFLOW_MODE='settings'
$env:DRY_RUN='false'
python kpnet_main.py
```

ローカルで翌朝7時に自動実行する（Windows タスク スケジューラ）:

```powershell
# 1回だけ（明日7:00）
powershell -ExecutionPolicy Bypass -File .\scripts\register_7am_task.ps1

# 毎日7:00
powershell -ExecutionPolicy Bypass -File .\scripts\register_7am_task.ps1 -Daily
```

実行結果は `artifacts/<run_id>/` に保存されます。
- `csv/*.csv`
- `kpi_plot.png`
- `confirm_forced-charge.html`（`KP_SETTINGS_SEQUENCE=forced-only`）
- `confirm_forced-charge.html`, `confirm_green-mode.html`（`KP_SETTINGS_SEQUENCE=forced-then-green`）
- `kpnet_summary.json`

## 4. Cloud Run Jobs デプロイ例

推奨: 自動化スクリプトで 23:00 / 07:00 ジョブと Scheduler を一括登録

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy_gcp_jobs.ps1 `
  -ProjectId <PROJECT_ID> `
  -Region us-central1 `
  -SchedulerRegion us-central1 `
  -DataBackend postgres `
  -PgHost <COMPUTE_ENGINE_IP_OR_DNS> `
  -PgDatabase solar_ops `
  -PgUser solar_app `
  -RunSmokeTest
```

上記は以下を実施します:
- API有効化
- Artifact Registry 作成
- Docker build/push
- Secret Manager に監視ログイン情報登録
- 実行用 / Scheduler用の専用サービスアカウント作成
- Cloud Run Job 2本（23時用 / 7時用）デプロイ
- Cloud Scheduler 2本（`0 23 * * *`, `0 7 * * *` JST）作成/更新
- 東京リージョン（`asia-northeast1`）の既存Schedulerは `pause` して停止（削除しない）

```powershell
# 例: 環境
$PROJECT_ID="YOUR_PROJECT"
$REGION="asia-northeast1"
$JOB_NAME="solar-battery-controller"
$IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/solar-controller/runner:latest"

# Artifact Registry (未作成なら作成)
gcloud artifacts repositories create solar-controller `
  --repository-format=docker `
  --location=$REGION `
  --project=$PROJECT_ID

# Build & push
gcloud builds submit --tag $IMAGE --project=$PROJECT_ID

# Job 作成
gcloud run jobs create $JOB_NAME `
  --image=$IMAGE `
  --region=$REGION `
  --project=$PROJECT_ID `
  --task-timeout=1800 `
  --max-retries=1 `
  --env-vars-file=.env.prod
```

## 5. 07:00/23:00 実行の Scheduler 設定例

```powershell
$SCHEDULER_NAME="solar-battery-controller-7-23"
$SCHEDULER_REGION="asia-northeast1"
$PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format="value(projectNumber)")

gcloud scheduler jobs create http $SCHEDULER_NAME `
  --location=$SCHEDULER_REGION `
  --schedule="0 7,23 * * *" `
  --time-zone="Asia/Tokyo" `
  --uri="https://run.googleapis.com/v2/projects/$PROJECT_ID/locations/$REGION/jobs/$JOB_NAME:run" `
  --http-method=POST `
  --oauth-service-account-email="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"
```

## 6. 安全運用の注意

- Cloud Scheduler は at-least-once 実行です（重複実行の可能性あり）
- この実装は「現在値と同じなら更新しない」ことで冪等性を高めています
- 認証情報は本番では Secret Manager 連携を推奨します
- サービス利用規約に反しない範囲で利用してください

## 7. リリース前チェック

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\pre_release_check.ps1
```

実行内容:
- Pythonコードのコンパイル検証
- ユニットテスト（`pytest`）
- セキュリティ設定チェック（`scripts/security_check.py`）

## 8. DB保存方針（今回の運用）

- DB形式: `DATA_BACKEND=postgres`（本番推奨）または `sqlite`
- 23時ジョブのみ `db_pipeline_main.py` を実行し、以下をDBに反映
  - モニタリングCSVの30分データ
  - 日照（翌日予測、当日実績）
  - 23時設定結果 + 7時予定設定（23時時点の計画値）
- 毎回のCloud Storage追加は行わない（無効化）
- 週1回のみ、直近7日で更新された行を差分バックアップ（JSON）として保存

## 9. ダッシュボード（Web）

ローカル起動:

```powershell
python dashboard_server.py
```

`http://127.0.0.1:8080` で以下を確認できます:
- 日照予測/実績/差分
- 日次・月次の自家消費量と節約額
- 蓄電池設定値と実績
- 蓄電池方程式とパラメータ（分散・サンプル数）
- 初期表示は最新1か月。横スクロールで過去データを必要時のみ追加取得
- UIは日本語表示、スマホ幅ではグラフを縦並び表示
- 複数Y軸グラフは補助線の間隔を揃えて可読性を確保

認証URL（毎回の入力を省略）:

- `DASHBOARD_BASIC_USER` / `DASHBOARD_BASIC_PASSWORD` 設定時は Basic認証が有効になります。
- 次の形式で初回アクセスすると、認証情報をURLセーフBase64化した `auth` クエリからセッションCookieを発行し、`/` にリダイレクトします。
- `http://127.0.0.1:8080/?auth=<urlsafe_base64("user:password")>`
- Cookieは `HttpOnly` / `SameSite=Strict`（HTTPS時は `Secure`）です。

PNGモック生成:

```powershell
python dashboard_mock_png.py
```

出力先: `artifacts/dashboard_mock.png`

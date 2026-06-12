# Solar Controller Automation (Cloud Run Jobs)

04:30 / 07:00（JST）を主系として自動実行する Python 実装です。23:00ジョブは残していますが、現在のデプロイ既定ではSchedulerをpauseし、必要時の手動実行/プレビュー用途に寄せています。

1. ブラウザで 12 時間先の太陽日射時間を取得  
2. モニタリングサービスにログインして CSV を取得  
3. CSV と予報値から蓄電池設定を判定  
4. 蓄電池設定（充電上限/モード）を更新

実行基盤は **Cloud Run Jobs + Cloud Scheduler** を想定しています。

## 公開向け説明文と画面イメージ

- 説明文（日本語）: [docs/PUBLIC_DESCRIPTION_JA.md](docs/PUBLIC_DESCRIPTION_JA.md)
- Codexトークン節約運用ルール: [docs/codex_token_usage_rules.md](docs/codex_token_usage_rules.md)
- リモートCodex安全運用セットアップ: [docs/REMOTE_CODEX_SETUP_JA.md](docs/REMOTE_CODEX_SETUP_JA.md)
- Google Cloud運用前提: [docs/GCP_OPERATION_JA.md](docs/GCP_OPERATION_JA.md)
- Google Cloud無料運用（初心者向け手順）: [docs/GCP_FREE_BEGINNER_JA.md](docs/GCP_FREE_BEGINNER_JA.md)
- 運用条件ファイルガイド: [docs/OPERATION_CONDITIONS_GUIDE.md](docs/OPERATION_CONDITIONS_GUIDE.md)
- 現在の判定ルール（条件木）: [docs/CURRENT_DECISION_TREE_JA.md](docs/CURRENT_DECISION_TREE_JA.md)
- 東・南・西アレイ発電予測: [docs/PV_ARRAY_FORECAST_JA.md](docs/PV_ARRAY_FORECAST_JA.md)
- 消費電力量予測モデル仕様: [docs/CONSUMPTION_FORECAST_MODEL_JA.md](docs/CONSUMPTION_FORECAST_MODEL_JA.md)
- 不在予定入力シート仕様: [docs/OCCUPANCY_SCHEDULE_JA.md](docs/OCCUPANCY_SCHEDULE_JA.md)
- Google Cloud料金情報CLI: [docs/GCP_PRICING_CLI_JA.md](docs/GCP_PRICING_CLI_JA.md)
- Google Cloud実費確認CLI: [docs/GCP_ACTUAL_COST_CLI_JA.md](docs/GCP_ACTUAL_COST_CLI_JA.md)
- ダッシュボードPNG: [docs/images/dashboard.png](docs/images/dashboard.png)

## データ保存（段階運用）

- 現在: `artifacts/` に実行ごとの `summary.json` とCSVを保存
- 推奨本番: `DATA_BACKEND=firestore` で Firestore に永続化（無料枠運用しやすい）
- 代替本番: `DATA_BACKEND=postgres` で Compute Engine 上 PostgreSQL に永続化

注意: Cloud Run のコンテナファイルシステムはインメモリで、インスタンス停止時に永続化されません。  
クラウド本番で履歴を残す場合は Firestore / PostgreSQL などのDB連携を使ってください。  

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
3. 夜間グリーンモードプロファイル確認/登録（既定）
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
- `KP_SETTINGS_SEQUENCE=forced-only` : 夜間グリーンモードのみ（既定）
- `KP_SETTINGS_SEQUENCE=forced-then-green` : 夜間グリーンモード適用後にグリーンモードも適用
- `KP_DYNAMIC_FORCED_PROFILE=true` : `artifacts/night_charge_plan.json` から
  - 必要充電量(kWh)
  - 7時目標SOC
  - 夜間実測充電レート(kW推定)
  を使って、夜間グリーンモードの `SOC下限` / `SOC上限` / `充電開始時刻` を自動算出
  - 23時設定時は `充電終了` を運用条件から決定し、逆算で開始時刻を決定（0:00を跨がない）
  - 既定条件: 曇り/雨相当（低日照予報）の日は `充電終了=07:00`
- `KP_DYNAMIC_MODE_SWITCH_BY_TIME=true` : 現在時刻で設定先を自動選択
  - 夜間(23:00-07:00): グリーンモード + SOC下限(安心)=最大値
  - 日中(放電開始は予報連動): 晴れ予報=06:00開始 / 曇り予報=07:00開始（終了は23:00）
  - この設定が `true` のときは `KP_SETTINGS_SEQUENCE` より時刻判定を優先
- 04:30夜間コントローラ（`CLOUD_JOB_SLOT=03`）:
  - CSVを1回取得
  - 23時計画と同じ対象日のまま、必要時だけ3時台に予報を再確認
  - 7時から逆算した時刻に強制充電を開始
  - 3時台の再計算で内容が変わった場合だけDB/ダッシュボードを更新
- `KP_OPERATION_CONDITIONS_PATH`:
  - 固定条件 / 変動条件 / 優先順位 を外部JSONで管理
  - 既定: `config/operation_conditions.json`
  - 固定条件「0時跨ぎ禁止」「開始=終了禁止」を最優先で強制

主な環境変数（`.env`）:

- `KP_MONITOR_USERNAME`, `KP_MONITOR_PASSWORD`
- `KP_ENFORCE_HTTPS=true` : `KP_BASE_URL` を https に制限
- `KP_ALLOWED_HOSTS=ctrl.kp-net.com` : 接続先ホストを許可リスト化
- `KP_WORKFLOW_MODE=csv|settings|all`
- `KP_SETTINGS_SEQUENCE=forced-only|forced-then-green`
- `KP_FORCE_SETTINGS_PROFILE=auto|forced|green`
- `KP_DYNAMIC_FORCED_PROFILE=true|false`
- `KP_DYNAMIC_MODE_SWITCH_BY_TIME=true|false`
- `KP_OPERATION_CONDITIONS_PATH=config/operation_conditions.json`
- `ADJUST03_REFRESH_ENABLED=true`
- `ADJUST03_REFRESH_HHMM=03:10`
- `ADJUST03_SUN_EPSILON_H=0.05`
- `ADJUST03_TEMP_EPSILON_C=0.2`
- `ADJUST03_SOC_EPSILON_PERCENT=1.0`
- `ADJUST03_KWH_EPSILON=0.2`
- `KP_NIGHT_PLAN_PATH=artifacts/night_charge_plan.json`
- `KP_NIGHT_CHARGE_WINDOW_START=23:00`
- `KP_NIGHT_CHARGE_WINDOW_END=07:00`
- `KP_DAY_DISCHARGE_WINDOW_START=07:00`
- `KP_DAY_DISCHARGE_WINDOW_END=23:00`
- `PV_ARRAY_FORECAST_ENABLED=true`（`config/pv_arrays.json` の東・南・西などの面別アレイをOpen-Meteo hourly GTIで予測）
- `PV_ARRAY_CONFIG_PATH=config/pv_arrays.json`
- `PV_ARRAY_CALIBRATION_LOOKBACK_DAYS=45`（実測発電量でperformance_ratioを補正する履歴日数）
- `NIGHT_RESERVE_SOC_PERCENT=0`（翌朝SOC目標の予備残量）
- `KP_DEFAULT_CHARGE_POWER_KW=4.0`（夜間実測が取れない場合のフォールバック。実測の強制充電中央値に合わせる）
- `ADJUST03_FORCE_CHARGE_RATE_FALLBACK_PERCENT_PER_HOUR=40`（4:30制御でSOC実測レートが取れない場合のフォールバック）
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
- `DATA_BACKEND=sqlite|postgres|firestore`
- `DATA_DB_SYNC_ENABLED=false`（既定。逐次Cloud Storage同期は無効化）
- `DATA_DB_WRITE_ONLY_23=false`（23時Scheduler pause運用のため、04:30/07:00側でもDB永続化を許可）
- `DATA_WEEKLY_BACKUP_ENABLED=true`（週1回だけ差分バックアップ）
- `DATA_WEEKLY_BACKUP_WEEKDAY=5`（土曜）
- `DATA_WEEKLY_BACKUP_DIR=artifacts/backups/weekly`
- `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`（`DATA_BACKEND=postgres` 時）
- `FIRESTORE_PROJECT_ID`, `FIRESTORE_DATABASE_ID`（`DATA_BACKEND=firestore` 時）
- `DRY_RUN=true` の間は設定登録は行わず、確認画面到達までを検証

翌日予測から自動で設定登録する手順（ローカル実行）:

```powershell
# 1) 最新CSV取得
$env:KP_WORKFLOW_MODE='csv'
python kpnet_main.py

# 2) 翌日予測と実績から必要夜間充電量を算出
python energy_model_main.py

# 3) 設定実登録（時刻で自動切替: 夜間=グリーン / 日中=グリーン）
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
- `confirm_night-green.html`（`KP_SETTINGS_SEQUENCE=forced-only`）
- `confirm_night-green.html`, `confirm_green-mode.html`（`KP_SETTINGS_SEQUENCE=forced-then-green`）
- `kpnet_summary.json`

## 4. Cloud Run Jobs デプロイ例

推奨: 自動化スクリプトで 23:00 / 04:30夜間コントローラ / 07:00 ジョブと Scheduler を一括登録します。既定では23:00 Schedulerを作成/更新後にpauseし、04:30夜間コントローラを主系にします。

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy_gcp_jobs.ps1 `
  -ProjectId <PROJECT_ID> `
  -Region us-central1 `
  -SchedulerRegion us-central1 `
  -DataBackend firestore `
  -RunSmokeTest
```

上記は以下を実施します:
- API有効化
- Artifact Registry 作成
- Docker build/push（Cloud Run Jobs runner は `requirements-runner.txt` を使い、未使用のPlaywright/Chromiumは含めない）
- Secret Manager に監視ログイン情報登録
- 実行用 / Scheduler用の専用サービスアカウント作成
- Cloud Run Job 3本（23時用 / 04:30夜間コントローラ用 / 7時用）デプロイ
- Cloud Scheduler 3本（`0 23 * * *`, `30 4 * * *`, `0 7 * * *` JST）作成/更新
- `solar-battery-run-23` は既定でpause（23:00を有効化したい場合は `-Enable23Scheduler`）
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

## 5. 07:00/04:30/23:00 実行の Scheduler 設定例

```powershell
$SCHEDULER_REGION="asia-northeast1"
$PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format="value(projectNumber)")

gcloud scheduler jobs create http "solar-battery-run-23" `
  --location=$SCHEDULER_REGION `
  --schedule="0 23 * * *" `
  --time-zone="Asia/Tokyo" `
  --uri="https://run.googleapis.com/v2/projects/$PROJECT_ID/locations/$REGION/jobs/solar-battery-23:run" `
  --http-method=POST `
  --oauth-service-account-email="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"

gcloud scheduler jobs create http "solar-battery-run-03" `
  --location=$SCHEDULER_REGION `
  --schedule="30 4 * * *" `
  --time-zone="Asia/Tokyo" `
  --uri="https://run.googleapis.com/v2/projects/$PROJECT_ID/locations/$REGION/jobs/solar-battery-03:run" `
  --http-method=POST `
  --oauth-service-account-email="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"

gcloud scheduler jobs create http "solar-battery-run-07" `
  --location=$SCHEDULER_REGION `
  --schedule="0 7 * * *" `
  --time-zone="Asia/Tokyo" `
  --uri="https://run.googleapis.com/v2/projects/$PROJECT_ID/locations/$REGION/jobs/solar-battery-07:run" `
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

- DB形式: `DATA_BACKEND=firestore`（推奨）または `sqlite` / `postgres`
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
- 蓄電池設定値と実績（kWh軸とSOC(%)軸を分離）
- 蓄電池方程式とパラメータ（分散・サンプル数）
- 表示期間は `1ヶ月(日)` / `年(12ヶ月)` / `全て(日)` ボタンで切替。`前` / `後` で集計月または集計年を移動
- 集計月は既定で前月15日〜当月14日を当月分として扱います。締め日は `DASHBOARD_AGGREGATION_CLOSE_DAY` で変更できます
- 最新計画の1時間ごとの予想発電量・予想充電量・予想消費電量
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

# Google Cloud無料運用ガイド（初心者向け）

このガイドは、**できるだけ無料枠内で運用**するための実行手順です。  
対象日は **2026-05-02 時点** の公式情報です（無料枠は将来変更される可能性があります）。

## 0. 先に結論（無料を外しにくい設定）

- リージョンは **US系** を使う（推奨: `us-central1`）
- Cloud Scheduler は **3ジョブまで**（23時/3:10/7時）
- Artifact Registry の世代保持を絞る（`keepCount=2`）
- DBは `firestore` を使う（Cloud SQLは使わない）
- 課金アラート（予算）を必ず設定

## 1. 事前準備

- Google Cloud CLI（`gcloud`）にログイン済み
- このリポジトリをローカルに配置済み
- `.env` に監視サイトの認証情報を設定済み

## 2. 無料枠向けの基本設定（コピペ可）

PowerShellで実行:

```powershell
cd C:\VSC\SolerControler
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\gcloud.ps1 auth login
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\gcloud.ps1 config set project <PROJECT_ID>
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\gcloud.ps1 config set run/region us-central1
```

必要APIを有効化:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\gcloud.ps1 services enable `
  run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com `
  cloudscheduler.googleapis.com secretmanager.googleapis.com firestore.googleapis.com
```

Firestore（無料枠対象の標準DB）をUSで作成:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\gcloud.ps1 firestore databases create --database="(default)" --location=us-central1 --type=firestore-native
```

## 3. ジョブを無料寄り設定でデプロイ

`firestore` バックエンド（推奨）でデプロイ:

```powershell
pwsh -NoProfile -File .\scripts\check_production_env.ps1 -CheckCloud
pwsh -NoProfile -File .\scripts\deploy_production_from_env.ps1
```

これで以下の3つのCloud Schedulerジョブが作成されます。

- `solar-battery-run-23`（23:00 JST）
- `solar-battery-run-03`（04:00 JST）
- `solar-battery-run-07`（07:00 JST）

## 4. ダッシュボード（任意）

ダッシュボードも `deploy_production_from_env.ps1` が `.env` のproject・region・image設定からビルドして更新します。

## 5. 無料枠チェック手順（毎月）

### 5-1. Schedulerジョブ数（重要）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\gcloud.ps1 scheduler jobs list --location us-central1
```

- 目安: **3ジョブ以内**で維持
- 注意: **Pausedでもジョブ数として課金対象カウント**されます

### 5-2. Artifact Registry容量

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\gcloud.ps1 artifacts repositories list --project <PROJECT_ID>
```

cleanup policy（既存ファイル）を適用:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\gcloud.ps1 artifacts repositories set-cleanup-policies solar-runner --location us-central1 --project <PROJECT_ID> --policy .\scripts\artifact_cleanup_policy.json
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\gcloud.ps1 artifacts repositories set-cleanup-policies solar-dashboard --location us-central1 --project <PROJECT_ID> --policy .\scripts\artifact_cleanup_policy.json
```

## 6. 課金ガード（必須）

- Cloud Billing の **Budgets & alerts** で、まず `100円` と `500円` の2段階アラートを作成
- 予算アラートは通知のみです。必要なら自動停止ワークフローを追加

参考:
- 通知で課金停止の自動化: `disable billing with notifications` 公式ドキュメント

## 7. この構成で無料に効く理由

- Cloud Runの無料枠は請求先アカウント単位で毎月リセット
- Cloud Schedulerは請求先アカウントごとに毎月3ジョブ無料（本構成は3ジョブ）
- Artifact Registryは 0.5GB まで無料（超えた分のみ課金）
- Cloud Storage無料枠は USリージョン（`us-east1/us-west1/us-central1`）で適用

## 8. 公式情報（確認元）

- Google Cloud無料枠一覧（Cloud Run / Cloud Build / Artifact Registry / Compute Engine / Cloud Storage）  
  https://docs.cloud.google.com/free/docs/free-cloud-features?hl=ja
- Cloud Scheduler料金（3ジョブ無料、pausedもカウント）  
  https://cloud.google.com/scheduler/pricing
- Artifact Registry料金（0.5GBまで無料）  
  https://cloud.google.com/artifact-registry/pricing
- Cloud Run料金  
  https://cloud.google.com/run/pricing
- Cloud Billing予算アラート  
  https://cloud.google.com/billing/docs/how-to/budgets
- 課金停止の自動化（通知連動）  
  https://cloud.google.com/billing/docs/how-to/disable-billing-with-notifications

# Google Cloud運用前提の説明（日本語）

本プロジェクトは、Google Cloud上で次の構成で運用することを想定しています。

## 運用構成

- **Cloud Run Jobs**
  - `solar-battery-23`（毎日 23:00 JST）
  - `solar-battery-03`（毎日 04:00 JST、夜間充電コントローラ）
  - `solar-battery-07`（毎日 07:00 JST）
- **Cloud Scheduler**
  - 23時ジョブ、04:00ジョブ、7時ジョブを定期起動
- **Artifact Registry**
  - コンテナイメージ管理
- **Cloud Run Service（Dashboard）**
  - ダッシュボード表示用Web

## 処理フロー

1. 23:00 ジョブで外部取得を行わず、04:00判断まで蓄電池を待機モードへ変更
2. 04:00 ジョブで予報・CSV取得、当日計画・DB反映・Sheets/Drive退避を行い、必要時に強制充電へ切替
3. 07:00 ジョブで日中運用設定へ切替（グリーンモード等）
4. 日次データはDBへ蓄積し、ダッシュボードで可視化

## 無料運用しやすい方針

- リージョンは US（`us-central1`）を基本
- 不要なジョブ/サービスは停止し、古いリージョンは「削除せず停止」
- Artifact Registry は cleanup policy で世代を絞る
- DBは `Firestore` を推奨（`(default)` DB を US に作成し `DATA_BACKEND=firestore` で運用）

## セキュリティ方針

- 認証情報は **Secret Manager** で管理
- `.env` や実データCSVはGitへコミットしない
- 接続先ホストを `KP_ALLOWED_HOSTS` で制限
- `KP_ENFORCE_HTTPS=true` でhttps強制

## リリース/更新

- 本番更新: `scripts/deploy_production_from_env.ps1` が `.env` を検証し、Dashboard、Job、Scheduler、smoke、データ取込、Driveバックアップを一括更新
- 個別値をコマンドへ直接記述せず、事前に `scripts/check_production_env.ps1 -CheckCloud` を実行
  - 更新のたびに `scripts/check_gcp_free_tier_capacity.ps1` を自動実行
  - 更新のたびに `scripts/prune_artifact_registry.ps1` で旧digestを自動削除
  - `-FailOnCapacityOverage` を付けると無料枠超過時にデプロイを停止
- 変更後はCloud Run revisionと実画面を確認

## 監視ポイント

- Cloud Run Job 実行ログ（成功/失敗）
- Scheduler 実行履歴
- Dashboard のAPI応答と描画
- Artifact Registry 容量（不要イメージ世代の削減）

### 容量チェック手動実行

```powershell
. .\scripts\production_env.ps1
Import-ProductionEnv
pwsh -NoProfile -File .\scripts\check_gcp_free_tier_capacity.ps1 `
  -ProjectId $env:GCP_PROJECT_ID `
  -MaxArtifactRegistryMB 500 `
  -FailOnOverage
```

## 初心者向けの無料運用手順

- [docs/current/ops/GCP_FREE_BEGINNER_JA.md](GCP_FREE_BEGINNER_JA.md)
- [docs/current/product/OPERATION_CONDITIONS_GUIDE.md](../product/OPERATION_CONDITIONS_GUIDE.md)

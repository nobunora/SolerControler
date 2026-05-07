# Google Cloud運用前提の説明（日本語）

本プロジェクトは、Google Cloud上で次の構成で運用することを想定しています。

## 運用構成

- **Cloud Run Jobs**
  - `solar-battery-23`（毎日 23:00 JST）
  - `solar-battery-03`（毎日 03:10 JST、予報微調整）
  - `solar-battery-07`（毎日 07:00 JST）
- **Cloud Scheduler**
  - 23時ジョブ、3:10ジョブ、7時ジョブを定期起動
- **Artifact Registry**
  - コンテナイメージ管理
- **Cloud Run Service（Dashboard）**
  - ダッシュボード表示用Web

## 処理フロー

1. 23:00 ジョブで翌日予報・CSV取得・夜間充電計画・設定反映
2. 03:10 ジョブで予報再取得（10分間隔×最大3回）し、必要時に夜間設定を微調整
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

- ダッシュボード更新: `cloudbuild.dashboard.yaml` でビルド後、Cloud Run Service更新
- バッチ更新: `scripts/deploy_gcp_jobs.ps1` でJobとSchedulerを更新
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
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check_gcp_free_tier_capacity.ps1 `
  -ProjectId project-a36d57fc-79a5-459a-827 `
  -MaxArtifactRegistryMB 500 `
  -FailOnOverage
```

## 初心者向けの無料運用手順

- [docs/GCP_FREE_BEGINNER_JA.md](GCP_FREE_BEGINNER_JA.md)
- [docs/OPERATION_CONDITIONS_GUIDE.md](OPERATION_CONDITIONS_GUIDE.md)

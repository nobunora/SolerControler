# CLIでGoogle Cloudの実費を確認する

このプロジェクトには `scripts/get_gcp_actual_cost.ps1` を追加済みです。  
Cloud Billing API (`v1beta:generateInsights`) を使って、月初から当日までの実費をCLIで確認できます。

注意:
- 請求上のクレジット適用が大きいと、`subtotal` がマイナス（実質クレジット超過）になることがあります。
- Cloud Billing の集計はリアルタイムではないため、反映に遅延が出る場合があります。

## 前提

- `gcloud` がインストール済み
- `gcloud auth login` 済み
- `gcloud config set project <PROJECT_ID>` 済み
- 対象の Cloud Billing アカウントに閲覧権限がある

## 実行例

```powershell
pwsh -File .\scripts\get_gcp_actual_cost.ps1
```

- `-ProjectId` を省略した場合は `gcloud config` の現在プロジェクトを利用します
- `-BillingAccountId` を省略した場合は、プロジェクトに紐づく課金アカウントを自動解決します

### JSONで取得

```powershell
pwsh -File .\scripts\get_gcp_actual_cost.ps1 -AsJson
```

### 上位サービス件数を変更

```powershell
pwsh -File .\scripts\get_gcp_actual_cost.ps1 -TopServices 20
```

### Billing Accountの設定元

```powershell
GCP_BILLING_ACCOUNT_ID=<billing-account-id>
```

実値はGit管理外の `.env` に保存します。通常は引数指定せず、スクリプトが
`GCP_PROJECT_ID` と `GCP_BILLING_ACCOUNT_ID` を読み込みます。

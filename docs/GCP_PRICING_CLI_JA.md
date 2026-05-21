# CLIでGoogle Cloudの料金情報を取得する

このプロジェクトには `scripts/get_gcp_pricing.ps1` を追加済みです。  
`gcloud` のログイン済みアカウントを使って Cloud Billing Catalog API を呼び出し、サービス別の SKU 料金を取得できます。

## 前提

- `gcloud` がインストール済み
- `gcloud auth login` 済み
- `gcloud config set project <PROJECT_ID>` 済み（未設定でも動く場合がありますが、設定推奨）

## 使い方

### 1) サービスを検索する

```powershell
pwsh -File .\scripts\get_gcp_pricing.ps1 -ListServices -Service "Compute"
```

### 2) 料金SKUを取得する（先頭20件）

```powershell
pwsh -File .\scripts\get_gcp_pricing.ps1 -Service "Compute Engine" -Top 20
```

### 3) リージョンで絞る

```powershell
pwsh -File .\scripts\get_gcp_pricing.ps1 -Service "Compute Engine" -Region "asia-northeast1" -Top 20
```

### 4) 通貨をJPYで取得

```powershell
pwsh -File .\scripts\get_gcp_pricing.ps1 -Service "Cloud Storage" -CurrencyCode "JPY" -Top 20
```

### 5) JSONで取得

```powershell
pwsh -File .\scripts\get_gcp_pricing.ps1 -Service "BigQuery" -Top 10 -AsJson
```

## 主なオプション

- `-Service`: サービス表示名の部分一致（例: `Compute Engine`）
- `-ServiceId`: サービスIDを直接指定（例: `6F81-5844-456A`）
- `-Region`: リージョン絞り込み（例: `asia-northeast1`）
- `-CurrencyCode`: 通貨コード（例: `USD`, `JPY`）
- `-Top`: 取得件数（`-All` 未指定時）
- `-All`: 全ページ取得
- `-AsJson`: JSON出力
- `-ListServices`: サービス一覧のみ表示

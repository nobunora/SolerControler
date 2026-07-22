# 本番デプロイ再現手順

この手順は、Windows / PowerShell 7 環境から本番を安全に検証・デプロイし、途中終了した場合も成功済み工程を再利用して再開するための標準手順です。

## 適用範囲

- Cloud Run Job（23時、03時、07時）のビルド・更新
- Cloud Scheduler の確認
- 本番設定の検証
- デプロイ後のDryRun

認証情報、project、region、resource IDはGit管理外の `.env` と `scripts/production_env.ps1` から取得します。値をコマンド、tracked file、報告、チャットへ転記しません。

## 1. 事前確認

```powershell
git status --short
python -m pytest
python scripts/security_check.py
git check-ignore .env
pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 -ValidateOnly
```

次の全条件を満たすまでデプロイしません。

- 関連テストと全体回帰テストが成功している
- `security_check.py` が成功している
- `.env` がignore対象で、stageされていない
- `-ValidateOnly` が必須設定を検証し、「No deployment was performed」と表示する

## 2. 通常デプロイ

最初は必ず高レベルの公式ラッパーを使用します。

```powershell
pwsh -NoProfile -File scripts/deploy_production_from_env.ps1
```

低レベルスクリプトや独自の `gcloud` 更新コマンドへ置き換えません。

## 3. Windowsで途中終了した場合の再開

成功済み工程をログで確認してから、同じ公式ラッパーを再開します。確認できない工程は成功扱いにしません。

### 3.1 pre-release後に終了した場合

テスト、Firestore/SQLite同期、dashboard parityが成功済みの場合だけ `-SkipPreRelease` を使用します。

```powershell
pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 -SkipPreRelease
```

### 3.2 Cloud Build開始後に終了した場合

Cloud Buildが `SUCCESS` であることを読み取り専用で確認します。成功確認後だけ、ビルド済みの `runner:latest` を再利用します。

```powershell
pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 `
  -SkipPreRelease -SkipJobBuild -SkipDashboardBuild -SkipKpNetImport -SkipDriveBackup
```

`WORKING`、`QUEUED`、失敗、または状態不明のビルドを再利用しません。

### 3.3 ジョブ1本の更新後に終了した場合

更新成功が出力されたジョブだけをスキップし、残りをジョブ単位で再開します。

03時ジョブだけを更新する例:

```powershell
pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 `
  -SkipPreRelease -SkipJobBuild -SkipJob23Deploy -SkipJob07Deploy `
  -SkipDashboardBuild -SkipKpNetImport -SkipDriveBackup
```

07時ジョブだけを更新する例:

```powershell
pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 `
  -SkipPreRelease -SkipJobBuild -SkipJob23Deploy -SkipJob03Deploy `
  -SkipDashboardBuild -SkipKpNetImport -SkipDriveBackup
```

23時ジョブだけを更新する場合は `-SkipJob03Deploy -SkipJob07Deploy` を指定します。

### 3.4 ジョブ更新済みでScheduler工程だけ再開する場合

```powershell
pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 `
  -SkipPreRelease -SkipJobBuild -SkipJobDeploy `
  -SkipDashboardBuild -SkipKpNetImport -SkipDriveBackup
```

## 4. 本番反映の合格条件

次のすべてを確認します。

- 23時、03時、07時の3ジョブが意図した最新イメージへ更新されている
- 変更した非機密環境変数が対象ジョブへ反映されている
- SchedulerがAsia/Tokyoで有効
- Scheduler時刻が23時=`0 23 * * *`、03時=`0 3 * * *`、07時=`0 7 * * *`
- Secret値を出力していない
- DryRunが完了している

DryRunは公式ラッパーで実行します。

```powershell
pwsh -NoProfile -File scripts/run_cloud_job_from_env.ps1 -Slot 07 -DryRun
```

非同期実行の受付だけで成功扱いにせず、最新executionの `Completed=True`、`ResourcesAvailable=True`、`Started=True`、`ContainerReady=True` を確認します。読み取り専用の診断で識別子を取得しても、報告やチャットには記載しません。

## 5. commit / push前

デプロイ中にラッパーを修正した場合は、関連テストだけでなく全体回帰を再実行します。

```powershell
python -m pytest
python scripts/security_check.py
git diff --check
git check-ignore .env
git status --short
```

本番反映内容とコミット内容が一致していることを確認してから、対象ファイルだけをstage、commit、pushします。push後はローカルHEADとremote追跡HEADの一致、およびcleanな作業ツリーを確認します。

## 禁止事項

- ValidateOnlyやテストを実施せずにデプロイする
- 成功ログのない工程を推測でスキップする
- credential-bearing commandを手組みする
- `.env`、Secret値、project番号、resource IDをtracked fileや報告へ記載する
- DryRunの受付だけで本番検証成功と判断する
- 失敗したビルドや状態不明のイメージをジョブへ反映する

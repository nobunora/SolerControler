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
pwsh -NoProfile -File scripts/production_deployment_gate.ps1 -RunPreRelease
```

次の全条件を満たすまでデプロイしません。

- 作業ツリーがcleanである
- ローカルsource/DBバックアップが成功している（`-SkipLocalBackup`は禁止。バックアップは`.env`を含めない）
- `security_check.py`、関連テスト、全体回帰テストが成功している
- `.env` がignore対象で、stageされていない
- `-ValidateOnly` が必須設定を検証し、「No deployment was performed」と表示する

ゲートは `artifacts/deployment_state/preflight-*.json` に非機密の判定結果を保存します。ゲートが失敗した場合は本番ラッパーを起動しません。

## 2. 通常デプロイ

最初は必ず高レベルの公式ラッパーを使用します。

```powershell
pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 `
  -SkipPreRelease `
  -RunSettingsRoundTripTest `
  -SettingsRoundTripTargetSoc <今回の03時目標SOC> `
  -StatePath artifacts/deployment_state/production-<開始時刻>.json
```

低レベルスクリプトや独自の `gcloud` 更新コマンドへ置き換えません。
デプロイラッパーは工程ごとに状態を書き込みます。`running` のまま終了した工程は成功扱いにしません。

夜間SOCの変更を含むデプロイでは、`-RunSettingsRoundTripTest` を必須とします。この工程は、Schedulerを持たない専用Cloud Run Jobで実行します。(1) 実機の`SocChargeMode`候補が今回の目標SOC以上であること、(2) 待機への可逆な設定変更と読戻し、(3) **60秒後**にデプロイ直前のcontrolled settings全項目へ復元して読戻すこと、を確認します。明示的な`-TestExecution`を伴う専用ジョブのため時間帯を問いません。候補不足、変更失敗、または復元不一致では、復元後に工程を失敗として停止します。

### 2.1 短縮経路（検証を維持する場合）

通常経路ではジョブ更新中にも07時ジョブのDryRunを実行します。デプロイ後の公式DryRunを別途実施する場合は、次のオプションで重複実行を避けられます。

```powershell
pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 `
  -SkipInlineSmokeTest `
  -StatePath artifacts/deployment_state/production-<開始時刻>.json
pwsh -NoProfile -File scripts/run_cloud_job_from_env.ps1 -Slot 07 -DryRun
```

`-SkipInlineSmokeTest` は検証を省略する指定ではなく、デプロイ工程内の重複待機を省略する指定です。合格判定には、必ず後段の公式DryRunとCloud Run executionの4条件確認を使用します。

runner／dashboardのCloud Buildは直前のArtifact Registryイメージをキャッシュ候補として再利用し、用途別のignoreファイルで不要なテスト・文書・生成物を送信しません。依存関係を変更した場合はキャッシュ効果が下がるため、通常より長くなることがあります。

### 2.2 失敗・手戻りを減らす実行規約

- 本番へ送る内容は、ゲート実行前に一つのcommitへ固定する。未commit差分のままビルドしない。
- 同一commitでrunnerを複数回ビルドしない。公式ラッパーのキャッシュ利用ビルドを一度だけ行い、外側の待機が切れた場合はCloud Buildの終端状態を確認してから、同じ状態ファイルで再開する。
- `-SkipInlineSmokeTest` は、直後に公式07時DryRunを実行する場合だけ使う。設定往復テストと07時DryRunの双方のCloud Run終端条件を確認するまで合格にしない。
- 設定往復テストは専用Jobだけから明示実行する。Schedulerを追加せず、Cloud Runの再試行は0回とし、失敗時に同じ設定変更を自動で重ねない。
- `failed`または`running`の工程を手動で成功へ書き換えない。実機設定が復元済みであることをログから確認した後にのみ、同じ状態ファイルの`-Resume`で未成功工程を再実行する。

## 3. Windowsで途中終了した場合の再開

`artifacts/deployment_state/production-*.json` の `status=success` を確認してから、同じ公式ラッパーを再開します。`failed`、`running`、`skipped_manual` は成功扱いにしません。

同じcommitの状態ファイルを明示して再開します。`-Resume` はJSON内で明示的に `success` となった工程だけをスキップし、Cloud側の現在状態から成功を推測しません。

```powershell
pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 `
  -Resume `
  -StatePath artifacts/deployment_state/production-<開始時刻>.json
```

### 3.1 pre-release後に終了した場合

状態ファイルで `pre_release.status=success` が確認できる場合だけ `-Resume` または `-SkipPreRelease` を使用します。

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

外側のPowerShellや実行環境の待機が先に終了しても、Cloud Build／Cloud Runの状態を読み取り専用で確認するまで失敗・成功を推測しません。再開時は同じcommitの状態ファイルを使い、`success` が記録された工程だけをスキップします。各工程の失敗時には、機密語を除去したエラー概要を状態ファイルへ保存します。

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
- Cloud Run smokeの最新executionで `Completed=True`、`ResourcesAvailable=True`、`Started=True`、`ContainerReady=True` を確認している

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
- 状態ファイルの `running` または `failed` を手動で `success` に書き換える

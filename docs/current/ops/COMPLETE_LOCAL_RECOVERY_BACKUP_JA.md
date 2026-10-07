# ローカル完全バックアップと復旧の境界

## 取得

Driveの定時バックアップは履歴データの補助であり、サイト全体の復旧セットではない。運用設定の従来バックアップも秘密値を削除するため単独では復旧できない。全期間のFirestore原本、参照先GCSオブジェクト、実行イメージ、設定・秘密情報をローカルに退避するには次を使う。

```powershell
pwsh -NoProfile -File scripts/backup_operational_state_from_env.ps1 -Complete
```

保存先はGitで無視される `artifacts/backups/operational/complete-<UTC timestamp>/`。現WindowsユーザーとSYSTEMだけにアクセスを制限する。ユーザーの明示指示により、`.env`、Secret Managerの有効なバージョンの値、クラウド設定は復旧作業でそのまま使える平文で保存する。秘密情報・原本をPRや公開レポートへ貼らない。

途中終了は同じディレクトリで再開する。

```powershell
pwsh -NoProfile -File scripts/backup_operational_state_from_env.ps1 -Complete -ResumePath <保存先>
```

成功済みステージのハッシュを照合してから再利用する。未完了ステージを成功と解釈しない。ソースのGit bundle・作業差分、ローカルSQLite、全期間のFirestore型付きREST文書、索引・フィールド設定、GCSの読取可能な全世代、OCIイメージ全manifest/config/layer、Cloud Run/Scheduler/IAM/API/rules設定、秘密値、Driveのバックアップファイル、Cloud Loggingの保持中の全ログを対象にする。

日時によるデータの切り捨てはしない。Firestoreは一つのreadTimeで取得し、存在しない親文書の下のサブコレクションも走査する。全サービスが同一時点の原子的スナップショットになるわけではない。取得時間帯はmanifestに残す。

`summary.safe.json` と `manifest.private.json` の `status=complete`、`gaps=[]` が完全取得の条件。権限不足・API制約・読取不能なsoft delete/無効な秘密値バージョン・未取得のページがあれば完全と報告しない。部分取得のステージは `partial` とし、再開時に不足を再確認する。稼働設定が参照するダイジェストも取得対象とし、レジストリから消えたイメージは不足として記録する。既に完全削除されてリモートにもバックアップにもない過去データは回復できない。

## オフライン検証

クラウド認証や本番接続なしでハッシュ、型付きFirestore文書のローカル復元、平文秘密値の読取を検証できる。

```powershell
python scripts/backup_complete_remote.py --output <保存先> --verify-only
```

この検証は取得した原本の完全性とローカル読取を確認する。クラウドへの復元・本番ジョブ実行はしない。

## 稼働中の旧イメージがレジストリにない場合

通常の取得は読み取りだけを行う。動作に必要な旧イメージの保存をユーザーが明示承認した場合に限り、次のオプションでCloud Runの保持中Executionから、同じプロジェクトの既存レジストリへエクスポートしてローカルに取得する。

```powershell
pwsh -NoProfile -File scripts/backup_operational_state_from_env.ps1 -Complete -ResumePath <保存先> -RecoverRequiredImages
```

このオプションはリモートに復旧用イメージを作成する。ジョブ実行・設定変更・デプロイ・IAM変更は行わない。元イメージとエクスポート後のダイジェストが異なる場合があるため、`registry/cloud_run_exports/*.json` の `source` と `recovery_reference` を復旧時に対応させる。受付結果が不明なエクスポート要求は自動で再送しない。権限不足や保持済みExecutionがない場合は未完了として記録する。

## 復旧順序

1. 復元先と管理権限を確定し、スケジュールされた制御を停止した環境を用意する。
2. `git bundle` を復元し、必要な作業差分を適用する。`local/.env`、`secrets/secret_versions.private.json`、各バージョンの `recovery_file` に保存された平文の秘密値を使う。復号は不要。秘密値を画面やログへ出さない。
3. 保存済みのAPI/IAM/サービスアカウント/バケット/Secret Manager/Firestore設定を再作成する。現在の資源を無条件で上書きしない。
4. GCSのオブジェクト名と原本バイトを復元し、MD5/SHAと計画原本SHAを照合する。Firestore文書は `fields` の型付きREST値を使って元の文書パスへ復元する。通常のSQLite同期は完全復元の代用にしない。サーバーが付与するcreateTime/updateTimeやGCS世代番号は新規復元で変わる。
5. 必要なOCIイメージを元のダイジェストで再登録するか、保存ソースから標準デプロイ手順で再ビルドする。変更履歴と使用するイメージを照合する。
6. `PRODUCTION_DEPLOYMENT_RUNBOOK_JA.md` のゲート・復旧確認を実施する。設定・計画・表示を照合し、制御ジョブの適用確認を終えてからSchedulerを再開する。

別プロジェクトへ復旧する場合は、IAM主体、Firestore referenceValue、GCS参照、サービスURI等の対応表が必要。平文なので元のWindowsユーザーの鍵には依存しない。別PCへコピーするときも保存先のアクセス権を限定する。

自動のクラウド復元は実装・実行しない。本番への復元はユーザーの明示承認と上記の運用ゲートを必要とする。バックアップ取得だけで実クラウド復旧の成功を証明したと扱わない。

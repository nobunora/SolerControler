# 詳細計画の保存・バックアップ・復元

## 契約

03時の計画生成後、`cloud_job._archive_generated_plan`が独立した子プロセスで詳細JSONを保存する。最大20秒、06:45まで60秒未満なら開始しない。生成成功・アーカイブ成功・実機設定成功は別の事実として記録する。保存失敗時は`plan-archive`ログにfailedを出し制御を継続する。失敗したJSONはコンテナ終了で失われ得るため、failed/skipped_time_budgetを監視対象にする。

23/07の操作、03の時刻境界、古い計画の再利用禁止を維持する。lease、07 gate、03末尾のDrive/Sheets処理は復活させない。2026-10-03に承認された保存復旧として保護文書に境界を追記した。

## 保存形式

設定済みNIGHT_PLAN_ARCHIVE_GCS_PREFIX（または既存の日次prefix）配下の`decisions/<date>/<sha256>.json.gz`に、JSON原本のバイト列を保存する。作成時に世代一致条件0を付けて上書きを禁止し、同一内容の再試行は読戻し一致を確認して再利用する。

Firestoreの`night_plan_decisions/<date>--<sha256>`が各版の索引。`night_charge_plans/<date>`と`latest`は既存読取との互換参照として同じ詳細オブジェクトを指す。GCSに保存された原本と、03-plan-provenanceログのplan_sha256を対応づける。

索引には生成時刻、原本SHA、コードのPLAN_SOURCE_REVISION、費用モデルのハッシュを保持する。コード版はデプロイ時のgit HEADから設定するため、通常のクリーンなデプロイ手順を使う。`record_status=generated`であり、実機が適用したことを示す状態ではない。03-plan-provenanceと実機read-backによる採用確認を別途行う。

GCS保存後にFirestoreが失敗しても原本は残る。索引の自動修復は今回含めていない。孤立オブジェクトは原本を回収して同一JSONの保存処理を再実行すれば索引を再作成できるが、日付/latestも更新するため、本番への再投入は日付・影響を確認して実施する。

## 独立Driveバックアップ

デプロイ処理はDriveBackupFolderIdが解決でき、DisableDriveBackupが指定されていなければ、独立Cloud Runジョブとスケジュールを作成・再開する。既定スケジュールは既存パラメータの01:10 JSTを維持するため、当日03時の計画がDriveへ入るのは通常翌日のバックアップとなる。03時の制御完了を待たせない。

同期用TABLE_SPECSは変更しない。バックアップだけにnight_charge_plans、night_plan_decisions、forecast_plans、forecast_hourly_snapshotsを追加する。詳細計画はGCS URIだけでなく、ハッシュを照合した原本JSON文字列をplan_jsonとして同梱する。参照切れや破損はバックアップ失敗とし、完全成功として扱わない。

Driveへ書いたデータファイルを再取得しSHA一致を確認してからmanifestを公開する。容量制約時の累積形式は従来どおり最大14世代。更新前の累積内容をローカルにも退避するが、使い捨てCloud Run内の退避だけで永久保全を保証しない。長期の原本はGCSのimmutable objectに残る。GCSも同時に失われる場合の保持期間は別の運用設計課題。

## ローカル復元

ダウンロード済みバックアップに対して、次を実行する。クラウドへの書込みは行わない。

```powershell
python scripts/restore_plan_snapshot.py <downloaded-data.json.gz> <isolated-output-directory>
```

単体collections形式とgenerations[].snapshot形式の双方を読む。全計画のハッシュ検証後に日付・SHA由来のファイルへ保存し、同じ名前の異なる内容は上書きしない。古いバックアップが詳細JSONを含まない場合は復元可能と偽らず失敗する。

## 検証と本番確認

tests/test_plan_snapshot_recovery.pyは同日複数版・再送・破損・完全バックアップ・オフライン復元・保存失敗・時間不足を検証する。tests/test_drive_backup.pyはDrive読戻し破損時にmanifestを公開しないことを検証する。既存のnight_soc_protected_contractとデプロイテストも必須。

ローカルのテストは実サービスのIAM、容量、速度、定時起動を証明しない。デプロイは既存のPRODUCTION_DEPLOYMENT_RUNBOOK_JA.mdとラッパーを用い、実際の03時の原本SHAとGCS/Firestore/Drive読戻し、23/07の実機read-backを確認する。確認前は本番復旧完了と報告しない。

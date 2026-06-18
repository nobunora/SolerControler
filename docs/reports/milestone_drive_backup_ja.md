## Change Summary

- Google Drive 退避用のバックアップ処理を追加した。
- ソースは変更時のみ ZIP を更新し、データは Firestore 全件を日次で gzip JSON に保存する構成にした。
- Cloud Run Jobs のデプロイ手順に Drive バックアップ Job の作成と日次 Scheduler を追加した。

## Design Intent

- ソース喪失とデータ喪失の両方に備え、Drive をオフサイトの復旧点にする。
- 1.7MB 規模のデータは毎日フルで上書きし、世代管理は当面不要にする。

## Alignment With Existing Design

- 既存の `scripts/deploy_gcp_jobs.ps1` / `README.md` / `.env.example` の運用流儀に合わせた。
- 既存の Firestore コレクション構造と `app.db_sync.TABLE_SPECS` を再利用した。
- 新規ライブラリは追加せず、既存の `google-api-python-client` と `google-auth` 系を使った。

## Alternatives and Why They Were Not Chosen

- GitHub だけの退避: GitHub 障害や権限事故の単一障害点になるため採用しなかった。
- ローカルのみの退避: PC 故障時に失われるため採用しなかった。
- 差分のみの世代管理: 今のデータ量では不要で、復旧手順が複雑になるため採用しなかった。

## Files Changed

- `.env.example`
- `README.md`
- `scripts/deploy_gcp_jobs.ps1`
- `app/drive_backup.py`
- `scripts/backup_drive.py`
- `tests/test_drive_backup.py`

## Scope Not Changed

- DB スキーマ名、既存の Firestore コレクション名、既存の設定キーは変更していない。
- 23:00 / 04:30 / 07:00 の既存ジョブ構成は変更していない。
- 既存の Sheets エクスポート処理はそのまま残している。

## Tests

- Commands run:
  - `python -m py_compile app\\drive_backup.py scripts\\backup_drive.py`
  - `python -m pytest tests\\test_drive_backup.py -q`
  - `python scripts\\backup_drive.py --mode source --skip-drive --pretty`
- Results:
  - `pytest` は 2 件とも成功
  - source バックアップはローカルで ZIP / manifest を生成できた
- Reason they could not be run, if applicable:
  - Drive への実送信は、この時点ではフォルダの編集権限未確認のため未実施

## Points a Human Should Confirm

- Drive 共有フォルダが Cloud Run 実行サービスアカウントから編集可能か
- 日次バックアップの時刻が 01:10 JST で問題ないか
- source / data を同一フォルダに置く運用で問題ないか

## Remaining Risks

- Drive フォルダに編集権限がない場合、実送信時に失敗する。
- Firestore 認証情報がない環境では data バックアップは実行できない。
- 現状は最新世代のみを上書きするため、履歴復元が必要になった場合は世代保持に切り替える必要がある。

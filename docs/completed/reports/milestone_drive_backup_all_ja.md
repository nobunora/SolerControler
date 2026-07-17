# マイルストーン報告: Google Drive への全量バックアップ実行

## Change Summary

- `app/drive_backup.py` の Firestore スナップショットのソート処理を修正し、文字列と数値が混在しても落ちないようにした。
- `tests/test_drive_backup.py` に混在型のソートキーを検証するテストを追加した。
- `scripts/backup_drive.py --mode all` を実行し、ソースとデータの両方を Google Drive にアップロードした。

## Design Intent

- Drive 退避を、認証済みの人間ユーザー経由で実運用できる状態にする。
- データ側のバックアップ生成で、型差異による停止をなくす。

## Alignment With Existing Design

- 既存の Drive バックアップ構成を維持しつつ、エラーが出た箇所のみを修正した。
- ソースは更新時のみ上書き、データはその時点の最新スナップショットを保存する方針を維持した。

## Alternatives and Why They Were Not Chosen

- Firestore の各コレクションを文字列化してから並べる案: 余計な変換を増やすため不採用。
- ソートをやめる案: 復旧差分や比較のしやすさが落ちるため不採用。

## Files Changed

- `app/drive_backup.py`
- `tests/test_drive_backup.py`
- `docs/completed/reports/milestone_drive_backup_all_ja.md`

## Scope Not Changed

- Drive 認証方式
- バックアップ先フォルダID
- 既存のソース除外ルール
- 既存の Cloud Run / Sheets / KP-NET の実装

## Tests

- Commands run:
  - `python -m pytest tests/test_drive_backup.py -q`
  - `python -m py_compile app\drive_backup.py scripts\backup_drive.py`
  - `python .\scripts\backup_drive.py --mode all --folder-id $env:DRIVE_BACKUP_FOLDER_ID --pretty`
- Results:
  - `tests/test_drive_backup.py`: 3 passed
  - `py_compile`: success
  - Drive upload: success
  - Source archive uploaded: `source.zip`
  - Data snapshot uploaded: `data_snapshot.json.gz`
- Reason they could not be run, if applicable:
  - なし

## Points a Human Should Confirm

- この Drive フォルダを継続利用してよいか
- データ側のバックアップ頻度を毎日で固定してよいか

## Remaining Risks

- Firestore 側のコレクションが今後新しい型を含む場合、必要に応じてソート正規化の追加調整が要る。
- Drive 容量・共有設定は引き続きアカウント側の条件に依存する。

## Next Recommended Milestone

- 定期実行ジョブからこの `backup_drive.py --mode all` を自動実行し、失敗時のみ通知する。

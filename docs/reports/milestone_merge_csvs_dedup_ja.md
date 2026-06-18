# マイルストーン報告: CSV結合の重複除外対応

## Change Summary

- `app/csv_merge.py` に重複行除外を追加した。
- 結合時は同一ヘッダの行内容をキーにして、先に出た行だけを残すようにした。
- `scripts/merge_csvs.py` の出力に `duplicate_count` を追加した。

## Design Intent

- CSV結合時に、複数 run にまたがる同一行を1回だけ残す。
- 手動実行用スクリプトとしてはそのままにし、常駐ジョブにはしない。

## Alignment With Existing Design

- 既存の `artifacts/**/csv/*.csv` を拾う方針を維持した。
- `source_file` は追跡用に残せるが、重複判定には含めないようにした。

## Alternatives and Why They Were Not Chosen

- `source_file` も含めて重複判定する案: 元データが同じでも run が違うと残ってしまうため不採用。
- 行を並べ替えずに原順序のまま放置する案: 重複が残るため不採用。

## Files Changed

- `app/csv_merge.py`
- `scripts/merge_csvs.py`
- `tests/test_csv_merge.py`
- `README.md`
- `docs/reports/milestone_merge_csvs_dedup_ja.md`

## Scope Not Changed

- CSVの生成元
- Cloud Run / Scheduler の定期実行
- Drive バックアップの保存先

## Tests

- Commands run:
  - `python -m pytest tests/test_csv_merge.py -q`
  - `python -m py_compile app\csv_merge.py scripts\merge_csvs.py`
  - `python .\scripts\merge_csvs.py --input-root artifacts --include-source-file --pretty`
- Results:
  - `tests/test_csv_merge.py`: 3 passed
  - `py_compile`: success
  - 実データ結合: success
  - 出力: `artifacts/combined_csv/merged-20260613-193246.csv`
  - 入力CSV数: 31
  - 結合後行数: 2,304
  - 重複除外行数: 19,038

## Points a Human Should Confirm

- 重複判定を「完全一致行」でよいか
- 同一行のうち、どの run の `source_file` を残すかで問題がないか

## Remaining Risks

- 別 run の重複が多い場合、結合後ファイルがかなり小さくなる。
- 将来ヘッダが変わると、結合時にエラーになる。

## Next Recommended Milestone

- 必要なら、月別や日別で分けて結合するオプションを追加する。

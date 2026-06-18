# マイルストーン報告: CSV結合スクリプト追加

## Change Summary

- `artifacts/**/csv/*.csv` をまとめて 1 つの CSV にする `scripts/merge_csvs.py` を追加した。
- 結合ロジックは `app/csv_merge.py` に分離し、手動実行用の入口だけを `scripts/` に置いた。
- `source_file` 列を付けるオプションを追加し、どの run の CSV か追えるようにした。

## Design Intent

- 常時動かすジョブではなく、必要なときだけ手で実行する補助ツールにする。
- CSV を 1 つにまとめるときに、元ファイルの場所も必要なら追跡できるようにする。

## Alignment With Existing Design

- 既存の `artifacts/<run_id>/csv/` 配置に合わせ、`csv` フォルダだけを拾うようにした。
- `history.csv` のような要約履歴は対象外にし、元CSVの結合に寄せた。

## Alternatives and Why They Were Not Chosen

- 単純なファイル連結: ヘッダ重複や列ズレを見逃すので不採用。
- 自動定期実行: 常用ツールではないため不採用。

## Files Changed

- `app/csv_merge.py`
- `scripts/merge_csvs.py`
- `tests/test_csv_merge.py`
- `README.md`
- `docs/reports/milestone_merge_csvs_ja.md`

## Scope Not Changed

- 既存の CSV 生成処理
- Cloud Run / Scheduler の定期ジョブ
- Drive バックアップの実行条件

## Tests

- Commands run:
  - `python -m pytest tests/test_csv_merge.py -q`
  - `python -m py_compile app\csv_merge.py scripts\merge_csvs.py`
  - `python .\scripts\merge_csvs.py --input-root artifacts --include-source-file --pretty`
- Results:
  - `tests/test_csv_merge.py`: 3 passed
  - `py_compile`: success
  - 実データ結合: success
  - 出力: `artifacts/combined_csv/merged-20260613-192341.csv`
  - 入力ファイル数: 31
  - 結合行数: 21,342

## Points a Human Should Confirm

- `source_file` 列を常に付ける運用にするか
- `history.csv` も別途まとめたいか

## Remaining Risks

- 取り込む CSV のヘッダが将来変わると、結合時にエラーになる。
- 同じ月の CSV を複数 run からまとめると、重複行はそのまま残る。

## Next Recommended Milestone

- 必要に応じて、重複排除オプションや `history.csv` 向けの別マージ手順を足す。

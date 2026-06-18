# マイルストーン報告: Soler ソースを C:\ に ZIP 退避

## Change Summary

- `scripts/export_source_bundle_to_c.ps1` を追加し、SolerControler のソース一式をコピーして `C:\SolerControler-backups` 配下に ZIP 退避できるようにした。
- バックアップ対象はソース・設定・ドキュメント・テスト類とし、`.env`、生成物、`artifacts/`、`.git/`、`__pycache__/` などは除外した。
- 実行時に `backup_manifest.json` を ZIP 内へ含め、後から復元対象を確認できるようにした。

## Design Intent

- 共有ストレージやクラウド連携に頼らず、まずローカルで持ち出し可能なバックアップ ZIP を作る。
- 秘密情報と復旧用ソースを分離する運用に合わせる。

## Alignment With Existing Design

- 既存の `scripts/backup_local.ps1` と `README.md` のバックアップ方針を参考にしつつ、今回の用途に合わせて「C:\ へ出力する単発アーカイブ」に寄せた。
- `.env.example` は含め、`.env` と各種生成物は含めない方針に合わせた。

## Alternatives and Why They Were Not Chosen

- `git ls-files` のみを使う案: 未追跡の変更を拾えないため不採用。
- `artifacts/` も含める案: サイズと用途が別なので、既定では除外し、必要なら `-IncludeArtifacts` で切り替え可能にした。

## Files Changed

- `scripts/export_source_bundle_to_c.ps1`
- `docs/reports/milestone_export_source_bundle_to_c_ja.md`

## Scope Not Changed

- 既存の Drive バックアップ処理
- DB 同期ロジック
- 既存の Cloud Run / Sheets / KP-NET 実行ロジック

## Tests

- Commands run:
  - `powershell -ExecutionPolicy Bypass -File .\scripts\export_source_bundle_to_c.ps1`
  - `python` で ZIP 内のファイル名を確認
  - `Get-Item C:\SolerControler-backups\SolerControler-source-20260613-180914.zip`
- Results:
  - ZIP 作成成功
  - 出力 ZIP サイズ: 約 1.35 MB
  - ZIP 内ファイル数: 99
  - `.env` / `artifacts/` / `*.db` / `*.log` などの除外を確認
- Reason they could not be run, if applicable:
  - 自動テストはこのスクリプト単体では不要なため未実施

## Points a Human Should Confirm

- `artifacts/` も今後この ZIP に含める運用にするか
- 出力先を `C:\SolerControler-backups` で固定してよいか

## Remaining Risks

- ZIP に含めていない `artifacts/` が必要な復旧要素の場合は、別のバックアップ経路が必要。
- `C:\` 直下の保存先は環境によって権限差があるため、必要なら出力先を変更する。

## Next Recommended Milestone

- データ復旧用に `artifacts/` や DB を別ジョブで C:\ へ退避する。

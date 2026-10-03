# リリース品質チェックの固定契約

ローカル・CI・本番事前検証は `scripts/pre_release_local.ps1` を共通入口とする。実行するチェックはすべて必須であり、失敗をwarningへ変換して合格させない。

| チェック | 対象・目的 |
| --- | --- |
| Ruff check | Pythonの未定義名・未使用import/変数。formatterは実行しない |
| Import Linter | pyproject.tomlのDomain・Configuration・Parsingの3契約 |
| mypy | app/scripts全体。pyproject.tomlのstrict対象を維持し、テスト前に実行 |
| Oxlint / node --check | Git管理下の全JavaScriptのlint・構文 |
| compileall | 既存のアプリ・起動入口のPython構文 |
| pytest | 全体回帰・歴史的障害保護契約 |
| Node tests | dashboardの計算・モジュール・bootstrap動作 |
| security_check | 秘密情報と設定のセキュリティ検査 |

`-CheckPrerequisites` は必要コマンドの存在確認のみで、リリース合格ではない。`-SkipInstall` は依存インストールだけを省略する。失敗を許容するモードは設けない。

## 通常リリースから除外する探索的チェック

- **ty**: 正式なPython型契約はmypyに統一する。異なる型判定器・テストfake・外部型メタデータの差を同時にリリース条件へ持ち込まない。mypyの対象や厳格さは縮小しない。
- **tscの一括checkJs**: browserとNodeを混在させたad-hoc起動であり、window拡張や実行環境を宣言する専用tsconfigがない。JSはOxlint・全ファイル構文検査・動作テストで検証する。JSの静的型検証を保証するものではない。
- **deptry**: 分離環境での実行がdistribution/import名の誤対応を生む。依存関係の宣言・実行環境を対応付けた専用設定が未整備のため通常ゲートから除外する。未使用・未宣言依存がないことを保証するものではなく、依存変更時はrequirementsと利用箇所を個別にレビューする。
- **energy_planの参考Import Linter契約**: config/importlinter_advisory.iniに明記された将来の境界目標で、現在のmonitoring_history利用を禁止する正式契約ではない。設定は設計検討用として残す。既存3契約は引き続き必須。

除外理由は診断を合格へ偽装するためではなく、現行の検査対象・責任範囲を明確にするためである。探索結果から実際の障害が確認された場合は通常の修正・回帰テスト対象とする。金融入力・制御・復元の実動作確認はこの品質ゲートとは別に本番手順で行う。

# SolerControler 保守性改善・段階別実装設計書

## 実施結果（2026-07-16）

本設計書に基づく今回の保守性改善は完了した。既存の公開契約を維持しながら、次を実施した。

- DB backendで重複していた業務計算を `app/operations/domain.py` に集約し、各adapterを互換facadeへ変更した。
- dashboardのtyped model/serviceとdate/API/storeモジュールを導入し、bootstrap順序とCSP互換を自動テストで固定した。
- SQLiteとFirestoreの31日sliceを同一契約へ正規化し、完了日の実データparity検査をintegration pre-releaseへ追加した。
- 強制充電判断を状態機械へ分離し、非finite値、SOC境界、timeout、開始・監視失敗時のstandby fail-safeをテストした。
- energy plan documentとKP-NET settings intentをtyped domainへ分離し、欠損・重複・dry-run境界をテストした。
- JavaScriptテスト、段階的strict mypy、security checkをローカルpre-releaseへ統合した。
- 旧03時plan比較helperなど、実運用経路に未接続だったコードを削除した。

最終検証は FirestoreからSQLiteへ最新データを同期した後に実施し、Python **261 passed**、JavaScript 3系統、strict mypy 17 source files、security check、SQLite/Firestoreの前日31日parityが全て成功した。PostgreSQL実サービスとKP-NET実機への書込みはローカル環境で安全に実行できない外部運用検証であり、今回の実装完了判定には含めていない。再デプロイも直前の指示に従い実施していない。

> 対象リポジトリ: `C:\VSC\SolerControler`  
> レビュー基準HEAD: `88c11ea` (`Embed dashboard bootstrap payload in HTML`)  
> 作成日: 2026-07-15  
> 文書の性格: 実装着手前の保守性改善設計書。確認済み事実と未確認事項を分離して記載する。  
> 注意: 本文書作成時点ではソースコード変更を行っていない。

## 収録ファイル

1. `00_全体方針_実装選定とロードマップ.md`
2. `01_第1段階_挙動固定と特性テスト.md`
3. `02_第2段階_低リスク共通化.md`
4. `03_第3段階_DBドメインとアダプタ分離.md`
5. `04_第4段階_Dashboardローダ統合.md`
6. `05_第5段階_強制充電状態機械化.md`
7. `06_第6段階_EnergyModelパイプライン分割.md`
8. `07_第7段階_KPNETとDashboardJS分割.md`
9. `08_調査証跡と未調査事項一覧.md`

## 読み方

- 最初に `00_全体方針_実装選定とロードマップ.md` を読む。
- 実装担当者は対象段階の文書だけでも作業できるよう、各文書に現状、追加調査、ギャップ、3案比較、採用案、実装手順、テスト、ロールバック、完了条件を記載した。
- 「確認済み」は今回の静的調査・テスト実行で裏付けられた事項である。
- 「追加調査が必要」はコード全体または本番運用条件を追加確認しない限り確定できない事項である。
- 行番号はレビュー時点のHEADに基づく。後続変更によりずれる可能性がある。

## 現時点の品質基準値

- Pythonテスト: **214 passed in 9.01s**
- JavaScript計算テスト: **成功**
- mypy: **237 errors in 23 files / 34 source files checked**
- Docker runtime: **Python 3.12**
- mypy設定: **Python 3.14**
- Pythonモジュール間の循環import: **今回の調査では検出なし**

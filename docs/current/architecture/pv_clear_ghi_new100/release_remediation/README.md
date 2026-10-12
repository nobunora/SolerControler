# NEW100導入前の不一致・修復・実backend検証を解消する実装パッケージ

基準source：`1dd8786`、調査日：2026-10-12 JST。**詳細設計であり、以下の追加実装・試験・本番反映は未実施。**
今回の指示は調査・ステージ仕様・再監査。既存実装と正規リリース手順を置換する新frameworkは作らない。

## 最初に読むもの

1. [調査・結論](report.md)：確定した不一致、新たな漏れ、未検証。
2. [R01](steps/R01-validation-contract.md)：今回の48行不一致を解消する最初のsource変更。
3. 担当stageの必要source・禁止symbol・テストだけを読む。[index.json](index.json)を参照。
4. 合否・横断条件は[監査表](review.md)と[変更境界](boundaries.json)へ戻る。
5. 仕様を追加・変更するときは[クラス・変数・競合の再監査](collision-review.md)を適用し、その追加内容自身も再確認する。

## 当初目的と選択した方針

- PVはNEW100の `max(0,C)*min(F,Q)` と年周期1次を維持。消費補正一回、PV補正の二度掛けなし。
- 本番Firestoreの同世代PV/load/容量/保存SOCを表示する。過去予測は当時の原本を保持し、リプレイできない欠損予測は未取得のまま。
- 実測は信頼できる元CSVで保存側を修復。dashboard GETで補間・推測・保存しない。
- SQLiteは既存の検証用モデルのまま。容量・毎時SOCを新schemaへ追加せず、共通field parityとFirestoreの原本一致を**両方必須**にする。単なるignore追加は採らない。
- 本番操作は正規wrapper・固定commit・backup・state・read-backを維持。承認を受領していても失敗条件をPASSへ変えない。
- 新恒久flag、env key、DB table/collection、制御lease、HTTP endpoint、依存追加は不要。予定する運用CLIは一回限りの対象範囲・検証・StatePath指定だけ。

## 実装順序

| stage | 目的 | 具体的な出口 |
| --- | --- | --- |
| [R00](steps/R00-evidence-and-gates.md) | 証拠・検査順序・停止条件を固定 | source/データSHA、既存48行の再現、品質→試験→同期/parityの順序 |
| [R01](steps/R01-validation-contract.md) | SQL対応差と本番SOC原本の検証を分離 | 共通field drift0＋独立原本照合PASS、旧/新/不明のcoverage |
| [R02](steps/R02-source-repair.md) | 過去CSVの限定修復経路 | dry-run差分と競合guard、raw source修復、同入力retry安全 |
| [R03](steps/R03-derived-history.md) | 料金・夜間・校正入力確認・履歴を伝播 | 影響範囲再生成、履歴専用公開、179日超上限の解消、原本予測不変 |
| [R04](steps/R04-adapter-proof.md) | 実serverで競合・中断・復旧を実証 | SDK transportと別の証拠、commit不明read-back、有限予算 |
| [R05](steps/R05-release-acceptance.md) | 正規導入・画面・実機・backup受入 | 全必要gate、同commit、state終端、同原本表示・実機復元 |

R02/R03の運用計画・ローカル試験はデプロイ前。**旧本番imageで新修復CLIを実行しない。** 新コードの本番反映・修復・asset公開の厳密順序はR05で固定する。R04が新たなsource対策を要求した場合は、その限定差分の試験・R00/R01の再確認を先に行う。

## 仕様追加・変更時の必須サイクル

追加した仕様自身も検査対象に含め、変更対象symbolの定義・直接caller/callee・戻り型・変数所有者・書込先・失敗/再開を確認する。sourceの同名有無だけで競合なしと判断しない。stage本文、index.json、boundaries.json、テスト/PASS条件を同じ変更で更新し、独立再確認後に文書PASSを記録する。
競合時は、唯一の入口/書込責任者、既存引数・関数の再利用、固定原本からの再読取で解消できるかを先に検討する。追加flag、別状態管理、generic framework、二重呼出による回避を先に採用しない。必要な期限や証拠は既存処理の局所引数とreceiptへ限定し、処理の成功判定を別booleanへ複製しない。

## 状態の意味

このパッケージの文書レビューPASSは、実装、48行不一致の解消、実server試験、本番修復・deploymentの成功を意味しない。既存G04/G06/G07/G08は、各証拠が揃うまでOPENを維持する。
G06の当時の取得時刻・全旧scenario不足は「デプロイ時に復元できる未完作業」と説明しない。将来の原本記録と過去の検証不能を分ける。

## 委任と証拠

親：R00/R01・実データparity再調査・統合。source修復担当：R02/R03。adapter担当：R04/R05。別担当：全stage独立レビュー。
CodebaseMemoryはon-demand CLIで親が直列query、ready8833 nodes/31107 edges。artifact診断は索引対象外のため直接sourceとruntime結果を確認。持続daemon・watcherなし。

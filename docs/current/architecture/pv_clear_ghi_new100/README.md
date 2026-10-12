# 新100％PV予測 実装パッケージ revision 8

文書構造の検証、ローカル実装合格、本番合格は別である。
実装と証拠は [implementation-status](implementation-status.md)、導入前の残件は [gates.json](gates.json) に集約する。
2026-10-12の全表示再監査と未実装の追加仕様は [dashboard_followup](dashboard_followup/README.md)。
全81表示・18観点・7供給gapのD01〜D05は文書設計であり、次のユーザー実装指示を待つ。

## 必要な箇所だけ読む

1. [index.json](index.json) から担当stepを選ぶ。通常は全step本文を読み込まない。
2. `python docs/current/architecture/pv_clear_ghi_new100/tools/validate_package.py --step S04`
   で必要文書、依存出口、累積gate、変更境界だけを表示する。
3. 表示された共通契約・必要schema・担当本文を読む。前stepは出口と成果物だけを参照する。
4. [boundaries.json](boundaries.json) のallowedは許可するsymbol単位の上限であり、function全体の自由な変更許可ではない。
   同stepの変更禁止コード/変数が優先する。列挙されていない本番symbolは変更不可。
5. 未確定binding/承認は対象patchを停止する。実装後測定gateはfixture作成を妨げず、採用PASSを停止する。

## ステップ索引

| step | 責務と成果物 | 必要な固有正本 |
| --- | --- | --- |
| [S00](steps/S00-input-gates.md) | 認定・availability・保存・閾値・翌00時の着手条件 | data-contracts / gates |
| [S01](steps/S01-pure-pv.md) | 太陽幾何、年周期、日射対応、純PVと学習 | data-contracts |
| [S02](steps/S02-calibration-io.md) | 完成係数の読取・原子的更新・訂正 | data-contracts |
| [S03](steps/S03-load-correction.md) | 消費補正の等価抽出、旧PVの二度掛け防止 | data-contracts |
| [S04](steps/S04-plan-integration.md) | 同run入力、最終PV、夜間SOCへの一度の結線 | data-contracts |
| [S05](steps/S05-plan-output.md) | PV24/load24/SOC17、単一full traceと保存丸め | data-contracts |
| [S06](steps/S06-economic-confounders.md) | PV以外の変更を分離した経済/SOC比較 | data-contracts |
| [S07](steps/S07-control-publication.md) | 原本保存、採用CAS、古入力拒否 | publication-contracts / data-contracts |
| [S08](steps/S08-forecast-projection.md) | 採用後forecast/parameters projection | publication-contracts |
| [S09](steps/S09-dashboard-publication.md) | asset公開、世代、cache、読取性能 | publication-contracts |
| [S10](steps/S10-backup-release.md) | 復元・通し試験・release引継ぎ | publication-contracts / data-contracts |

責務順は S00→S01→…→S10。これは成果物依存順であり、全gate CLOSEDを要求する循環ではない。
各stepは入口、変更対象、変更禁止、クラス・変数、分岐、試験/PASS、中断、出口の8節を持つ。

## 正本と検証

- [contracts.md](contracts.md): 全体不変条件I01〜I15、容量/料金/制御/readerの禁止境界。
- [data-contracts.md](data-contracts.md): 入力schema、NEW型、係数pack、説明traceの唯一の定義。
- [publication-contracts.md](publication-contracts.md): 保存順序、世代比較、署名、冪等性、失敗境界。
- [index.json](index.json): step依存・gate・試験ID・全R/Cチェックの担当箇所。
- [boundaries.json](boundaries.json): 実sourceに照合する既存symbol/保護anchorとNEW symbol。
- [gates.json](gates.json): 未確定条件、最小確認、証拠、停止・承認条件。

`python docs/current/architecture/pv_clear_ghi_new100/tools/validate_package.py --self-test` は
文書/schema/参照/依存/既存symbol/保護anchorを検査し、不正fixtureが拒否されることも確認する。
これは将来実装の動作・数値・実adapter・本番結果を証明しない。PLANNED試験は実行済みと表示しない。

## AIへの割当て

定型実装担当には1step・read_set・許可symbol・試験/PASSだけを渡す。未定義の外部contractや
OPEN gateの判断を推測させない。親は統合と停止条件を判断し、独立担当は文書矛盾だけを監査する。
CodebaseMemoryは既存sourceの一段の経路確認だけに使い、ready/source鮮度を確認して短時間で終了する。
NEW symbolは実装済みsourceと照合し、graph反映前は既存定義と推測しない。リンク/依存/anchor照合は標準Pythonで行う。

全体レビュー時だけindexのchecksを辿り、担当stepの条件・証拠・未完gateを突き合わせる。
新研究/恒久flag/依存追加/制御変更/承認推測をパッケージ化のついでに行わない。

Q01: legacy07運転profileの保護文書とsource表記の相違は別調査。PV変更で修正しない。
本番mutation/mergeは現在の会話での明示承認と既存runbook/Skillが必要。

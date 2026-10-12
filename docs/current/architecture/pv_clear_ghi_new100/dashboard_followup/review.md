# 独立レビュー

## 実装後の確認（2026-10-12追記）

D01〜D06の実装は[実装結果](implementation-report.md)へ記録。D01 focused99件、D04/backend136件、D06 source44件、frontend追加Node3群と保護8関数を独立担当で検証し、残指摘0。
最後に親のcanonicalゲート1,297 passed/1 skippedとChromeの合成データ表示を確認した。本番操作/実cloud/Postgres実DBの証明ではない。
以下は実装前の文書レビュー記録として保持する。

2026-10-12。対象はこの追加表示パッケージの文書。production source/test変更0、source探索の重複0。
README・contracts・ui-display-inventory.md/json・data-path-audit・D01〜D05・index・boundariesを確認。sourceの事実確認は各監査担当の証拠を参照し、独立したruntime再実証とは扱わない。

## 現在の結果

**PASS — 文書の整合性・実装可能な限定手順のレビュー。残指摘0。**
RV01〜10は修正を確認した。全81表示・18riskの割当と5stepの8必須見出しを照合し、未割当・unknown参照・readset参照切れは0。
source実装・予定tests・runtime/ブラウザ/本番のPASS判定ではない。以下は解消済み指摘の記録。

| ID | 箇所 | 具体的な未定義・不一致 | 最小修正 |
| --- | --- | --- | --- |
| RV01 | contracts C02 status/reason_code/field表 | 理由codeを有限とするが列挙がなく、legacy/invalid/unsupported時のNEW専用fieldの必須/nullable条件も未定義 | 状態ごとのshapeと有限reason enumを記述。欠測を0やNEW有効へ読み替えない |
| RV02 | ui-display-inventory U79、JSON U79 fields と contracts C02 | inventoryのtraining_start/training_end・hashesが正本のtraining_start_date/training_end_date・個別SHA名と不一致 | inventoryの候補field名をC02正本へ合わせる |
| RV03 | contracts C01/C02 と D01（作成待ち） | raw exact/producer identity一致のscope、およびbrowserのper-day run bindingが契約表だけでは確定しない | D01で固定raw検証はbootstrap producerだけ、browserは既存public per-day runId、history未提供扱い、新meta mapなしを明記 |
| RV04 | data-path-audit §4 と contracts C02 | auditでは5totals fieldをnamespace直下として列挙、正本ではtotals object配下 | auditをC02のnested totalsへ統一 |
| RV05 | data-path-audit §5 と D01変更禁止 | auditはsnapshots._validate_publication_payload変更を提案、D01は同symbol変更禁止 | D01の変更範囲へ合わせ、auditから当該validator変更候補を外す |
| RV06 | data-path-audit §8 と D01テスト | NEW test名がtest_dashboard_pv_model_summary.pyとtest_dashboard_new100_diagnostics.pyに分裂 | D01正本のsuite名へ統一 |
| RV07 | D02 bindPvModelToForecast と data-path-audit browser比較 | D02 single nonempty runはmissing rowとknown runの混在時判定が不明、auditは全rowが同nonempty run | D02で全対象rowのnonempty同runを明記し、missing/mixedは未確認 |

RV01〜07はC02状態shape/理由enum、U79正本名、nested totals、validator呼出のみ、suite名、全24row nonempty bindingにより解消。

| 解消ID | 箇所 | 必要な情報・分岐 | 最小修正 |
| --- | --- | --- | --- |
| RV08 | D01分岐2/4、C02 | 既知legacy集合とaware時刻の順序式が未列挙。index readsetだけで実装すると判定を推測する必要がある | 正本へlegacy識別集合と意味別の時刻順序式を固定 |
| RV09 | D04分岐1/2、data-path §6 private scalar | 全同dateの別runをmismatchとする文とselected runだけ採用が衝突。mixed known hashはscalar保持なし→unknownへ消え得る | 比較はselected-run subsetに限定。既存読取hourlyからknown hash混在をreviewへ伝える手順を追加flagなしで記述 |
| RV10 | C02 common shape、D01分岐6 | 巨大unknown model/run/control IDではcommon-only fallback自体が4KiBを超え得る | 不正/巨大identityはNone、縮退後もbytes再検査。IDをtruncateして架空一致を作らず、巨大common ID試験を追加 |

RV08はC02のlegacy source/selected_method集合、trace/pack/totals key paths、UTC時刻順序式とD01 fixtureへ反映された。
RV09は既存selected-run subset内だけの比較、全row同一known rawでのみmatched、mixed known hashを既存読取hourlyから再確認するD04/data-path手順へ反映された。
RV10はcommon UTF-8長/形式の検証、超過None・truncate禁止・fallback自身の4KiB再検査へ反映された。
data-pathのbudget/混在伝播記述もC02/D04と同期した最終版を再読した。

R07/R08/R10/R12/R13/R14/R16はindexでNOT_FIXED_RETAIN_OR_EXPLICIT_RESIDUAL、D05で保持/別残件として扱う。
保持試験が通っても既存問題が修正済みとは報告しない。これらを今回のNEW100表示接続修正へ暗黙追加していない点を確認した。

## 合格範囲と残る検証

モデル要約はbootstrap producer固定rawと既存identityだけから作り、4KiB/stdllib/noNumPyを守る。
browserは既存public per-day runだけをbindingに使い、新meta map/HTTP/DBschemaを増やさない。
NEW/legacy/invalid/unsupported・旧係数保存世代/晴天日/13時間区間・設定σ/学習精度を分ける。
SOC23終端を正常に保持し、07..23保存17点・0..6実績/参考・計画最大/参考最大を分ける。保護SOC計算helperを変更しない。
known hash mismatchだけのplan由来field抑止、正常actual/PV/load保持、全24時間/金融07..22、欠測≠有効0の境界を確認した。
private scalar除去、lazy history/GET DB0/既存immutable asset/ETag/有限fallbackの手順と予定回帰へ接続されている。

独立文書検査は上記81/18/5step/readset/boundaryの照合。親の追加文書validatorの20source hash・8負fixture PASSは受領証拠であり、ここでproduction sourceを再探索していない。
SQL persisted SOCの未対応は未連携/参考として残し、schema拡張を許可していない。
実ブラウザ、実cloud/server race・読取/byte/cache測定、G06経済原本、G08導入承認は未完。PLANNED testsをEXECUTEDへ変えていない。
最小の次の入口は、ユーザーの次の実装指示後にD01〜D05の限定scopeと必須品質gateを適用すること。

## 最終統合後のD02補足確認

2026-10-12、D02のrefreshDashboard末尾のfillLearningParams再呼出と、D02 index/boundariesだけを追加確認した。
refreshDashboardはMODEL_DISPLAY_REBIND_CALL_ONLYの17番目の限定対象へ登録され、既存cached rowsのdate/runを再評価する。
fetch/merge方針・mainの既存初期呼出・SOC計算helper・他producerの変更許可は増やしていない。
予定fixtureはvalid→同日別run吸収→refreshで未確認、未知run→全24row同run復帰を含み、初期描画だけの判定漏れを検証する。
文書PASS・残指摘0を維持。追加実装/予定fixtureの実行証拠とは扱わず、他source/doc再探索0、今回もreview.mdだけを編集した。

## 表示構成の最終事実訂正

D03/D05・C04/C07・reportの該当記述だけを再確認した。学習tableは1個5columns、時間別tableは作らない。
D03に残ったchart/tableとtable/summaryの旧文言2箇所もchart/summary/noteへ修正された版を確認した。
SOC datasetは既存1本、保存点があればsaved点だけを描画し、23終端をそのまま保持する。
保存全無しのSQL/legacyに限る参考表示もobserved点をline/peakから除外し、別日/unknown scheduleで推定helperを呼ばず未連携とする。
dataset/axis/SOC計算helperの変更許可は増やしていない。保存SOCはfinite numericの0..100だけで、bool/文字列のcoerce・範囲外clipをしない。
aware ISOはinstantを維持してJST表示へ変換し、host/browser timezoneや画面取得時刻で由来を置換しない。
reportは文書PASSと未実装/PLANNED/実browser・cloud・経済・別残件を分離している。
最終結果は**文書PASS、残指摘0**。source/test変更0、全source/docs再探索0、編集は本review.mdだけ。

D04/R11の最終補足も確認し、48件の完全日表示は既存review.complete_dayがtrueの場合だけとする（date存在だけでは宣言せず、false/date有fixtureを予定し、backend complete_day算法・schema・変更権限を維持）。文書PASS・残指摘0を維持する。

## 最終照合の範囲

- C02要約4,096 bytes/stdllib、status別shape、原本固定/既存identity/理由enum、正常PV/loadを残す失敗分岐。
- NEW式と旧係数/保存世代数/EWMA/設定σ/neutral uncertaintyの意味の分離。
- SOC07..23の17保存点・23終端・0..6参考・peakの由来、保護helper不変、SQL SOC schema拡張なし。
- 全24時間と07..22金融窓、正当0と欠測/未完、date-only金融予想とknown hash mismatchの扱い。
- 81表示・18riskとD-step変更箇所/test/PASS/readsetへの全対応、必要情報に到達できるindex/link。
- lazy history、GET DB0、immutable static asset/cache、追加request0、既存bounded fallback。
- 代替案や判断を残す場合はPLANNED/未確認を明示。文書合格を実browser/live cloud/経済/production承認へ広げない。

## 編集範囲

独立レビュアの変更は本review.mdだけ。README/contracts/inventory/steps/index/boundariesの修正は各所有担当へ返す。
今は追加設計・文書レビューのみであり、次のユーザー指示をproduction source実装の入口とする。

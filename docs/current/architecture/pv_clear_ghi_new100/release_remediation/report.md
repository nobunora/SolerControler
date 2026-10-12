# 導入前不一致・修復・実backend検証の追加調査報告

2026-10-12 JST。基準HEAD `1dd8786`。今回の範囲は詳細調査、ステージ仕様作成、仕様再監査。追加本番実装・デプロイ・データ変更は実施していない。

## 結論

前回の「残りは検証だけ」という整理では不足していた。48行の問題はPV/消費計算値の誤りではなく、**SQLに保存しない容量・計画SOCを、両backend同一fieldとして要求する検査契約の不整合**だった。
加えて、元CSV修復→料金・夜間日計→校正・全履歴→backupのつながりを調べ、通常wrapperと既存producerだけでは保証できない手順・上限・並行性の問題を見つけた。各問題をR00〜R05へ割り当て、旧PV/負荷/SOC/容量の計算核と制御を変更しない追加仕様にした。

仕様は[入口](README.md)、[stage索引](index.json)、[変更境界](boundaries.json)、[再監査](review.md)へ分割。必要stageだけを読める構成とする。文書レビューの結果と、未実装/未検証の状況は分ける。

## 1. 実データの再確認

同じ検証用SQLと本番Firestoreを31日window（終了2026-10-11）でread-only照合した。

| 項目 | 結果 |
| --- | --- |
| 時間別logical row数 | SQLite744 / Firestore744、identity欠落/重複0 |
| 不一致の行 | 10/10の24行＋10/11の24行＝48行 |
| SQLにない表示容量 | 48点 |
| SQLにない保存SOC | 34点＝17点×2日 |
| 両側共通fieldの値差 | 0 |
| 当該2日のforecast metadata | 保存済み |
| 同日night planの生成時刻 | 当該forecast issuedと一致 |
| 詳細JSONがnight documentにembedされているか | 両日とも無し |
| 既存archive URI / detail SHA参照 | 両日とも有り |
| archive bytesの取得・SHA検証 | 今回未実施。参照の存在を原本検証PASSとはしていない |

前回の正規gateは日別5datasetのparity成功、時間別48不一致で停止。29＋15の保護pytestは成功したが、その実行では後段pre_release_localに到達していない。同一sourceの直前1299件成功を今回integration gateの成功と表記しない。

SQL readerはrun/issuedを保持し、正常実測を0へ埋めていない。Firestore readerは同run metadataから容量/SOCを付けている。SQL schema/同期にはそのfieldがない。既存仕様がSQLの未連携/参考表示を認めているため、ここへ新schemaを足す案は今回の最小案に採用しない。

## 2. 修正方針

### 48行不一致

共通PV/load/実測/run/issuedなどは厳格parityを保持。SQL未対応の容量・SOCだけは、Firestoreの同世代原本一致を別検証でPASSにした後に共通比較から分ける。
単なるIGNORED_FIELDS追加、原本検証の任意化、同日だけの結合、別runの最新容量への置換は不可。原本資格が不明ならINCONCLUSIVE・非zero。NEW100の受入は対象日すべて完全証拠が必須。

### 過去CSV修復

通常import wrapperにTargetMonths CLIはないが、下位の既存KP_CSV_TARGET_MONTHSは明示月を尊重する。したがって「9月CSVを取得できる経路が全くない」は不正確。問題は正規wrapperの月指定・対象差分レビュー・通常4日取込window・raw競合guard・修復stateを一連の手順として固定していないこと。
予定する一回限りwrapperは既存env keyへのProcess-only overrideをfinallyで復元し、.envを書き換えない。production CSV readerを使い、保存原本と一致した9/20の13欠損区間のみをnative precondition付きで挿入する。

### 派生データ・履歴

ローカル修復はcost_dailyを変更していない。raw修復再試行がunchangedになった時、既存cost再生成がスキップされても、最初のcost書込が完了していたと判断できない。修復stageはrawとdaily/cost等の終端証拠を独立記録する。
過去rawを変えた影響は対象日のload/buyだけでなく、翌朝夜間窓、月内昼間買電・料金段階、校正原本hash・complete区間数、materialized historyまで伝播する。dashboardでの補間によって隠さない。
全履歴source revision検査はmonitoring_samplesを一queryで最大3660件に制限する構造。原本8493行、9/20だけの13追加なら8506行、最新10/12の20行も加えたローカルcopyは8526行であり、いずれも上限を超える。全範囲を同条件で検査するfull rebuildはsource inspection上拒否される。実serverのfull rebuildは今回未実行。R03で範囲paging・有限総上限・metadata8KiB・drift拒否を維持する限定修正を指定する。限定13区間の本番修復で8526を期待値にしない。

### 実backendの証拠

現行testsはfakeまたは実SDK＋local transportであり、Firestore/GCS serverの競合・制限・中断・commit応答喪失を通していない。SDK各read timeoutやmax_attemptsだけでは、begin/commit/認証refreshを含む全工程の有限期限を証明しない。
Firestore transactionとGCS uploadをまたぐ完全atomicityは保証できない。単一writer・source revision再検証・異常検出と再公開の対象範囲を明示し、commitが拒否されたのか、応答だけ失われたのかをserver read-backで確定する。

## 3. 新しく見つかった事項

| 観点 | 確認した内容 | owner |
| --- | --- | --- |
| 検査契約 | SQL非対応fieldの存在差を検査失敗にしている。原本検証なしのignoreも危険 | R01 |
| 品質順序 | integrationは品質前にpytest、parity失敗なら全体品質に未到達 | R00 |
| 過去月取得 | wrapper defaultと既存月envの差。Process-only月選択が可能 | R02 |
| raw並行書込 | 通常同期のblind batchではレビュー済み13区間の不在をcommitまで保証しない | R02 |
| cost再試行 | changed_count0/既存runで派生費用修復の完了を判断できない | R03 |
| history上限 | 3660件per-query上限と8493/8526保存raw数が衝突 | R03 |
| 校正再読 | 9/20は現時点の現在＋前請求期間に入り得る。常に未反映ではないが、範囲外訂正の確実な再構築は別途必要 | R03 |
| commit応答喪失 | timeout→拒否を断定できない。serverで採用結果を照合する | R04 |
| backup範囲 | 埋込packの復元と独立calibration current/state原本群の復旧は同一ではない | R04/R05 |
| emulator限界 | 本番transaction/index/制限を完全再現しないので単独で本番証明に使わない | R04 |

独立再監査では、さらに実装指示の丸め/型/原本不存在、前後asset phase、SDK再試行順序、料金・日計内部RPC期限/commit receiptを具体化した。本番constraintsはFirestore2.34/auth2.60で、local2.31/auth2.58と異なる。対象wheelをdownloadだけ行い、2.34のbegin/commit/rollback/cleanupをASTで読取確認した。install・target SDK実行は0、auth refresh/runtime/serverは未検証。予定server試験は同candidate image digestで行い、image生成前に同digest証明完了を要求する循環を避ける。

## 4. 公式資料の確認

本番server・emulator・条件付き書込の境界は、2026-10-12に一次資料を確認した。

- [Firestore emulator](https://docs.cloud.google.com/firestore/native/docs/emulator?hl=en)：本番のtransaction concurrency、index、timeout/size制限を完全には再現しない。新しいemulatorのJava要件と導入済み版を実装時に確認する。emulator試験と隔離実server試験を分ける。
- [GCS request preconditions](https://docs.cloud.google.com/storage/docs/request-preconditions)：generation=0の作成と特定generationの条件付き書込/削除を使う。object名だけでcleanupしない。
- [Firestore transactions](https://firebase.google.com/docs/firestore/manage-data/transactions)：server側のtransaction制限とアプリの工程期限は別。自動retry時に課金・raw/model二重適用を発生させない。

この資料はAPIの保証範囲の根拠であり、現在sourceのrace/crash試験に代わらない。

## 5. 確定・不確定・検証済み・未検証

**確定/今回再確認**：48行/48容量/34SOCの存在差、共通数値差0、2日archive参照と同発行時刻、SQL保存項目なし、既存schema禁止、legacy daily validation projectionが夜間target/chargeだけをNULLに補う仕様。
**sourceで確定**：cost/unchangedの分岐、通常month/4日window、source revision上限、producer/client deadlineの未証明範囲。実serverの結果を見たという意味ではない。
**以前のローカル検証済み**：9/20の48slot復元、raw既存更新0、日計179×6項目一致、38日point損失再評価、既存品質1299成功/1skip。今回の追加仕様実装成功には流用しない。
**未検証**：archive bytesの独立照合、R01追加検査、修復wrapper/CAS、全派生cost/校正/historyの通し再生成、実server競合/中断/応答喪失/復旧、native backup復元、production browser/API/cache/read/bytes/実機往復。
**資料不足で不確定**：全過去日の旧scenario/当時実測取得availability、年全季節の予測精度、過去全機器容量/出力制限。現在のデプロイで過去資料を作らない。

## 6. 今回変更したもの・していないもの

作成/更新：release_remediationの文書・索引・境界・再監査、既存入口文書への追加仕様参照。Git無視analysisにread-only診断scriptとsafe件数証拠。
変更なし：app/scripts/static/templates/testsのproduction/試験source、.env、DBschema、原本CSV/予測、Cloud/実機、本番Firestore/GCS/Drive、既存gateのOPEN状態。commit/push/merge/deployなし。
次の最小作業はR00/R01の実装・局所回帰。修復/実server/本番受入は各依存とgateに従う。今回の調査依頼だけで新しい本番mutationを開始しない。

## 7. Bounded Verification記録

Result：調査対象のruntime存在差は再確認済。詳細設計と再監査の結果はreview.mdを正本とする。実装/本番は未実施。
Expected：現行検査48不一致を再現し、契約/保存/表示を別に調べ、後続料金/校正/履歴/backupに見落としがないか分類する。
Observed：上記の存在差、導入前に必要な追加手順・source上限・server証拠の漏れを検出。
Failure evidence：既存正規gateはforecast_hourly48errors、今回は独立read-only診断で日付/field内訳と共通数値差0を確認。診断scriptRuff成功。
CodebaseMemory：ready8833/31107、定義14symbolと直接neighborを親が直列CLIで取得。一部builtin/get等に低信頼edgeがあり、source確認だけを根拠に採用した。docs/analysisの新仮称はgraphにあると主張しない。
Additional files read：parity/history_reconstruction/sqlite/sync/validation_projection/forecast_persistence/PublishedPlanおよび各担当の直接修復・公開・backup依存。今回セッションの探索上限解除を適用。全repo無差別scanはしていない。
Recommended next action：R00/R01の限定実装。原本不足なら値捏造やgate緩和で進めず、R01の必要証拠を調べる。

## 8. 追加仕様自身を含めた競合再監査・単純化

利用者の追加指示に従い、新設予定symbol/署名/戻り型/引数/状態所有者/書込先と直接callerを再確認した。[クラス・変数監査](collision-review.md)に15観点と対応表を記録した。単に既存名との衝突がないだけでは合格とせず、二重計算・二重公開・再開時の別原本採用まで確認した。

当初追加案より単純な構造へ変更した。原本検査は既存parity CLIに集約し、新moduleのmain/独立CLIを撤回。校正の更新は通常plannerだけとし、corrected_dates/recheck_dates/旧日読取helper/校正更新stageを撤回。中間asset公開と前後切替引数/4receiptも撤回し、保存raw/daily/costのread-back→D通常plannerのcurrent bootstrap→E履歴専用一関数→最終検証とした。Drive実行もR05だけに集約した。

必要な追加対策は局所的に残した。SDK transaction lifecycleは既存予定の一helperを三callerで共有し、immutable forecast snapshotにも同deadlineを渡す。本番imageへpytestを入れず、同planner digestのstdlib runnerでnativeproofする。build fixtureとexact executionの証拠を正式wrapperへ接続し、採用後の失敗は唯一の原本receiptから未完投影だけ復旧する。新production class/恒久flag/env/schema/依存は0。

単純化の範囲も明示した。この限定修復は、修復日が通常contextの現在＋前請求期間内にあり、26区間の観測原本hashが修復rawと一致する場合だけ進める。任意の範囲外旧日を自動修復・再校正する機構は追加せず、範囲外なら停止して別仕様へ渡す。builder内部RPCの強制期限、identity log前crashの採用特定、横断atomicityは保証しない。未証明をflagや成功stateで隠さない。

文書・索引の整合、source SHAの不変、独立再レビューの最終結果はreview.mdに記録する。今回も実装・本番試験・データ変更は0。

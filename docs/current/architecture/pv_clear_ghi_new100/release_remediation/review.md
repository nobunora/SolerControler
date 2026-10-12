# 追加パッケージの独立レビュー・目的・交絡チェック

2026-10-12 JST。範囲はR00〜R05の**仕様整合・実装指示・調査根拠**。production/test source変更と新試験・server操作は0。
結果：以下の指摘は文書へ反映。最終の構造検査・独立確認の証拠を本書末尾へ追記する。追加実装・本番はPLANNED/未実施である。

## 1. 段階別・独立確認の方法

1. 親が既存失敗48行をread-onlyで再調査し、SQL/Firestoreの保存・結合・検査を調べた。
2. 保存修復担当がCSV→native raw→daily/cost→校正→history→Driveを調査してR02/R03を作成。
3. adapter担当が各fake/SDK/server層とdeadline/commit結果/復旧、正規deploymentを調査してR04/R05を作成。
4. 別担当が各仕様とsourceの直接関係を独立チェック。親は探索を繰り返さず、指摘と競合する契約を統合した。
5. 全文を一括で「問題なし」とせず、通常経路・異常/欠損・protected/性能・交絡・復旧・stage境界を分けて確認した。

## 2. 指摘・修正のサイクル

| ID | 発見した不足/矛盾 | 最終指示/対象 |
| --- | --- | --- |
| J01 | PV/load丸めとcharge/SOCの丸め段が曖昧 | R01：Python round4dp、chargeはraw差、SOC追加round0。legacy宣言窓外PV0と任意欠測を区別 |
| J02 | 後段legacy分類が結果enumにない | R01：LEGACY_NO_PLAN_METADATAを列挙、rawdoc不存在を証明してのみ適用 |
| J03 | capacity0を負容量拒否だけでは検出できない | R01：strict>0、SOC/chargeの正当0とは別のfixture |
| J04 | metadata原本が不変でもforecast行だけ変わるraceが抜ける | R01：全24行id/native revision/digestと原本の取得前後照合、全pair一回retry |
| J05 | reviewed SHAがmutable journalや自身を含み得る | R02：immutable prepared manifestとmutable execution journalを分離し自己参照を避ける |
| J06 | 初回attemptでingested_atが変わり承認payloadが確定しない | R02：prepare UTC固定、完全write payload SHA、commit/readback時刻は別記録 |
| J07 | forecast前/後snapshot順序に矛盾、同stage successで片方がskipされる | 前回phase分離案を今回C07で再検討。中間asset公開/phaseを撤回し、D bootstrap/E historyの別owner＋一つの採用原本で解消 |
| J08 | metadata棄却/読取例外をdocument不存在と誤認する | R01：rawdocの不存在独立確認、invalid/partial FAIL、RPC不明INCONCLUSIVE |
| J09 | 手製bounded transactionにSDK状態/再試行の実装指示不足 | R04：begin/commit/rollback/cleanup、first id、commitABORTEDのみretry、結果属性・exception chaining・ACKunknownを指定 |
| J10 | late source driftは一回再生成すれば必ず解決と読める | R04：nativeMax/countが既存CAS条件を満たす場合だけ。古訂正max不変/削除は保守FAIL/G04OPEN、guard緩和0 |
| J11 | test-only codeがbuild転送へ混入する範囲が未調査 | R04：tests/g07へ配置。3正規CloudBuildignore tests除外と明示COPYを確認。ignore変更不要、実bytes計測は未実施 |
| J12 | rollback対象旧imageが修復raw/current packと互換か未証明 | R05：旧digestでread-only復号/smokeを事前確認。不能なら自動pack戻しや独自rollbackへ進まない |
| J13 | purelegacy common比較がbool≈int/数値stringを許容する | R01：新validatorの数値type/range検査、元compare_rows契約不変。capacity/target/charge1e-9許容差を指定 |
| J14 | 既存daily/cost CALL ONLYでは内部期限・途中commit receiptを渡せない | R03：限定optional deadline/on_progress、式/450/default/戻り型維持、ACK/unknown/readbackを固定。R04で実server予算を検証 |
| J15 | ローカルSDK2.31と本番constraints2.34が違う | R04：target2.34 wheel原本でAPI構造を読取確認。auth2.60との差・同container/native試験は未検証として条件化 |
| J16 | source boundaryのsymbol所属とテスト新設場所が曖昧 | index/boundaries：path-symbol対とNEW source/テストを分離。R03daily/costのRPC限定許可を追加 |
| J17 | 同candidate digestでのserver証明をimage生成前に要求する循環 | R04/R05：fixture/ソース準備はbuild前、実候補digestのnative証明はbuild内、planner/control/web更新前に必須。旧S10停止条件の段階境界を限定仕様として明示 |

J01〜J04→J05〜J13→J14〜J17の順で修正・再確認。現行sourceを改修してPASSさせた記録ではない。

## 3. 当初目的・見落とし監査

| 目的/観点 | ownerと検査内容 | 文書で解決したこと / 実証状況 |
| --- | --- | --- |
| 新100％予測を壊さない | P01、R01/R03/R05 | C/F/Q/年周期basis/補正一回のsourceを変更禁止。同じ原本から表示を検証。モデル改善の再学習はしない |
| 消費・温度・容量・料金を二度掛けしない | P01/P02/P06、R01 | raw original→projection→displayの一方向。新計算の追加0。式不変をhash/fixtureで確認する予定 |
| 日付/時刻・世代一致 | R01/R03/R04 | run/issued/SHA、24行/17SOC、07開始/23終了、06:30朝判定を維持。実原本照合は未実施 |
| 固定軸の見切れ | P10、R05 | 既実装の8グラフ・SOC0〜100を変更しない。本番全表示受入で確認する予定 |
| 実測必ず保存側で修復 | R02/R03 | 原本13区間のみ、既存35不変。他日の欠損は捏造しない。本番修復は未実施 |
| 過去予測をリプレイ不能なら保持 | P09、R03 | 旧PV/load/forecast原本の再予想/上書き0。legacy原本不明は分類/停止 |
| 日計・前夜・料金段階への伝播 | R03 | raw/daily/costの別receipt、9/21夜間窓、9/1〜固定末端の全派生料金。実金額未再計算 |
| 欠損と0・非有限・型 | R01/R02/R03 | bool/string/NaN/inf/負・partial day/17SOC・capacity0を明示。修復preflightで不完全金融入力を拒否 |
| 古い訂正をhistoryへ反映 | R03/R04 | 8493/8506/8526>3660のpaging、source前後検査、採用確定後history専用full rebuild一回。実serverのread/time/byteは未測定 |
| 晴天認証/季節/劣化との交絡 | R03、G06 | 9/20は認証を再確認し、未晴天を訓練へ加えない。温度/劣化容量を再適用しない。全年精度/旧scenario不足は未証明 |
| 多重write・古版・同版retry | R02/R04 | nativecreate/CAS/同版sample加算0、ACK喪失分類、source後退保守FAIL。server未試験 |
| 性能・cache・初期request | R01/R03/R04/R05 | producer/検証CLIだけ追加読取。GET DB0、31日bootstrap/lazyhistory/ETag保持。実本番bytes未測定 |
| backupとcurrent参照の区別 | R02/R03/R04 | source preimage・Drive raw pack・独立calibration原本/参照を分け、valid currentを勝手に戻さない。native復元未試験 |
| 部分成功・観測性・再開 | R02/R03/R04/R05 | 成功/UNKNOWN/PARTIAL/FAILを分離、receiptと局所journal。失敗を出力成功としない |
| 制御/実機への齟齬 | P02/P11、R05 | 23/03/07/06:30・mode-only・60秒復元・DB非依存を維持。実機操作は未実施 |
| 対策自体が新リスクになるか | R04、J09/J14/J15 | SDKprivate lifecycle依存・callback失敗・旧image互換・非atomic窓を列挙して試験/停止に割当。証拠なし安全保証0 |

## 4. 未調査・未検証の区別

今回読んだsource範囲で指摘された経路に、ownerなし・停止条件なしの事項を残さない。**全codebase/全provider/backendを調査したという保証ではない。**

未調査または未取得で、実装時の明示preflightに割り当てたもの:

- 2日のGCS archive bytes/generation pin/SHA、修復実行時の最新native35/13不存在、8/31cost baseline・実単価/締日。
- 9/20衛星認証・current収録日/pack hash、source訂正が通常請求範囲外になるケースの実再構築。
- 隔離実Firestoreの対象/権限・emulator component、docker/container実行環境、google-auth2.60 refresh経路、全RPCの実elapsed。
- 同時producer稼働実態、GCS後source変更の実復旧、旧image digestによる新旧pack読取互換、nativebackup復元。
- 本番HTML/API/全8グラフ表示、request/read/bytes/cache、実機SET/保持/復元、Job終端4条件。

これらは未定義の実装動作ではなく、fixture・対象・必要証拠・FAIL/INCONCLUSIVE条件をstageに割り当てた未実証。全旧scenario/当時availability不足はG06の既知限界であり、そのデータが存在することを前提にした作業指示を作らない。

## 5. 合否の境界

- 文書PASS：stage・source-symbol・禁止field・class/variable・input/output・test・出口/停止が整合し、既知指摘が反映され、リンク/索引/依存が検査できる。
- 実装PASS：追加sourceを実装し、必須品質/局所/同じ実入力のregressionが合格。
- adapter PASS：同candidate image/SDKで必要な実serverケース・復旧・有限性を実証。
- 本番PASS：正規backup/gate/固定commit/stateful deploy、source修復・派生・同原本画面・実機復元・終端条件が成功。

後段を前段の合格から推測しない。global dependencyの `production_requires R05` は**R05内の正規orchestrationで実行するという意味**で、R05全stage success後でなければR02/R03を実行できない循環条件ではない。

## 6. 最終検査の記録

独立担当によるR00〜R05/index/boundaries/report/reviewの文書レビュー後、J17の残指摘（build前の同digest証明要求）をR05の実装手順・停止条件・旧S10限定更新対象へ統合した。最終限定レビューは文書PASS、重大・中の未反映指摘0。R04のdaily/cost戻り型の表とテスト条件も日計dict[str,int]/料金intへ統一し再確認した。source source-bearing SHAはindex/boundariesで固定し、今回app/scripts/static/templates/testsに実装差分がないことをgit diffで確認する。
構造検査は6stage・11Markdown・78local links・19許可group・12保護group・42source SHA照合・依存循環0でPASS（集計後の参照追加は最終再検査を正本とする）。
上記は前回文書検査の記録。追加仕様自身の再監査では[collision-review](collision-review.md)のC01〜C15を反映し、R00〜R05/index/boundariesのclass/変数/ownerを同期した。追加予定historical helperとphase指示を撤回、唯一CLI/通常planner/履歴専用関数/唯一採用receiptへ単純化した。今回の最終検査・独立レビューは以下へ追記する。
既存本体packageの文書検査もPASS（11step/31checks/34protected、OPEN4gate維持）、追加dashboard packageの文書/source状態検査PASS（6step/81表示/18risk/20source hash）。新仕様のコード試験成功と表現しない。
最初の本体文書検査は新docリンクの`:line`記法をfile名として判定してFAILしたため、行番号はlabelへ移しrelative fileリンクに統一して再検査PASS。検査器を緩める変更は0。
今回使ったanalysis診断/文書検査scriptはRuff lintを実行し成功。production pytest・実server試験は未実行。

## 7. 追加仕様自身の最終再レビュー

C01〜C15をclass/署名/戻り型/変数所有者/書込先/再開まで確認し、三担当の独立文書再レビューはいずれもPASS、既知の未反映指摘0。途中で見つかったhistory builderのdict戻り、artifact_from_payloadのkind引数、SnapshotArtifactとnative generationの区別、plannerから共有deadlineを渡す接続も修正して再確認した。

最終構造検査は6stage/12Markdown/87local links/20許可group/12保護group/43source SHA照合、依存循環0でPASS。二重CLI・二重校正owner・history専用symbol・期限callee・build/実execution境界・テスト許可・採用receipt所有者の索引検査もPASS。既存本体package検査PASS（11step/31checks/34protected）、dashboard追加package検査PASS（6step/81表示/18risk/7gap/20source SHA）。G04/G06/G07/G08はOPEN維持。

Ruff全体lint＋今回のartifact検査script lint、security_check、git diff --checkはPASS。app/scripts/static/templates/tests/.codebase-memoryに今回変更差分0、.env ignored、CodebaseMemory残留process0。pytest/Node/nativeserver/OCI/browser/device/本番mutationは今回実行0、文書のPASSからそれらの成功を推測しない。

# 全表示再監査と追加設計の報告

2026-10-12。追加実装は行っていない。計算器NEW100の未commit作業ツリーを基準に、ダッシュボードの全表示と供給を再照合した。

## Result

追加仕様の文書レビューは **PASS**。実装済み/画面正常/本番安全という意味ではない。
全表示81項目、18観点、19供給群、7供給gapを記録し、D01〜D05へ修正または保持/別残件を割当てた。
HTMLは37ID・13見出し・8chart24series・13review KPI・7button・学習table1個5columns。不在のフォーム/時間別table/連携設定も確認した。

仕様入口は [README](README.md)、field正本は [contracts](contracts.md)、担当別は [index](index.json)。
全項目は [表示一覧](ui-display-inventory.md)/[machine inventory](ui-display-inventory.json)、供給側は [data-path audit](data-path-audit.md)。

## Checks performed

確認順はfrontend全表示→raw/projection/adapter/DTOの供給→同日別run/partial/legacy/backendの交絡→追加仕様→独立レビュー→修正→再レビュー。
CodebaseMemoryはready後にtargetの一hop82edgeだけをroutingへ使用し、低確信suffix edgeを事実へ読み替えなかった。
frontendは6 source/templateと直接Node3 testsを限定読取、backendは直接依存だけを段階参照。全source/log/履歴の無差別走査はしていない。

合成Node VM/Chart/DOM stubはnetwork0で当該rendererを評価した。これは実browser/live計画の合格ではない。
前段の同source canonical 1,168 passed / 1 skippedとlint/type/JS/securityは既存品質証拠として参照し、今回source変更なしのため全体suiteを重複実行しなかった。
追加設計testsは全てPLANNED、未作成・未実行。文書validatorの正負fixtureは別に実行した。

最終文書検査: 追加packetの81/18/7割当、5step/17提案target、20source hash、8負fixtureはPASS。
元packetの11step/31check/34保護境界と12負fixtureもPASS。実行scriptのRuff checkとgit diff --checkもPASS。
これらは予定表示機能の動作合格とは別である。

    python docs/current/architecture/pv_clear_ghi_new100/dashboard_followup/tools/validate_followup.py --self-test
    python docs/current/architecture/pv_clear_ghi_new100/tools/validate_package.py --self-test

## Expected

NEW100の式・学習根拠・件数・対象期間・時刻/版が実際の採用予測と結び付き、PV24とSOC17の区間/由来が分かること。
正常な0と欠測/部分取得、保存SOCと実測/参考、全日PVと金融07〜22、計画生成と実機適用を混同しないこと。
既存の制御/料金/容量/寿命/バックアップと、初期1bootstrap・lazy history・assetありDB0・ETag/cacheを維持すること。

## Observed

### 追加修正対象

| 問題 | 確認根拠 | 追加仕様 |
| --- | --- | --- |
| 新学習日数・区間・theta4・係数版が表示へ未接続 | extractorがtraceを落としJSは旧scalesのみ | D01小要約＋D02表示 |
| PV説明式が旧GTI/PR/温度補正、旧係数/保存回数が新学習と混在 | template/learning renderer/source | D02モデル別説明、日/区間/保存回数/設定σの区別 |
| 同日別runへ古い学習要約が結び付き得る、初期描画だけ | diagnosticにはdateだけ、main時のみlearning描画 | D01producer固定raw、D02全24row run一致とrefresh再bind |
| 欠測が時間別summaryで0に合算される | VMでPV08=nullをgap表示するが合計はpartial注記なし | D03完全性と実績subtotal、valid0維持 |
| 別日のlatest scheduleが夜間量/参考SOCへ流用される | VMで旧日2kWhへ別日9kWhを表示 | D03同date条件、未知は未連携 |
| 0〜6時の実測/参考値と保存SOCを一つの予想線/最大点へ混在 | VMで0時actual40を予想系列へ表示 | D03保存点のみ計画線、SQL/legacyは参考表示 |
| 日次reviewのPV/loadとplan由来買売/目標/夜間量のraw版未照合 | backendは日付だけでplan.result fallback | D04selected-run subset/raw SHAの照合、部分fieldだけ未確認 |
| 日別予測が24区間完備か表示に出ない | SQL partial rowのaggregate/DTO | D04 optional hour countとcaption。数値を再補正しない |

18観点中11は今回の追加表示修正に割当て、7は既存描画・条件付き問題・不存在範囲として保持/別残件を明記。
「18件すべてが本番で発生した不具合」や「この11対応で既存7件も解消」とは報告しない。

### 正常と確認した範囲

- Firestoreはfinal PV/load24と、07〜23の17保存SOCを同runで供給する。PV日合計はfinal24を使い、旧predicted PVを再適用しない。
- **保存SOC23=99の合成入力は99のまま表示した。** 「23が一律再計算される」という初期疑いは撤回し、0〜6/最大点混在の問題と分離した。
- 既存予実の日別PV/loadペア、07前06:30目標判定、制御時刻、料金/資産/容量/温度/劣化/寿命式は今回の変更禁止。
- SQLに保存SOC/容量/targetがないのはSELECTの漏れだけではなく保存schemaの未対応。今回新schemaを作らず、未連携/参考と説明する。

### 実装を小さくする判断

モデル要約を既存pv_forecast_diagnosticsに最大4KiBで加える。full trace/全訓練日/24QFC/座標を送らず、NumPy/観測/fit/storageへの依存を追加しない。
新class/endpoint/DBcollection/env/恒久mode flag/public per-date mappingを作らない。
原本exact bindingは既存bootstrap producerの固定rawだけ。browserは既存date/runで判定し、未知を同版一致と推測しない。
SOCは既存dataset1本の描画分類を変えるだけ。計算helperやaxis/dataset数は維持し、時間別tableを新設しない。
reviewは既存readのidentityを使い、known mismatchのplan由来予測だけNone。実績/PV/load/請求/実機証拠を消さない。

## Failure evidence

初回監査で学習接続/説明/SOC根拠/partial/別日schedule/review bindingを確認。
独立レビューで10件の仕様不足を修正した: status別shape/reason enum、候補field名、raw検証scope、nested totals、validator編集範囲、test名、全24row binding、legacy/time/key path、selected-run mixed hash伝播、fallback自体のbyte上限。
最終統合ではrefresh再bindの呼出しを明記し、table1個5columnsという事実とSOC単一datasetの最小案も再確認した。
最終の独立review結果・確認範囲は [review.md](review.md)。修正済みは文書の不足であり、production表示の不具合ではない。

## CodebaseMemory queries

index_status ready 8,275 nodes / 29,528 edges。workflow供給/DTO・bootstrap・renderer対象の一hop82結果を使用。
partial12件はPS/configで、表示の結論はsource/VM/保存契約を優先。CLIは一時利用のみ、終了後process0。
NEW候補helperをgraphに実在すると仮定せず、17提案targetと既存symbolの存在は文書validatorで区別した。

## Additional files read

sessionの広い探索承認と今回の全表示確認範囲でfrontend6 source/template・Node3 tests、backendの直接adapter/DTO/projection/集計/原本/SQLschemaを確認した。
親はwarnings分類、現行legacy source集合、式/key path、static cache/保護文書、関連CLIの比較窓を確認。
追加scopeは [index source paths](index.json)、変更可能/禁止は [boundaries](boundaries.json) のsymbol/variablesで限定する。

## Files modified

今回のproduction source/test変更は0。追加は本dashboard_followup文書、machine inventory/index/boundaries、文書validator。
元パッケージREADME/implementation-statusに導線を追加。Gitignored artifactへquery/validation/scriptを保存。
以前のNEW100実装の作業ツリー差分は維持し、今回の表示実装を完了したとしない。

## Residual risks

- 7保持/別残件: gap線の補間/固定軸の条件付きclip、history fallbackのhourly上書き、見出し番号、寿命モデル表現差、warning表示上限/実機確認、不存在の設定pane。期間cost/life欠測0の扱いも今回hourly修正へ一括拡大しない。
- SQLの保存SOC/run未対応は表示で未連携とする。機能parityの新storage実装は別設計。
- 実browserの全描画、最新本番asset/DB、actual cloud競合/bytes/cacheは未実証。
- 新表示を実装してもG06の経済原本不足・G07 server競合/性能・G08導入承認は残る。
- 関連CLIの全日final24と07〜22actual PV比較窓差は別scope。今回のdashboard packageでreport計算を変更しない。

## Recommended next action

ユーザーの実装指示後、D01のモデル要約生成から着手する。

# 新100％ 実装・検証の状況

2026-10-12。session限定の無制限ファイル変更承認に基づく実装。
merge・本番操作はG08の別境界。ローカルの証拠を本番競合実証へ読み替えない。

全stepのローカル実装・回帰は完了。canonical品質gateは1,167 passed / 1 skippedとlint/type/依存境界/JS/security PASS。
skipはopt-in外部Open-Meteo試験。観測・予報APIは別のlive GET smokeで確認した。
G04/G06/G07/G08は実証範囲・不足原本・導入承認の残件としてOPENを維持する。

## 実装経路

1. 既存context/7日消費履歴/料金期間を維持し、消費予測を生成。
2. `clear_ghi_update.load_calibration_for_context` が既存archive配下のcurrentを読み、必要時に観測を更新。
   初回だけ最大52,608実績行、衛星取得最大1,096日。共通60秒の協調期限と有限SDK timeoutを使用。
3. `clear_ghi_observations` は実績00/30ペアと衛星GHI・日照秒を認定。認定は高度10度以上の主要帯、学習は認定済み日の05〜17時13区間。
   保存原本hashと本当に取得を完了したavailabilityを保持し、過去の取得時刻を遡って作らない。
4. `ClearGhiStore` は既存bucket/prefixまたはlocal directoryでimmutable原本/pack/stateを先行保存し、currentをCAS。
   同じ原本は再送せず、原本に変化がなければ再fit・新hash作成も行わない。
5. `clear_ghi_adapter` は消費補正だけを一回使用し、日射の原timestampを一度だけ前区間へ整列。
   PVは `max(0,C)*min(F,Q)` 一回、年周期1次。旧PV・reshape・残差・PV温度補正の再適用はない。
6. 最終PV24/load24から07〜22・朝07〜09・昼10〜15の値を夜間計算へ渡し、既存SOC最適化を実行。
7. `result_builder` は唯一のfull traceと既存計画を生成し、既存path overrideでrun専用原本を保存。
8. plannerはimmutable archive→control採用CAS→forecast/parameter投影→画面assetの順で公開。
   採用されなかった旧候補によるmutable投影を禁止。

## 実行証拠の入口

- [詳細報告](../../../../artifacts/analysis/pv-new100-implementation-20261011/report.md)
- [provider smoke](../../../../artifacts/analysis/pv-new100-implementation-20261011/provider-smoke.safe.json): 実GETのみ。観測48行、予報/過去予報各25日射行、気象各24行、next00あり。
- [local calibration smoke](../../../../artifacts/analysis/pv-new100-implementation-20261011/calibration-smoke.safe.json): 保存実績8,393行と実衛星応答から17晴天日/221区間を学習。再取得でhash不変。書込はlocalのみ。
- [gate一覧](gates.json): prospective運用の成立、historical availability、実cloud、導入承認を分離。

ローカル観測履歴は研究37日の凍結入力と同一集合ではない。17日を37日評価の学習集合として流用しない。

## 設計IDと実試験の対応

| step / 設計ID | 主な実試験ファイル・条件 |
| --- | --- |
| S00 INPUT-001..004 | `test_pv_clear_ghi.py`: 対象日/availability/原本訂正/非有限/shift。`test_clear_ghi_runtime.py`: 実績ペア、原本取得時刻、単位、同hash維持、欠測、same-response tail |
| S01 PV-001..006 | `test_pv_clear_ghi.py`: 幾何fixture、うるう年、周期basis、pulse、13区間fit、rank/condition、欠損修復、Q0、hash/frozen型 |
| S02 CAL-* | `test_clear_ghi_calibration_io.py` / `test_clear_ghi_store.py`: local原子保存、CAS/中断/readback、期限、stateSHA選択復旧、訂正・退役、旧参照保持 |
| S03 LOAD-* | `test_load_correction_equivalence.py`: disabled/欠測/thermal等の消費同値、旧全result10case golden、取得回数、PV callback0 |
| S04 PLAN-001..005 | `test_energy_model_runtime.py` stage順序 / `test_clear_ghi_runtime.py`: 窓集計/気温0/容量入力、cost有無で実optimizer→raw計画保存、旧PV callback0、failed current/refresh |
| S04 PLAN-006 / S06 ECON-* | 同じ凍結原本による経済replay。37日PV一因子と3日8組を区別し、37日の未保存scenarioを捏造しない。G06参照 |
| S05 OUTPUT-* | `test_clear_ghi_runtime.py`: trace/原本hash/同PV出力、旧constructorは既存runtime。SOC17/容量/4dpは次行の実SDK投影試験 |
| S07 CONTROL-* | `test_control_publication_provenance.py` / `test_separated_runtime.py` / `test_plan_snapshot_recovery.py`: 私有原本/採用順序/旧入力/同版retry/採用後失敗・既存control契約 |
| S08 PROJ-* | `test_forecast_projection_generation.py`: 実Firestore SDKをローカルtransportで使用、同世代24値/容量/SOC17/26write、既存parameters移行・retry増分0 |
| S09 DASH-* | `test_dashboard_publication_generation.py` / 既存dashboard tests: source revision/旧候補/修正/競合/8KiB、実GCS SDK request、artifactありDB0/ETag/lazy history |
| S10 BACKUP-* | `test_pv_clear_ghi_backup_roundtrip.py` / 既存Drive回帰: raw bytes/SHA/full trace復元、改竄/欠落拒否、新schema/collectionなし |

上表は実装されたfixtureへの対応表。設計表の個々の仮称関数が全て存在することや、全条件を実cloudで実証したことは意味しない。
最新の全体チェック件数は詳細報告のcanonical quality gate結果を参照する。

## 読取・保存の境界

browser→既存HTTP→既存artifact service→GCS/local gzipのGET経路は同じ。JS/endpoint/cacheを変更していない。
初期request数、history lazy、ETag、artifactありDB0の既存回帰を維持する。
producerにだけFirestore source revisionのbefore/after読取とCAS再確認を追加。
native update_time・doc集合hash・countをsource scopeごとに記録、cost historyは最大3,660＋sentinel、metadata圧縮8KiB超は旧asset保持。
historyは同generationのpayload/metadataを読み、通常incrementalはretained digestと境界refreshで維持する。
範囲外の過去訂正とlegacy初回だけ既存bounded full rebuildへ進む。通常新planで履歴が停止しないことを回帰確認済み。
実DBとGCS間の完全な原子性、同数のdelete+insertの個別識別、local複数writerの分散CASは保証しない。
本番のbackend reads・圧縮bytes・cache hitsはG07/G08の実測残件。

## 未完の意味

G04は実DB訂正とasset公開の並行性が未証明。local/実SDK transport試験だけで完全原子性を保証しない。
G06の過去取得時刻と全37日の旧scenario原本は現存資料から証明できない。確認不能を推測値で埋めない。
G07の実Firestore/GCS競合・中断はSDK transport試験と別で、production測定が残る。
G08はmerge/deploy/live検証の承認と実行。ファイル変更承認だけで完了扱いしない。
新依存、恒久mode flag、新envキー、新collection、新公開HTTP契約は追加していない。
通常warm校正の範囲外で古い実績を訂正した場合は明示的な校正再構築が必要。自動全履歴走査や古い訂正の自動反映は行わない。

## 2026-10-12 サンプル数表示の追加調査

旧表の7は直近7完備日の有効日別ペア数で、更新の累計ではない。保存10/9→10/10計画は7を保ち学習日・係数を更新していた。
新モデルは原本にtraining_dates/training_samplesを保存するが、既存diagnostics extractor/JSはそのfieldを表示しない。
新学習日数・区間数・係数版の画面結線は未実装。S09/G04のローカル表示残件として扱う。
旧7日履歴定数の変更で解決せず、producer側の小さな要約と表示単位を接続する。
[追加調査報告](../../../../artifacts/analysis/pv-new100-implementation-20261011/sample-count-report.md)。
この追加調査の全品質gateは1,168 passed / 1 skipped、局所診断3 passed。表示欠落の再現成功を表示機能PASSとしない。

全表示の再監査・追加実装詳細は [dashboard_followup](dashboard_followup/README.md) に分離した。
表示81項目・18観点・7供給gapの役割、D01〜D05、具体symbol/変数/禁止範囲、PLANNED testsを参照する。
2026-10-12の追加指示でD01〜D06の実装を完了。学習要約、説明式、保存/参考SOC、同日別run review、全8グラフの軸を接続した。元データ修復はCSV取込・日計保存側で行い、dashboardで補完/保存する処理は追加しない。
最終canonicalは1,297 passed/1 skipped、追加Node3群とChrome合成表示PASS。[追加実装結果](dashboard_followup/implementation-report.md)を正本とする。本番データ修復/本番デプロイは未実施、G04/G06/G07/G08は保持。

# 追加仕様自身のsymbol・変数・書込責任の再監査

2026-10-12、source基準1dd8786。今回も文書のみ。各stage本文が実装の正本、ここは競合理由・単純化判断・再確認の索引。実装/実server/本番PASSとは区別する。

## 1. 追加したものも対象にする検査手順

1. 変更symbolの定義/同名、直接caller/callee、署名・戻り型、既存default、import方向を確認する。
2. 追加変数の作成元/所有者/寿命/更新者/参照者を固定。同じ成功判定・設定・原本を二つのmutable stateへ複製しない。
3. 同じ入力の二度計算、同じassetへの二重公開、時間/世代の混成、begin/commit/rollbackと内部calleeの期限漏れを別々に確認する。
4. 正常→拒否→ACK不明→採用後失敗→resumeの順に追跡。既知の採用原本から未完処理だけ復旧し、状態が不明なら停止する。
5. 問題時は唯一ownerと既存primitiveで解消できるかを先に比較。flag/別context/別frameworkを先に足さない。
6. 本文・index・boundaries・テスト/PASSを同時に更新し、変更した仕様も再レビュー。未実証はowner/必要証拠/停止条件を割り当てる。

## 2. 今回の指摘と採択した最小構造

| ID | 競合・不足 | 採択した構造 / 正本 |
| --- | --- | --- |
| C01 | parityと独立原本CLIで同じ入力を別々に取得できる | R01: parity.mainだけがCLI。新moduleはcollector/pure関数だけ、NEW main撤回 |
| C02 | SQLと画面の日計mergeが共有され、共通parityだけでは誤りを共有する | R01: 保存battery_dailyの各NULLだけ原本値と独立照合、nonnullは保存値を保持 |
| C03 | window/partition署名・coverage・exitが未定義 | R01: dict＋既存mainの一coordinator、署名/結果schema/exit0,1,2を唯一正本へ固定 |
| C04 | prepareがpolicy/fixedendをpinするのにcfg/end入力がない | R02: cfg/cost_end_tsを明示keyword、cfgは既存PipelineConfig、二度目env読取0 |
| C05 | R03校正fitと通常Dのfit/storeが二重owner | R03/R05: 通常plannerだけが校正更新。corrected_dates/recheck_dates/旧日reader/校正更新stageを撤回 |
| C06 | 9月取得のProcess envが親の通常予測へ漏れる余地 | R02: 月取得を子pwshへ隔離、parent.env/context不変。新env key0 |
| C07 | before/after full publisherがbootstrapとhistoryを重複更新する | R03/R05: 中間はraw/daily/cost read-backだけ。Dはcurrent bootstrap、Eは履歴専用一関数。SnapshotPhase/4phase journal撤回 |
| C08 | R03とR05がDriveを重複起動する余地 | R05だけがbackup実行owner、R03はprivate bundle/receipt検査だけ |
| C09 | current transactionだけの期限対策ではimmutable snapshot/readmodelが無期限 | R04: snapshotへ同deadline、forecast/parameter/readmodelは一helperで3/3/1。独立SDK retry loop0 |
| C10 | 本番imageにpytestが無く、controlにFirestore/Storageが無い | R04: nativeproofはplanner同digest/stdlib direct-caseだけ。host pytest維持、production依存追加0 |
| C11 | fixture入力経路とbuild resume proof資格がない | R05: 一回fixturePath→canonical SHA、digest保存→nativeproof。proof無し旧successはskip不可 |
| C12 | 採用後E/F失敗のresumeがDを再生成する | R05: exact execution/identity log/native read-backで唯一採用receipt。同raw投影復旧、生成/archive/CAS再実行0 |
| C13 | default昨日のparityでは新targetを評価しない | R05: 同SQLへの正規再同期→receipt.target_date明示、NEW>=1とrun/SHA一致を要求 |
| C14 | 本文と索引のbuild/テスト/constant許可がずれる | index/boundariesを同期。未実装historical helper/test撤回、raw上限は既存関数内localだけ |
| C15 | atomic create13で既存35の競合も防止したと読める | R02: 原子的範囲は13だけ。35は前後検出→committed_drift停止、無意味な35再write/lease追加0 |

旧案との差は追加flagではなくownerの削減。一回運用CLIのstageはprepare/source/daily/cost/history/verifyだけ。既存通常planner入力と別の固定計算contextを運搬する案は、配送/二度読取が増えるので採らない。

## 3. クラス・変数・更新者の対応

| symbol / 変数 | 所有者・寿命・参照境界 |
| --- | --- |
| 既存PipelineConfig / cfg.db_path,data_backend,artifacts_dir,料金policy | repair mainが一回取得→prepare/派生へ渡す。helper from_env0、fixedend/policy SHAはimmutable manifest |
| prepare_source_repair / candidate_sha256,prepared_at,cost_end_ts | R02がimmutable準備を所有、applyは再生成しない。journal更新で承認hashを変えない |
| repair journal / stages,status,receipt_sha256 | repair Pythonだけが書く。release PowerShellは修復status/unitを書き換えずhashを参照 |
| forecast-intent/forecast-execution/adopted-forecast.json | R05が起動と採用の証拠を所有。採用bodyは一箇所、両stateはSHA参照だけ。exactexecution未知はSTOP |
| 既存PublishedPlan / selected_plan,raw SHA,control version | immutable採用receiptのversion.payloadから取得。生成/採用/表示時刻を分け、latest同日で置換しない |
| 既存ClearGhiStore / pack/state/actual/certification,current | Dの通常updaterだけが更新。R03は観測hash/認証/availabilityを確認、planからcurrent復元0 |
| _run_bounded_projection_transaction / deadline,max_attempts,attempt,transaction_id | forecast_persistenceの一helper、caller既存3/3/1。deadline再設定/同SDK global monkeypatch0 |
| persist_forecast_snapshots / deadline,immutable IDs | 同projection予算を受領。通常None default/first provenance不変、ACK不明を全対象read-back |
| write_dashboard_history_snapshot / published_plan,deadline,sourceepoch,generation | history専用writer。bootstrapはメモリ内検査、bootstrap PUT0。既存CAS/validator/default publisherを変更しない |
| _read_dashboard_source_epoch / monitoring_max_documents,cursor,digest | 内部local52,608、他collection上限維持。global/env変数を追加しない |
| AdapterProofFixturePath/SHA / image_digests | R05 buildのprivate入力identity。planner同digestだけnativeproof、stateへproof前に保存 |
| BackendProofFixture/RpcRecorder/RaceBarrier | test-only、production class追加0。native runnerはpytest import0、case関数を直接呼ぶ |
| WindowResult/DayResult / coverage,common_parity_errors | R01 pure dict結果、公開要約のみ。原本bytes/URI/秘密識別子を出さず6dataset ANDを一回判定 |

型/default/署名の実装正本は[R01](steps/R01-validation-contract.md)、[R02](steps/R02-source-repair.md)、[R03](steps/R03-derived-history.md)、[R04](steps/R04-adapter-proof.md)、[R05](steps/R05-release-acceptance.md)。同名helper/予定NEW pathの既存衝突は担当が直接確認し0。既存module間の低信頼graph edgeはsourceで確認、意図したbackend境界を統合しない。

## 4. 残る限界と停止条件

- 修復日が通常contextの現在＋前請求期間外なら、この限定packetではSTOP。将来の任意旧日訂正を自動処理できるという約束を撤回する。未来学習・欠損生成・旧予測rewriteは0。
- source→derived→GCSは横断atomicではない。現在35競合、古い訂正/削除、source後退、late mutationは既存native guard/read-backで検出してFAIL。未知writerを排除したと宣言しない。
- exact executionのidentityログ前crash/候補複数はUNKNOWN。current/time/commitだけの推測採用と再planner禁止。
- history builder内部にはdeadline APIがなく、前後検査だけで内部DB pending requestを強制停止できない。実server elapsedで検証し、期限未証明はG07 OPEN。新flagやtimeout無視で合格させない。
- isolated OCI実行基盤/権限、auth refresh、実server競合、旧digest互換、原本archive bytes、本番画面/実機は未実証。

## 5. 検査記録

段階別source照合を既存担当へ委任し、親は同探索を繰り返さず契約を統合。CodebaseMemoryは直列on-demand、ready既存indexからsource/docs差分を更新して9029nodes/31353edges。定義9symbol/直接caller26edgeを取得（backend同名誤解決は採用しない）。persistence=falseでも共有artifact差分を検出したため、この操作が生成した2ファイルだけを元HEADへ復元。daemon/process残留0。
文書・索引・保護SHA検査と最終独立再確認の結果は[review](review.md)へ記録。新production/test実装、本番書込、pytest/nativeproof/Cloud実行は今回0。

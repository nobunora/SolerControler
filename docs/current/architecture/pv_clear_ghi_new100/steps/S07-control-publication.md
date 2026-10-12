# S07 control公開と不変原本

依存: S06。関連gate: G05。coverage: R07/R09/R11/R15、C05trace/C08save-before-control/C09freshness/C12protected-vars/C14logs/C16gates。

## 入口・参照範囲

- S06の固定raw・trace・経済差の受入証拠を受け取る。文書完備だけでは実装を開始しない。
- AIルーティング: 呼出し地図/保護影響/競合判定はCodex（上位レビュー担当）、限定patchはLuna。G05未完ならLunaは停止し対象lockの限定承認へ戻す。
- 正本は [共通契約](../contracts.md)、[公開契約](../publication-contracts.md)、[gates](../gates.json)。次段のmutable forecast/asset変更を本段へ混ぜない。
- CodebaseMemoryは親が確認済みready indexの `archive_plan_snapshot → _update_plan_read_models`、`planner.main → forecast_job.main/FirestorePlanStore.publish` の一hopだけを経路図に使用。sourceで順序を確認する。

## 変更対象

| 実ファイル・完全修飾symbol | 限定差分 |
| --- | --- |
| [forecast_job.py](../../../../../app/runtime/forecast_job.py) `app.runtime.forecast_job.main` | recovery/CSV/db/energy生成をNEW入口へ抽出、旧mainの既定persist/任意assetは保持 |
| 同上 `app.runtime.forecast_job.generate_forecast_plan` **NEW** | [公開契約の署名](../publication-contracts.md#new入口の署名失敗境界)。Path返却、mutable forecast保存0 |
| [plan_snapshot.py](../../../../../app/backup/plan_snapshot.py) `app.backup.plan_snapshot.archive_plan_snapshot` | raw一回読取→NEW immutable入口→既存projectionという旧wrapper互換 |
| 同上 `archive_plan_original` / `project_plan_read_models` **NEW** | 同raw保存とprojectionを分離、署名/例外/receiptは公開契約に従う |
| [planner.py](../../../../../app/separated_runtime/planner.py) `app.separated_runtime.planner.main` | private生成→bytes固定→immutable→control CAS。成功後のprojection呼出しはS08へ引継ぎ |
| [plan_store.py](../../../../../app/separated_runtime/plan_store.py) `app.separated_runtime.plan_store.FirestorePlanStore.publish` | 既存再読/CAS loop内のprovenance比較だけ追加 |

## 変更禁止

| symbol・実source anchor | 保持する変数/コード境界 |
| --- | --- |
| `FirestorePlanStore.publish`: `for _ in range(3):` | `pointer/incoming_time/old_time/precondition/writes`。3回、`timeout=(10, 60), max_allowed_time=60`、immutable/current一commit。RESTに存在しないverify write/別doc preconditionを作らない |
| 同上 `old_time > incoming_time or (old_time == incoming_time and old.version_id != plan.version_id)` | 古候補/同時刻異版拒否を削除・緩和しない。既存版/document budgetも保持 |
| `planner.main`: `# HISTORICAL_FAILURE_LOCK: the planner owns BOTH the exact immutable` | `path/plan/client`。原本＋latest/dayのplanner所有を維持。G05は移動する呼出し順と追加回帰を限定承認する |
| NEW `forecast_job.generate_forecast_plan`（旧mainから移動）: `# HISTORICAL_FAILURE_LOCK: recover prior saved forecasts before refreshing` | `target_date/recovery/plan_path`、先行recovery、31日境界、各 `_run` の `timeout_seconds=240`。旧mainに新skip flagを追加しない |
| `plan_snapshot._plan_order/_update_plan_read_models`: `_plan_order(previous) >= _plan_order(doc)` | `doc/raw/refs/existing/previous/value`。既存transaction読取先行・単調更新・`max_attempts=1`を再利用、全面再実装しない |

保護文書 [historical regions](../../../agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md) と予想履歴障害根拠 `93e8c3c/894e9f3/7899423/76f1e91` を対象diff前に再確認する。23/03/07のcontroller、lease、SOC API、env/depsは対象外。

## クラス・変数差分

| 所在 | 既存/NEW・変数 | 型/意味/制約 |
| --- | --- | --- |
| `planner.main` | 既存 `path/plan/rate/client`、NEW local `raw/archived_doc/receipt` | `bytes/dict/dict`。PublishedPlan.sha256と同raw、rateの既存算出を保持。新共有stateクラス不要 |
| `archive_plan_original` NEW | `raw/plan/day/sha/decision_id/doc` | `raw`から一度parse。生成原本のhashと初回由来を保持 |
| `project_plan_read_models` NEW | `archived_doc/raw` | docのhash/date/generated_atとrawを再検証して既存helperへ渡す |
| `FirestorePlanStore.publish` | 既存 `old/plan/precondition`、NEW provenance比較local | traceの確定keysのみ使用。不明はnull/理由、新profile pointer/leaseを仮定しない |
| `forecast_job.generate_forecast_plan` NEW | `target_date/artifacts_dir/plan_path/client/recovery` | 既存生成localを移動。run-private Path、検証不能はValueError、旧command失敗は伝播 |

## 実装手順・分岐

1. G05に対象lock・障害・差分・先行回帰・明示承認を記録する。未承認なら変更0で停止する。
2. 生成専用入口を作る。`publish_read_models=False` がmutable保存を止めると誤解せず、旧mainは既定の保存動作を保つ。
3. private path→raw一回読取→PublishedPlan固定。同run retryは同bytes/hash/generated_at、共有path競合なら先に排他条件を確認する。
4. immutable候補をNEW入口へ保存。`if_generation_match=0`、gzip read-back、`decision.create`/AlreadyExists照合を保持。receiptの`status="verified"`をbooleanへ変えない。
5. control pointer再読内にprovider/係数訓練as_of比較を入れる。既知→不明・同site/basis後退を拒否、不明同士の鮮度限界はログへ残す。
6. immutable/control失敗は先へ進まない。control拒否ならcandidate projection0、生成証拠は残る。
7. control成功の同rawをS08へ渡す。CAS後projection失敗をcontrol失敗と扱わず採用版/段階を記録する。

## テスト・PASS条件

実行はcode-quality-auditと必須品質gate後。NEW試験は追加済み・ローカル実行済み。実server競合はG07 OPEN。

| ID・状態 | fixture/条件 | command・厳密PASS |
| --- | --- | --- |
| CONTROL-01 EXISTING | fake session、古生成/同時刻異版/current競合 | `python -m pytest tests/test_separated_runtime.py -q`。atomic publish/再読/23・07 DB非依存を維持、旧順序assertの更新はG05後のみ |
| CONTROL-02 EXISTING | immutable version/retry/原本改竄/古retry | `python -m pytest tests/test_plan_snapshot_recovery.py -q`。hash/read-back/初回起源/単調latest、archive失敗・期限で制御継続 |
| CONTROL-03 NEW LOCAL | `test_control_publication_provenance.py` 新generated＋古issued、既知→不明、古as_of、CAS再読 | `python -m pytest tests/test_control_publication_provenance.py -q`。CAS読取内比較、拒否commit0、再読後旧候補拒否。実server3回競合は未実証 |
| CONTROL-04 NEW LOCAL | `test_forecast_projection_generation.py` private/shared/hash、control拒否、採用後projection故障 | `python -m pytest tests/test_forecast_projection_generation.py -q`。採用前mutable候補0、原本混合0、control再publish0。並行server/crashはG07未実証 |

失敗testをskip/期待値緩和でPASSにしない。fake PASSは実adapter raceの証明ではない。

## 中断条件

- G05未承認、traceのsite/model_id/basis/issued/as_of binding不足、生成pathを一runへ固定不能。PV profileはpublication-contractsのmodel_id対応を使用。
- 既存helperの全面変更、snapshot backend/new env/deps、controllerの予報/係数取得が必要になった。
- 旧main/recovery/23・07独立性の回帰失敗、raw/hash不一致、CAS後成功を再実行しようとした。

## 出口・引継ぎ

S07 PASSは固定raw/receipt/archived_doc/control版・採用成功証拠と拒否fixture、変更diff/品質結果をS08へ渡す。最小handoffは採用済み同bytesのprojection境界確認。未完は理由と必要symbolだけ返す。

2026-10-12限定承認でgenerator/private原本/archive/control所有を実装。保護anchorは生成専用呼出し・既存CAS・採用後projectionへ更新し、時刻/lease/controller APIとlockは保持した。ローカル証拠はG05、実adapter未完はG07へ渡す。

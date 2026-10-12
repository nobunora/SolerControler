# 2026-10-12 実装・実サーバー受入記録

設計checkpointは7b9983c。以下は設計後の実装に合わせた追加境界と実施状況。実サーバー証拠、デプロイ、SOC SETが未完の段階では完了と記録しない。

## 追加した変更境界

| Stage | Source / symbol / variable | 変更範囲・所有者 | 変更禁止 |
|---|---|---|---|
| R01 | validate_forecast_display_evidence::load_forecast_evidence_for_window、bundle.published_plan_document | pinしたversion文書を一度だけGETしPublishedPlanのversion/SHAで原本を独立検査。共通SQL/Firestore parityとAND | compare_rows、IGNORED_FIELDS、NULLを一致へ偽装する処理 |
| R02/R03 | monitoring_repair prepare / native monthly source revisions / calibration preimage | immutable manifestへ件数・ID・native revision digestとcurrent rootをpin。daily/cost前のcandidate13以外のsource変更を停止。prepare入口で既存ForecastSettings/array構成を一度だけ取得 | native旧35件の更新、月scope拡大、calibration pointer不明を不存在扱い |
| R03 | clear_ghi_observations::observed_day_records event、checked_actual_sha256_by_date | completeな5–17時の26実測を既存actual_source形式でSHA化し観測eventへ記録。repairがraw26 SHAとの一致を必須確認。非晴天も入力検査可能 | 認証閾値、晴天選択、校正係数、actual/certification原本の意味 |
| R05 | planner::write_forecast_intent/capture_adopted_forecast_receipt/recover_adopted_forecast_projection | 同じ原本・採用versionを使う限定関数。PSがprivate receiptを所有しPython repair journalはSHA参照。projection-only復旧 | 再計算、control CASの二回実行、receipt内容の別stateへの複製 |
| R04/R05 | run_server_adapter_proof_from_env.ps1、tests/g07 | 同planner候補digestの本番専用Job。一時image/command/args変更、固定SHA/generationのrunner bundle、exact execution、元Job仕様復元 | 新infra、IAM/secret/env/retry/Scheduler変更、実計画/実モデル/機器設定への書込 |
| R06 | soc_setting_probe、run_kpnet_soc_setting_probe_from_env.ps1 | 部署完了と同commitを条件に本番JobからSOC一項目を30/50へ変更・60秒保持・元設定復元 | 通常23/03/07、mode-only往復、全充放電窓、その他設定、ACK不明後の盲目write |

production class追加0。保存・原本検査・SDK試験・機器試験は各既存責任境界で実行する。修復とforecastの共有state framework、dashboard補間、PV係数二重適用は追加しない。

R07は10/13の明示承認による限定例外。workflow::run_kpnet_mode_only_profileのexplicit deadline判定1行だけを絶対時刻比較へ変更し、240+60秒予算・min・io_deadline・時刻所有権・全設定保持・lockを維持する。修正前2failを再現、修正後の関連/保護回帰97 PASS。03第一試行の例外発生場所は同guardであるが、当時のmonotonic値が未記録なので本番原因の断定はしない。詳細はR07を参照。

最終追加sourceを含む10/13の全体pre-releaseは1498 passed / 1 skipped。R01必要scope115、native terminal型/NEW/時間境界51、R07関連保護97のfocused回帰もPASS。全変更を正式commitへ固定後のproduction gateと実server native/forecast/repair/device受入は別に記録する。変更境界25群・保護13群の実装SHA、対象source存在、局所リンクは全一致。元本・履歴不足をローカルPASSへ読み替えない。

変更境界のimplemented_source_sha256はCRLFをLFへ正規化したsource textのSHA-256。Windows checkoutの改行変換で別実装と判定しない。設計基準source_sha256は当時のレビューcopyを示す値として維持し、実装整合は正規化SHAとGit差分で確認する。

## 検証と残条件

全体静的検査（Ruff、Import Linter3契約、mypy、tracked JS Oxlint/node構文、compileall）と全体pre-releaseはPASS（1408 passed / 1 skipped、security PASS）。R06と既存mode-onlyの17回帰PASS、R00/R01の66回帰PASS。準備段階の実サーバーGETにより、9/20のcandidate13件・既存35件、料金範囲9/1–10/12、校正currentのnative不存在を証明した。まだsource writeは0。最後の変更を含む正式preflight gate、候補imageのnative proof、元13区間の本番修復、採用D/readmodel/history/Drive証拠、完全部署後の実SOC設定往復は個別のruntime receiptで確認する。

G06：旧予報に当時のNEW100入力provenanceや全旧scenarioが存在しなかった範囲は復元済みとはしない。今朝の保存原本には旧optimizer全scenarioがあり、PV比較は可能だがNEW100 radiation receiptは存在しないため、時間意味から再構築したcounterfactualとして区別する。

本番事前gateの初回はR01原本collectorが共有60秒を使い切り、after読取でDeadlineExceededとなった。exact refsをget_allへまとめ、同文書集合・before/after・60秒を保持して43.408秒で完了した。原本未取得は最終21日（当初19日と別世代原本2日）で、取得済みと扱わない。10/4・10/5のnight値差は別run原本の比較だったため、数値修復候補は不適格とし、本番書込を行わない。

2026-10-13の導入指示に合わせR01で必要scopeと全履歴scopeを分離した。純旧schema・容量/SOC全点非提供・native全取得/同revision・構造正常・原本不足だけの21日を非必須とし、全履歴INCONCLUSIVEを保持する。実31日唯一CLIはcommon/required PASS・exit0、容量48点/SOC34点を原本検証、1481 logical reads/2233929 bytes。NEW0/0は導入後受入とはしない。R05はpost-DでNEW>=1・全NEW原本/容量24/SOC17・採用identity bindingを強制する。R01 focused115、R05/R06 safety focused48 PASS。CLI新flag0、scheduled制御源変更0。正式gateの再実行が必要で、初回failedを手動successへ変更しない。

10/13朝02:34 JSTの旧原本をread-onlyで保存した。旧07目標100%、容量8.634371289935377 kWh。03 Job第一試行は強制開始直前のmode-only時間予算判定でforced_failureとなり、standby処理を実行した。03:10のモバイルAPI実測SOC0%、測定時刻03:10:13・取得03:10:21。ジョブ再試行中のため採用予測・機器への競合変更を開始していない。原因とterminal evidenceの確認前にこの制御を正常と記録しない。

今朝の原本02:32 JSTの旧目標7%は再現した。費用の0.00441円差は保存hourly値の4桁丸めによる範囲。旧NightChargeInputsのPV override 8.4839kWhと、cost optimizerが最終比較に使った13.9030kWhは異なる段階の値であり、経済比較では後者を使う。

凍結済みの実装用認証原本packは17晴天日/221時間、研究用21晴天日とは収録範囲が異なる。このpackは当朝前に完成していた。研究の21日収録を当時本番採用済みとみなさず、実サーバーDが採用するtraining_dates/actual/certification SHAを別途確認する。

OS/DNSのhard cancellation、実token refreshを発生させた時間予算、過去欠測予報の当時値再現は未検証。通常RPCのdeadlineとelapsed計測を、それらの保証へ拡大解釈しない。

10/13の実native成功応答はfailedCountを省略し、spec.taskCount=1/status.succeededCount=1とcompletionTime/4conditions=Trueを返した。失敗専用JobはfailedCount=1/Completed=False、実行中03はrunningCount=1/Completed=Unknownだった。これを根拠にrun_kpnet_soc_setting_probe_from_env.ps1::Assert-SocNightTerminal、run_server_adapter_proof_from_env.ps1::Assert-ProofTerminal、run_cloud_job_from_env.ps1のforecast receipt branch内::Assert-ForecastReceiptTerminalを限定修正した。各exact describeで正整数taskCountと整数succeededCountの一致を要求し、提供されたfailed/cancelled/pending/running countは厳密整数0だけ受け入れる。省略は全task成功という独立証拠により評価し、missing値を0へ変換しない。bool/string/fraction/null、失敗、未完了、取消を拒否し、最終全成功のretryは拒否理由にしない。既存SOC cycle、latest dry-run、他Slotの分岐とprotected制御sourceは変更禁止のまま差分0。コード・ローカル回帰と本番wrapper実行成功は別証拠とし、今回担当のCloud/機器操作0を維持した。

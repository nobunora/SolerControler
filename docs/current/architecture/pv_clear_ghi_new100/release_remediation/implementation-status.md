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

## 検証と残条件

全体静的検査（Ruff、Import Linter3契約、mypy、tracked JS Oxlint/node構文、compileall）と全体pre-releaseはPASS（1408 passed / 1 skipped、security PASS）。R06と既存mode-onlyの17回帰PASS、R00/R01の66回帰PASS。準備段階の実サーバーGETにより、9/20のcandidate13件・既存35件、料金範囲9/1–10/12、校正currentのnative不存在を証明した。まだsource writeは0。最後の変更を含む正式preflight gate、候補imageのnative proof、元13区間の本番修復、採用D/readmodel/history/Drive証拠、完全部署後の実SOC設定往復は個別のruntime receiptで確認する。

G06：旧予報に当時のNEW100入力provenanceや全旧scenarioが存在しなかった範囲は復元済みとはしない。今朝の保存原本には旧optimizer全scenarioがあり、PV比較は可能だがNEW100 radiation receiptは存在しないため、時間意味から再構築したcounterfactualとして区別する。

今朝の原本02:32 JSTの旧目標7%は再現した。費用の0.00441円差は保存hourly値の4桁丸めによる範囲。旧NightChargeInputsのPV override 8.4839kWhと、cost optimizerが最終比較に使った13.9030kWhは異なる段階の値であり、経済比較では後者を使う。

凍結済みの実装用認証原本packは17晴天日/221時間、研究用21晴天日とは収録範囲が異なる。このpackは当朝前に完成していた。研究の21日収録を当時本番採用済みとみなさず、実サーバーDが採用するtraining_dates/actual/certification SHAを別途確認する。

OS/DNSのhard cancellation、実token refreshを発生させた時間予算、過去欠測予報の当時値再現は未検証。通常RPCのdeadlineとelapsed計測を、それらの保証へ拡大解釈しない。

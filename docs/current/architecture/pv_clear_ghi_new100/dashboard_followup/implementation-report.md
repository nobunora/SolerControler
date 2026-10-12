# ダッシュボード追加実装・元データ修復の結果

2026-10-12。D01〜D06のローカル実装と検証は **PASS**。本番デプロイ・本番保存データの修復は未実施。
基準HEADは `d401b15de199ff8f1215935d01080a67a4c95266`。前段NEW100の未commit差分を保持し、その上に追加した。

## 変更概要

| step | 完了内容 | 主な証拠 |
| --- | --- | --- |
| D01 | 固定した採用原本から4KiB以内のモデル要約。原本検証成功後だけ同版へ結線 | diagnostics/publication focused 99件PASS |
| D02 | モデル・係数4個・晴天日数・時間区間数・期間・取得/生成時刻の表示。同日同runが確認できない場合は未確認 | model display Node PASS |
| D03 | 保存SOC17点と参考/実測を区別。有効0と欠測、全日合計と取得済み分を区別 | hourly display Node PASS、SOC保護10件PASS |
| D04 | 選択済み予測と計画原本の由来照合。既知不一致の計画由来予想だけ未確認。私用markerを公開前に除外 | backend focused136件PASS、new37件を含む |
| D05 | 全表示81項目、8グラフ24系列、異常/旧版/部分取得/読取性能の回帰。43DOM（旧37＋6） | 全体ゲート・追加Node3群・ローカルChrome |
| D06 | CSV同期と日計保存で元データを修復。元実績がunchangedでも供給日の保存日計を再生成 | 元データfocused44件PASS（新31件＋既存13件） |

## 元データ修復の境界

ユーザーの「元データを修復し、ダッシュボードでの補完を行わない」という訂正を採用した。
修復経路はCSV→monitoring_samples→dashboard_daily_metricsの保存側。ダッシュボードの新しい復元fallback/補間/保存経路は追加していない。

- 欠落・誤集計した日計は保存済み監視原本から再生成する。同じCSVの再試行でも、原本の再書込・料金再計算をせず修復できる。
- 同じ取込runの再試行でも、日計の修復だけを行う。設定・予測・制御計画の再適用はしない。
- 実測0は0。None・非有限・負値・boolを0として保存しない。元データに値が無い場合は欠測を残す。
- 未観測の日中/夜間買電・夜間充電を0にしない。観測済み0と区別する。
- CSVの対象window（既存4日）または既存の明示FULL_BACKFILLで供給範囲を限定する。恒久flag/env/schemaは増やしていない。
- 欠測は `monitoring_daily_source_incomplete` と日付・件数・有限理由・field名でログに残す。
- 表示側の00/30時刻の時間集約は保存実測の通常集計。2区間が揃わなければNone、予測が無い時刻も保存実測だけを表示する。新しい値は作らない。
- 予測が再現できなければ欠測を維持。既存の明示legacy参考推計は従来由来のまま保持し、当時原本へ昇格しない。

原本CSV・機器側にも観測が無い区間は復元不能。推定値で埋めて「実測が修復済み」と表示することはしない。

## グラフの変更

全8グラフの24系列を対象に、有限表示値とstack正負累積が軸範囲に入るよう上下限を拡張する。SOC軸0〜100%は保持する。
正常な既存固定範囲は維持。保存値のclip、PV再補正、系列/軸の追加はない。
曲線補間の架空peak/負PVと欠測をまたぐ線を防ぐため、tension=0/spanGaps=false。
未知の実測を0にしたdataset代入は表示maskでnullを保つ。料金/容量/寿命の算術モデルは変更していない。

ローカルChromeの合成データで、7.5kWh/h→上限10kWh/h、日別PV実績48kWh→上限80kWh、SOC0〜100%、17日/221区間/4係数、履歴ボタン後のモデル表示を確認。
全8canvasが生成されconsole warning/errorは0。合成入力による表示証拠であり、本番実績の精度/状態の証明ではない。

## 読取・転送と保護境界

- 初期bootstrap1request、historyは明示操作まで0。既存asset GETのDBread0・ETag304・versioned static cache回帰を維持。
- D04のFirestore collection読取は3→3。SQLの追加raw SELECTは0。画面からCSV/API/archiveを取得しない。
- D01の同fixture追加転送はraw1,329 bytes/gzip520 bytes、loader1回で増分0。実本番の圧縮転送・cache hit測定は未実施。
- frontend保護8関数のsource hashは不変。backendのdaily fallback/complete_day/metadata guard/rolling参考推計はAST不変。
- 原本publication validator、CAS/source epoch、06:30目標判定、23/03/07制御、料金・資産・容量・劣化・寿命式、backup契約は変更していない。

## 検証

`code-quality-audit`と固定QUALITY_GATEに従い、Ruff lint、Import Linter3契約、mypy app/scripts213files、全管理下JSのOxlint/構文、compileallをテスト前に実行。

    pwsh -NoProfile -File scripts/pre_release_local.ps1 -SkipInstall

最終結果 **1,297 passed / 1 skipped（55.29秒）**、既存Node3群・security含めPASS。skipは既存opt-in外部試験。
追加Node3群（model/hourly/axis）も明示実行しPASS。これらは既存wrapperに含まれると仮定していない。

初回全体検査では、時間窓テストのfixtureが買電欠測を0と想定して1件失敗した。fixtureを明示実測0へ訂正し、欠測をNoneに保つ別31件の修復試験とともに再確認した。期待値を弱めた修正ではない。

## 独立レビューと残件

D01とD06の独立検証、frontend8保護関数、backend4保護領域を確認。実装で残指摘なし。
18監査観点のうち13を実装対象とし、R10/R12/R13/R14/R16の既存履歴fallback/見出し番号/寿命説明/警告・実機確認/不存在UIは従来の明示残件として保持する。
全既存問題が消えた、という結論ではない。

本番Firestore実SDK・Postgres実DB・実cloud競合・本番データ修復・デプロイは未検証/未実施。G04/G06/G07/G08はこのローカルPASSだけで閉じない。
古いmaterialized historyの欠測は、保存元を修復後、既存23時full_history_rebuildまたは既存の明示再生成で反映する。GETで再生成しない。
本番適用時はcanonical import/deployment wrapperを使い、実保存値と履歴再生成を確認する。今回commit/push/merge/本番操作は行っていない。

[仕様入口](README.md)・[共通契約](contracts.md)・[元データ修復手順](steps/D06-source-repair.md)・[設計レビュー](review.md)

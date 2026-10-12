# D05 全表示・交絡・性能の検証

## 入口・参照範囲

indexの全81表示と18risk、D01〜D04の出口、[contracts](../contracts.md)、[review](../review.md)。
全sourceの再探索を行わず、各表示のfield/renderer/proofへのリンクを使う。

## 変更対象

新しい直接testsだけを追加し、既存testsは変化したoptional表示契約に必要なassertionsのみ追加。
PLANNED test pathsはindexに一意に記載。コード実装前の文書検証だけではruntime PASSにしない。
検証用fixtureは合成NEW、保存legacy、SQL未連携、partial、missing、stale/mixedを区別する。

## 変更禁止

既存test skip/恒久bypass/期待値弱体化、DBschema/依存install、Ruff formatter、control/finance/backup変更は禁止。
本番deploy/Job/import/backup/mergeをこの設計の検証として起動しない。新Cloud writeをlocal fake PASSで承認しない。

## クラス・変数差分

case fieldsはbackend/model/binding/completeness/forecast_date/view_dateで、実行条件のfixtureだけ。
本番mode/flag/envに持ち込まない。測定はrequest count/DBreads/compact UTF8 bytes/gzip bytes/cache behavior。

## 実装手順・分岐

1. D01のschema/型/未確認reasonだけを先に検証する。
2. D02の説明/件数/時刻/旧係数/設定σを検証する。
3. D03の24区間/17保存SOC/23終点/partialactual/欠測0/別日scheduleを検証する。
4. D04の同日別runと金融/actual優先を検証する。
5. 別stageで交絡: old/newモデル、同日複数run、SQL raw identity無、明示history閲覧、画面refresh、period変更、実績遅着、失敗要約を組合せる。
6. R07/R08は追加承認C08/C09で軸包含と実測復元/欠測表示を検証する。R10/R12/R13/R14/R16は既存period/cost/life/history/calculationsを保持確認し別残件に記録する。
7. 全37旧DOM/13見出し/8chart24series/13reviewKPI/7buttons/学習table1個5columnsと追加6DOMをcoverage照合する。不存在の時間別tableを作らない。
8. 初期bootstrap1request、history明示操作までhistory0、assetありDB0、compact<=4KiB、fulltrace transfer0、ETag304/versionedstaticを測る。

## テスト・PASS条件

実装時はcode-quality-audit/QUALITY_GATE/pre_releaseを先に実行し、失敗をtriageしてから直接tests。
既存Node3群＋Python dashboard forecast/review/backend/history/publicationを維持し、最後にcanonical全体gateを実行する。
追加testsはcanonicalに自動で含まれると推測しない。実装後は必須品質check成功を確認して次を明示実行する。

    python -m pytest tests/test_dashboard_new100_diagnostics.py tests/test_dashboard_review_provenance.py -q
    node --check tests/test_dashboard_model_display.js
    node --check tests/test_dashboard_hourly_display.js
    node tests/test_dashboard_model_display.js
    node tests/test_dashboard_hourly_display.js
    node tests/test_dashboard_axis_ranges.js

既存pre_release wrapperの契約やskip条件は変更しない。追加suiteの結果と最後のcanonical結果を両方記録する。
ローカルVM/HTTP harnessと実ブラウザの観測を分け、実機/本番/サーバーrace未検証はINCONCLUSIVE。
全81項目に保持/修正/未連携/別残件の一つがあり、全18riskがD-stepまたは明示除外へ割当てられること。

## 中断条件

undefined field/未割当risk、追加request/read、過去raw rewriting、protected source差分、欠測の0化、同版捏造、未測定を数値推定した場合はPASS停止。

## 出口・引継ぎ

G04へ表示実証と全coverage、G07へ本番read/bytes/cache未測定項目、G08へ具体変更/検証/残件を渡す。
本追加実装だけでG06経済原本不足やG07実server競合をCLOSEDへ変更しない。

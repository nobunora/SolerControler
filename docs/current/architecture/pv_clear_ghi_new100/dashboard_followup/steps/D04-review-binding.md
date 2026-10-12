# D04 日次レビューの由来

## 入口・参照範囲

[contracts C06](../contracts.md)、data-path-auditの日次review/SQL未連携、inventory R11。
既存のenergy_daily/forecast_hourly/読み取ったnight_charge_plansとmetricsだけを利用する。
ユーザー訂正後、元データ修復はD06へ移管。下記の保存実測の通常集約/表示は値を補完する処理ではない。daily回復fallback・新raw buffer/再scanは追加しない。

## 変更対象

| path / symbol | 限定差分 |
| --- | --- |
| app/dashboard/firestore_repository.py::_firestore_forecast_hourly_between | fields allowlist(現601行)に既に保存されているraw_plan_sha256/control_version_id/generated_atを追加。private markerとして保持。新query0 |
| 同file::_snapshot_forecast_rows_between | 既存snapshot候補の同private3identityを保持するだけ。選択順/cutoffは変更しない |
| 同file::_build_firestore_daily_reviews | energy_dailyの選択runと既存取得plan.detail_sha256を照合。known mismatchならplan由来予想fieldだけNone＋理由 |
| app/dashboard/aggregation.py::_build_energy_daily | 表示日毎の選択予測からprivate identityとforecast_available_hour_countを保持。既存数値・選択ルールは変更しない |
| app/dashboard/service.py::assemble_dashboard_slice | review照合完了後、public listからprivate3markerを除く。元raw objectを破壊せず公開fieldを保持 |
| static/dashboard.js::renderDailyReview | plan_forecast_binding/reasonと区間数を説明。None/unknownに0円/0%を代入せず、正常actual/PV/loadを残す |
| 同file::renderWindow | PV/load chartのtooltip/status captionへknown partial日数/legacy区間未知を付記するだけ。Y値・期間集計・料金/寿命は編集禁止 |
| app/dashboard/firestore_repository.py::_firestore_forecast_hourly_between | 既存monitoring readerからPV/flowを忠実表示用の通常集約へ渡す。新daily fallback/新raw scanを追加しない |
| app/dashboard/history_reconstruction.py::firestore_forecast_hourly_with_reconstruction | 実測専用rowをoriginal予測日と誤認しない条件だけ。callback/buffer/schemaは変更しない |
| app/dashboard/service.py::merge_forecast_hourly_actuals と隣接純helper | JST00/30の2slotを揃えactual PV/loadを復元。forecast無のhourも実測専用rowを残し、予測Noneを維持 |
| app/dashboard/sqlite_repository.py::load_sqlite_query_snapshot / app/dashboard/postgres_repository.py::load_postgres_slice | 既存要求範囲のraw readを一回にまとめ、同純helperへ渡す。schema・forecast SELECT/provenanceを変えない |

上記reader symbol名の最終正本はdata-path-auditのfield allowlist参照。schema/newcollection/financial producer変更は禁止。

## 変更禁止

energy_daily forecast PV/loadの選択済み日別ペア、累積料金/売電/資産/容量/SOC計算、reviewの07前06:30実績、complete_day判定を変更しない。
全reviewを欠落扱いにしない。正常なactual cost/実績charge/実測SOCをplan mismatchで隠さない。
private hashを24row公開したり、新public per-date mapを追加したりしない。history/原本を再予測して書き換えない。
history_reconstruction::_metadata_matches_forecast_rowは本体完全不変。callback変更は実測共有だけであり、原本/再構成の生成根拠・日別PV/load正本・06:30 lockは維持する。

## クラス・変数差分

private3scalarは `_raw_plan_sha256` / `_control_version_id` / `_plan_generated_at`。date/runは既存public値を使い複製しない。
review公開localsはplan_forecast_binding=matched / legacy_unknown / mismatched、plan_forecast_binding_reason=None / identity_missing / plan_forecast_mismatch。
PUBLIC追加は日次reviewのこの2fieldsと、energy_daily/reviewのforecast_available_hour_count:int|None。forecast_complete flagは作らない。
scopeはplan由来forecast_day_buy_kwh/forecast_sell_kwh、target_soc_percent/forecast_night_charge_kwhのplan fallbackだけ。metricsの実設定/測定証拠は既存優先を保持。

## 実装手順・分岐

1. 読取済みhourlyのprivate identityを保持し、energy_dailyの既存選択runがあればそのsubsetだけを採用する。日内の別run履歴が存在するだけでmismatchにしない。
2. countは選択run内finite PV/load両方のdistinct hour。選択run不明で複数known runが混ざる場合はmismatched、一known+unknownはlegacy_unknown。区間証拠なしlegacy日計はNone。数値/選択ルールを変更しない。
3. selected-run subsetの全rowが同一known raw hashを持ちplan.detail_sha256へ同値ならmatched。known+missingはlegacy_unknown。UTC時刻だけでraw一致を推測しない。
4. 両方knownで異なるならmismatched。plan由来expected day buy/sell、計画target/夜間充電fallbackをNoneにし、理由を表示する。
5. identity欠落legacyはlegacy_unknown。既存数値を保持し由来未確認と表示する。一致したと捏造しない。
6. actual/metrics/control read-backは独立に維持。単位/全日24と金融07..22の対象範囲を注記する。
7. private markerをDTO前に除去。追加DBquery0、public24row hash複製0をtestで確認する。
8. known count<24は予測部分値としてnote/tooltipへ区間数、Noneはlegacy coverage不明と表示。count0でも既存stored有効0へ変換/上書きしない。
9. unique aggregate markerがNoneになっても、reviewは既存読取済みselected-run hourly内のknown hash集合を再確認する。複数known hashはmismatchedとして伝播させ、新flag/queryを作らない。
10. 「48件揃った完全日」の注記は既存review.complete_dayがtrueの時だけ。dateがあるだけで完全と宣言しない。backendのcomplete_day判定本体は保持する。
11. 既存要求windowの保存実測を通常集約する。有効な00/30両slotのfieldだけhourlyに合計し、forecast無でもactual専用rowを残す。補間・前値保持は行わない。
12. 日計欠測の補完をdashboardへ追加しない。D06で修復した保存日計を読み、保存元欠測はNoneのまま表示する。
13. actual専用rowはforecast count/run binding/原本検証の予測集合へ混ぜない。既存legacy rolling参考推計は従来由来のまま保持する。

## テスト・PASS条件

PLANNED `tests/test_dashboard_review_provenance.py`: 同日同run/hash、別run履歴ありselected subset正常、selected subset同run複数hash、known+missing、選択run不明複数known run、legacy missing、actual financials/control targetあり、plan fallbackのみ。UI fixtureはdate有/complete_day falseも完全日と表示しない。
PASSはmismatched予測fieldだけNone、actual/PV/load維持、same-version値一致、legacy unknown明示、coverage0/23/24/Noneと有効0、全日/金融窓表記、private marker漏洩0/read追加0。
既存review/chart forecast divergenceと06:30警告のprotected回帰を必須とする。
追加 `tests/test_dashboard_actual_display.py`: forecast無しでも保存実測表示、PV/load片側欠測、半時間片側欠測、有効0/非有限/負値、JST日付、重複slot、SQL/Firestore parity、補間0。forecast/history保護テストと初期asset DB0も必須。元データ修復はD06のCSV→日計保存試験で別に確認する。

## 中断条件

選択runがstorageにないSQLを対応するための新schema/保存projector、financial result再計算、追加plan readが必要なら停止し未連携を明記する。

## 出口・引継ぎ

D05へmatched/mismatch/legacy/actual-priorityの同一input fixtureとprivate/public境界を渡す。

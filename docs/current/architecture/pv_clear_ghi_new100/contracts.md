# 共通契約 — この文書と担当ステップだけ読む

対象source: `d401b15de199ff8f1215935d01080a67a4c95266`。全ページは**実装予定の詳細設計**。NEWと表示した関数/field/testは現行に存在しない。文書の検査PASSと実装・本番PASSを混同しない。

## 読取と変更の範囲

入口は[README](README.md)。必要なstep IDを選び、[index.json](index.json)の当該entryと依存stepの**出口**だけを参照する。全stepを一度に読まない。型/式が必要なら[data-contracts](data-contracts.md)、公開時は[publication-contracts](publication-contracts.md)を追加する。

変更許可は担当stepの「変更対象」のqualified symbolとfieldだけ。ファイル全体への許可ではない。変更禁止はこの文書＋担当step＋AGENTS/protected lockの和集合。許可symbol内に禁止field/segmentがあれば、禁止segmentを維持して周囲だけを変更する。[boundaries.json](boundaries.json)は同じ範囲の機械検査用索引。

## 不変条件

| ID | 固定する契約 | 担当 |
| --- | --- | --- |
| I01 | 新100％は `max(0,C)*min(F,Q)` 一列。旧PV EWMA/physical residual/weather reshape/雨雲倍率/追加PV温度倍率を掛けない | S01/S04 |
| I02 | theta順 `[a,b,c,e]`、`x=(h+0.5-12)/6`、年位相 `2π(DOY-1)/Y`、Y=365/366、Qは幾何学高度Haurwitz1分中央60点積分 | S01 |
| I03 | W/m²提供者時刻tの直前1時間を[h,h+1)へ一回整列、温度を一括shiftしない。翌00時sourceを同日00時としない | S01/S04 |
| I04 | 完了した対象日前の認定晴天だけで全履歴fit。availability≤固定as_of、同日訂正は置換。既存消費/旧PVの7日履歴を拡張しない | S00/S01/S02 |
| I05 | 値24個/有限非負。実測0≠欠損。F/source不在を許すPV0はQ=0だけ。有効F=0やC≤0によるPV0も正当。日中総gap≤2・内部両anchorのみK補間、欠損を0に隠さない | S01 |
| I06 | load EWMA一回→温度時間配分→comfort適用なら24値置換。disabled/raw/scenario/診断の旧意味を保持 | S03 |
| I07 | night一回、最終load/PVを使用。decision07〜22、morning07〜09、midday10〜15。PV/load24とSOC07〜23境界17点を区別 | S04/S06 |
| I08 | 容量の温度増加と劣化を含む既存有効容量、料金・目的関数・制約・実SOC APIは変更しない | S04/S10 |
| I09 | 原本を一度固定、retry同bytes/hash/generated_at。shared可変pathを再読しない。互換Path helperは同SHAのrun固有不変fileだけで使用可 | S07/S08 |
| I10 | archive generated証拠≠control adopted≠device applied。raw_sha256/control_version/forecast_run_idを同じIDと扱わない | S07/S08 |
| I11 | control既存CASと旧原本を保持。採用拒否候補でmutable表示を上書きしない。projectionが制御pointerをlock/rewriteしない | S07/S08 |
| I12 | fulltraceは `decision_rationale.pv_clear_ghi` 一箇所。pack canonical/hash、F/Q/C/最終PV/補間/鮮度を説明。各hourへtrace24複製しない | S05 |
| I13 | HTML shell→bootstrap gzip fastpath、assetありDBread0、default31日bootstrap、履歴明示操作、historyなし404、ETag/gzip/cache保持。GETから再学習/予測なし | S09 |
| I14 | archive hash/read-back/first provenance/既存単調projection、Drive schema2/restoreを維持。計画pack復元≠current係数参照復元 | S02/S10 |
| I15 | 異常・欠損・更新失敗・公開拒否・表示失敗はstage/reason/版/hashで可視化。秘密/rawHTTPをdumpせず、fail/retain/再試行を一境界で決める | 全step |

## 共通の変更禁止field・コード境界

次は関数内のfield単位の保護。担当stepのより具体的なcode anchorも必須。

| 保護 | 明示的に変更しないもの |
| --- | --- |
| 蓄電池/金融 | `EnergyModelCoefficients.battery_usable_capacity_kwh`, `battery_temp_coeff_per_deg`, `battery_cycle_capacity_fade_per_cycle`, `battery_round_trip_efficiency` とその計算式。`NightChargeInputs.reserve_soc_percent`, `cycle_count`, `battery_temp_c`, `soc_now_percent`、月次請求入力/料金目的関数 |
| 旧公開型 | `ForecastCorrectionInput.hourly_pv_forecast` を削除/optional化しない。`ForecastCorrectionPolicy.enabled/skip_pv_correction/allow_load_safety_floor` とfrom_env既定値。`PlanDocumentV1`必須fieldをrenameしない |
| 時間/制御 | `night_soc_time_contract.py` の23/03/07所有権・06:45/06:50/06:55締切、`max_consecutive_soc_failures`、API `measureFlg=GW` の完了/要求以後測定検証。HTML/CSV fallbackを復活させない |
| 原本/復元 | `detail_sha256`, `raw_json`, `generated_at`, `source_revision`、archive receipt `status="verified"` の意味。checksum・復元前全対象検証・旧legacy読取を緩めない |
| 履歴 | `_complete_hourly_rows`、forecast recovery31日上限・recovery先行、7日monitoring選択/欠損≠0のlock、`forecast_run_id/forecast_issued_at/source`のbackend parity |
| 表示 | 保存SOC/同有効容量/同forecast世代、欠落時最新実測起点fallback、朝判定06:30。`SNAPSHOT_SCHEMA_VERSION`/固定asset名/cache policy/ETag/historylazyを無断変更しない |

どのstepもdeps/env/defaults/scheduler/IAM/device設定、旧lockの削除、全体formatter、controlへのDB/lease依存追加を許可しない。sourceのcompatibility label（例: legacy `green`）を、文書の別表現だけで書換えない。現行実機意味の確認は本PV変更と別に行う。

## クラスの責務とデータ流れ

`ClearGhiCalibration` は完成packの値と由来だけ。`ClearGhiForecast` は固定日射/係数の予測結果だけ。private `LoadCorrectionPreparation` は消費演算準備だけ。既存 `PvForecastBundle` は最終PV/load/scenarioとoptional説明データを運ぶ。既存 `NightChargeInputs/Result` は充電判断、`PlanDocumentV1/PublishedPlan` は原本/公開境界。汎用plugin、追加control mode class、公開状態flagを作らない。

```text
固定input/pack → 消費24・新PV24 → night07〜22を一回
 → 既存constraints/optimizer → 原本＋fulltrace
 → immutable生成証拠 → 既存control CAS
 → 採用版projection → fresh producer slice → 同世代asset
```

## 未決・試験・承認の扱い

[gates.json](gates.json)のOPENは、必要証拠/担当step/停止条件を明示した未完事項。解決済みと仮定しない。実装者は未決定数値を既定値へ昇格せず、provider/新storage/世代情報を推測しない。

step依存は実装handoffの順序。文書作成完了を実装handoff完了へ置換しない。テスト表のPLANNEDは追加する試験仕様、EXISTINGは再実行する既存試験。両者を実行結果と混ぜない。

patch前にscopeと現在diff確認、必須品質check後に対象test、禁止変数/呼出し/副作用のdiff確認、そのstepのPASSと次へ渡すartifactを記録する。変更箇所だけ読めばよいが、不一致が見つかった直接依存は調べる。CodebaseMemoryはparentが一時CLIで照会し結果を短く配布、graphはsource/testの代わりではない。formatterは実行しない。

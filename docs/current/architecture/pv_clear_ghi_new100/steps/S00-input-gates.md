# S00 — 固定入力・未完ゲートの確定

## 入口・参照範囲

前提stepなし。[共通契約](../contracts.md)、[gates.json](../gates.json)、[index.json](../index.json)のS00だけを読む。このstepは入力結線の証拠確定で、制御/クラウド変更を実行しない。

## 変更対象

| 対象 | 操作/成果物 |
| --- | --- |
| `gates.json` G01/G02/G03/G09 | 既存データ供給・認定available_at・初期pack配置・数値条件・翌00時取得範囲の証拠、解決日時、対応仕様を記録 |
| S01/S02/S04の入力契約 | 実装が必要な限定symbol/パラメータを証拠に基づいて具体化。外部contract/env追加なら承認を別に記録 |
| ローカル実装作業packet | 対象日/as_of/site/basis、immutable input hash、run固有原本の場所、料金/容量/実SOCの同世代fixtureを渡す |

ここにsource編集許可はない。データ接続の調査結果を仕様へ反映してから担当stepのsourceを変更する。

## 変更禁止

| symbol/field | 禁止操作 |
| --- | --- |
| 既存7日選択/`context.rows` | 全晴天日を消費/旧PV校正へ注入しない |
| 予報 `shortwave_radiation_w_m2` | 晴天認定の衛星**実測**として使用しない |
| `available_at`, `provider_issued_at`, `retrieved_at` | 欠落をnow/取得時刻で偽造しない |
| `theta`, `rank`, `condition_number` | 保存研究の全21日係数を過去評価へ固定流用、候補100を根拠なく閾値採用しない |
| cloud/env/IAM/slot | 調査のための新Job/API/設定更新を実行しない |

## クラス・変数差分

新classはここで作らない。S01の`ClearGhiCalibration`用入力を固定する。`run_started_at`は開始、`as_of`は入力cutoff、`retrieved_at`は取得、`provider_issued_at`は既知発行時刻で役割を分ける。時刻はtimezone付きdatetime、比較はUTCの同じ瞬間。対象日より前の訓練日だけを使う。

## 実装手順・分岐

1. G01: 実績の00/30分ペアと、日照分/衛星GHI/主要時刻の認定原本を一日ずつ対応。取得元版/hash/available_atの欠落を列挙する。認定不明は訓練へ入れない。
2. G02: 同site/basisの完成pack一つと有効参照を、通常起動・cold start・更新失敗・backup restoreでどう取り出すか確定する。生成された計画のpackをcurrentへ無条件昇格しない。
3. G03: 保存済み過去だけfitに対し、rank/特異値/条件数・摂動感度を比較し、拒否規則を固定する。数値依存は既存NumPyだけ。
4. G09: 現行API一応答内に対象日01時〜翌00時が存在するか、previous-runsのstart/end範囲とsuffixも確認。追加HTTPを前提にしない。温度の日付parserは維持。
5. 上記がOPENなら該当stepへ「完了」を渡さない。計算fixtureによる局所試験と、実provider供給の成立は別に記録する。

## テスト・PASS条件

| ID/状態 | 条件・fixture | PASS条件 |
| --- | --- | --- |
| INPUT-001 PLANNED | 対象日/未来/available_at超過の認定行、同日訂正版、実測0/欠損 | 未来・不明を除外、同日置換、0保持、訓練日一意 |
| INPUT-002 PLANNED | 同site/basis原本、破損current、初期未配置、復元された旧計画pack | 現在参照の解決根拠を説明、原本なし生成失敗、誤昇格0 |
| INPUT-003 PLANNED | rank不足/季節偏り/摂動を含む既存訓練集合 | 採用/拒否の数値規則と感度結果が対応し、未決閾値0 |
| INPUT-004 PLANNED | source翌00時/重複/offset、previous-runsレスポンスと温度 | 23区間のsourceを同取得回で確保、温度shift0、追加HTTP0 |
| INPUT-005 EXISTING | [履歴回帰](../../../../../tests/test_forecast_monitoring_history.py) | 7日/請求期間/欠損0保護を保持 |

入力契約試験はS01/S02のplanned test fileへ追加する。INPUT-001〜004の実行入口は追加後 `python -m pytest -q tests/test_pv_clear_ghi.py tests/test_clear_ghi_calibration_io.py`。現時点で両fileはNEW、試験は実行済みではない。EXISTINGは `python -m pytest -q tests/test_forecast_monitoring_history.py`。前に現行必須品質gateを通す。

## 中断条件

G01/G02/G03/G09の必要証拠なし、外部contract変更が必要、当時のavailable_atを再現出来ない場合は停止し、該当gateと最小の次の調査対象だけを記録する。既存予想を0や推測packで上書きしない。

## 出口・引継ぎ

解決済みgateの証拠と入力fixtureをS01/S02へ渡す。未解決gateはOPENのまま見える形で渡す。sourceの実装はまだ完了ではない。next: [S01](S01-pure-pv.md)。

# R00 証拠・対象・検査順序の固定

依存なし。対象は追加実装前の基準。既存G04/G06/G07/G08を自動CLOSEDにしない。

## 変更対象・変更禁止

| ファイル/symbol | 許可する限定差分 |
| --- | --- |
| scripts/pre_release_integration.ps1 | 最初のpytestより前に既存pre_release_local.ps1を一回呼ぶ順序へ変更。同scriptの品質検査→全pytest/Node/securityの契約を利用し、新skip追加なし。その後既存保護試験・read-only同期・R01parityを行う |
| release_remediationの文書/index/boundaries | source-bearing commit、調査入力と既存gateの位置づけを明示。旧記録の検査件数を今回値と置換しない |
| 追加予定 tests/test_pre_release_integration_contract.py | 呼出し順序と重複pre_release_local呼出し0・失敗伝播を固定 |

変更禁止：production_deployment_gateのclean/.env/backup/security/ValidateOnly条件、pre_release_localの必須Ruff/import/mypy/Oxlint/syntax/compile/pytest/Node/security、既存保護試験の削除・成功偽装、state成功手編集。
新class・恒久variable/env/flagなし。既存 `$SkipInstall` は意味を維持。計画書に書いた仮称テストは未実装として扱う。

## 事前記録

1. source SHA・branch・git diff/status。原本データと修復copyを分離し、DB/CSV/凍結予測入力のSHAを保存する。
2. 9/20のbefore35→after48 slots、PV2.834、load20.203→32.002、buy18.766→30.530を原本集計で照合。未提供値を推定しない。
3. parquet/DBの全原本があると仮定しない。179日・177完全日・4/17初日部分・10/12途中日を別記し、どの日時の取得snapshotか保存する。
4. 正規gate失敗記録を保持。10/10・10/11各24行＝48行、容量48field、SOC34field、両側共通field数値差0の再現をR01へ渡す。
5. 本番引数・ID・秘密は.env/production_env.ps1内だけ。privateログをtracked docsへコピーしない。報告は件数・reason・SHA/日付のみ。

## 検査の順序

現行pre_release_integrationは保護pytest→同期/parity→pre_release_localなので、parity失敗時はその実行内の全体品質検査へ到達していない。この事実を「1299件が今回gateで成功」と報告しない。
予定順序はpre_release_local→既存保護pytest2組→正規read-only同期→R01の共通/原本両検証。保護pytestの再実行は既存入口の要求を保ち、任意skipを追加しない。
R01両検証の唯一の入口は改修済parity CLI一回。新原本moduleはcollector＋純関数だけで、別main/CLIを作らない。R01 mainが同じ選択入力でAND判定するため、重複取得と異なるsnapshotの混成を避ける。
source変更後の実装担当はcode-quality-audit Skillを適用し、必須静的検査を通過してから新局所pytestを実行する。診断に失敗したら当該原因だけを修正する。

## クラス・variable・state

新classなし。新しい永続state schemaは追加しない。既存production gateの `checks[].name/status/error_code` をそのまま使う。
新試験結果は分析artifactへ `source_revision`, `input_sha256`, `status`, `coverage`, `failure_reasons` を保存する。case名やIDの実値を秘密識別子の代わりに出力する。
NOT RUN/INCONCLUSIVE/FAIL/PASSは別値。pytest件数・独立原本照合件数・実server試験件数を合算しない。

## テスト・PASS条件

- `test_quality_gate_precedes_any_pytest`: integration内でpre_release_local呼出しが最初の保護pytestより前、かつ一回だけ。
- `test_each_failure_stops_integration`: quality/保護/sync/parity失敗で後続へ進まず非zero。warning化なし。
- `test_validation_has_one_coordinator`: integrationのparity呼出一回、standalone原本CLI呼出0。R01のいずれか失敗が同じparity exitへ伝播。
- `test_protected_commands_preserved`: 29/15件を実行した既存対象のcommandを維持。件数を固定assertするのではなく対象path/順序を固定。
- 既存 `tests/test_dashboard_only_deployment_contract.py`, `tests/test_forecast_history_contract.py` の意味を保持。

PASSは順序/失敗伝播と原本SHAの証拠がそろうこと。現在の48差を消したかはR01の別判定。本番環境・依存が不足ならINCONCLUSIVEとし、品質/安全の失敗はFAIL。

## 停止・出口

秘密混入、原本と修復copy取り違え、当時原本の捏造、protected command弱化、新skipが必要なら停止。
R01へsource/対象日/失敗fieldを渡す。R02〜R05へ共通のSHA・証拠階層を渡し、各stageは自分の実装・検証完了だけを更新する。

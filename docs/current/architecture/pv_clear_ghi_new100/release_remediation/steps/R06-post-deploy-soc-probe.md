# R06 デプロイ完了後のSOC設定往復と今朝の経済リプレイ

2026-10-12の利用者指示で追加。SOC設定変更はデプロイしてから実行する。既存R05のmode-only試験と23/03/07の契約を変更せず、独立した明示試験として実装する。

## 変更対象・禁止

- NEW app/kpnet/soc_setting_probe.py::run_soc_setting_probe：既存profile_from_current_settings/_apply_and_verify/compare_setting_readbackを再利用。SocChargeMode候補だけ取得し、30/50の実機対応値で現値と異なる一値を設定、60秒保持、元socChargeModeへ復元して全設定読戻し。
- NEW scripts/run_kpnet_soc_setting_probe_from_env.ps1、tests/test_kpnet_soc_setting_probe.py。正式wrapperがcompleteのseparated deployment stateと同commitを必須とし、未完/別commit/不正時間帯/実行中controlは起動拒否。ローカルで機器SETを実行せず、本番の既存専用Jobを使用する。
- 既存run_settings_roundtrip、HISTORICAL_FAILURE_LOCK、scheduled mode-only、全充放電窓/契約電流/安全SOC/停電設定は変更禁止。SocChargeMode以外のpayload差を拒否。未知送信結果を盲目再送/復元しない。

## 入出力・責任

production新class0。run_soc_setting_probe(*,target_soc_percent:float=30.0,hold_seconds:int=60)->dict[str,object]。target候補が現値なら30/50のもう一方を明示記録して選ぶ。supported候補不在/非有限/hold!=60/DRY_RUNは送信前拒否。
wrapper parametersは必須DeploymentStatePath/ReceiptPath、TargetSoc=30/50、明示TestExecution、既存ProbeJobName。state.kind=separated_runtime_deployment/status=complete/repository_commit=HEAD/stages.verification.status=success/image_digests.controlを必須照合。07:15–22:29 JSTだけ実行し、専用Jobと23/03/07 Jobの実行中・不明状態を拒否する。専用Job imageは部署済みcontrol digestとの一致を必須とする。
専用Jobのcommand/argsだけを一回変更し、python -cからrun_soc_setting_probeを呼ぶ。既存image/環境/secret/retry/IAM/Schedulerは変更しない。exact execution_nameに属する一つの構造化JSON結果を検査し、finallyで元command/argsを復元してimage含めnative readbackする。device restoreとJob restoreを別項目に記録し、片方でも不明ならFAIL。DockerfileのCOPY appにより既存control imageにmoduleが入るため、新依存・別CLIのCOPY・新entrypoint classは不要。
localはinitial/snapshot/restore/maps/selected/phase/unknown。公開はtarget/applied code/changed_fields/読戻し値/hold_elapsed_seconds/restore/statusとsnapshot_hash。初期設定全値・機器IDは公開しない。部署済み条件はwrapper、device状態は既存KpNetClientだけが所有、外部stateへcontroller leaseを追加しない。
設定後と保持後にSOC code一致＋他field不変を確認。復元前の他field変更は他writerとして停止し、他writerを初期値へ戻さない。既知失敗は現在snapshotを読み、対象SOCだけ復元可能と証明できる時だけ復元。ACK不明はread-only確認し、後続writeを重ねない。
設定値の30/50は機器の離散候補。計算した朝7時の連続SOC目標とは別の値であり、目標の実達成を今回の設定往復から推測しない。

## リプレイ

今朝02:32 JSTの保存原本を取得しSHAを固定。現行は保存PV/load/容量/SOC/料金/シナリオを再現。新PVは同じ保存日射、評価日前の認証晴天だけの年間周期N1、max(0,C)*min(F,Q)。当日実測・後日の予報を入力へ混ぜない。
比較は同消費/容量/料金/optimizer条件のPV置換を先に行い、方式が変更する残差シナリオや修復金融入力の影響を別ケースとして分ける。目標SOC、夜間充電量/費用、期待日中買電量/費用、売電量/評価、23時残電評価、目的関数と候補SOCの差を報告。旧方式の保存値再現誤差があれば先に調査する。
保存予報に新方式の入力時刻/provenanceが無い場合は、原本の時間意味からのリプレイ再構築と明示し、当時のNEW100計画が存在したと記録しない。実績損失は取得済み区間と未完日を分ける。

## PASS・停止

追加回帰は未deploy拒否/候補30↔50/他field保持/60秒固定/SETとrestore読戻し/既知失敗cleanup/ACKunknown後write0。他のscheduledモード回帰も必須。
本番PASSはdeploy complete、同commit、正式wrapper、実SET→読戻し→60秒→対象SOC復元→全snapshot読戻し、前後realtime SOC APIと測定時刻。物理電池のSOCが同値へ戻ることは要求せず、設定の復元と区別する。
リプレイと実機証拠はartifacts/analysisへ、project/resource/credentialはprivateだけ。codeと模擬試験の合格を実機成功へ読み替えない。

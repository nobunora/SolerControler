# KP-NET障害再発確認スクリプト

## 目的

`run_kpnet_incident_validation_from_env.ps1` は、手動運用の03時引継ぎと07時green遷移をローカルのダミー状態で確認した後、実機KP-NETの設定書込み・read-back・復元を一度だけ確認する明示的な検証入口です。

手動引継ぎステージは `2099-01-01` のインメモリストアだけを使うため、Firestore、本番計画、Schedulerへ書き込みません。実機ステージだけがKP-NET設定を変更します。

## 実行条件

PowerShell 7から、次の明示的な実行指定を付けて起動します。

    pwsh -NoProfile -File scripts/run_kpnet_incident_validation_from_env.ps1 -TestExecution

既定値は次の通りです。

- `SocChargeMode`: 実機候補の最大値として50%
- 検証用充電開始／終了: 既定では初期スナップショットを維持（指定時だけ一時変更）
- 保持時間: 60秒固定
- 結果: `artifacts/validation/kpnet-incident-validation.json`

開始・終了時刻を変更する場合も、実機が許容する短い無害な時間帯を指定します。未指定なら開始時の時刻をそのまま使い、不要な時間帯変更を行いません。

    pwsh -NoProfile -File scripts/run_kpnet_incident_validation_from_env.ps1 `
      -TestExecution -TestChargeStart 23:59 -TestChargeEnd 00:01 `
      -ResultPath artifacts/validation/kpnet-incident-validation-20260827.json

## 合格条件

1. 手動引継ぎ `MANUAL_OPERATION` がインメモリストアへ記録される。
2. 明示的な手動所有者だけが07時ゲートを通過し、green profileが選択される。
3. 実機の全SOC制御項目と契約電流を開始時にスナップショットする。
4. 50%の強制充電候補と検証用時間をKP-NETへ書き込み、read-backが一致する。
5. 60秒経過後、開始時スナップショットをKP-NETへ復元する。
6. 復元後のread-backが一致する。

いずれかに失敗した場合は終了コード1です。失敗後も復元を試み、復元不能の場合は結果JSONの `restore_after_failure_error` と終了コードで明示します。結果JSONには認証情報、プロジェクト識別子、実機の全設定値を記録しません。

この入口はSchedulerを作成せず、既存の専用Cloud Run設定往復Jobの `--max-retries 0` 契約を変更しません。本番デプロイは別途、運用ランブックの事前ゲートを通してから行います。

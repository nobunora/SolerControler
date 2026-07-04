# 月次段階料金着地点SOC目的関数 デプロイ報告

作成日時: 2026-06-21 23:46 JST

## 概要

SOC判断を、売電未契約中の売電ペナルティ、昼間買電の段階料金、月次検針期間の着地点を含む期待コスト評価へ更新した。
検針期間は暦月ではなく、既存ダッシュボードと同じ `15日から翌月14日` を既定とする。

## 主な変更

- `app/soc_cost_optimizer.py`
  - 月次段階料金の着地点ペナルティを追加。
  - 候補SOCの内訳に `expected_monthly_tier_landing_penalty_yen` を追加。
- `energy_model_main.py`
  - 検針期間内の昼間買電実績と、残り期間の昼間買電見込みをSOC optimizerへ渡す。
  - `soc_cost_risk` に月次累計、残り見込み、検針期間、月次ペナルティ係数を出力。
- `scripts/deploy_gcp_jobs.ps1`
  - Cloud Run Jobsへ今回のSOC目的関数・1時間天気・純余剰headroom・月次着地点設定を渡す。
  - PowerShell 7運用に合わせた作業方針と、既存Secret Manager認証情報の再利用に対応。
- `.env.example`
  - 月次着地点、売電ペナルティ、1時間天気、段階料金設定を追加。
- `AGENTS.md`
  - 原則 `pwsh` を使う方針を追記。

## シミュレーション

代表10日をローカルリプレイし、`artifacts/replay/monthly_tier_landing_summary_fixed_20260621.csv` に集計した。
今回の同期済み過去runでは検針期間内の累計昼間買電が0kWhとして出るケースが多く、月次着地点ペナルティは弱い第1段階未使用ペナルティに留まった。
段階単価のみの前回結果と比べると、全体としてほぼ同等で、6/21のみ `56% -> 54%` へ小幅低下した。

## 検証

- `scripts/pre_release_check.ps1 -SkipInstall`
  - Firestore -> SQLite同期成功
  - compileall成功
  - `124 passed in 49.36s`
  - security_check成功
- 追加確認
  - `python -m pytest tests\test_soc_cost_optimizer.py tests\test_energy_model.py -q`
  - `29 passed in 2.52s`
  - `python scripts\security_check.py`
  - 成功

## デプロイ

- Project: `codrivernavi-web-20260510`
- Region: `us-central1`
- Image: `us-central1-docker.pkg.dev/codrivernavi-web-20260510/solar-controller/runner:latest`
- Cloud Build: `ed0b8951-9dd7-46cb-9057-fe20b8061c49` 成功
- 更新Jobs:
  - `solar-battery-23`
  - `solar-battery-03`
  - `solar-battery-07`
  - `solar-sheets-export`
  - `solar-drive-backup`
- Scheduler:
  - `solar-battery-run-23` はpause
  - `solar-battery-run-03` / `solar-battery-run-07` を更新
  - `solar-sheets-export-daily` を更新
  - `solar-drive-backup-daily` を更新

## 後処理

- Artifact Registry cleanupを実行。
- 最終容量確認:
  - Artifact Registry total: 394.618 MB / 500 MB
  - Cloud Build bucket: 30.991 MB / 5120 MB
  - App data bucket: 0 MB / 5120 MB
  - overageなし

## 注意点

- ローカルHARファイルは存在しなかったため、既存のSecret Manager `kp-monitor-username` / `kp-monitor-password` を再利用してデプロイした。
- 旧Tokyo schedulerは存在せず、pause対象確認でNOT_FOUNDが出たが、us-central1側の現行scheduler更新は完了している。
- 月次着地点効果をより正確に見るには、検針期間内の昼間買電累計が実績DBから十分に入る状態で再評価する。

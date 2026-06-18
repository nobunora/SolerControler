## Change Summary

- `OVERNIGHT_DISCHARGE_GUARD_CAP_KWH` の推奨運用値を `2.0` に変更した。
- KP-NET の `SocChargeMode` が10%刻みなどで生SOC目標より上に丸められる場合、充電開始時刻は丸め後上限ではなく生SOC目標に届く時刻で逆算するようにした。
- 条件木と運用ガイドへ、SOC丸め時の開始時刻補正ルールを追記した。

## Design Intent

- SOC上限コードはKP-NET候補値の制約に合わせて安全側へ丸める。
- ただし過充電を避けるため、34%目標に対して40%コードを送る場合でも、開始時刻は34%到達を狙う。
- 04:30主運用で、23時ジョブに頼らず7:00目標へ寄せる。

## Alignment With Existing Design

- 既存の `_build_dynamic_forced_profile()` 内で、夜間計画とCSV実績から充電時間を推定する構造を維持した。
- 03系ジョブ側に既にあるSOC上昇率ベースの考え方に合わせ、CSVのSOC増分を中央値で評価し、データ不足時は `ADJUST03_FORCE_CHARGE_RATE_FALLBACK_PERCENT_PER_HOUR` を使う。
- `SocChargeMode` の候補丸め自体は変更せず、開始時刻だけを補正対象にした。

## Alternatives and Why They Were Not Chosen

- `SocChargeMode` を34%相当へ無理に合わせる案は、KP-NET候補値に存在しない設定を送れないため採用しない。
- 常にSOC上昇率だけで逆算する案は、丸めが発生しない通常ケースまで挙動が変わるため採用しない。

## Files Changed

- `.env.example`
- `README.md`
- `app/kpnet_workflow.py`
- `docs/CURRENT_DECISION_TREE_JA.md`
- `docs/OPERATION_CONDITIONS_GUIDE.md`
- `scripts/deploy_gcp_jobs.ps1`
- `tests/test_kpnet_workflow.py`

## Scope Not Changed

- `SocChargeMode` の候補選択ルールは変更していない。
- Cloud Run へのデプロイ、コミット、プッシュはこの変更では未実施。
- Google Driveバックアップ関連の既存未コミット差分はそのまま維持した。

## Tests

- Commands run:
  - `python -m pytest tests/test_kpnet_workflow.py -q`
  - `python -m pytest tests/test_energy_model.py tests/test_cloud_job_runner.py -q`
  - 2026-06-19 の同期済み夜間計画を使ったローカル確認
- Results:
  - `tests/test_kpnet_workflow.py`: 9 passed
  - `tests/test_energy_model.py tests/test_cloud_job_runner.py`: 35 passed
  - 2026-06-19相当では、生目標34%、SOC上限40%、開始06:09、終了07:00、SOCレート由来51分になった。

## Points a Human Should Confirm

- 実機では次回04:30実行後の `kpnet_summary.json` または Cloud Run ログで `duration_source=soc-rate-rounded-target` と開始時刻を確認する。
- Cloud側にCSVがある実運用では、SOC上昇率がフォールバックではなくCSV実測から取れるか確認する。

## Remaining Risks

- CSV内にSOC増分サンプルが不足する日はフォールバック40%/hを使うため、実際の充電速度が大きく違うと数分から十数分ずれる可能性がある。
- KP-NET側の設定反映遅延が大きい場合は、開始時刻だけでは完全には吸収できない。

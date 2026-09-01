# ダッシュボード警告調査証跡 2026-09-01

## 調査基準

- 観測時点: 2026-09-01 JST。
- repository branch: `master`。
- source HEAD: `f8bc8c5cd0ed945f039be7b71bb37d716f551444`。
- CodebaseMemory: `status=ready`、5936 nodes、18328 edges、indexed commitはsource HEAD。
- backend: `firestore`。秘密値、認証情報、project/account identifierは記録しない。

## 再現・採取コマンド

以下はすべて読み取りまたはローカル集計であり、設定変更・ジョブ起動・デプロイは含まない。

```powershell
. .\scripts\production_env.ps1; Import-ProductionEnv
```

```powershell
python .\scripts\kpnet_soc_gap_report.py `
  --run-dir .\artifacts\20260831-091000 `
  --date 2026-08-29
```

```powershell
& .\scripts\gcloud.ps1 scheduler jobs describe solar-battery-run-03 `
  --location <configured-scheduler-region> `
  --project <configured-project> `
  --format=json
```

```powershell
& .\scripts\gcloud.ps1 run jobs executions list `
  --job solar-battery-03 `
  --region <configured-region> `
  --project <configured-project> `
  --limit=5
```

## Firestore dashboard read

`load_firestore_slice(end_date=None, window_days=31, include_static=True)` の結果:

| 項目 | 値 |
|---|---|
| today JST | 2026-09-01 |
| global data bounds | 2026-04-17〜2026-08-31 |
| dashboard end date | 2026-08-31 |
| latest schedule | `fallback-default` |
| latest schedule plan date | 2026-08-31 |
| settings completed | false |
| warning count | 2 |
| warning codes | `soc_target_unreached`, `settings_completion_unconfirmed` |

### Warning input values

| code | date | target | PV charge end SOC | source/status |
|---|---|---:|---:|---|
| `soc_target_unreached` | 2026-08-29 | 100.0% | 56.0% at 16:30 | `applied` / `normal` |
| `settings_completion_unconfirmed` | 2026-08-31 | - | - | `fallback-default` / source未設定 |

## Settings event and execution timeline

| source | time (UTC) | slot/job | status |
|---|---|---|---|
| settings event (latest) | 2026-08-28 21:28:55 | 03 / standby-mode | `applied`, readback true |
| pipeline run (03 latest) | 2026-08-28 21:31:41 | 03 | CSV rows 2844 |
| Scheduler last attempt | 2026-08-31 18:00:01.658827 | 03 | `ENABLED` |
| Cloud Run execution start | 2026-08-31 18:00:01.778491 | 03 | started |
| Cloud Run execution finish | 2026-08-31 21:10:01.073904 | 03 | `Completed successfully` |

追加確認した範囲では、`night_charge_plans` と `night_soc_execution` の2026-08-30/31文書は存在しなかった。manual-csvのpipeline runは8/31まで存在するが、03 settings eventの更新を示すものではない。

## 2026-08-29実績の要点

公式 `kpnet_soc_gap_report.py` のローカル出力とCSV行確認:

| 区間/時点 | SOC | PV | 充電 | 備考 |
|---|---:|---:|---:|---|
| 06:30 | 100% | 0.043kWh | 0.000kWh | 夜間充電後 |
| 07:00〜14:30 | 100% | 正値 | 0.000kWh | 目標到達状態を確認 |
| 15:00 | 91% | 0.085kWh | 0.000kWh | 夕方負荷・放電開始 |
| 16:00 | 70% | 0.021kWh | 0.000kWh | 低下継続 |
| 16:30 | 56% | 0.024kWh | 0.050kWh | `pv_charge_end`採用点 |
| 19:00 | 0% | 0.000kWh | 0.000kWh | 日中終了後 |

公式レポートの集計値:

- 設定SOC 100.0%、夜間充電予測9.997kWh、実績9.575kWh。
- 最終PV予測5.286kWh、日中PV実績2.174kWh。
- 日中買電期待値8.344kWh、実績14.392kWh。
- 夜間SOC監査は`PASS`、07:00 SOC誤差0.0%、read-back一致true、競合書込み数0。

## 品質確認ログ

| command | result |
|---|---|
| `python -m ruff check .` | PASS: All checks passed |
| `python -m mypy app scripts --show-error-codes` | PASS: 174 source files |
| `python -m pytest -q tests/test_dashboard_data.py` | PASS: 40 passed in 3.83s |
| `node tests/test_dashboard_calculations.js` | PASS |
| `node tests/test_dashboard_modules.js` | PASS |
| `node tests/test_dashboard_bootstrap.js` | PASS |
| Import Linter | 未実行: `lint-imports`未登録、module entrypoint非対応 |
| deptry / ty | 未導入 |
| Oxlint / tsc | package/tsconfig未設定 |

## 制限事項

- Cloud Runの実行状態は読み取ったが、アプリケーションログのイベント単位相関までは完了していない。
- CodebaseMemoryのcoverageはbest-effort。対象sourceにrecorded issueはないが、完全性の証明ではない。
- 生成された `artifacts/20260831-091000/kpnet_soc_gap_report_2026-08-29.md` はローカル検算用であり、追跡対象の生ログには含めない。

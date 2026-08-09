# Entrypoints

| ファイル | 起動目的 | 主な委譲先 |
| --- | --- | --- |
| `main.py` | ローカルの制御フロー | `app.local_control.workflow` |
| `cloud_job_runner.py` | Cloud Runの時刻別Job | `app.runtime.cloud_job` |
| `energy_model_main.py` | 夜間充電計画の生成 | `app.energy_plan.workflow` |
| `kpnet_main.py` | KP-NETのCSV取得・設定 | `app.kpnet.workflow` |
| `db_pipeline_main.py` | 保存・集計パイプライン | `app.operations.workflow` |
| `dashboard_server.py` | ダッシュボードHTTPサーバ | `app.dashboard.server` |
| `sheets_export_main.py` | Google Sheets出力 | `app.exports.sheets` |

## 読み方

1. 実現したい操作を決める。
2. 対応する入口ファイルを開く。
3. 委譲先のパッケージへ進む。
4. 外部I/Oや保存の変更では [データとバックエンド](04-data-and-backends.md) も読む。

入口ファイルは薄く保ちます。業務規則・外部I/O・データ変換を入口へ追加しないでください。

次: [判断フロー](06-decision-flow.md)

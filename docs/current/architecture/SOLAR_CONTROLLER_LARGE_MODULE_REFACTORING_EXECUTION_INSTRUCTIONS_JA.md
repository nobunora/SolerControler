# SolarController 巨大モジュール段階分割 実行指示書

作成日: 2026-08-02  
前提: `completed/refactor/2026-08-next-refactoring/` の完了記録を基準とする。

## 1. 目的

残る巨大モジュールを、公開契約・安全な実行順・外部I/O条件を変えずに、機能単位の正規モジュールへ段階分割する。行数削減そのものを目的にせず、各モジュールを一つの責務で説明できる状態にする。

対象の優先順は次で固定する。

1. R-0 基準・所有表の記録
2. R-1 `app/forecasting/correction.py`
3. R-2 `app/energy_plan/workflow.py`
4. R-3 `app/kpnet/workflow.py`
5. R-4 `app/runtime/cloud_job.py`
6. R-5 `app/forecasting/pv_array.py`
7. R-6 `app/dashboard/data.py`
8. R-7 最終検証・監査・文書移送

後続カードを前倒ししない。カード中の小分割も上から一つずつ完了する。

## 2. 禁止事項

- Cloud Run、Firestore、PostgreSQL、Drive、Sheets、KP-NET、Open-Meteoなどの実サービスへ接続しない。
- 公開関数名、CLI名、環境変数名、JSON/CSV/DBキー、終了コードを変更しない。
- `# type: ignore`、`# noqa`、mypy設定緩和、検査対象除外でエラーを隠さない。
- 一つのカードに無関係な整形、改名、依存追加、互換モジュール削除を混ぜない。
- カード途中でコミットしない。カード終了時に一つだけコミットする。

## 3. 再発防止ゲート（全カード必須）

### 3.1 開始ゲート

各カードの開始前に必ず次を順に実行し、すべて成功した場合だけ進める。

```powershell
git status --short
git log -1 --oneline
python -m pytest -q <カード固有テスト>
python -m mypy <対象package> --no-incremental
```

`git status --short` が空でない、または直前カードの完了コミットがない場合は停止する。開始コミット、対象関数一覧、monkeypatch対象を進捗ログへ記録する。

### 3.2 所有権ゲート

分割前に移動対象を表にする。表には「関数/クラス」「移動先」「呼出し元」「公開かprivateか」「テストpatch先」を必ず書く。移動後に次を確認する。

```powershell
rg -n '^def |^class ' <旧モジュール> <新モジュール>
rg -n 'monkeypatch|patch\(' tests
rg -n 'from app\.(artifact_cleanup|browser_automation|comfort_load_forecast|config|constants|consumption_forecast|csv_merge|csv_utils|dashboard_data|db_sync|decision|drive_backup|energy_model|firestore_ops|forecast_correction|history_store|kpnet_workflow|main|models|monitoring_csv|night_plan_archive|night_plan|occupancy_schedule|operations_db|postgres_ops|pv_array_forecast|pv_physical_forecast|sheets_export|soc_cost_optimizer|soc_decision_feedback|tariff|time_windows|utils|weekly_backup)(\.|\s|$)' app scripts
```

旧モジュールには orchestration、公開API、またはテスト互換の薄い委譲だけを残す。移動済みの実装を旧・新モジュールに二重保持してはならない。

### 3.3 構造完了ゲート

各小分割は、次の三点を満たさなければ完了ではない。

1. 新モジュールが実装を所有し、旧モジュールから実際に呼ばれる。
2. 新モジュール名だけで責務が分かる（単なる再exportや空のwrapperではない）。
3. 新規または既存テストが、公開契約・失敗時のfallback・安全な実行順のいずれかを検証する。

最終監査ではモジュールの存在だけでなく、`rg` と呼出し経路を確認する。完了記録に「分離済み」と書く前に、その実装が旧モジュールに残っていないことを確認する。

### 3.4 コミット・監査ゲート

カード終了時にカード固有テスト、mypy、compileall、`git diff --check` を成功させ、変更ファイルがカードの許可範囲だけであることを確認してから一つだけコミットする。最終文書を `completed/` へ移すのは、独立した再監査が成功してからに限る。

進捗ログおよび最終監査には、実行したコマンドの結果だけを書く。推測、未実行の検査、またはモジュール名だけを根拠に「完了」と記録してはならない。

## 4. カード

### R-0: 基準・所有表

変更可能ファイル: 新しい進捗ログのみ。

各対象の行数、関数/クラス一覧、既存テスト、patch境界、候補移動先を記録する。実装変更は禁止。

### R-1: Forecast correction

`correction.py` から以下を順に分離する。

1. SQLite/Firestore forecast history I/O
2. Open-Meteo weather I/O
3. 温度特徴量・ridge regression・hourly multiplierの純粋計算
4. correction結果の組立て

`build_forecast_correction` と公開互換wrapperは orchestration に残す。

必須テスト:

```powershell
python -m pytest -q tests/test_forecasting_correction_compatibility.py tests/test_energy_model.py tests/test_external_site_access.py
python -m mypy app/forecasting --no-incremental
```

### R-2: Energy Plan workflow

順に、月次料金見通し、PV不確実性/選択、plan quality、最終output組立てを分離する。`build_energy_plan` は実行順とport配線だけを持つ。

必須テストは既存Energy Plan回帰一式と `python -m mypy app/energy_plan --no-incremental`。

### R-3: KP-NET workflow

時間ルール、CSV可視化、profile payload組立てを分離する。認証・HTML・HTTP実装をworkflowへ戻さず、`client.py` を正規所有先に保つ。

必須テスト:

```powershell
python -m pytest -q tests/test_kpnet_workflow.py tests/test_kpnet_settings_intent.py tests/test_external_site_access.py
python -m mypy app/kpnet --no-incremental
```

### R-4: Cloud Job

retry/command adapter、03時のplan調整、slot orchestrationを分離する。`_monitor_partial_forced_and_stop` は一つの安全なmonitor lifecycleとして維持し、純粋計算とI/Oだけを外へ出す。

必須テスト:

```powershell
python -m pytest -q tests/test_cloud_job_runner.py tests/test_forced_charge_state_machine.py tests/test_forced_charge_settings.py
python -m mypy app/runtime app/forced_charge --no-incremental
```

### R-5: PV array

既存の `pv_array_adapters`、`pv_array_calibration`、`pv_array_selection` を実装の正規所有先に拡張する。provider response mapping、calibration計算、ensemble/candidate選択が `pv_array.py` に再び同居しないことを確認する。

必須テスト:

```powershell
python -m pytest -q tests/test_pv_array_forecast.py tests/test_forecasting_pv_array_compatibility.py tests/test_external_site_access.py
python -m mypy app/forecasting --no-incremental
```

### R-6: Dashboard data

会計期間/日次集計、共通slice assembler、backend互換repository wrapperを順に分離する。backend固有SQL・Firestore readは統合しない。

必須テスト:

```powershell
python -m pytest -q tests/test_dashboard_data.py tests/test_dashboard_server.py tests/test_dashboard_backend_parity.py tests/test_firestore_dashboard_metrics.py
python -m mypy app/dashboard --no-incremental
```

### R-7: 最終検証と独立監査

```powershell
pwsh -NoProfile -File scripts/pre_release_local.ps1 -SkipInstall
python -m pytest -q
python -m mypy app scripts --no-incremental
python scripts/security_check.py
git diff --check
git status --short
```

さらに、R-0の所有表と実ファイル・呼出し経路を照合し、未移動実装、旧互換import、無根拠な完了記述が0件であることを独立監査する。失敗があれば新しい是正カードを作り、R-7をやり直す。

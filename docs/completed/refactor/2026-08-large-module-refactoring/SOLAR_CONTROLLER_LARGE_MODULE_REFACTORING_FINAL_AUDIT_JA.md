# SolarController 巨大モジュール分割 最終監査

監査日: 2026-08-02

## 監査範囲

R-0の所有表とR-1〜R-6の完了条件を、旧モジュールのtop-level定義、正規所有モジュール、呼出し経路、互換patch境界と照合した。モジュールの存在だけでは完了と判定していない。

## 独立監査結果

- R-1: 履歴I/O、気象I/O、温度/ridge/hourly補正、結果dict組立てはそれぞれ `correction_history_io.py`、`correction_weather.py`、`correction_model.py`、`correction_result.py` が所有する。`correction.py` は公開workflowと互換wrapperだけを保持する。
- R-2: 月次料金、PV候補/選択、不確実性/quality、最終output組立ては分離済みで、`workflow.py` はport配線と実行順を保持する。
- R-3: 時間ルール、CSV可視化、profile payload、HTTP client、HTML/auth supportは `rules.py`、`csv_visualization.py`、`profile_builder.py`、`client.py`、`client_support.py` が所有する。独立監査で検出した旧HTML/auth helper残存も追加是正した。
- R-4: command/retry本体、03時plan調整、slot orchestrationは `command_adapter.py`、`adjust03_plan.py`、`slot_orchestration.py` が所有する。`cloud_job.py` は安全上不可分なmonitor lifecycle、terminal transition、公開mainを保持する。
- R-5: provider mapping、calibration、ensemble/candidate選択は `pv_array_adapters.py`、`pv_array_calibration.py`、`pv_array_selection.py` が所有し、`pv_array.py` には公開workflowと配列物理計算だけが残る。
- R-6: 会計/日次集計、共通slice assembly、SQLite repository、backend互換repository objectは `aggregation.py`、`slice_assembler.py`、`sqlite_repository.py`、`backend_repositories.py` が所有する。backend固有SQL/Firestore readは各repository moduleに維持した。
- 旧モジュールのAST定義一覧と対象責務名を照合し、実装の二重保持は0件だった。互換名は直接importまたは薄いport wrapperで、計算・I/O実装を再保持していない。

## 最終検証

- `pwsh -NoProfile -File scripts/pre_release_local.ps1 -SkipInstall`: 成功
- `python -m pytest -q`: `438 passed, 1 skipped`
- `python -m mypy app scripts --no-incremental`: 0エラー（156 source files）
- `python scripts/security_check.py`: 成功
- `python -m compileall -q app`: 成功（pre-release内）
- `git diff --check`: 成功
- 実サービス接続、認証情報使用、Cloud/DB/Firestore変更: なし

## 判定

R-1〜R-6の指定責務は正規所有先へ移動され、R-7の全ゲートと独立構造監査に合格した。未完了項目は0件であり、この指示書一式をcompletedへ移送できる。

# SolerControler 13件修正作業 指示書インデックス

## 目的
この一式は、コード全体を一度に読み込まず、1件ずつ独立して修正できるようにした作業指示書である。
作業者またはAIは、最初にこのインデックスだけを読み、担当する1ファイルだけを追加で読むこと。

## 基準情報
- 対象: `C:\VSC\SolerControler`
- レビュー時HEAD: `5430f40`
- 関連テスト確認結果: `105 passed`
- レビュー時の作業ツリー: clean
- 注意: 既存テストが成功していても、再現済みの実害バグが残っている

## 実施順序
### 第1段階: 制御安全性・run整合性
1. `01_latest_schedule_event_overwrite.md`
2. `02_initial_realtime_soc_failure.md`
3. `03_logout_failure_overrides_soc.md`
4. `04_monitor_soc_unavailable_fail_open.md`

### 第2段階: SOC入力の信頼性
5. `05_battery_quarter_icon.md`
6. `06_soc_clamp_hides_invalid_data.md`

### 第3段階: 説明可能性・Firestore・表示整合性
7. `07_objective_explanation_mismatch.md`
8. `08_firestore_bounds_only_sunshine.md`
9. `09_firestore_cache_key.md`
10. `10_daily_review_date_mismatch.md`
11. `11_night_grid_charge_fixed_window.md`

### 第4段階: 構造改善
12. `12_dashboard_html_giant_function.md`
13. `13_backend_loader_duplication.md`

## 依存関係
- 02でSOC取得APIとフォールバック構造を決めた後、03と04を実施する。
- 05と06は同じHTML解析関数を触る。作業は連続してよいが、コミットは分ける。
- 08を先に直してから10を直す。
- 01が完了して最新スケジュールが正しくなってから11を直す。
- 12と13は機能修正完了後に行う。先に大規模リファクタリングしない。

## 1件ごとの標準手順
1. 対象指示書だけを読む。
2. 指定された関数の現在コードを確認する。
3. 指示書記載の条件で問題を再現する。
4. 問題1件だけを修正する。
5. 指示書記載のテストを追加する。
6. 関連テストを実行する。
7. `git diff` で無関係な変更がないことを確認する。
8. 1件だけをコミットする。
9. 作業報告を残す。

## 全体完了時の確認
```powershell
python -m pytest -q tests/test_cloud_job_runner.py tests/test_kpnet_workflow.py tests/test_energy_model.py tests/test_soc_decision_feedback.py tests/test_soc_cost_optimizer.py tests/test_dashboard_data.py tests/test_operations_db.py
```

可能なら:
```powershell
python -m pytest -q
```

## 作業報告テンプレート
- 対応番号:
- 変更ファイル:
- 再現方法:
- 根本原因:
- 修正内容:
- 追加テスト:
- テスト結果:
- 仕様判断:
- 残課題:
- 互換性への影響:
- ロールバック方法:

## 共通禁止事項
- 例外を握りつぶして成功扱いしない。
- 欠損値と数値0を混同しない。
- 異なる日付・異なるrunの情報を混ぜない。
- 外部入力をclampで正常化しない。
- 表示説明と実計算を不一致のままにしない。
- 複数問題を1コミットへ混ぜない。

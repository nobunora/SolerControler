# SolerControler ロジック不整合分析 v3 検証報告書

作成日: 2026-07-04  
対象分析: `C:\Users\nobun\Downloads\SolerControler_logic_inconsistencies_2026-07-04_v3.md`  
対象リポジトリ: `C:\VSC\SolerControler`  
検証方法: 静的コード確認。パッチ適用なし。  

## 1. 結論

提示された分析内容は、主要な指摘について概ね妥当です。特に以下の4点は、現行コードでも高リスクの実不整合として確認できました。

1. KP-NET へ送信する停電時設定が差分判定対象から漏れている。
2. `confirm-failed` が記録されても KP-NET ワークフロー全体は正常終了扱いになる。
3. `night_charge_plan.json` の欠損・不適用状態が、設定適用前に十分に拒否されていない。
4. DB パイプラインが設定結果の成功/失敗を見ずに `battery_daily_metrics` を更新できる。

一方で、分析文書の提案には、即時適用すべきものと、設計変更として段階的に進めるべきものが混在しています。最初に実施すべきは、外部契約やDBスキーマを変えない小さな安全ゲートです。ダッシュボードの run/provenance 結合や `battery_daily_metrics` への由来カラム追加は重要ですが、影響範囲が大きいため第2段階以降が妥当です。

## 2. 検証前提

検証時点で作業ツリーには既存の未コミット変更がありました。今回の検証では既存変更を戻さず、新規報告書のみを追加対象とします。

分析ファイルに記載されていた主要ファイルの短縮 SHA256 は、現行リポジトリでも一致しました。

| ファイル | 短縮 SHA256 |
|---|---:|
| `app/kpnet_workflow.py` | `72DCC70ACE72` |
| `app/operations_db.py` | `4847EE4D847D` |
| `db_pipeline_main.py` | `B6E88264D0F1` |
| `cloud_job_runner.py` | `310D0451C5FB` |
| `tests/test_kpnet_workflow.py` | `3B8A2FF4A00E` |
| `tests/test_operations_db.py` | `1FBDD19E037C` |
| `tests/test_cloud_job_runner.py` | `7CC102DDF143` |

追加で `app/dashboard_data.py` も確認しました。短縮 SHA256 は `9A9A5E535D7C` です。

## 3. 指摘別の妥当性評価

| No | 指摘 | 妥当性 | 優先度 | 判断 |
|---:|---|---|---|---|
| 1 | 停電時設定が差分判定から漏れる | 確認済み | 高 | 即時修正 |
| 2 | `confirm-failed` が正常終了扱い | 確認済み | 高 | 即時修正 |
| 3 | `_load_night_charge_plan()` が plan quality と欠損値を検証しない | 確認済み | 高 | 即時修正 |
| 4 | `0/0` 計画が30分充電枠になり得る | 確認済み | 高 | 即時修正。ただし期待仕様の明文化が必要 |
| 5 | `cloud_job_runner._read_plan_meta()` が欠損/不正を `0.0` 化する | 確認済み | 高 | 即時修正 |
| 6 | `stage_partial=false` が設定適用スキップではない | 確認済み | 中-高 | ログ改善と入力検証を優先 |
| 7 | `_ensure_night_plan_available()` 最終分岐が日付再確認しない | 確認済み | 中-高 | 即時修正 |
| 8 | DB metrics が設定ステータスを見ずに upsert される | 確認済み | 高 | 即時修正 |
| 9 | `night_plan.result` fallback が不適用計画でも使われ得る | 確認済み | 高 | 即時修正 |
| 10 | 最新 settings run 選択が status を見ない | 確認済み | 中 | metrics gate を先に適用 |
| 11 | dashboard が別日/別runの完了イベントを混ぜ得る | 確認済み | 中-高 | 第2段階で修正 |
| 12 | `battery_daily_metrics` に provenance がない | 確認済み | 中 | スキーマ変更として別途設計 |
| 13 | `_refresh_plan_for_same_date_if_changed()` が settings 取り込みを明示的に無効化しない | 確認済み | 中 | 低リスク修正 |
| 14 | settings_events が複数の意味を混在させる | 確認済み | 中 | 短期は読み手側で分類、長期は設計整理 |

## 4. 根拠と詳細

### 4.1 KP-NET 停電時設定の差分検出漏れ

`app/kpnet_workflow.py` では `onPowerOutageMode` と `onPowerOutageChargePowerW` を payload に含めています。

- `app/kpnet_workflow.py:1371`
- `app/kpnet_workflow.py:1372`

しかし差分判定の `compare_keys` には含まれていません。

- `app/kpnet_workflow.py:1399`
- `app/kpnet_workflow.py:1414`

そのため、KP-NET 現在値との差分が停電時設定だけの場合、`changed_fields` が空になり、`skipped-no-change` として扱われます。これは実設定の未反映を正常扱いするため、安全上の影響があります。

適用すべき対応:

- `compare_keys` に `onPowerOutageMode` と `onPowerOutageChargePowerW` を追加する。
- `tests/test_kpnet_workflow.py` に停電時設定だけが異なるケースを追加し、`changed_fields` に該当キーが入ることを確認する。

影響範囲:

- KP-NET 設定差分判定。
- `skipped-no-change` の信頼性。
- 後続の DB metrics gate で `skipped-no-change` を成功扱いする前提。

### 4.2 `confirm-failed` がプロセス成功扱いになる

`confirm_setting()` が失敗した場合、現在は `setting_results` に `confirm-failed` を追加した後、`continue` します。

- `app/kpnet_workflow.py:1685`
- `app/kpnet_workflow.py:1689`
- `app/kpnet_workflow.py:1700`

一方、`run_kpnet_workflow()` は例外が出ない限り `return_code = 0` とします。

- `app/kpnet_workflow.py:1747`
- `app/kpnet_workflow.py:1754`
- `app/kpnet_workflow.py:1769`

このため、確認画面で失敗した設定がクラウド実行側では成功扱いになります。`cloud_job_runner._run()` は subprocess の exit code しか見ないため、この不整合は後続 DB 反映まで伝播します。

適用すべき対応:

- `confirm-failed` を summary に記録した直後、`RuntimeError` を raise する。
- `finally` で summary は保存されるため、監査情報は失われない。
- 設定シーケンスに複数 profile がある場合でも、1つでも確認失敗したら失敗終了にするのが妥当。

影響範囲:

- `kpnet_main.py` の終了コード。
- `cloud_job_runner.py` の retry/失敗判定。
- DB パイプラインの設定反映タイミング。
- テストでは fake client による `confirm_setting()` 失敗ケースを追加する必要があります。

### 4.3 `night_charge_plan.json` の検証不足

`_load_night_charge_plan()` は plan file の存在と `csv_paths` は確認しますが、`result` / `forecast` / `inputs` の型や `plan_quality.should_apply` を確認していません。

- `app/kpnet_workflow.py:455`
- `app/kpnet_workflow.py:459`
- `app/kpnet_workflow.py:464`
- `app/kpnet_workflow.py:465`
- `app/kpnet_workflow.py:476`
- `app/kpnet_workflow.py:477`

特に `required_night_charge_kwh` と `target_soc_7_percent` は、キー欠損時に `0.0` になります。この挙動は「正当なゼロ」と「欠損」を区別できません。

`energy_model_main.py` は `plan_quality` を生成しており、`forecast.date` や `target_soc_7_percent` が欠ける場合は `should_apply=false` にします。

- `energy_model_main.py:325`
- `energy_model_main.py:349`
- `energy_model_main.py:355`
- `energy_model_main.py:364`
- `energy_model_main.py:2121`

したがって、設定適用側が `plan_quality` を無視している点は設計上の穴です。

適用すべき対応:

- `result` / `forecast` / `inputs` が dict であることを確認する。
- `required_night_charge_kwh` と `target_soc_7_percent` のキー欠損を拒否する。
- NaN / inf / 不正文字列 / 負の必要充電量 / SOC 範囲外を拒否する。
- `forecast.date` 空文字を拒否する。
- 実設定適用時は `plan_quality.should_apply is False` を拒否する。

注意点:

- `required_night_charge_kwh == 0.0` 自体は即拒否しない方がよいです。既存テストにもゼロ値を有効ケースとして使うものがあります。
- `plan_quality` が存在しない古い fixture や過去 artifact をどう扱うかは互換性判断が必要です。実運用の設定適用では必須、テストや過去データ読取では互換維持、という分け方が現実的です。

### 4.4 `0/0` 計画が30分充電枠になる問題

`_build_dynamic_forced_profile()` では、必要充電量がゼロの場合 `duration_minutes_kwh = 0` になり、SOC rate 経路も使われなければ `duration_minutes = 0` になります。

- `app/kpnet_workflow.py:846`
- `app/kpnet_workflow.py:853`
- `app/kpnet_workflow.py:885`

その後、`charge_start_minute == charge_end_minute` になった状態で `_apply_fixed_time_rules()` に渡されます。固定ルール `forbid_same_start_end` は start=end を禁止するため、最小30分の枠に補正します。

- `app/kpnet_workflow.py:365`
- `app/kpnet_workflow.py:367`
- `app/kpnet_workflow.py:368`

既存テスト `test_build_dynamic_forced_profile_switches_discharge_start_by_forecast()` は `required_night_charge_kwh=0.0` かつ `target_soc_7_percent=0.0` の計画を使っていますが、充電開始時刻は検証していません。

- `tests/test_kpnet_workflow.py:410`
- `tests/test_kpnet_workflow.py:412`
- `tests/test_kpnet_workflow.py:413`
- `tests/test_kpnet_workflow.py:432`
- `tests/test_kpnet_workflow.py:434`

適用すべき対応:

- 仕様として「必要充電ゼロなら充電枠を変更しない」または「充電モードを無効化する」を明確にする。
- 少なくとも `duration_minutes <= 0` のときは、`forbid_same_start_end` により充電枠を30分へ広げないようにする。
- テストに `charge_start_h` / `charge_start_m` の期待値を追加する。

影響範囲:

- 03/adjust の夜間 profile 生成。
- ダッシュボードに表示される予定充電枠。
- KP-NET に送る充電開始/終了時刻。

### 4.5 Cloud runner 側の plan meta 読み取りが欠損を `0.0` にする

`cloud_job_runner._read_plan_meta()` は forecast/result/inputs を読み、欠損・不正値を `0.0` へ寄せています。

- `cloud_job_runner.py:61`
- `cloud_job_runner.py:67`
- `cloud_job_runner.py:70`
- `cloud_job_runner.py:71`

`plan_quality.should_apply` も読みません。これにより、設定適用に使ってはいけない plan が「必要充電ゼロ」の plan と同じ扱いになる可能性があります。

適用すべき対応:

- 欠損とゼロを区別する。
- 実設定適用前の meta 読み取りでは、不正な plan を例外にする。
- `plan_quality.should_apply is False` は 03 設定適用を止める。
- ログには plan invalid の理由を出し、`stage_partial=false` の通常経路とは区別する。

影響範囲:

- `_monitor_partial_forced_and_stop()`
- `_should_stage_partial_forced()`
- 03/adjust の強制充電開始判断。
- Firestore へ保存する 03 monitor schedule。

### 4.6 `stage_partial=false` は「設定しない」ではない

`_monitor_partial_forced_and_stop()` では `stage_partial` が false の場合、partial monitor をスキップしつつ dynamic night profile を適用します。

- `cloud_job_runner.py:755`
- `cloud_job_runner.py:759`
- `cloud_job_runner.py:765`
- `cloud_job_runner.py:770`

この挙動自体は、通常の非 partial plan では設計意図の可能性があります。ただし、不正 plan が `0.0` に丸められて `stage_partial=false` になった場合にも同じ経路へ入る点が危険です。

適用すべき対応:

- `stage_partial=false` 経路をなくすのではなく、その前に plan validation を入れる。
- ログ文言の `skip` は「partial monitor をスキップ」の意味だと分かる表現に変える。

影響範囲:

- `tests/test_cloud_job_runner.py:188` の現在期待値は、非 partial plan で forced dynamic profile と DB pipeline が実行されることを固定しています。
- validation を `_read_plan_meta()` 側に入れる場合、このテストは monkeypatch により大きく壊れにくいです。

### 4.7 `_ensure_night_plan_available()` の最終分岐

`_ensure_night_plan_available()` は最初の再生成や既存 plan 判定では target date を確認します。

- `cloud_job_runner.py:667`
- `cloud_job_runner.py:672`

しかし最終再生成後は `plan_path.exists()` のみで true を返します。

- `cloud_job_runner.py:691`
- `cloud_job_runner.py:692`

適用すべき対応:

- 最終 return を `plan_path.exists() and _night_plan_file_date(plan_path) == target_date` に変更する。
- 可能なら同じ場所で `plan_quality.should_apply` も確認する。ただし、この関数は「利用可能性」の責務に留め、適用可否は設定直前で再検証する方が安全です。

### 4.8 `_refresh_plan_for_same_date_if_changed()` の settings 取り込み

`_refresh_plan_for_same_date_if_changed()` は plan が変化したときに `db_pipeline_main.py` を直接実行しますが、環境変数で `DATA_PIPELINE_INCLUDE_SETTINGS=false` を明示していません。

- `cloud_job_runner.py:1047`
- `cloud_job_runner.py:1050`
- `cloud_job_runner.py:1052`

`db_pipeline_main.py` 側のデフォルトは `DATA_PIPELINE_INCLUDE_SETTINGS=true` です。

- `db_pipeline_main.py:329`
- `db_pipeline_main.py:330`

適用すべき対応:

- この refresh が forecast/plan DB 更新だけを目的とするなら、`DATA_PIPELINE_INCLUDE_SETTINGS=false` を明示する。
- 既存の最新 settings run を偶然再取り込みする副作用を避ける。

### 4.9 DB metrics の成功ステータス gate 不足

`db_pipeline_main.py` は settings summary を取り込んだ後、ステータスを見ずに `upsert_battery_daily_metrics()` を呼びます。SQLite/Postgres/Firestore の全経路で同様です。

- SQLite: `db_pipeline_main.py:95` から `db_pipeline_main.py:110`
- Postgres: `db_pipeline_main.py:182` から `db_pipeline_main.py:197`
- Firestore: `db_pipeline_main.py:264` から `db_pipeline_main.py:279`

`operations_db.upsert_battery_daily_metrics()` 自体も summary の `setting_results` の成否を確認しません。

- `app/operations_db.py:955`
- `app/operations_db.py:964`
- `app/operations_db.py:966`
- `app/operations_db.py:975`

適用すべき対応:

- settings audit の取り込みは維持する。
- dashboard-facing な `battery_daily_metrics` の upsert だけを成功ステータスで gate する。
- 成功扱い候補は `applied` と `skipped-no-change`。ただし `skipped-no-change` は停電時設定の compare fix 後に信頼できる。
- `dry-run-confirmed`、`confirm-failed`、`unknown`、`error`、空の `setting_results` は metrics 更新不可にする。

実装候補:

```python
SUCCESSFUL_SETTING_STATUSES = {"applied", "skipped-no-change"}

def settings_summary_successful(summary: dict[str, object]) -> bool:
    if summary.get("error"):
        return False
    results = summary.get("setting_results")
    if not isinstance(results, list) or not results:
        return False
    return all(
        isinstance(item, dict)
        and str(item.get("status", "")).strip() in SUCCESSFUL_SETTING_STATUSES
        for item in results
    )
```

配置方針:

- 最小修正なら `db_pipeline_main.py` に helper を置き、各 backend の `upsert_battery_daily_metrics()` 呼び出し前で判定する。
- 再利用性を重視するなら `app/operations_db.py` に helper を置き、Postgres/Firestore からも import する。ただし private helper の共有が増えるため、名前と責務を明確にする。

影響範囲:

- SQLite/Postgres/Firestore の metrics 更新。
- ダッシュボードの最新 battery metrics 表示。
- 失敗設定後に古い metrics が残る可能性があります。この場合は「更新しない」方が「失敗値で上書きする」より安全です。

### 4.10 `night_plan.result` fallback の扱い

`_extract_battery_daily_from_summary()` は `DATA_PREFER_NIGHT_PLAN_METRICS=false` でも、summary に値がない場合は `night_plan.result` を fallback として使います。

- `app/operations_db.py:425`
- `app/operations_db.py:429`
- `app/operations_db.py:431`
- `app/operations_db.py:435`
- `app/operations_db.py:437`
- `app/operations_db.py:441`

この helper は Postgres/Firestore からも使われます。

- `app/postgres_ops.py:617`
- `app/postgres_ops.py:619`
- `app/firestore_ops.py:466`
- `app/firestore_ops.py:468`

適用すべき対応:

- `night_plan.plan_quality.should_apply is False` の場合は `np_result = {}` として fallback を禁止する。
- `plan_quality` が欠けている古い fixture は従来通り fallback を許可する。

既存テストへの影響:

- `tests/test_operations_db.py:105` の fallback テストは `plan_quality` のない legacy plan を使っています。この互換方針なら既存テストを維持できます。
- 新規に `plan_quality.should_apply=false` のケースを追加し、fallback されないことを確認するべきです。

### 4.11 最新 settings run 選択が status を見ない

`find_latest_csv_and_settings_runs()` は `setting_results` が存在すれば latest settings として選びます。

- `app/operations_db.py:454`
- `app/operations_db.py:461`
- `app/operations_db.py:464`

これは audit 取り込みという意味では許容できます。しかし metrics 更新の入力として使うには危険です。

適用すべき対応:

- 短期: latest selection は維持し、metrics upsert 側で成功 gate する。
- 長期: `latest_settings_run_for_audit` と `latest_successful_settings_run_for_metrics` を分ける。

短期対応を優先する理由:

- 呼び出し元が多い探索関数の契約を変えずに済む。
- 失敗や dry-run の監査イベントを DB に残せる。
- dashboard-facing metrics の安全性だけを先に上げられる。

### 4.12 settings_events の意味混在

`settings_events` には実適用、no-change、dry-run、confirm-failed、将来予定が同じテーブル/collection に入ります。

- `app/operations_db.py:260`
- `app/operations_db.py:748`
- `db_pipeline_main.py:30`
- `db_pipeline_main.py:45`
- `app/firestore_ops.py:210`
- `app/firestore_ops.py:260`

これは監査ログとしては自然ですが、ダッシュボードが「設定完了」を判断するデータとしては分類が必要です。

適用すべき対応:

- 短期: ダッシュボード側で `status` と `detail_json.schedule_source` と `plan_date` を厳密に見る。
- 長期: event type、actual/planned/dry-run/failure の分類フィールドを追加する。

### 4.13 dashboard の完了判定混在

`_build_latest_schedule_from_events()` は、イベントの `detail_json` や `plan_date` を確認する前に `completed_row` を選んでいます。

- `app/dashboard_data.py:416`
- `app/dashboard_data.py:418`
- `app/dashboard_data.py:420`
- `app/dashboard_data.py:423`
- `app/dashboard_data.py:424`
- `app/dashboard_data.py:466`

そのため、別日または別性質の `applied` / `skipped-no-change` が、現在表示中 schedule の完了扱いに混ざる可能性があります。

また、SQL/Firestore から schedule builder へ渡すイベントは、run/provenance 情報を落としています。

- SQLite: `app/dashboard_data.py:862`
- Postgres: `app/dashboard_data.py:1118`
- Firestore: `app/dashboard_data.py:1397`

適用すべき対応:

- `completed_row` の選択を `detail_json` と `plan_date` フィルタ後に移動する。
- `estimated-from-night-kwh` は設定完了扱いにしない。
- 短期では `run_id` なしでも plan date の混在を抑えられます。
- 長期では `run_id` / `source_doc_id` / `event_id` を query と schedule builder に渡す。

影響範囲:

- ダッシュボードの「設定完了未確認」警告。
- 最新スケジュール表示。
- Firestore/SQLite/Postgres の取得カラム。

### 4.14 `battery_daily_metrics` に provenance がない

SQLite の `battery_daily_metrics` には date と metrics はありますが、どの settings run / source doc / status から来た値かを持ちません。

- `app/operations_db.py:281`
- `app/operations_db.py:289`

Postgres も同様です。

- `app/postgres_ops.py:114`
- `app/postgres_ops.py:122`

Firestore upsert でも同様に、metrics 値中心の document です。

- `app/firestore_ops.py:477`
- `app/firestore_ops.py:482`

適用すべき対応:

- これは即時安全修正ではなく、スキーマ変更として別パッチにする。
- 候補フィールド:
  - `settings_run_id`
  - `source_doc_id`
  - `source_status`
  - `source_profile`
  - `plan_quality_status`
  - `plan_should_apply`
  - `updated_from_summary_path` または backend に応じた source id

## 5. 実際に適用すべき対応内容

### Phase 1: 即時安全修正

外部契約やDBスキーマを変えず、失敗値・不正計画の反映を止める修正です。

1. `app/kpnet_workflow.py`
   - `compare_keys` に停電時設定2キーを追加する。
   - `confirm-failed` 記録後に `RuntimeError` を raise する。
   - `_load_night_charge_plan()` で欠損/型/非finite/範囲/forecast date を検証する。
   - 実設定適用時に `plan_quality.should_apply is False` を拒否する。
   - `duration_minutes <= 0` のとき、同一 start/end 補正で30分枠を作らない。

2. `db_pipeline_main.py`
   - settings summary の audit ingestion は残す。
   - `battery_daily_metrics` upsert だけ、成功ステータスの場合に限定する。
   - `applied` と `skipped-no-change` のみ成功扱いにする。

3. `app/operations_db.py`
   - `night_plan.plan_quality.should_apply is False` の場合、`night_plan.result` fallback を禁止する。
   - legacy plan で `plan_quality` がない場合は互換維持する。

4. `cloud_job_runner.py`
   - `_read_plan_meta()` で欠損/不正値を `0.0` に丸めない。
   - `plan_quality.should_apply is False` を設定適用前に拒否する。
   - `_ensure_night_plan_available()` 最終 return を日付一致確認つきにする。
   - `_refresh_plan_for_same_date_if_changed()` で `DATA_PIPELINE_INCLUDE_SETTINGS=false` を明示する。

### Phase 2: ダッシュボードの混在抑止

1. `app/dashboard_data.py`
   - `completed_row` 選択を detail/plan_date filter 後へ移動する。
   - `estimated-from-night-kwh` を設定完了扱いにしない。
   - 取得カラムに可能な範囲で `run_id` / `source_doc_id` / `event_id` を追加する。

2. テスト
   - 別日の `applied` が現在 plan の `settings_completed=true` に使われないことを確認する。
   - `planned-from-23` や `confirm-failed` が完了扱いにならないことを確認する。

### Phase 3: provenance 設計

1. `battery_daily_metrics` に由来情報を追加する。
2. settings event、night plan、battery metrics を date/run/source_doc_id で追跡可能にする。
3. dashboard 表示に「実適用値」「予定値」「推定値」「失敗/未確認」を明示的に分ける。

## 6. 影響範囲

### 6.1 実運用への影響

良い影響:

- KP-NET 設定失敗時に後続処理が成功扱いになりにくくなる。
- 不正または不完全な `night_charge_plan.json` が設定適用に使われにくくなる。
- dashboard-facing metrics が failed/dry-run で上書きされにくくなる。

注意点:

- これまで成功扱いだった一部の実行が失敗終了になる可能性があります。
- 古い artifact や `plan_quality` のない plan を実設定に使っていた場合、運用上の再生成が必要になる可能性があります。
- metrics gate により、失敗実行後は古い `battery_daily_metrics` が残る場合があります。これは安全側の挙動ですが、ダッシュボードには「未更新/未確認」警告が必要です。

### 6.2 テストへの影響

更新が必要な可能性が高いテスト:

- `tests/test_kpnet_workflow.py`
  - 停電時設定差分。
  - `confirm-failed` 非ゼロ終了。
  - plan validation。
  - `0/0` 計画時の充電開始時刻。

- `tests/test_cloud_job_runner.py`
  - `_read_plan_meta()` の欠損拒否。
  - `_ensure_night_plan_available()` の日付不一致拒否。
  - `_refresh_plan_for_same_date_if_changed()` の env 指定。

- `tests/test_operations_db.py`
  - `plan_quality.should_apply=false` で fallback しないケース。
  - legacy no-plan-quality fallback は維持。

- `tests/test_dashboard_data.py`
  - 別日 applied の完了判定混入防止。
  - estimated schedule と settings completion の分離。

### 6.3 DB/API への影響

Phase 1 では DB schema 変更は不要です。  
Phase 3 の provenance 追加では、SQLite/Postgres/Firestore の schema/document 変更が必要です。

外部 API 変更:

- Phase 1 では基本的に不要です。
- ただし `kpnet_main.py` の終了コード挙動は変わります。これは不具合修正として妥当です。

## 7. 推奨パッチ順序

1. KP-NET 差分検出と `confirm-failed` 非ゼロ化。
2. DB metrics gate。
3. `night_plan.result` fallback の `should_apply=false` 抑止。
4. plan validation と `0/0` 充電枠抑止。
5. cloud runner の plan meta validation と日付再確認。
6. dashboard の完了判定混在抑止。
7. provenance schema 追加。

この順序の理由:

- 1から3は失敗・不適用データが実績風に見える問題を小さく止められます。
- 4と5は制御ロジック側の安全性を上げます。
- 6と7は表示と追跡性の改善で、変更範囲がやや広くなります。

## 8. 検証すべきテスト観点

### KP-NET workflow

- 停電時設定だけが異なる場合、`changed_fields` に `onPowerOutageMode` または `onPowerOutageChargePowerW` が入る。
- `confirm_setting()` が `ok=False` を返す場合、summary に `confirm-failed` が残り、プロセスは非ゼロ終了する。
- `plan_quality.should_apply=false` の plan は dynamic forced profile 生成で拒否される。
- `required_night_charge_kwh` または `target_soc_7_percent` 欠損は拒否される。
- `required=0,target=0` が意図しない30分充電枠を生成しない。

### DB pipeline / operations DB

- `confirm-failed` summary は `settings_events` に記録されるが、`battery_daily_metrics` は更新されない。
- `dry-run-confirmed` summary は metrics を更新しない。
- `applied` summary は metrics を更新する。
- `skipped-no-change` summary は、停電時 compare 修正後に metrics を更新する。
- `plan_quality.should_apply=false` の night plan は `night_plan.result` fallback に使われない。

### Cloud runner

- `_read_plan_meta()` は欠損 target/required を拒否する。
- `_read_plan_meta()` は `plan_quality.should_apply=false` を拒否する。
- `_ensure_night_plan_available()` は最終再生成後も forecast date 一致を要求する。
- `_refresh_plan_for_same_date_if_changed()` は settings ingestion を明示的に無効化する。
- `stage_partial=false` の通常 plan では、現行意図通り dynamic night profile 適用が維持される。

### Dashboard

- plan date が違う `applied` event は current schedule の `settings_completed=true` に使われない。
- `planned-from-23` は完了扱いにならない。
- `estimated-from-night-kwh` は推定表示であり、設定完了とは扱わない。

## 9. 人間の確認が必要な点

1. `required=0,target=0` の正しい仕様
   - 充電枠を変更しないのか。
   - 充電モードを 0 にするのか。
   - それとも実設定適用では拒否するのか。

2. `plan_quality` がない古い plan の扱い
   - 実運用では拒否し、テスト/過去データ読取では許可するのが推奨です。

3. `skipped-no-change` を成功扱いする条件
   - 停電時設定の compare 修正後は成功扱いでよいと考えます。
   - 修正前に metrics gate だけ入れる場合、`skipped-no-change` の扱いは慎重に決める必要があります。

4. dashboard の provenance 設計
   - 短期は plan date filter で十分か。
   - 長期で run/source_doc_id を schema に入れるか。

## 10. 最終判断

分析ファイルの中心主張である「失敗・不完全・不適用の設定/計画が、成功または実績に近い形で後段へ伝播し得る」という指摘は妥当です。

ただし、すべてを一括で直すのではなく、まずは以下の最小安全修正を優先するべきです。

1. 停電時設定を差分判定に含める。
2. `confirm-failed` を非ゼロ終了にする。
3. `battery_daily_metrics` 更新を成功 settings summary に限定する。
4. `plan_quality.should_apply=false` の `night_plan.result` fallback を止める。
5. 設定適用前の night plan validation を強化する。

この5点で、最も危険な伝播経路を小さなパッチで遮断できます。その後、cloud runner の厳密化、dashboard の完了判定整理、provenance schema 追加へ進むのが妥当です。

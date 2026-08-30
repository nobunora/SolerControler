# CodebaseMemory 調査結果のトリアージ・保守方針

## 目的

2026-08-30 の CodebaseMemory 調査で得た未使用候補、低信頼 `CALLS`、`SIMILAR_TO` / `SEMANTICALLY_RELATED` を、誤削除・誤統合を起こさず実装へつなげるための判断基準を定める。

本書は次の調査結果を根拠にする。

- [`docs/completed/reports/codebase_memory_unused_similarity_20260830_ja.md`](../../completed/reports/codebase_memory_unused_similarity_20260830_ja.md)
- 同調査の raw evidence（既存の `.log`。今後の保存形式は後述のレポート規則に従う）
- 該当する production source、compatibility test、architecture ADR、agent rules

重要: CodebaseMemory の `in_degree = 0`、低 confidence、類似度は **修正命令ではない**。候補抽出に使い、最終判断はソース、テスト、公開/互換契約、動的呼び出し、依存方向を確認して行う。

---

## 1. すぐに対応できる項目

以下は現行ソースと 2026-08-30 の `rg` 結果を突き合わせた時点で、削除リスクが比較的低い。実装は各項目を別の論理パッチとして行い、cleanup と設計変更を混ぜない。

### 1.1 `correction_history_io._clip_float`

対象: [`app/forecasting/correction_history_io.py`](../../../app/forecasting/correction_history_io.py)

現状:

- private helper。
- CodebaseMemory は inbound 0 / outbound 0。
- `app/`, `scripts/`, `tests/` の直接検索でも定義以外の参照なし。
- 同ファイルの現在の履歴読込経路はこの helper を使わない。

実装方法:

1. `_clip_float` の定義だけを削除する。
2. import、例外処理、履歴取得ロジックには触れない。
3. 同名の別実装を「ついでに」統合しない。

検証:

- `python -m ruff check app/forecasting/correction_history_io.py`
- forecast correction / history 系の既存 pytest を実行する。
- CodebaseMemory を再 index し、削除シンボルが消え、周辺 call graph に予期しない変化がないことを確認する。

### 1.2 `cloud_job._estimate_required_charge_kwh`

対象: [`app/runtime/cloud_job.py`](../../../app/runtime/cloud_job.py)

現状:

- private helper。
- inbound 0 / outbound 0。
- `latest_soc_percent` を受け取るが使用しない。
- 現行 03 時制御は `_monitor_partial_forced_and_stop` が SOC を実測しながら制御しており、この helper を経由しない。

実装方法:

1. `_estimate_required_charge_kwh` の定義だけを削除する。
2. `HISTORICAL_FAILURE_LOCK` 配下の 03/07 ownership、06:55 fence、forced/standby sequence は変更しない。
3. `_read_plan_meta` の payload や `required_night_charge_kwh` 自体は削除しない。今回確認できたのは helper が未使用という事実だけであり、plan field の契約廃止を意味しない。

検証:

- `python -m ruff check app/runtime/cloud_job.py`
- `python -m pytest tests/test_cloud_job_runner.py tests/test_historical_failure_protection.py`
- 既存の 03 hard-fence / mismatch / standby regression が全て維持されること。
- CodebaseMemory の変更影響範囲を確認し、protected region への意図しない変更がないこと。

### 1.3 `analyze_hourly_weather_vectors._daily_from_hourly`

対象: [`scripts/analyze_hourly_weather_vectors.py`](../../../scripts/analyze_hourly_weather_vectors.py)

現状:

- private analysis helper。
- inbound 0 / outbound 0。
- 現行 `main` から参照されない。

実装方法:

- helper 定義のみ削除する。
- 分析 CLI の入力、出力 JSON/CSV、model feature は変更しない。

検証:

- `python -m ruff check scripts/analyze_hourly_weather_vectors.py`
- `python scripts/analyze_hourly_weather_vectors.py --help` が副作用なく成功すること（CLI が `--help` を提供する場合）。
- このスクリプトを対象にする既存の weather-analysis test があれば実行する。
- CodebaseMemory 再 index 後に当該 node が消えること。

### 1.4 `analyze_multi_day_weather_contribution._day_series`

対象: [`scripts/analyze_multi_day_weather_contribution.py`](../../../scripts/analyze_multi_day_weather_contribution.py)

現状:

- `_day_series` と `_rolling_daily_mean` は同じ trailing-day mean を計算する。
- `_day_series` は直接参照なし。
- `_rolling_daily_mean` は現在の `_build_rows` から使用される。

実装方法:

- **共通 helper を新設しない。** 未使用の `_day_series` だけを削除し、使用中の `_rolling_daily_mean` をそのまま残す。
- この削除を「DRY 化」の名目で feature construction まで広げない。

検証:

- `python -m ruff check scripts/analyze_multi_day_weather_contribution.py`
- weather contribution / impact analysis の既存 pytest を実行する。
- 同じ保存済み入力で分析出力が削除前後で不変であることを確認できる場合は比較する。

### 1.5 `.cbmignore`

root の [`.cbmignore`](../../../.cbmignore) で `docs/completed/reports/` を index 対象外にする。

理由:

- 2026-08-30 調査ではレポート追加後だけで node / edge が増加した。
- 完了済みレポートは証跡であり、現在の実装・設計そのものではない。
- 過去の調査文書を再び `Section` / semantic edge として index すると、古い判断が次の類似検索に混ざる自己参照ノイズになる。

除外してはいけないもの:

- `docs/current/`
- `AGENTS.md`
- `tests/`
- production source

特に `tests/` は低信頼 `CALLS` の一因でもあるが、compatibility contract と `TESTS` edge の根拠でもあるため、ノイズ対策として丸ごと除外してはならない。

検証:

1. CodebaseMemory を再 index。
2. `status=ready` を確認。
3. `docs/completed/reports/` の `File` / `Section` が新規 index 対象から外れることを確認。
4. `docs/current/`、source、tests が引き続き検索できることを確認。
5. node / edge 数の減少そのものではなく、必要な code graph が残ることを成功条件にする。

---

## 2. 注意しながら対応すべき項目

### 2.1 `_archive_weather_rows` の削除候補

対象: [`app/energy_plan/workflow.py`](../../../app/energy_plan/workflow.py)

現状:

- `_archive_weather_history(...)` は `WeatherHistoryFetchResult` を返し、`_DefaultWeatherHistoryPort.load_history()` が現行経路として使用する。
- `_archive_weather_rows(...)` は同じ `archive_weather_history(...)` を呼び、その `.rows` だけを返す旧 wrapper 形状。
- repo 内直接検索では定義以外の参照なし。

ただし、`workflow.py` の private helper を過去の test / script が直接 import していた可能性や、未追跡のローカル運用コードを静的調査だけで完全には否定できない。

実装前確認:

1. repo 全体で `_archive_weather_rows` の直接 import / attribute access / string reference を検索する。
2. Git history で導入目的を確認し、`_archive_weather_history` への移行時に compatibility seam として意図的に残されたものか確認する。
3. `docs/current/` と ADR で旧 return contract (`list[dict]`) を外部契約として約束していないか確認する。

確認後に旧互換契約が存在しない場合の実装:

- `_archive_weather_rows` のみ削除する。
- canonical `archive_weather_history` と `_archive_weather_history` は保持する。
- weather cache、chunking、timeout、partial-result diagnostics は変更しない。

検証:

- `python -m pytest tests/test_energy_model.py`
- 特に archive weather の cache hit、partial chunk、timeout/HTTP/JSON error classification test を維持する。
- `python -m ruff check app/energy_plan/workflow.py app/energy_plan/weather_history.py`
- CodebaseMemory 再 index 後に weather-history call path を確認する。

互換用途が確認できた場合:

- 削除しない。
- wrapper に compatibility seam である理由を短いコメントで明示し、対応する compatibility test を追加する。

### 2.2 `_run_optional` の廃止候補

対象: [`app/runtime/command_adapter.py`](../../../app/runtime/command_adapter.py)

現状:

- `_run_optional` は `_run` の単純 alias ではなく、例外を捕捉して optional step を non-fatal にする意味を持つ。
- 現行 Cloud Job の直接経路からは参照が見つからない。
- 現在の runtime には、失敗を見えなくしないことが重要な hard-fence / read-back contract がある。

実装前確認:

1. `_run_optional` の import、monkeypatch、文字列参照を repo 全体で検索。
2. Git history で optional step を廃止した変更とセットで残ったものか確認。
3. production / operator script が `app.runtime.command_adapter._run_optional` を直接 import していないか、運用文書も確認。

未使用が確定した場合の実装:

- `_run_optional` だけを削除。
- `_run`、timeout、process-group kill、retry policy、secret masking は変更しない。
- 「unused cleanup」と「例外方針変更」を同じ patch にしない。

検証:

- `python -m ruff check app/runtime/command_adapter.py app/runtime/cloud_job.py`
- `python -m pytest tests/test_cloud_job_runner.py tests/test_historical_failure_protection.py`
- command retry / timeout を扱う既存 test があれば同時に実行。

### 2.3 weather code -> class の重複解消

対象:

- [`app/energy_plan/weather_history.py`](../../../app/energy_plan/weather_history.py) の `weather_class`
- [`app/forecasting/pv_array_calibration.py`](../../../app/forecasting/pv_array_calibration.py) の `_weather_class_from_code`

両者は Open-Meteo weather code を `clear/cloudy/fog/rain/snow/storm/other` に分類する同一ロジックを持つ。これは、将来片側だけ変更される semantic drift の危険があるため、共通化する価値が高い。

ただし [`ADR 0003`](../architecture/adr/0003-energy-plan-boundaries.md) は forecasting と energy_plan の責務分離を定めている。片方からもう片方へ import して重複を消すのは避ける。

推奨実装:

1. 中立な pure domain module（例: `app/domain/weather.py`）を作成する。
2. canonical 関数 `weather_class_from_code(weather_code: int | None) -> str` をそこへ移す。
3. `energy_plan.weather_history.weather_class` は既存 import contract を壊さないため、当面 canonical 関数への薄い compatibility wrapper とする。
4. `pv_array_calibration._weather_class_from_code` も既存 monkeypatch / private import の有無を確認し、必要なら薄い wrapper として一段残す。契約がなければ直接 canonical を利用する。
5. classification table を一箇所だけにする。

新規 test:

- `tests/test_domain_weather.py` などに table-driven test を置き、少なくとも次を固定する。
  - `None -> unknown`
  - `0 -> clear`
  - `1, 3 -> cloudy`
  - `45, 48 -> fog`
  - `51, 67, 80, 82 -> rain`
  - `71, 77, 85, 86 -> snow`
  - `95, 99 -> storm`
  - 未分類 code -> `other`

回帰 test:

- `python -m pytest tests/test_domain_weather.py tests/test_energy_model.py tests/test_pv_array_forecast.py tests/test_forecasting_pv_array_compatibility.py`
- `python -m ruff check app/domain/weather.py app/energy_plan/weather_history.py app/forecasting/pv_array_calibration.py`
- dependency direction を確認し、forecasting と energy_plan の相互 import を新設しない。

### 2.4 dotenv loader の重複解消

対象:

- canonical: [`app/configuration/environment.py`](../../../app/configuration/environment.py) `load_dotenv_if_present`
- duplicate: [`scripts/backup_drive.py`](../../../scripts/backup_drive.py) `_load_dotenv`

`scripts/backup_drive.py` は既に repo root を `sys.path` に追加して `app.*` を import しているため、単独 script 起動を維持したまま canonical helper を利用できる構造になっている。

推奨実装:

1. `scripts/backup_drive.py` で `from app.configuration.environment import load_dotenv_if_present` を import。
2. duplicate `_load_dotenv` を削除。
3. `main()` の `_load_dotenv()` を `load_dotenv_if_present()` に置換。
4. parser、Drive backup、secret handling、環境変数名は変更しない。

追加/回帰 test:

- canonical helper に対して quote stripping、comment/blank skip、既存 `os.environ` を上書きしない `setdefault` semantics を test する。
- script import 後に canonical helper を使用できることを test する。
- `python -m pytest tests/test_configuration_environment.py tests/test_drive_backup.py`
- 必要なら `tests/test_backup_drive_script.py` を追加して script entry point の `.env` load contract を直接固定する。
- `python -m ruff check app/configuration/environment.py scripts/backup_drive.py`

### 2.5 `pv_array` の compatibility wrappers

対象: [`app/forecasting/pv_array.py`](../../../app/forecasting/pv_array.py)

少なくとも `_response_json_object`、`_http_get_with_retry`、`_provider_order_from_env` はソース上で compatibility wrapper と明示されている。`_parse_time` / `_parse_forecast_solar_time` も provider adapter 分離前の seam である可能性がある。

対応:

- 現時点では「直接参照がない」という理由だけで一括削除しない。
- 廃止する場合は compatibility-retirement 専用 patch とする。
- legacy module identity を固定する [`tests/test_forecasting_pv_array_compatibility.py`](../../../tests/test_forecasting_pv_array_compatibility.py) を読み、公開 surface と private test monkeypatch を先に確認する。
- wrapper を残す場合は compatibility 理由を docstring/comment で統一する。

---

## 3. 統合してはいけない、または類似度だけでは統合しないもの

CodebaseMemory の `SIMILAR_TO` / `SEMANTICALLY_RELATED` は「レビュー候補」であり、「共通化候補」ではない。以下は同形でも責務境界が異なるため、類似度だけで統合しない。

### 3.1 domain-specific JSON readers

- [`app/backup/night_plan_archive.py`](../../../app/backup/night_plan_archive.py) `read_plan_file`
- [`app/operations/domain.py`](../../../app/operations/domain.py) `read_summary`

両方とも JSON object を読むが、エラー文言と domain boundary が異なる。3 行程度の重複を消すために generic helper と object-name parameter を増やすと、意味が薄くなる。

原則: **統合しない。**

再発防止コメントを置く場合の推奨形:

```python
# codebase-memory: keep-separate — domain-specific JSON boundary and error contract;
# do not genericize this reader based on similarity alone.
```

### 3.2 Firestore / SQLite / Postgres の persistence helper

同名・同形なのは adapter parity の結果であり、backend-specific transaction、query、error、typing の境界を守るための意図的な重複である。

原則:

- backend 実装を「似ている」という理由だけで一つの巨大 helper にしない。
- 共通化するなら backend-independent model / normalization のみ。
- parity test は残す。

推奨コメント:

```python
# codebase-memory: keep-separate — backend adapter parity is intentional;
# preserve backend-specific query/error/transaction boundaries.
```

### 3.3 standalone PowerShell operational scripts

`Invoke-GCloud`、state/error helper 等が類似していても、各 script が独立 entry point として validation、secret handling、resume/stop condition を持つ場合は安易に共通化しない。

特に CodebaseMemory が PowerShell script を部分解析している現状では、類似 edge は追加調査の入口に留める。

推奨コメント:

```powershell
# codebase-memory: keep-separate — standalone operational entry point;
# preserve independent validation, secret handling, and failure boundary.
```

### 3.4 test fake / fake response / fake client

SDK-shaped fake が `stream`, `order_by`, `create`, `update`, `execute` 等の同名 method を持つのは意図的な duck typing である。テスト間で「似ている」ことだけを理由に巨大 fake framework へ統合しない。

### 3.5 診断 CLI の `main`

診断 CLI は bootstrap、argument parse、出力整形が似やすい。一方で入力契約・診断目的・成果物が異なる。Jaccard が高くても、共通化により CLI の独立再現性が落ちる場合は統合しない。

### 3.6 ルール

CodebaseMemory が類似実装を提示した場合、統合前に必ず次を確認する。

1. owner / domain responsibility は同じか。
2. public/legacy/monkeypatch contract は同じか。
3. error message / exception semantics は同じか。
4. standalone entry point である必要はないか。
5. backend-specific behavior はないか。
6. dependency direction を悪化させないか。
7. 共通化後の abstraction が元コードより明瞭か。

1 つでも「異なる」があり、その差が設計上意味を持つ場合、類似度だけでは統合しない。

`codebase-memory: keep-separate` コメントは、同じ intentional duplication が繰り返し誤提案される箇所にのみ追加する。全ファイルへ機械的に付与しない。

---

## 4. 低信頼 `CALLS` の深掘り結果

2026-08-30 の調査では `CALLS` 4,445 件中 confidence < 0.5 が 692 件だった。

- builtins 宛先: 240
- tests 宛先: 215
- その他: 237

### 4.1 ソースを直してはいけない例: `datetime.now()` の誤結合

[`app/backup/artifacts.py`](../../../app/backup/artifacts.py) の `collect_cleanup_candidates` は明示的に次を呼ぶ。

```python
current = now or datetime.now()
```

ところが低信頼 edge の代表例では `_SystemMonitorClock.now` へ近似解決されている。`_SystemMonitorClock.now` は [`app/runtime/cloud_job.py`](../../../app/runtime/cloud_job.py) の別 class method であり、この cleanup code とは無関係である。

判定:

- source は十分明示的である。
- method 名 `now` の suffix/unique-name 近似が誤った target を選んだ graph false positive。
- production code を rename / wrapper 化して CodebaseMemory に合わせてはいけない。

対応:

- false-positive regression case として記録する。
- blast-radius 判断ではこの edge を根拠にしない。
- CodebaseMemory 側で再現可能なら upstream issue / parser improvement 候補とする。

### 4.2 ソースを直してはいけない例: Firestore fluent query

[`app/dashboard/firestore_repository.py`](../../../app/dashboard/firestore_repository.py) は Google Cloud Firestore の query object に対して `order_by(...).limit(...).stream()` や `q.stream()` を呼ぶ。`client` / query は外部 SDK object で、境界では `Any` を使う箇所がある。

テスト側にも Firestore-shaped fake の `stream()` が存在する。また [`tests/test_drive_backup.py`](../../../tests/test_drive_backup.py) には Drive/Firestore を模した `stream`, `create`, `update`, `execute` 等の fake method がある。

このため suffix resolver が production SDK call を test fake method へ結ぶことがある。

判定:

- production query を fake 名に合わせて rename しない。
- test fake を graph のためだけに共通化/rename しない。
- SDK object 全体へ Protocol を追加するのも、type checker / design 上の利益がない限り CodebaseMemory のためだけには行わない。

### 4.3 builtins 240 件

`dict.get`, `str.lower`, `list.append` 等への低信頼 edge は dead-code 判定上のノイズとして扱う。production source の修正対象にしない。

### 4.4 tests 宛先 215 件

fake / monkeypatch / fixture / protocol-shaped object は実行時 dispatch を模倣するため、同名 method が多数存在する。これも原則 source 修正対象ではない。

ただし、test code 自体が production API と異なる fake signature を持ち、実際の契約を誤って表現していることがソース確認で判明した場合は test defect として直す。**低 confidence だけを理由には直さない。**

### 4.5 直接 call の解決漏れ

`pv_array_calibration._weather_class_from_code` はソース上で直接呼ばれているにもかかわらず、局所的に inbound 0 と見えた例がある。

判定:

- readable な direct call を CodebaseMemory のために書き換えない。
- この種のケースは index/parser quality の regression sample として扱う。

### 4.6 「直すべきもの」の定義

低信頼 edge を見て source を直してよいのは、**ソース確認で実際の設計/可読性問題が独立して確認できた場合だけ**とする。

修正候補になる例:

- app 内で同名 private helper が複数あり、import/alias が人間にも曖昧で、誤った helper を呼ぶ危険がある。
- stale wrapper / stale alias が残り、現在の canonical owner が不明瞭になっている。
- dynamic string dispatch が registry/contract なしに増殖し、rename safety が実際に失われている。
- fake signature が production SDK / protocol contract と一致しておらず、test が実動作を保証していない。

修正対象にならない例:

- builtins の同名 method。
- 外部 SDK の fluent/dynamic method。
- 意図的な test fake。
- explicit qualified call が CodebaseMemory 側だけで誤解決されたもの。
- domain/back-end 境界を守る intentional duplication。

### 4.7 残り 237 件の扱い

現在 GitHub に保存された raw log は「その他 237 件」という集計までで、各 caller/callee/file/confidence/解決戦略を保存していない。したがって **237 件を全件分類済みとは主張しない。**

次回のローカル CodebaseMemory 調査では、低信頼かつ builtins/tests を除いた edge について、最低限次を raw evidence に出力する。

- caller qualified name
- caller file path / line
- callee qualified name
- callee file path / line
- confidence
- resolution strategy (`suffix_match`, `unique_name` 等)

その一覧を次の順に分類する。

1. explicit direct call をソースで確認。
2. external SDK / builtin / fake / protocol / dynamic dispatch か確認。
3. app-to-app edge だけ、import と実 call path を確認。
4. 実コードに独立した問題がある場合だけ source fix 候補にする。
5. graph だけが誤っている場合は source を変えず、CodebaseMemory false positive として記録する。

優先順位は安全クリティカル / production mutation / money / persistence / Cloud Job control を先にし、692 件を一括修正しない。

---

## 5. CodebaseMemory を使った削除・統合の必須手順

### 未使用候補

1. index `ready` を確認。
2. inbound/outbound / `CALL_REFERENCE` を確認。
3. `rg` 等で直接参照、import、`getattr`、string dispatch、monkeypatch を確認。
4. compatibility test / legacy module / standalone script を確認。
5. source history が必要なら確認。
6. 最小削除を実装。
7. focused test。
8. CodebaseMemory 再 index と impact review。

### 類似候補

1. similarity score を根拠に実装しない。
2. それぞれの owner、input/output、error、external contract を読む。
3. dependency direction を確認。
4. intentional duplication なら keep-separate comment または本書の rule で保護。
5. semantic drift の実害がある pure logic だけ中立 owner へ寄せる。

---

## 6. レポートファイルのルール

詳細ルールは [`report_template.md`](report_template.md) と合わせて適用する。

### 保存条件

- `docs/completed/reports/` に新規レポートを作るのは user が明示的に依頼した場合だけ。
- 通常の作業結果は PR body / final report で足りるなら、新しい completed report を作らない。

### リンク

- tracked Markdown から `C:\...`、`C:/...`、`/home/...` 等のローカル絶対パスへリンクしない。
- repo 内ソースは repository-relative Markdown link を使う。
- ローカルパスが再現情報として必要なら plain text の環境例として示し、クリック可能な source link の代わりにしない。

### raw evidence

- `.gitignore` が `*.log` を除外しているため、tracked evidence のために `.log` を force-add しない。
- raw command evidence を Git に残す必要がある場合は、明示的な user request の下で `.txt` または `.md` を使い、秘密情報を除去する。
- 大量ログは tracked report に貼らず、必要な command と要約、判定根拠だけを残す。

### 秘密情報

- `.env` の値、token、credential、project/account identifier 等の秘密・運用上の sensitive value を記録しない。
- 必要なら key の存在、masked value、non-sensitive status のみ記録する。

### CodebaseMemory

- completed reports は `.cbmignore` で index から除外する。
- current architecture / agent rules は index する。
- 過去レポートの記述より現在の source / test / current docs を優先する。

### 調査レポートの最低限の再現情報

- repository / branch または commit
- tool version
- index status
- scope / exclusions / partial parse
- 実行した query / search の要約
- static-analysis limitation
- 「候補」と「確定事実」の区別
- source/test での検証結果
- 未確認事項

---

## 7. 実装順序

推奨順序は次の通り。

1. `.cbmignore` とレポート/トリアージルールを導入。
2. 4 件の低リスク dead code を、それぞれ最小 cleanup として削除。
3. weather classification を neutral domain owner へ一本化。
4. dotenv loader を canonical helper へ寄せる。
5. `_archive_weather_rows` / `_run_optional` / pv_array compatibility seam を履歴・契約確認後に個別判断。
6. 低信頼 app-local 237 edge の詳細一覧を取得し、source defect と graph false positive を分離。
7. intentional duplication で誤提案が繰り返される箇所だけ `codebase-memory: keep-separate` コメントを追加。

この順序なら、CodebaseMemory の価値を利用しつつ、解析ツールに production code を合わせる本末転倒な変更を避けられる。

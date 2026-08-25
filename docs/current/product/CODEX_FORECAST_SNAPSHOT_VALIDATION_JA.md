# Codex向け: forecast snapshot Phase 0 実機・実データ検証指示

## 目的

PR #6 / #7 で追加した append-only forecast snapshot が、実際の運用データに対して次の目的を満たすことをローカル環境で確認する。

1. 同じ対象日時に対する複数の forecast vintage を失わず保存できる。
2. forecast が実際に生成・固定された時刻を基準に `lead_minutes` を再現できる。
3. `forecast weather / shortwave -> provider GTI / physical PV -> final PV forecast` を同じ vintage 内で追跡できる。
4. 既存の latest-value contract (`forecast_hourly`) を壊さない。
5. 将来の walk-forward 評価で、cutoff 後に発行された forecast を誤って利用しない。

この作業では **production の forecast correction、PV予測式、SOC optimizer、充放電判断ロジックを変更しないこと**。

---

## 0. 作業開始条件

- `master` 最新を取得して作業する。
- `AGENTS.md` と `docs/current/agent/agent_working_rules.md` を先に読む。
- テスト実行前に repository の code-quality audit を実行する。
- 実DBを直接破壊的に変更しない。SQLite検証は必ずコピーを使用する。
- credential、project ID、site ID、個人情報を commit / log / fixture に含めない。

今回の検証でコード修正が必要になった場合は、原因を再現する focused test を先に追加し、最小変更に留めること。

---

## 1. PR #7 の focused validation

まず repository の通常手順に従って quality audit を実行し、その後以下を確認する。

```powershell
python -m pytest tests/test_forecast_snapshot.py tests/test_energy_plan_output.py -q
```

必要に応じて full suite も実行する。

```powershell
python -m pytest -q
```

### 判定

- `tests/test_forecast_snapshot.py` が全件成功すること。
- `tests/test_energy_plan_output.py` が成功すること。
- repository 全体 quality に既知の無関係な失敗がある場合は、今回変更由来か既存 debt かを分離して報告すること。
- 今回変更した `app/operations/forecast_snapshot.py` / `app/energy_plan/output.py` に新規の型・lintエラーを残さないこと。

---

## 2. 実際の `night_charge_plan.json` の構造確認

ローカル運用で生成された最新の `night_charge_plan.json` を使い、秘密情報を出力せず、以下のフィールドだけを確認する。

### 必須確認項目

- top-level `generated_at`
- `forecast.date`
- `forecast.source`
- `forecast.hourly_weather`
- `pv_array_forecast.source`
- `pv_array_forecast.provider`
- `pv_array_forecast.hourly`
- ensemble 使用時の `pv_array_forecast.provider_forecasts`
- `pv_array_forecast.calibration.effective_factor` または `factor`
- `result.final_pv_forecast_source`
- `daytime_soc_optimization.hourly_pv_forecast_kwh`

### 特に確認すること

1. `generated_at` がUTCの timezone-aware timestampであること。
2. `pv_array_forecast.hourly` の対象hourと `forecast.hourly_weather` のhourが対応すること。
3. ensembleの場合、selected hourly rowとprovider別hourly rowを同じhourで追跡できること。
4. Open-Meteo physical forecastを使うhourについて、可能な場合は per-array GTI (`*_gti_w_m2`) がplan内に存在すること。
5. final PV forecastとphysical PV forecastが同一概念として誤って上書きされていないこと。

値そのものをREADMEやPRへ貼らず、フィールドの有無・型・対応関係だけ報告すること。

---

## 3. snapshot builder を実planで dry-run

実planを読み込んで `build_forecast_snapshot_rows(...)` を呼び、DBへ書き込む前に生成行を検査する。

確認対象は、少なくとも日中3時間（午前・正午付近・午後）と夜間1時間。

各行で以下を検証する。

- `snapshot_id`
- `forecast_run_id`
- `issued_at`
- `issued_at_source`
- `target_at`
- `lead_minutes`
- `forecast_pv_kwh`
- `forecast_shortwave_radiation_w_m2`
- `forecast_cloud_cover`
- `forecast_provider`
- `pv_provider`
- `pv_input_source`
- `pv_model_source`
- `physical_pv_kwh`
- `forecast_pv_calibration_factor`
- `pv_forecast_detail_json`
- `quality_flags_json`

### 期待値

- `issued_at_source` は通常 `plan` になること。
- `lead_minutes = target_at - issued_at` が時差込みで正しいこと。
- 日中PVが正のとき shortwave が欠損/0なら quality flag が立つこと。
- physical sourceを使用しているのにphysical evidenceが取れない場合、`missing_physical_pv_detail` が立つこと。
- 夜間0 PVを異常として誤判定しないこと。

---

## 4. SQLite migration を本番DBコピーで検証

### 絶対条件

**本番SQLiteファイルそのものにはテストを書き込まない。**

検証用コピーを作り、そのコピーに対して実行する。

### migration前確認

```sql
PRAGMA table_info(forecast_hourly_snapshots);
SELECT COUNT(*) FROM forecast_hourly_snapshots;
```

### snapshot persistence実行後

```sql
PRAGMA table_info(forecast_hourly_snapshots);

SELECT
    date,
    hour,
    issued_at,
    lead_minutes,
    forecast_pv_kwh,
    physical_pv_kwh,
    forecast_provider,
    pv_provider
FROM forecast_hourly_snapshots
ORDER BY date DESC, hour, issued_at;
```

### 合格条件

- PR #6以前/直後に作られた旧schemaから migration が成功する。
- 既存snapshot件数・既存rowが消えない。
- `pv_provider`, `physical_pv_kwh`, `pv_forecast_detail_json` が追加される。
- 既存 `forecast_hourly` のschema/最新値 semantics が変化しない。
- 同じ `(date, hour)` に複数snapshotを保持できる。

---

## 5. vintage identity / idempotency の実データ検証

ここは最重要項目。

### Case A: 同一planの再取込

同じ `night_charge_plan.json` を同じ内容のまま2回snapshot persistenceする。

期待値:

- 1回目: snapshot追加
- 2回目: 追加0
- snapshot総数は増えない
- 既存rowを書き換えない

### Case B: 同じtarget date/hourでforecastを再生成

同じ対象日について、異なる時刻にplanを再生成する。実サービスを呼び直す必要がある場合は通常の安全なローカル運用手順に従う。

期待値:

- 新しい `generated_at`
- 新しい `forecast_run_id`
- 新しい `snapshot_id`
- 同一 `(date, hour)` に旧/new両vintageが残る

### Case C: 同じpipeline inputだがforecast内容だけ変わる

CSV/settings run identityが同一でも、planの生成時刻またはforecast内容が異なれば別vintageとして残ることを確認する。

これが成立しない場合は blocker とする。

---

## 6. operational cutoff leakage test

`select_latest_snapshot_before(...)` を実snapshot相当データで確認する。

例:

- vintage A: 00:00発行
- vintage B: 02:00発行
- operational cutoff: 01:00

期待値:

- Aのみeligible
- Bは必ず除外

さらにtimezone表現が異なる同一時刻でも比較が正しいことを確認する。

例:

- `2026-08-26T00:00:00+09:00`
- `2026-08-25T15:00:00Z`

この比較でfuture leakageが起きる場合は blocker。

---

## 7. latest contract 非回帰確認

snapshot機能は additive evidence store であり、既存consumerは引き続き `forecast_hourly` をlatest forecastとして読める必要がある。

確認すること:

1. 同じtarget dayを再取込した場合、`forecast_hourly` は従来通り最新値になる。
2. `forecast_hourly_snapshots` は旧vintageも保持する。
3. dashboard / correction history / SOC optimizer の読み取り先が意図せずsnapshot tableへ切り替わっていない。
4. snapshot保存失敗時に既存forecast計算ロジックを別アルゴリズムへfallbackさせる変更を入れない。

---

## 8. Firestore / PostgreSQL

利用可能なローカル・テスト環境がある場合のみ確認する。productionへの試験書込みは禁止。

### Firestore

- `forecast_hourly_snapshots/{snapshot_id}` がappend-only semanticsを保つ。
- 同一snapshot再実行でoverwriteしない。
- `pv_forecast_detail` がnested objectとして保存される。
- 同じtarget hourの別vintageは別documentになる。

### PostgreSQL

- 旧tableへの `ADD COLUMN IF NOT EXISTS` が再実行可能。
- `pv_forecast_detail_json` がvalid JSONBとして保存できる。
- `ON CONFLICT(snapshot_id) DO NOTHING` がidempotentに動く。

環境がなければmock/focused testのみでよく、接続情報を新規作成しないこと。

---

## 9. Codexが修正してよい範囲

検証で不具合が見つかった場合、原則として以下だけを変更対象とする。

- `app/operations/forecast_snapshot.py`
- snapshot integrationに必要な最小限の `app/operations/workflow.py`
- `app/energy_plan/output.py` のgenerated timestamp contract
- 対応するfocused tests
- この検証文書

### 原則変更禁止

- `app/forecasting/correction_model.py` の補正式/parameter
- adaptive gate のproduction化
- PV physical model の係数・式
- provider weight
- SOC optimizer objective/constraint
- charge/discharge command behavior

上記に問題が見つかった場合は、このPRで直さず別issue/PR候補として報告する。

---

## 10. Codexから返す結果

Codex側で検証・必要な修正を行った場合、PR本文またはPRコメントへ以下の形式で結果を残す。

### A. 実行した検証

- code-quality audit: PASS / FAIL
- focused tests: `x passed`
- full tests: `x passed, y skipped` または未実行理由
- real plan structure check: PASS / FAIL
- SQLite-copy migration: PASS / FAIL
- same-plan idempotency: PASS / FAIL
- regenerated-plan multi-vintage: PASS / FAIL
- cutoff leakage test: PASS / FAIL
- latest contract regression: PASS / FAIL

### B. 重要な観測値

秘密情報を含めず、以下だけを報告する。

- snapshot rows per forecast run
- `issued_at_source` の種類
- lead time range
- quality flag種類と件数
- physical evidence coverage率
- provider別evidenceが取得できたhour数

### C. 問題が見つかった場合

各問題について:

1. 再現条件
2. 根本原因
3. 影響範囲
4. 修正内容
5. 回帰test
6. production behaviorを変更したか (`NO` が原則)

を記載する。

---

## 11. Phase 0 完了判定

以下をすべて満たしたらPhase 0を完了とする。

- forecast issue/generated timeをprospectiveに固定保存できる。
- 同じtarget hourの複数vintageを保持できる。
- 同じplanの再取込は重複しない。
- forecast再生成は別vintageになる。
- forecast weather/shortwaveを保存できる。
- provider GTI / physical PV evidenceを可能な範囲で保存できる。
- final PV forecastを保存できる。
- cutoff後のforecastをwalk-forward readerが選ばない。
- latest `forecast_hourly` contractを壊さない。
- production correction / SOC behaviorを変更していない。

Phase 0完了後に初めて、prospective dataを使って `weather forecast error` と `PV conversion/model error` の寄与分解へ進む。

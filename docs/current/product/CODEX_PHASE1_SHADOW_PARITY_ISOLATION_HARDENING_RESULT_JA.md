# Phase 1 v2 shadow parity・物理分離 hardening 実装結果

## 実施範囲

PR #13 の指示書に従い、validation-only sidecar の v2 実装を追加した。対象は `app/operations/shadow_gate.py`、`scripts/forecast_shadow.py`、`tests/test_shadow_gate.py` および本結果文書のみである。production forecast、energy plan、SOC、battery/device control のコードとスケジューラは変更していない。

## 固定契約

- `policy_name = gate_per_hour_window21d_margin02pct`
- `policy_version = phase1-v2`
- 対象は `07 <= target_hour <= 22` かつ `baseline_forecast_pv_kwh > 0`
- production-like は同時刻・過去1–45日・v2 finalized primary・同一coarse weather class・shortwave ±30%・固定shrinkageを使用
- same-hour bias は同時刻・過去1–45日・v2 finalized primaryのみで、weather/shortwaveを参照せずrecency-only weighted medianを使用
- selector は同時刻・過去21日・3 distinct dates以上・strict `< 0.98 * baseline_mae` で、それ以外はbaselineへfallback
- v1 rows はv2の候補履歴・selector・reportから除外

## DB分離とprovenance

Decision/outcome mode は `--source-db-path` と `--shadow-db-path` を必須とし、resolved pathが同一ならschema作成前に終了する。source DBはSQLite `mode=ro` で開き、shadow schemaはshadow DBだけに作成する。Decision rowsにはweather code/class、shortwave、candidate values、policy/version、vintage、`source_code_version`を保存する。prospective primary decisionのsource code versionは空/null/`unknown`を拒否する。

Phase A/B/C の手動実行例:

```powershell
python scripts/forecast_shadow.py --mode decision `
  --source-db-path artifacts/source.db --shadow-db-path artifacts/shadow.db `
  --target-start 2026-08-01 --target-end 2026-08-31 `
  --cutoff-at 2026-08-26T00:00:00Z --decision-at 2026-08-25T15:00:00Z `
  --source-code-version <git-sha>

python scripts/forecast_shadow.py --mode outcome `
  --source-db-path artifacts/source.db --shadow-db-path artifacts/shadow.db `
  --target-start 2026-08-01 --target-end 2026-08-31 `
  --recorded-at 2026-08-26T02:10:00Z

python scripts/forecast_shadow.py --mode report `
  --shadow-db-path artifacts/shadow.db --target-start 2026-08-01 --target-end 2026-08-31
```

これらはsidecar専用コマンドであり、production scheduler hookは追加していない。

## Completeness の停止判定

リポジトリの証拠から確認できるのは、既定KP-NET設定が30分データであることと、一部の履歴処理が`:00`/`:30`のペアを仮定していることまでである。全期間のcadence、timestampが区間開始か終了か、欠測の後着補完、環境変数変更の有無を証明できないため、`sample_count > 0`をcompleteとは扱わない。

実装は次のとおりfail-closedである。

- 対象時間終了前: `target_hour_not_finalized`
- 終了後でcadence契約未証明: `actual_completeness_contract_unproven`
- 明示的に証明済みfixtureでexpected countと一致した場合だけcomplete outcomeを保存
- 現時点のv2 primary outcome collection: `BLOCKED`
- 30-day prospective evidence collection: `NOT_STARTED`
- production adoption eligibility: `NOT_ELIGIBLE`

## 必須チェックリスト

| 項目 | 結果 |
|---|---|
| frozen target-domain parity (07-22, forecast > 0) | PASS |
| prior positive-forecast eligibility | PASS |
| production-like weather/shortwave history parity | PASS |
| production-like shrinkage formula parity | PASS |
| same-hour history parity (no weather gate) | PASS |
| same-hour history parity (no shortwave gate) | PASS |
| same-hour weighting parity (recency only) | PASS |
| same-hour minimum-history parity | PASS |
| end-to-end frozen candidate parity | PASS |
| end-to-end frozen selector parity | PASS |
| strict 2% threshold parity | PASS |
| v1 excluded from v2 primary evidence | PASS |
| v2 policy version active | PASS |
| 30-day horizon reset to first valid v2 decision | PASS (report field; clock not started) |
| source DB path != shadow DB path | PASS |
| source DB opened read-only | PASS |
| source DB unchanged after shadow cycle | PASS (SQLite fixture test) |
| shadow schema only in shadow DB | PASS |
| source-code version mandatory | PASS |
| actual-source semantics documented | PASS |
| actual completeness rule proven | BLOCKED |
| partial actual rejected | PASS |
| prospective gap/health reporting | PASS |
| sidecar failure non-interference | PASS (sidecar-only imports; no production hook) |
| production forecast non-interference | PASS |
| SOC/control non-interference | PASS |
| focused tests | PASS |
| full test suite | PASS |
| changed-code Ruff | PASS |
| changed-code ty/mypy | PASS (`mypy --explicit-package-bases`) |
| Import Linter | NOT RUN (local executable unavailable; existing CI debt is separate) |
| Oxlint/tsc if applicable | NOT APPLICABLE (no JS/TS project configuration) |
| Firestore parity | NOT RUN |
| PostgreSQL parity | NOT RUN |

## 残る停止条件

Actual cadence/completeness契約が証明されるまで、新しいv2 primary outcomeは採点対象として保存されない。従って30日有効evidence horizonは開始せず、production adoptionは許可されない。契約を正式化する場合は、expected sample count、first/last sample timestamp、coverage statusを含む新しい検証・テストを先に追加する。

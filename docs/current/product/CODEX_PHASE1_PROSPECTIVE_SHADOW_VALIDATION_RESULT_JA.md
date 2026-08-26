# Phase 1 prospective shadow validation 実装・検証結果

## 概要

`CODEX_PHASE1_PROSPECTIVE_SHADOW_VALIDATION.md` の指示に従い、2026-08-26（Asia/Tokyo）に最新 `master`（`6ae72a4`）から、固定された時間別補正ゲートの prospective shadow path を実装した。これは証拠収集専用のサイドチャネルであり、既存の `forecast_hourly`、PV forecast、SOC最適化、充放電制御には接続していない。

固定ポリシーは次のとおりで、検証期間中に変更しない。

- 選択単位: hour ごと
- selector履歴: target日より前の trailing 21 calendar days
- 採用条件: baselineよりMAEが2%以上改善（厳密な `< baseline × 0.98`）
- 不足・不合格時: baselineへfallback

30日以上の実績を蓄積するまでは、production adoptionを推奨しない。今回のローカルスモークは意思決定の保存までで、対象日の実績がまだないためoutcomeは0件である。これは前向き収集の開始を確認する証拠であり、性能評価の結果ではない。

## Pre-Work Check

- 目的: 時間別候補予測と固定ゲートの意思決定を、actual PV到着前に凍結して保存する。
- 対象: SQLiteを第一実装としたshadow decision/outcome store、CLI、レポート、回帰テスト。
- 変更しない範囲: production PV補正式、physical PV係数、provider weight、SOC objective/constraint、制御コマンド、既存 `forecast_hourly` latest-value contract。
- 参照した設計: Phase 0 snapshot validation、hourly adaptive-gate simulation、既存snapshot persistence、AGENTS.mdの保護領域・品質手順。
- 未実施: production cloudへの書込み、Firestore/PostgreSQL実環境検証、shadow値を使うSOC/cost replay。

## 実装内容

### Decision side channel

`app/operations/shadow_gate.py` にSQLite契約を追加した。`ensure_shadow_schema()` は再実行可能なmigrationで、以下を作成する。

- `forecast_shadow_decisions`: immutable decision row、policy/version、decision/cutoff/target/issued時刻、lead、4候補、selector score、fallback理由、履歴窓、品質flag、provenance。
- `forecast_shadow_outcomes`: decision IDを主キーとするappend-only outcome row。actual PV、baseline/selected/candidate error、補正適用有無を保持。
- target hour、policy/version、decision time、snapshot、outcome lookup用index。

decision IDは、policy/version、cutoff、snapshot ID、選択モデル、候補値を正規化してSHA-256で決定的に生成する。同じimmutable入力のretryは重複せず、別snapshot/vintageは別decisionになる。

### 候補予測

各decisionで次の4候補を保存する。

1. `baseline`
2. `production_like_45d`
3. `same_hour_bias_45d_hl7d`
4. `same_hour_bias_45d_hl14d`

候補はcutoff以前に保存された、同じhourの過去45日以内のprospective decision/outcomeだけを参照する。7日/14日の候補は残差の重み付きmedian、production-like候補は同じweather codeかつshortwave ±30%の残差medianである。現行production correctionとの完全同値性は利用可能なsnapshot evidenceだけでは証明できないため、名称を `production_like` とし、production-equivalentとは扱わない。

### Causal selector

target日 `D` のselector scoreは `target_date < D` のoutcomeだけから計算する。異なるhourの履歴をpoolせず、過去21日内に3 distinct days未満なら `insufficient_shadow_history` を記録してbaselineを選ぶ。候補の最小MAEがbaselineの98%未満でない場合もbaselineへ戻す。

snapshotのissued時刻はISO時刻をUTCへparseしてからcutoffと比較するため、`+09:00` と `Z` の表記差によるlexical leakageを避ける。cutoff以前の同一target hourでは最新vintageを選ぶが、別vintageの既存decisionを上書きしない。

### CLIとレポート

`scripts/forecast_shadow.py` に明示的な3モードを追加した。

```text
python scripts/forecast_shadow.py --mode decision --target-start YYYY-MM-DD --target-end YYYY-MM-DD
python scripts/forecast_shadow.py --mode outcome --target-start YYYY-MM-DD --target-end YYYY-MM-DD
python scripts/forecast_shadow.py --mode report --target-start YYYY-MM-DD --target-end YYYY-MM-DD
```

実行時にproduction forecastへ書き込まず、decision/outcome/reportだけをSQLiteへ保存または出力する。reportはsample count、coverage、baseline/selected MAE、signed bias、correction/fallback rate、model/hour counts、quality flags、lead-time、vintage count、固定parametersを出力し、last 7/14/21日windowも生成する。

## ローカル検証結果

### 品質監査

| チェック | 結果 |
|---|---|
| `python -m ruff check .` | PASS |
| Import Linter | PASS（3 contracts kept / 0 broken） |
| `python -m py_compile app/operations/shadow_gate.py scripts/forecast_shadow.py tests/test_shadow_gate.py` | PASS |
| focused tests | **23 passed** |
| full suite | **506 passed, 1 skipped** |

GitHub Actionsの既存 `quality` 最新run（SHA `6ae72a4`、run `32913031831`）は `static/dashboard.js` の既存TypeScript診断（implicit any / Window property）で失敗していた。forecast snapshot/shadow固有の失敗ではなく、今回のPRへダッシュボード修正を混在させていない。

今回の再監査では、対象ファイルへのtyはPASS、Ruff/Import Linter/OxlintはPASSした。一方、リポジトリ全体のty（既存型・未導入numpy/matplotlib等）とdeptry（既存Google/依存定義差分）は既存診断、tscは既存dashboard JavaScript診断であり、本変更の範囲外として修正していない。

### 必須受入項目

| 項目 | 判定 | 根拠 |
|---|---|---|
| production forecast regression | PASS | production workflowからshadow moduleへの参照なし。既存snapshot/plan/full suiteがvalue regressionなしで成功。 |
| SOC/control non-interference | PASS | shadow-selected valueをforecasting、energy plan、SOC、controlへ渡すconsumerなし。 |
| shadow decision idempotency | PASS | 同一decisionのretryは0 duplicate。 |
| regenerated-vintage separation | PASS | snapshot IDが異なる再生成vintageは2 decisionとして保持。 |
| cutoff leakage protection | PASS | timezone-aware issued時刻でcutoff後snapshotを除外。 |
| strict-prior selector history | PASS | `target_date < D`、同一hourのみをscoreへ使用。 |
| 2% margin behavior | PASS | 候補がbaselineの98%未満の場合だけ採用。 |
| insufficient-history fallback | PASS | 3 distinct days未満はbaseline + explicit reason。 |
| outcome append-only | PASS | outcome retryは0件、decision rowは完全一致。 |
| SQLite migration/indexes | PASS | schema/index creationを2回実行しても成功。 |
| Firestore parity | NOT RUN | production cloudへ接続しない方針。 |
| PostgreSQL parity | NOT RUN | local parity環境なし。 |
| shadow report generation | PASS | CLI decision/outcome/report smoke成功。 |
| offline SOC/cost evaluation | DEFERRED | 既存の安全なcounterfactual interfaceをこのPRでは確認できず。 |

## PR #11 ハードニング指示への追従結果

PR #11（`docs: harden Phase 1 prospective shadow evidence integrity`）で指摘された、PR #10の証拠完全性を最新master上で修正した。

| 指示項目 | 判定 | 実装・検証 |
|---|---|---|
| prospective eligibility hard gate | PASS | primaryは `decision_at < target_at`、`decision_at <= cutoff_at`、`forecast_issued_at <= cutoff_at` を満たさなければfail closed。 |
| retrospective evidence exclusion | PASS | `retrospective_diagnostic`を明示保存できるが、selectorとprimary reportから除外。通常CLIはprimaryの過去targetを拒否。 |
| finalized-actual gate | PASS | target hour終了+5分以内、またはsample count 0のactualはoutcomeへ保存せず、diagnostic reasonを追記。 |
| multi-vintage primary-sample deduplication | PASS | 全vintageを監査保存し、decision/issued時刻とIDによる決定的な1件だけをprimary score/reportへ使用。 |
| production-like candidate parity | PASS | frozen diagnosticのmedian/variance/shrinkage（spread 0.6 kWh、n/(n+2)）と一致するfixtureを追加。 |
| same-hour candidate parity | PASS | frozen diagnosticのminimum 2 observations、recency decay、similarity weightをfixtureで比較。 |
| weather-class parity | PASS | 診断スクリプトと同じclear/fog/rain/snow/shower/other mappingをshadow側へ実装。 |
| timezone-safe actual aggregation | PASS | monitoring timestampを`TIMEZONE`（既定Asia/Tokyo）へ変換してdate/hourを作成。UTC/JST境界fixtureが成功。 |
| primary-report evidence filtering | PASS | valid primary decision、finalized outcome、diagnostic vintage、missing/incomplete reasonを分離集計。 |
| production/SOC/control non-interference | PASS | shadow moduleはproduction forecast/SOC/controlから参照されず、既存全テストが成功。 |

ハードニング後のCLI smokeでは、24 decisionを保存し、実績未到着のためoutcome 0件、primary reportの除外理由を `missing_finalized_outcome: 24` として出力した。

### CLI smoke

Phase 0の非機密SQLiteコピーを使用し、対象日1日・24時間をprospective decisionとして保存した。

- candidate snapshot count: 24
- inserted decision count: 24
- actual hour count at smoke time: 0
- inserted outcome count: 0
- report coverage: 0.0（actual未到着のため）
- recorded lead time: 824〜2204分
- forecast vintage count: 1

このDBは検証用コピーであり、production DBは変更していない。実績到着後に同じCLIのoutcome modeを実行すれば、decisionを変更せずに評価行を追加できる。

## データ契約と再現手順

識者が同じ結果を再出力できるよう、以下を固定する。

1. SQLite接続を開き、`ensure_shadow_schema(conn)`を実行する。
2. `forecast_hourly_snapshots`から対象date範囲を読み、issued_atをUTC化してcutoff以前の各 `(date,hour)` 最新rowを選ぶ。primaryで過去targetを再実行する場合はfail closedとなるため、診断用途は `retrospective_diagnostic` と明示する。
3. 各rowについて、baselineと3候補を計算し、target日以前のoutcome履歴で21日/2% selectorを適用する。
4. `build_shadow_decision()`の返却rowを`persist_shadow_decisions()`へ渡す。actualをdecision payloadへ混ぜない。
5. actual PVを `(date,hour) -> {actual, sample_count}` で渡し、target hour終了+5分後に `persist_shadow_outcomes()`を呼ぶ。未確定actualはoutcomeへ入れず、diagnostic tableへ理由を追記する。
6. `report_shadow_outcomes()`またはCLI reportで集計する。

actual PVのCLI集計は既存 `monitoring_samples` のtimestampをISO parseし、naive値はsite timezone、offset付き値はsite timezoneへ変換してから`date/hour`ごとにSUMする。`sample_count`も保存する。新しいweather provider、依存関係、credentialは追加していない。

## 残存リスク・過去からの経緯・現在の課題

- Phase 0でappend-only forecast vintage、generated/issued時刻、lead、weather/shortwave、physical/provider evidence、idempotency、cutoff protectionを確立した。Phase 1はそのsnapshotを入力に、actual到着後の因果評価を分離した。
- 過去のvector similarity実験は、全日hit判定と時間別hit判定、逆補正を比較する探索であり、期間依存性が確認されている。特に直近期間では時間帯ごとの改善が不安定だったため、今回の固定gateは本番補正を直接有効化せず、prospective evidenceを先に貯める設計とした。
- `production_like_45d` はfrozen diagnosticのproduction-shaped shrinkage式（coarse weather class、shortwave ±30%、spread 0.6 kWh、variance shrinkage）に合わせた。元のproduction補正式へ接続したわけではないため、production-equivalentという名称は使わない。
- まだ30日分のprospective outcomesがなく、MAE改善・bias・hour別安定性について結論を出せない。
- Firestore/PostgreSQLの同等保存は未実行。SQLiteのcontractを先に確立し、cloud parityは別作業とする。
- SOC、買電量、購入コスト、制約penaltyへのcounterfactual影響は未評価。shadow値をlive planへ注入しない。
- GitHub Actionsのdashboard TypeScript debtは既存課題として残る。今回の変更の品質判定とは分離して扱う。

## Final Report

- 変更概要: prospective shadow decision/outcome store、固定causal selector、report CLI、focused testsを追加。
- 設計意図: actualより前に候補と選択を凍結し、actual到着後は別append-only行で評価する。
- 既存設計との整合: SQLite first、backend-neutral fields、既存latest-value contract不変。
- 採用しなかった代替: production correctionへの自動接続、21日/2%のretune、legacy latest forecastの後付けseed、cloud production書込み。
- 変更ファイル: `app/operations/shadow_gate.py`、`scripts/forecast_shadow.py`、`tests/test_shadow_gate.py`、本報告書。
- rollback: 新規テーブルとside-channel CLIを削除または未実行に戻せばproduction pathへ影響しない。既存テーブルは変更していない。
- 人手確認: 30日prospective収集後、候補定義のproduction-equivalent性、hour別安定性、SOC/cost counterfactualをレビューしてから採用可否を決定する。

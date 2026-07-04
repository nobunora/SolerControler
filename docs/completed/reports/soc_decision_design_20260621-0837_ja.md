# SOC判断構造の設計整理レポート

作成日時: 2026-06-21 08:37 JST  
対象: `C:\VSC\SolerControler`  
目的: 既存SOC判断フローを、将来の1時間天気予報活用に耐える「制約付き候補評価型」へ整理するための設計判断材料を作る。  
注意: 本レポートは設計・評価のみであり、実装、デプロイ、push は行っていない。

## changed files

- `docs/completed/reports/soc_decision_design_20260621-0837_ja.md`

## commands run

- `git status --short`
- `Get-ChildItem docs -File | Select-Object Name,Length,LastWriteTime | Sort-Object LastWriteTime -Descending | Format-Table -AutoSize`
- `Get-Content docs/current/product/CURRENT_DECISION_TREE_JA.md -TotalCount 220`
- `Get-Content docs/current/product/OPERATION_CONDITIONS_GUIDE.md -TotalCount 240`
- `Get-ChildItem docs/reports -File | Select-Object Name,LastWriteTime | Sort-Object LastWriteTime -Descending | Format-Table -AutoSize`
- `Get-ChildItem -Path . -File | Select-Object Name,Length | Sort-Object Name | Format-Table -AutoSize`
- `Select-String -Path energy_model_main.py -Pattern 'def |class |target_soc_7_percent|required_night_charge_kwh|night_charge_plan|soc_cost_optimizer|forecast_sun_hours|plan_quality|decision_rationale'`
- `Select-String -Path app/energy_model.py -Pattern 'def |class |target_soc_7_percent|forecast|plan_quality|decision_rationale|night_charge_plan|soc'`
- `Select-String -Path app/soc_cost_optimizer.py -Pattern 'def |class |total_expected_cost|night_charge_cost|expected_day_buy_cost|expected_sell_opportunity_loss|candidate|soc'`
- `Select-String -Path app/kpnet_workflow.py -Pattern 'def |class |charge_end|charge_start|SocChargeMode|operation_conditions|fixed|variable|force_settings_profile|dynamic_forced_profile'`
- `Get-Content energy_model_main.py | Select-Object -Skip 1040 -First 360`
- `Get-Content app/kpnet_workflow.py | Select-Object -Skip 800 -First 300`
- `Get-Content app/energy_model.py | Select-Object -Skip 220 -First 210`
- `Get-Content app/soc_cost_optimizer.py | Select-Object -Skip 260 -First 250`
- `Get-Content config/operation_conditions.json`
- `Get-ChildItem -Recurse -File artifacts,logs -ErrorAction SilentlyContinue | Select-Object FullName,Length,LastWriteTime | Sort-Object LastWriteTime -Descending | Select-Object -First 80`
- `Get-ChildItem -Recurse -File -Include *.db,*.sqlite,*.csv,night_charge_plan.json -ErrorAction SilentlyContinue | Select-Object FullName,Length,LastWriteTime | Sort-Object LastWriteTime -Descending | Select-Object -First 120`
- `Get-Content artifacts/night_charge_plan.json -TotalCount 220`
- `Get-Content artifacts/local-smoke-kpnet/night_charge_plan.json -TotalCount 220`
- `Get-Content artifacts/local-smoke-kpnet/20260620-222658/kpnet_summary.json -TotalCount 240`
- `Get-Content docs/dashboard_soc_simulation_vision_ja.md -TotalCount 220`
- `Get-Content artifacts/analysis_last3_soc_optimizer_20260522-084447.json -TotalCount 260`
- `Get-Content artifacts/analysis_recent_backtest_detailed_20260521-224952.json -TotalCount 260`
- `Get-Content artifacts/analysis_recent_backtest_after_update_20260521-222559.json -TotalCount 260`
- `Get-Content logs/codex-soc-decision-design-20260621-083203.log -TotalCount 220`
- `Get-Content logs/codex-soc-decision-design-20260621-082959.log -TotalCount 220`
- `Get-Content logs/codex-soc-decision-design-20260621-012000.log -TotalCount 220`
- `Get-Content docs/current/agent/report_template.md -TotalCount 220`
- `Get-Content docs/completed/reports/shared_utils_constants_refactor_ja.md -TotalCount 220`
- `Get-Date -Format 'yyyyMMdd-HHmm'`
- `Get-Content artifacts/night_charge_plan.json -Raw | ConvertFrom-Json`
- `Get-Content artifacts/local-smoke-kpnet/night_charge_plan.json -Raw | ConvertFrom-Json`
- `Get-Content artifacts/local-smoke-kpnet/20260620-222658/kpnet_summary.json -TotalCount 240`
- `Get-Content artifacts/analysis_last3_soc_optimizer_20260522-084447.json -Raw | ConvertFrom-Json`
- `Get-Content artifacts/replay/20260502-113529/night_charge_plan.json -Raw | ConvertFrom-Json`
- `Get-Content artifacts/replay/20260524-183618/night_charge_plan.json -Raw | ConvertFrom-Json`
- `Get-ChildItem artifacts -Recurse -File -Filter night_charge_plan.json -ErrorAction SilentlyContinue`

## 現行条件木

### 参照した設計文書とコード位置

- `docs/current/product/CURRENT_DECISION_TREE_JA.md`
- `docs/current/product/OPERATION_CONDITIONS_GUIDE.md`
- `energy_model_main.py:1058-1355`
- `app/energy_model.py:232-391`
- `app/soc_cost_optimizer.py:272-485`
- `app/kpnet_workflow.py:807-1058`

### 条件木の要約

```text
ROOT
├─ 入口: Cloud job / energy_model_main / KP-NET workflow
├─ 予報・負荷・PVの用意
│  ├─ forecast_from_env_or_api
│  ├─ consumption_forecast
│  ├─ pv_array_forecast
│  └─ historical_profile / occupancy_adjustment
├─ 7時SOCの決定
│  ├─ app/energy_model.py
│  │  ├─ legacy daytime objective
│  │  └─ cost objective
│  └─ app/soc_cost_optimizer.py
│     └─ candidate SOC を sigma/load scenario で総当たり評価
├─ 出力パッケージ
│  ├─ result
│  ├─ daytime_soc_optimization
│  └─ decision_rationale
└─ KP-NET 反映
   └─ app/kpnet_workflow.py
      ├─ 夜間/日中プロファイルの組み立て
      ├─ 時刻条件の補正
      └─ SOCコードへの丸め
```

### 主要分岐

- `energy_model_main.py` は、予報、負荷予測、PV予測を組み立てたあと、`compute_night_charge_target()` と `optimize_target_soc_for_daytime()` / `optimize_soc_by_expected_cost()` を通して `target_soc_7_percent` と `required_night_charge_kwh` を決める。
- `app/energy_model.py` は、現行の「朝の不足回避 + 昼の余剰受け皿」型の昼間SOC最適化と、夜間充電量の計算を担当する。
- `app/soc_cost_optimizer.py` は、候補SOCを刻みで試し、`night_charge_cost + expected_day_buy_cost + expected_sell_opportunity_loss (+ peak penalty)` が最小の候補を選ぶ。
- `app/kpnet_workflow.py` は、`night_charge_plan.json` を入力にして、夜間/日中の時刻窓と `SocChargeMode` をKP-NET候補値へ丸める。

### 現在の条件木としての解釈

- 「候補生成」より前に、実質的なハード制約が少数ある。
- 「候補評価」では、リスク見積もりとコスト最小化が混在している。
- 「KP-NET反映」段階では、SOC候補の丸めと時刻窓の補正が別の層で行われる。
- この分離は今の実装としては妥当だが、将来の1時間天気予報を入れるなら、`plan_quality` を入口に置いて候補評価の可否を先に決めた方が説明しやすい。

## Hard constraint / Risk constraint / Objective 分類

| 分類 | 候補 | 現在の実装位置 | 判定 | コメント |
|---|---|---|---|---|
| Hard constraint | `forbid_cross_midnight` | `app/kpnet_workflow.py` / `config/operation_conditions.json` | 絶対除外 | 0時跨ぎ禁止はKP-NETへ送れないため、ハード制約で妥当。 |
| Hard constraint | `forbid_same_start_end` | `app/kpnet_workflow.py` / `config/operation_conditions.json` | 絶対除外 | 開始終了同一は送信不可。補正後も不正なら停止するので妥当。 |
| Hard constraint | `capacity_kwh <= 0` / 候補なし / 解析不能 | `app/energy_model.py` / `app/soc_cost_optimizer.py` | 絶対除外 | 現在は `None` / 例外で落ちる。`plan_quality=unsafe_to_apply` に寄せると説明しやすい。 |
| Risk constraint | `night_charge_end_by_forecast` | `app/kpnet_workflow.py` / `config/operation_conditions.json` | リスク評価 | 日照時間しきい値で 06:00 / 07:00 を切替。将来は1時間予報を使った候補評価へ移したい。 |
| Risk constraint | `day_discharge_start_by_forecast` | `app/kpnet_workflow.py` / `config/operation_conditions.json` | リスク評価 | 日照時間が短い日は放電開始を遅らせる。これも固定ルールより候補評価向き。 |
| Risk constraint | `morning_pv_headroom_guard` | `energy_model_main.py` | リスク評価 | 朝のPV headroom を守るための上限制約。現状は重要だが、出力には理由が薄く、rationale に分離した方がよい。 |
| Risk constraint | `historical_daytime_soc_gain_guard` | `energy_model_main.py` | リスク評価 | 過去の実績から上限を切る。経験則として有用だが、ルール理由を短く返せる形にしたい。 |
| Risk constraint | `overnight_discharge_guard` | `energy_model_main.py` | リスク評価 | 7時までの夜間放電見込みを差し引く。候補評価の前提条件として妥当。 |
| Risk constraint | `pv_uncertainty` / sigma buckets / weather upside | `app/soc_cost_optimizer.py` + `energy_model_main.py` | リスク評価 | 不確実性を候補集合で扱っている点は良い。`plan_quality` によって「どの程度の確からしさで評価したか」を明示したい。 |
| Objective | `minimize_night_charge_cost_plus_expected_day_buy_cost_plus_expected_sell_opportunity_loss` | `app/soc_cost_optimizer.py` / `energy_model_main.py` | スコア最小化 | 現行の主軸。候補評価型の土台としてそのまま使える。 |
| Objective | `legacy_peak_soc_objective` | `energy_model_main.py` | スコア最小化 | 後方互換として残っているが、現行の意図を説明するには優先度を下げた方がよい。 |
| Objective | `day_charge_window` | `config/operation_conditions.json` / `app/kpnet_workflow.py` | スコア最小化寄り | 現在は変動条件だが、実質は日中グリーンのデフォルト窓。将来は候補生成パラメータとして扱う方が自然。 |

### 現状の配置が不自然なもの

- `night_charge_end_by_forecast` と `day_discharge_start_by_forecast` は、現在は設定ルールとして書かれているが、将来の1時間予報活用では「候補を評価して採否を決める」側に置いた方が説明しやすい。
- `day_charge_window` は「時間帯ルール」に見えるが、実態はグリーンモードの候補生成デフォルトであり、ハード制約ではない。
- `legacy_peak_soc_objective` とコスト最適化の二系統が並んでいるため、`plan_quality` で「どちらの経路を使ったか」を記録しないと、後から見たときに判断経路が曖昧になる。

## plan_quality 設計案

最小案として 5 状態に絞る。

| plan_quality | 意味 | KP-NET反映 | 判定基準の例 |
|---|---|---:|---|
| `normal` | 予報・CSV・SOC・候補評価が揃い、通常経路で採用 | 可 | すべての必須データが揃い、硬い制約に抵触しない。 |
| `forecast_fallback` | 予報の一部が欠損しており、保守的な既定値で評価した | 可 | `sun_hours` 欠損、hourly forecast の一部欠損、日照時間しきい値に戻した。 |
| `partial_data` | 必須データの一部欠損で、採用判断が弱い | 停止 | 最新SOC、CSV、候補マップ、または night plan が不足。 |
| `stale_forecast` | 予報が古く、実行時点との差が大きい | 停止 | 予報生成時刻が実行時刻より古すぎる、または更新待ちを超過。 |
| `unsafe_to_apply` | 候補評価や制約整合が壊れており、設定反映は危険 | 停止 | ハード制約が解消しない、候補が空、窓が成立しない。 |

### 設計判断

- `normal` と `forecast_fallback` は、説明付きで反映可。
- `partial_data` / `stale_forecast` / `unsafe_to_apply` は KP-NET 反映停止にする。
- `plan_quality` は `night_charge_plan.json` の最上位に置き、ダッシュボードの先頭表示にも使う。

## decision_rationale 設計案

巨大JSONにしない前提で、以下の最小項目を持たせる。

```json
{
  "plan_quality": "normal",
  "selected": {
    "target_soc_percent_raw": 34.2,
    "target_soc_percent_rounded": 40,
    "soc_charge_mode_code": "40",
    "charge_start_time": "04:30",
    "charge_end_time": "07:00"
  },
  "rejected_candidates": [
    {
      "target_soc_percent": 30,
      "total_expected_cost_yen": 412.5,
      "rejection_reason": "higher_day_buy_risk"
    }
  ],
  "constraints": [
    "forbid_cross_midnight",
    "morning_pv_headroom_guard",
    "overnight_discharge_guard"
  ],
  "cost_breakdown_yen": {
    "night_charge": 210.0,
    "expected_day_buy": 83.4,
    "expected_sell_loss": 54.8,
    "peak_unmet_penalty": 5.2
  },
  "fallback": {
    "used": false,
    "reason": ""
  }
}
```

### 必ず残したい情報

- 採用SOC
- 却下候補の要約
- 効いた制約
- fallback状態
- コスト/リスク内訳
- KP-NET丸め前後のSOC
- KP-NETへ送る開始・終了時刻

### 現行実装との差分

- 現行 `decision_rationale` は「objective / guard / final value」中心で、却下候補が残らない。
- `plan_quality` がないため、失敗と保守的フォールバックの区別がしにくい。
- したがって、説明可能性を上げる最小改修の第一段は `plan_quality` と `rejected_candidates` の追加がよい。

## 過去データ評価結果

### 評価の前提

- Cloud同期は実施していない。
- 全期間再計算はしていない。
- 既存の `night_charge_plan.json`、`analysis_*.json`、`replay/*.json` の軽量確認だけで評価した。

### 参考として見た最新ライブ出力

- `artifacts/night_charge_plan.json`
- forecast date: `2026-06-21`
- `target_soc_7_percent`: `85`
- `required_night_charge_kwh`: `8.21184518507811`
- `soc_expected_total_cost_yen`: `353.386522542589`
- `buy_risk`: `true`
- `sell_risk`: `true`

これは現在の出力形の確認に使ったが、歴史比較の主対象ではない。

### 評価対象 1: 晴天相当の候補日

- 対象: `artifacts/replay/20260502-113529/night_charge_plan.json`
- forecast date: `2026-05-03`
- `sun_hours`: `6.2`
- `weather_class`: 空欄
- 現在の最終SOC相当: `target_soc_7_percent = 11.3783908235207`
- 必要夜間充電: `0.879444265609745 kWh`

所見:

- 晴天クラスのラベルは無いが、日照時間だけを見ると晴天相当の低充電日として扱える。
- この日は候補評価型にすると、低SOC候補が自然に勝つはずで、`plan_quality=normal` もしくは `forecast_fallback` の差分検証に向く。
- 追加で必要なデータは、7時SOCの実測と、候補SOCごとの却下理由の最小表示。

### 評価対象 2: 曇り/雨または低余剰日

- 対象: `artifacts/replay/20260524-183618/night_charge_plan.json`
- forecast date: `2026-05-24`
- `sun_hours`: `1.92901944444444`
- `weather_class`: `cloudy`
- 現在の最終SOC相当: `target_soc_7_percent = 100`
- 必要夜間充電: `1.56771464716222 kWh`

所見:

- 低余剰日では、現行ロジックが上限SOCへ寄ることがある。
- 候補評価型では、`forecast_fallback` ではなく `normal` だが、`day_buy` / `sell_loss` / `peak_penalty` の内訳を出すと「なぜ100%になったか」が説明しやすい。
- 追加で必要なデータは、日中の実負荷と昼間PVの時間帯別配分。

### 評価対象 3: 直近でSOC設定が疑問だった日

- 対象: `artifacts/analysis_last3_soc_optimizer_20260522-084447.json`
- 日付: `2026-05-17`
- `soc_0700_percent = 3.0`
- baseline `target_soc_7_percent = 0.0`
- optimized `target_soc_7_percent = 29.0`
- `sunset_soc_gain_percent_pt = 28.422460575005076`

所見:

- この日は最も「なぜそこまで上げるのか」を説明したいケース。
- 低い baseline では昼間買電を避けきれず、候補評価型では `rejected_candidates` に 0% 近辺を残すと改善理由が読める。
- 追加で必要なデータは、weather_class の明示、hourly PV/負荷、候補ごとの昼間買電・売電・峰値SOC。

### 評価の結論

- 手元成果物だけでも、「候補評価型へ整理する意義」は確認できた。
- ただし、晴天 class のラベルが明示された過去日が手元の主要サンプルでは見つからず、晴天相当の判定は `sun_hours` ベースで代替している。
- 実装前の合意材料としては十分だが、正式な検証には晴天日1件のラベル付き履歴が欲しい。

## 矛盾・改善候補

- `plan_quality` が未定義なので、失敗と保守的フォールバックが区別しづらい。
- `decision_rationale` が採用理由中心で、却下候補が残らない。
- `night_charge_end_by_forecast` と `day_discharge_start_by_forecast` は、設定値よりも候補評価の入力として扱った方が設計の一貫性が上がる。
- `day_charge_window` は「固定制約」ではなく「候補生成の既定値」に見えるため、今の文書だと役割が少し曖昧。
- `energy_model_main.py` 内で legacy objective と cost objective が併存しているため、どちらが使われたかを `plan_quality` と `decision_rationale` に明示した方がよい。

## 実装前に合意すべき論点

1. `stale_forecast` と `partial_data` は必ず KP-NET 反映停止にするか。
2. `forecast_fallback` は設定反映可とするか、あるいはレビュー待ちにするか。
3. 予報の鮮度閾値を何分/何時間にするか。
4. `rejected_candidates` は何件まで保持するか。
5. legacy peak objective を今後も温存するか、候補評価の説明用途だけに限定するか。
6. `day_charge_window` を固定ルールとして残すか、候補生成パラメータへ移すか。

## 次の最小改修単位

1. `plan_quality` を出力に追加する
   - 目的: 反映可否の判定を明文化する。
   - 対象ファイル: `energy_model_main.py`, `app/kpnet_workflow.py`
   - リスク: 既存の結果JSON互換性。
   - 必要テスト: 出力JSONのスキーマ確認、`normal` / `forecast_fallback` / `unsafe_to_apply` の分岐確認。

2. `decision_rationale` に却下候補の要約を追加する
   - 目的: 採用SOCの理由を候補比較で読めるようにする。
   - 対象ファイル: `energy_model_main.py`, `app/soc_cost_optimizer.py`
   - リスク: JSON肥大化。
   - 必要テスト: 候補数が多い場合でも固定件数で収まること。

3. 予報鮮度と fallback を候補評価の前段に分離する
   - 目的: 予報欠損時の説明を一箇所に集約する。
   - 対象ファイル: `energy_model_main.py`
   - リスク: legacy objective との優先順位が見えにくくなる。
   - 必要テスト: `forecast_fallback` が出る条件、`stale_forecast` が止める条件。

4. KPI 表示へ `plan_quality` / `decision_rationale` を渡す
   - 目的: ダッシュボードで説明可能性を上げる。
   - 対象ファイル: ダッシュボード読込側の集約層
   - リスク: UI 表示の文言増加。
   - 必要テスト: 集約JSONの読み取りと表示崩れ。

5. 晴天・低余剰・疑問日の3日バックテストを固定化する
   - 目的: 設計変更前後の比較対象を安定化する。
   - 対象ファイル: 分析スクリプトまたはレポート生成補助
   - リスク: 過剰な再計算。
   - 必要テスト: 3日だけの軽量集計で完結すること。

## サブエージェント分担

- 未使用。
- 主要4ファイルと既存成果物を直接確認し、重複探索を避けた。

## known limitations

- `rg` はこの環境では利用できず、PowerShell の `Select-String` / `Get-Content` / `Get-ChildItem` で代替した。
- 晴天 class が明示された過去日を手元の主要サンプルで確認できなかったため、晴天相当は `sun_hours` ベースで代替した。
- Cloud 同期、全CSV走査、全期間再シミュレーションは実施していない。
- `artifacts` 配下には大きいデータがあるが、軽量要約のみを参照した。
- 既存の `AGENTS.md` は作業前から変更状態だったため、そのまま保持した。

## next recommended milestone

`plan_quality` の契約を先に合意し、その後に `decision_rationale` の最小JSONを固定する。  
これが決まれば、実装開始前に「どの条件で KP-NET 反映を止めるか」を説明できる状態になる。

# CodebaseMemory 未使用ノード・エッジ・類似実装調査レポート

## Pre-Work Check

- Change purpose: CodebaseMemory のインデックス結果から、未使用候補・エッジ品質上の注意点・類似実装を抽出する。
- Change target: `C:\VSC\SolerControler` の CodebaseMemory プロジェクト `C-VSC-SolerControler`。
- Scope not changed: アプリケーションコード、テスト、設定、デプロイ定義は変更していない。
- Existing code or references reviewed: CodebaseMemory のノード／エッジ統計、`CALLS` の入次数、低信頼エッジ、`SIMILAR_TO`、候補シンボルの `rg` 結果、候補周辺のソース。
- Relationship to existing design: この調査は削除・統合を実施せず、次回の実装検討に使う候補台帳を作るもの。
- Unknowns: 動的な文字列呼び出し、フレームワーク経由の入口、テストの monkeypatch、未解決の import alias は静的グラフだけでは完全に判定できない。
- Human confirmation needed: 候補を実際に削除・統合する場合は、互換 API、運用スクリプトの単独実行性、外部呼び出し契約を確認する。

## 調査概要

CodebaseMemory のインデックス状態は `ready` で、再インデックス後の規模は次の通りだった。

| 項目 | 結果 |
|---|---:|
| プロジェクト | `C-VSC-SolerControler` |
| ルート | `C:/VSC/SolerControler` |
| ノード | 5,783 |
| エッジ | 18,013 |
| 期待ノード／エッジ | 5,783／18,013 |
| スキップファイル | 0 |
| 部分解析ファイル | 9 |

ノードとエッジの実数が期待値と一致しているため、今回のインデックスには欠落した dangling edge（片側のノードが存在しないエッジ）は確認されなかった。

レポート作成後にも再インデックスを実行した。最終状態は `ready`、5,805ノード／18,037エッジで、追加したMarkdownレポート等が反映された。ログファイル（`.log`）はリポジトリの除外規則により索引対象外である。

ただし、次のファイルは部分解析である。該当範囲のシンボル判断には CodebaseMemory 以外の `rg`／ソース確認を併用する必要がある。

- `.env.example`
- `config/importlinter_advisory.ini`
- `pytest.ini`
- `scripts/backup_operational_state_from_env.ps1`
- `scripts/deploy_production_from_env.ps1`
- `scripts/get_gcp_actual_cost.ps1`
- `scripts/get_gcp_pricing.ps1`
- `scripts/pre_release_local.ps1`
- `scripts/production_deployment_gate.ps1`

## 1. 未使用ノード候補

### 1.1 グラフ上の候補数

非テスト・非 export・非エントリーポイントに絞り、`CALLS` の inbound が0の候補を抽出した。

| ラベル | 候補数 |
|---|---:|
| `Function` | 40 |
| `Method` | 6 |

これは「未使用確定」ではなく、CodebaseMemory が解決した `CALLS` グラフ上で呼び出し元を持たない候補数である。フレームワーク入口、動的呼び出し、互換のための公開名、テスト monkeypatch は偽陽性になり得る。

### 1.2 テキスト検索でも定義以外の参照が見つからない強い候補

`app`、`scripts`、`tests` を対象にシンボル名を検索し、定義以外の直接参照が確認できなかったものは次の8件である。

| シンボル | 定義 | CodebaseMemory上の所見 | 判定 |
|---|---|---|---|
| `_clip_float` | [`app/forecasting/correction_history_io.py:13`](C:/VSC/SolerControler/app/forecasting/correction_history_io.py:13) | inbound 0、outbound 0 | 削除候補が最も強い。ただし同名の別実装 `app.forecasting.correction_calculations.clip_float` とは別物。 |
| `_estimate_required_charge_kwh` | [`app/runtime/cloud_job.py:76`](C:/VSC/SolerControler/app/runtime/cloud_job.py:76) | inbound 0、outbound 0 | 現在の実行経路から外れた旧補助関数の可能性。引数 `latest_soc_percent` も未使用。 |
| `_daily_from_hourly` | [`scripts/analyze_hourly_weather_vectors.py:163`](C:/VSC/SolerControler/scripts/analyze_hourly_weather_vectors.py:163) | inbound 0、outbound 0 | 同スクリプトの現行 `main` から参照なし。 |
| `_archive_weather_rows` | [`app/energy_plan/workflow.py:571`](C:/VSC/SolerControler/app/energy_plan/workflow.py:571) | inbound 0、outboundあり | `_archive_weather_history` と似た互換／旧ラッパーの可能性。outboundがあるため完全な孤立ノードではない。 |
| `_parse_time` | [`app/forecasting/pv_array.py:80`](C:/VSC/SolerControler/app/forecasting/pv_array.py:80) | inbound 0、outboundあり | provider adapter へ移行した後の互換ラッパー候補。 |
| `_parse_forecast_solar_time` | [`app/forecasting/pv_array.py:84`](C:/VSC/SolerControler/app/forecasting/pv_array.py:84) | inbound 0、outboundあり | 直接参照なし。Forecast Solar 専用の旧境界を保持している可能性がある。 |
| `_provider_order_from_env` | [`app/forecasting/pv_array.py:223`](C:/VSC/SolerControler/app/forecasting/pv_array.py:223) | inbound 0、outboundあり | `pv_array_selection.provider_order_from_env` への互換ラッパー候補。 |
| `_run_optional` | [`app/runtime/command_adapter.py:63`](C:/VSC/SolerControler/app/runtime/command_adapter.py:63) | inbound 0、outboundあり | 現行の Cloud Job から直接参照なし。過去の optional step 残置の可能性。 |

### 1.3 偽陽性として確認できた例

- `_night_plan_path` はグラフ上の inbound 0だが、[`slot_orchestration.py:35`](C:/VSC/SolerControler/app/runtime/slot_orchestration.py:35) の `_cloud_call("_night_plan_path")` という文字列ベースの動的呼び出しで使用される。削除不可。
- `_weather_class_from_code` はグラフ上の入次数だけを見ると候補に見えるが、[`pv_array_calibration.py:183`](C:/VSC/SolerControler/app/forecasting/pv_array_calibration.py:183) から直接呼ばれる。グラフの局所的な解決漏れを示す例である。
- `__post_init__`、port interface のメソッド、テスト用 fake のメソッドは、データクラス、依存性注入、Python protocol、pytest fixture 経由で呼ばれるため、inbound 0だけでは未使用とは言えない。

### 1.4 未使用ノードに対する結論

現時点で「削除候補」として優先調査するのは次の4件である。

1. `_clip_float`
2. `_estimate_required_charge_kwh`
3. `_daily_from_hourly`
4. `_archive_weather_rows`

ただし、削除パッチを作る前に、公開 import、過去の互換契約、外部ジョブからの直接 import、`getattr`／文字列ディスパッチを確認する必要がある。

## 2. エッジの利用状況と品質

### 2.1 エッジ種別

| エッジ種別 | 件数 | 主な意味 |
|---|---:|---|
| `DEFINES` | 8,156 | ファイル／モジュール等による定義 |
| `CALLS` | 4,445 | 関数・メソッド呼び出し |
| `IMPORTS` | 1,253 | import 関係 |
| `USAGE` | 1,281 | 型・シンボル利用 |
| `WRITES` | 682 | 代入／書き込み |
| `TESTS` | 668 | テスト対象関係 |
| `CONFIGURES` | 183 | 設定による参照 |
| `SEMANTICALLY_RELATED` | 184 | 意味的な関連候補 |
| `SIMILAR_TO` | 17 | 構造的類似候補 |
| `CALL_REFERENCE` | 30 | 通常のCALLS解決外の参照 |

ノード・エッジの実数は期待値と一致し、インデックス処理中のスキップも0件だった。したがって、エッジ数の不足や片側ノード欠落は今回の結果からは見つからない。

### 2.2 低信頼 `CALLS` エッジ

`CALLS` 4,445件のうち、信頼度0.5未満は692件（約15.6%）だった。

| 解決戦略 | 件数 |
|---|---:|
| `suffix_match` | 355 |
| `unique_name` | 337 |

低信頼エッジの宛先分類は次の通りである。

| 宛先 | 件数 | 解釈 |
|---|---:|---|
| `builtins.*` | 240 | `dict.get`、`str.lower`、`list.append` 等。未使用ではなく、名前解決の信頼度が低い組み込み呼び出し。 |
| `tests/*` | 215 | Firestore／Drive fake、Clock fake 等。テストダブルへの suffix 解決が中心。 |
| その他 | 237 | アプリ内の低信頼エッジ。個別にソース確認すべき範囲。 |

低信頼エッジの代表例は、`collect_cleanup_candidates` から `_SystemMonitorClock.now`、Firestore処理からテスト用 `stream`／`order_by`、通常コードから `dict.get`／`str.lower` への解決である。これらは未使用エッジというより、同名メソッド・組み込み関数・テストモックを構造解析が近似解決した結果と考えるのが妥当である。

### 2.3 `CALL_REFERENCE` 30件

`CALL_REFERENCE` は未使用エッジではなく、通常の `CALLS` 解決で取り切れない動的・間接参照の記録と解釈する。

確認できた例:

- dashboard Firestore の helper 群
- `workflow._run_soc_optimization` からの optimizer／decision prior 呼び出し
- `_cloud_call` を介した Cloud Job helper 呼び出し
- テスト内の fake clock／fake client 呼び出し

この種のエッジがあるため、inbound 0の候補を削除する前に文字列呼び出しとテスト monkeypatch を確認する必要がある。

## 3. 類似実装

### 3.1 `SIMILAR_TO`

17ペアが検出された。

- Jaccard 1.000: 14ペア
- Jaccard 0.953: 3ペア

#### 統合検討の優先度が高いペア

| 類似ペア | 類似度 | 所見 |
|---|---:|---|
| [`night_plan_archive.py:243`](C:/VSC/SolerControler/app/backup/night_plan_archive.py:243) `read_plan_file` ↔ [`domain.py:147`](C:/VSC/SolerControler/app/operations/domain.py:147) `read_summary` | 1.000 | どちらもJSON object読込。エラー文言と責務が異なるため、共通化は互換境界を確認してから。 |
| [`environment.py:11`](C:/VSC/SolerControler/app/configuration/environment.py:11) `load_dotenv_if_present` ↔ [`backup_drive.py:25`](C:/VSC/SolerControler/scripts/backup_drive.py:25) `_load_dotenv` | 1.000 | dotenv読込処理が実質重複。単独スクリプトの起動境界を保ちつつ共通 helper 利用を検討できる。 |
| [`weather_history.py:15`](C:/VSC/SolerControler/app/energy_plan/weather_history.py:15) `weather_class` ↔ [`pv_array_calibration.py:94`](C:/VSC/SolerControler/app/forecasting/pv_array_calibration.py:94) `_weather_class_from_code` | 1.000 | weather code→class変換が同一。分類表を一箇所に寄せる価値が高い。 |
| [`analyze_multi_day_weather_contribution.py:158`](C:/VSC/SolerControler/scripts/analyze_multi_day_weather_contribution.py:158) `_day_series` ↔ 同ファイル `_rolling_daily_mean` | 1.000 | 日次window平均の重複。前者は直接参照なし、後者は現行mainから使用。 |
| `diagnose_hourly_pv_correction_limits.main` ↔ `diagnose_hourly_pv_regime_bias.main` | 0.953 | 診断CLIの構造が近い。目的・出力・入力契約が異なる可能性が高く、即統合は避ける。 |

#### 意図的な重複の可能性が高いペア

- `check_gcp_free_tier_capacity.ps1`、`get_gcp_actual_cost.ps1`、`get_gcp_pricing.ps1`、`prune_artifact_registry.ps1` の `Invoke-GCloud`
- `deploy_production_from_env.ps1` と `production_deployment_gate.ps1` の state／error helper
- Firestore／SQLite／Postgres の同名 persistence helper
- テストファイル内の同型テスト、fake client、fake response

これらは独立実行スクリプト、バックエンド境界、テスト分離という設計上の理由があるため、類似度だけでは統合対象にしない。

### 3.2 `SEMANTICALLY_RELATED`

意味関連エッジは184件あった。上位には次のような「別実装だが同じ責務領域」の組が含まれる。

- `diagnose_hourly_pv_adaptive_gate.main` ↔ `diagnose_hourly_pv_regime_bias.main`: 0.957
- weather vector の `_group_contributions` 群: 0.940、0.935、0.929
- Firestore／SQLite の snapshot・model parameter helper: 0.828前後
- `build_hourly_load_forecast` ↔ `build_hourly_pv_forecast`: 0.832

`SEMANTICALLY_RELATED` は重複の確定証拠ではなく、調査対象のクラスタを見つけるための候補である。

## 4. 推奨する次の調査順

1. `_clip_float`、`_estimate_required_charge_kwh`、`_daily_from_hourly`、`_archive_weather_rows` を対象に、公開 import・動的参照・履歴上の互換用途を確認する。
2. `load_dotenv_if_present`／`_load_dotenv` の統合可否を、単独スクリプト起動時の `sys.path` とテスト契約を含めて確認する。
3. weather code分類を共通化する場合、`app.energy_plan.weather_history` を依存方向の基準にできるか Import Linter と既存テストで確認する。
4. `_day_series` を削除または `_rolling_daily_mean` に統合する前に、スクリプトのCLI入力と未追跡外部利用を確認する。
5. 低信頼 `CALLS` 692件は一括修正せず、まずアプリ内宛先237件を、組み込み／テスト宛先と分けてサンプリングする。

## Final Report

- Change summary: 調査結果と再現手順を本レポートに記録した。ソースコードの削除・統合は行っていない。
- Design intent: 静的グラフの候補と、`rg`・ソース確認で得た事実を分離し、誤削除を防ぐ。
- Alignment with existing design: 動的呼び出し、互換ラッパー、バックエンド分離、テスト fake を偽陽性要因として扱った。
- Alternatives not chosen: 類似度だけによる一括統合、inbound 0だけによる自動削除、低信頼エッジの一括再解決は実施しなかった。
- Files changed: 本レポートと [`codebase_memory_unused_similarity_20260830.log`](C:/VSC/SolerControler/docs/completed/reports/codebase_memory_unused_similarity_20260830.log)。
- Scope not changed: `app/`、`scripts/`、`tests/` の動作、デプロイ設定、環境変数、外部サービス設定。
- Tests: 実装変更がないため、テストは実行していない。CodebaseMemory CLIによるインデックス状態・検索・集計のみ確認した。
- Human confirmation points: 候補を削除／統合する場合、互換 API と動的呼び出しの維持要否を確認する。
- Remaining risks: 部分解析9ファイル、文字列ディスパッチ、外部ジョブからの直接 import、未追跡の実行経路は静的調査だけでは保証できない。

## 調査ログ

再現用のコマンドと主要な出力は、別ログ [`codebase_memory_unused_similarity_20260830.log`](C:/VSC/SolerControler/docs/completed/reports/codebase_memory_unused_similarity_20260830.log) に保存した。ログには秘密情報を含む `.env` の内容は記録していない。

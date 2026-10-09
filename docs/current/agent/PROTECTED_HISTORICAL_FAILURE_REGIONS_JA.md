# 過去の障害に基づく変更保護領域

この文書は、最近の障害と、その少し前までの修正履歴から、再変更で同じ事故を起こしやすい箇所を固定するための作業規約である。対象はファイル全体ではなく、記載したシンボル・定数・設定群に限る。

## 変更規則

2026-10-07 利用者承認の役割分離: 計算・CSV取得は計算用入口へ集約し、制御用入口は対象日・有限値・原本SHAを検証した公開計画を使用する。既存監視の操作・SOC・時刻契約は維持し、同じCSV由来の充電速度推定を任意引数で受け取る。旧入口の既定動作は維持する。`tests/test_separated_runtime.py`で原本一致、旧監視との操作トレース一致、欠損計画、23/07のDB非依存、時刻境界を検証する。移行・削除条件は `docs/current/architecture/SEPARATED_RUNTIME_MIGRATION_JA.md` に従う。

2026-10-03 利用者承認の保存復旧: 03時の計画生成直後に、独立した子プロセスで最大20秒の詳細JSON保存を行う。06:45まで60秒未満なら開始せず、保存失敗は明示して制御を継続する。これは監査データの保存だけであり、lease・過去計画fallback・07 gate・Drive末尾処理は復活させない。`tests/test_plan_snapshot_recovery.py`で失敗・期限・版保持・復元を検証する。

`HISTORICAL_FAILURE_LOCK` コメントが付いた領域は、通常のリファクタリング、簡略化、命名変更、既定値変更の対象にしない。変更が必要な場合だけ、次を満たしてから編集する。

1. 変更理由、影響する過去の障害、現行の実機または保存データによる根拠を変更説明に記録する。
2. 先に過去の失敗を再現する回帰テストを追加または更新する。
3. 外部状態を使う場合は、実機の候補値・read-back・復元結果を確認する。モックだけで成功扱いにしない。
4. `code-quality-audit` の適用チェック、対象テスト、必要なら本番ゲートを実行する。
5. 本番反映は `docs/current/ops/PRODUCTION_DEPLOYMENT_RUNBOOK_JA.md` に従い、失敗状態を手動で成功に書き換えない。

`tests/test_night_soc_protected_contract.py` は2026-08-28のSOC 0%事故用の必須ガードである。夜間SOCに限らず、ソース・設定・テスト・デプロイ変更を行う全ての変更で通常のpytest/preflightに含め、lockコメント、production default、連続した目標SOC、23→03→07のdevice read-backと時刻所有権を同時に確認する。このテストをskip、削除、warning化してはならない。

## 保護対象と根拠

| 保護対象 | 過去の根拠 | 再発防止する事故 | 拘束内容 |
| --- | --- | --- | --- |
| `app/kpnet/workflow.py::run_kpnet_mode_only_profile(profile="forced")`、`app/runtime/cloud_job.py::_RunnerMonitorDevicePort.apply_profile` | 2026-08-23以降の実機確認を2026-09-18に更新: 強制充電モードは `SocChargeMode` の値に依存せず動作する | `SocChargeMode` 候補取得・設定・read-backを強制開始条件にすると、不要な設定API失敗で強制充電そのものが開始できなくなる | 03時forced mode-onlyは `BatteryOperatingMode` をforced候補へ変更するだけとする。`SocChargeMode` は候補取得せず、設定せず、read-back判定対象にせず、現在値を保持する。充電停止は引き続き03時監視の実SOCと最終計画SOCで判断する。 |
| `app/runtime/slot_orchestration.py::_run_night_23` / `_run_adjust_03` / `_run_day_07`、`app/runtime/night_soc_time_contract.py` | 2026-08-29 利用者承認による `d1d7792` / `f2cfa51` の cross-slot ownership 置換。2026-09-18、07時点で実機がstandbyのまま残っている事象を確認し、利用者要件として07時の復帰先をeconomy、`SocEconomyMode=0%`へ明示変更 | 03の失敗・再試行や遅い03書込みが07の日中モード復帰を止める、または07後にstandby/forcedで上書きする | 23はstandby一回、07はeconomy一回を無条件read-backし、07では `BatteryOperatingMode` と `SocEconomyMode=0%` だけを変更する。その他のSOC設定、充放電時間帯、契約電流、停電時設定は現在値を保持する。03は06:45 realtime監視停止・06:50最終standby開始停止・06:55 I/O停止の単独所有とする。時刻は `night_soc_time_contract.py` を唯一の実行根拠とし、23/07へcross-slot依存を戻さない。2026-09-18のstandby残留は、この仕様変更とは別に07 Job実行・SET・read-backログを調査する。|
| `app/runtime/plan_persistence.py::acquire_night_soc_lease` | `79361c4`, `f910b98`, `0a804f4` | Firestore transaction の呼び出し順・generator互換性・既存リース判定の不整合で、所有権取得を誤って拒否または上書きする | このlegacy persistence契約はslot controlの外側だけに適用する。23/03/07のdevice controlへlease判定を戻さない。transaction は read 前に開始し、plan_id・owner・有効期限を維持する。 |

| `scripts/deploy_production_from_env.ps1::Get-DeploymentStageRecord`、`Invoke-DeploymentStage` | `0bc046b`, `92c32d0`, `5e46ff8` | 実行済みCloud操作が状態ファイルへ保存されず、再実行で不要な再ビルド・再実行や手戻りが発生する | OrderedDictionary と PSCustomObject の両方を明示的に扱う。成功した段階だけを resume でスキップし、stage状態の動的メンバー解決に戻さない |
| `app/forecasting/pv_physical.py::HOURS`、`OUTPUT_HOURS`、補正スケール | `4af0d59`, `f35a74f` | 05:00の物理PV出力を追加する際に、既存の07:00以降のSOC計画・校正スケールまで変わる | 出力時間窓の拡張と、既存計画時間の校正を分離する。時間窓・EWMA/実績比率の意味を変更する場合は同一入力の前後比較を行う |
| `static/dashboard.js::estimateHourlyForecastSoc`、`static/dashboard_calculations.js::forecastSocFromLatestActual` | 2026-09-05 ローカル画面で計画SOC欠落時に予想SOC系列が全点null | 保存計画が一時的に欠けただけで時間別予測グラフの予想SOCが消える | 計画SOCが有効なら従来の07:00目標起点を維持する。計画SOCが欠けても時間別実測SOCがあれば、最新実測値を起点に以後を予測して系列を残す。実測SOCもない場合だけ表示不能とする。 |
| `app/energy_plan/monthly_projection.py::previous_billing_period_for_target`、本番の月次料金環境値 | `541cd60` | 当月前半の観測だけ、または根拠のない第3段階ペナルティを使い、SOC目標に余計なバイアスを加える | 前月の実績を基準にし、料金・売電・ペナルティの未確認契約値を発明しない。料金入力はCSV読込から目的関数まで通し、raw集計と計画値を比較する |
| `app/kpnet/settings_roundtrip.py::run_settings_roundtrip`、`scripts/deploy_gcp_jobs.ps1` の設定テストJob、`scripts/deploy_production_from_env.ps1` の `settings_roundtrip` stage | `ee84e43`, `bf48f42`, `5e46ff8`、2026-09-04 非制御scope境界、PR #42 指示 | 実機設定を変更したまま戻らない、実行済みテストがデプロイ状態に記録されない、またはdashboard/forecast-only検証のためだけに無関係な実機設定変更を発生させる | runner/fullではsettings round-tripを必須とし、実行時だけ有効、保持時間60秒固定、初期スナップショットへ復元してread-back確認、Cloud Run retryは0回、Schedulerは作らない。復元処理と状態記録を削除・省略しない。dashboard-only と control Job revisionを変更しない明示`forecast` scopeでは実機round-tripを起動せず、stageを`skipped_not_applicable`として記録する。`control-readonly` は更新経路として拒否し、23/03/07 の更新は clean worktree、immutable image、必須 live probe を強制する `deploy_control_jobs_image_only.ps1` のみを使う。これらのscopeで`skipped_manual`へ緩めない。 |
| `app/settings/forced_charge.py::ForcedChargeSettings.from_env`、03 Job target | 2026-08-29 利用者承認 | 計画値と実機制御値の差を隠す | 0/30/50/80/100 を連続した実効目標とし、0は直ちにstandbyへ遷移する。|
| `app/runtime/cloud_job.py::_RunnerMonitorDevicePort.read_soc`、`_monitor_partial_forced_and_stop` | 2026-09-06 設定SOC 100%に対して実績65%で停止（`c6de93c`）。2026-10-10 利用者承認で古いWeb SOC経路をアプリAPIへ置換 | 遅延CSV・キャッシュ済みHTMLが03制御へ混入すること、またはSOCの単発取得失敗で強制充電を早期終了すること | 03制御SOCは `measureFlg=GW` の完了応答だけを使い、測定時刻が今回の要求以降であることを検証する。HTMLパーサー・CSV fallback経路は削除し復活させない。監視中の取得失敗は連続回数で数え、正常取得でリセットし、`max_consecutive_soc_failures` 到達時だけstandbyへ遷移する。60秒の取得予算と06:45/06:50/06:55の時刻所有権は維持する。`test_soc_api.py` と既存監視回帰テストで保護する。 |
| `app/kpnet/workflow.py::_preserve_night_soc_fields` (`EVIDENCE_20260829_SLOT23_PRESERVE`) | 2026-08-28 23:00実機読戻しでgreen=1のまま。2026-09-18、利用者要件としてmode切替時の不要な設定変更を禁止 | `batteryOperatingMode`までpreserveするとstandby=5をgreen=1で上書きする。一方、mode以外の値を固定profile由来にするとSOC・時間帯・契約電流・停電設定などを不要に変更し得る | `batteryOperatingMode`はpreserveせずstandby候補だけを書き、その他の書込み対象フォーム値はすべて直前の現在値を保持する。forced mode-onlyは`BatteryOperatingMode`だけ、07 economyは`BatteryOperatingMode`と`SocEconomyMode`だけ変更する。回帰テストで差分項目を固定する。 |
| `app/kpnet/profile_builder.py::_pick_battery_operating_mode_code` (`EVIDENCE_20260829_STANDBY_CANDIDATE`) | 2026-08-29実機候補値（0=economy, 1=green, 3=forced, 5=standby） | 旧standby=0 fallbackは経済モードを書き、待機read-backの意味を壊す | standbyは候補ラベルから5を選び、候補不在はfail closedする。実候補4値の回帰テストを必須とする。 |
| `scripts/deploy_gcp_jobs.ps1` 03 Job `--max-retries 3` と `app/runtime/cloud_job.py::_wait_for_03_platform_retry` (`EVIDENCE_20260909_JOB03_RETRY`) | 2026-09-09に03強制充電設定が時間予算例外で失敗し、実績SOCが0%のままになった。2026-08-29の無待機再試行は別plan_id/lease不整合を起こした | retryが即時に03制御へ入り、07:00所有権や元のread-back失敗を覆い隠す | Cloud Run retryは3回まで、retry taskは03 controller前に300秒待機して構造化ログを出す。06:45/06:50/06:55の既存I/Oフェンス、14100秒timeout、23/07独立性を変更しない。局所lock・03 deploy行・待機ログの回帰テストを必須とする。 |

`app/runtime/cloud_job.py::_monitor_partial_forced_and_stop` は、03制御結果の監査用として、単独のJSON stdout行（`message="03-terminal-audit"`）を最大1件出力してよい。この出力はbest-effortで、失敗しても制御・exit code・07へ影響してはならない。Firestore、DB、lease、owner、cross-slot hand-off、terminal-state保存は引き続き禁止する。

## 履歴の扱い

### 予測履歴7日と請求期間の分離（2026-10-08障害）

`app/energy_plan/monitoring_history.py` の `HISTORICAL_FAILURE_LOCK` は、予測に
直近の観測が揃った7日を使い、最新CSVの月で期間を切らない契約である。
当日・未来・欠損・非有限・負の発電/消費を含む日を学習せず、有効な実測0は保持する。
`pv_selection.py` のlockはSQLite校正履歴が選択済み7日を広げることを禁止する。
料金評価はこの7日に切らず、永続監視実績から現在と直前の請求期間を取得する。
現在の請求期間に欠けた日がある場合は正常な料金累積を発明せず停止する。
10/8の障害では9月分145.960 kWhが欠落し、累積27.161 kWhで朝5%を採用した。
正しい累積173.121 kWhだけを渡す再計算では朝57%となった。
既存`541cd60`の直前請求期間参照と、PV欠損を0にしない保護は維持する。
`tests/test_forecast_monitoring_history.py` が月跨ぎ・年跨ぎ・欠損日・CSV金融値の
集計・重複排除・期間欠落時の停止を固定する。ダッシュボードSOC起点の契約は変更しない。

### PV残差の欠損と実測0（2026-10-07障害）

`app/forecasting/correction_model.py::_physical_vector_residual_correction` と
`app/forecasting/correction_calculations.py::actual_hourly_totals_by_day` の
`HISTORICAL_FAILURE_LOCK` は、欠損・NULL・非有限・負のPVを実測0として学習しない契約である。
10/6の保存入力では139件中111件が欠損で、物理予想7.6392 kWhが3.5021 kWhへ下方補正された。
有効な実績との組だけでは8.1773 kWhとなった。この差を天気の誤差として扱わない。
無効な計測区間を含む時間はPVキーを除外し、後続の有効区間で復活させない。
日別の比率学習にもこの欠損を0として戻さない。実測0と有効な負残差は保持する。
`tests/test_physical_pv_residual_quality.py` が欠損、集計順序、実測0、負残差、履歴期間差の回帰を固定する。
保存済みの過去予想は当時の原本として保持し、この修正で後日の計算値へ置き換えない。

### 予想履歴と表示の共通契約（2026-10-06障害）

保護対象は `app/operations/forecast_recovery.py::recover_missing_forecast_snapshots`、
`app/runtime/forecast_job.py::main`、`app/operations/domain.py::extract_hourly_forecast_from_plan`、
`app/operations/forecast_persistence.py::_complete_hourly_rows`、
`app/dashboard/aggregation.py::_build_energy_daily`、
`app/dashboard/firestore_repository.py::_build_firestore_daily_reviews`、
`app/dashboard/history_reconstruction.py::_metadata_matches_forecast_row` の
`HISTORICAL_FAILURE_LOCK` コメントと以下の契約である。

- 7月16日の `93e8c3c` は予想抽出を共通化し、9月3日の `7899423` / `76f1e91` は原本スナップショットから履歴を回復した。9月18日の `a69146b` はSOC表示の正本を統一した。いずれも予想ジョブ失敗後の保存済み制御計画の取り込みを保証しておらず、レビューのPV直接参照が残っていた。
- 9月3日の `894e9f3` で予想専用ジョブを追加し、9月24日の `9692635` で実績取り込みを追加したが、当日対象・計画取り込み無効のため過去の欠損を回復しなかった。10月6日の起動失敗では、計画の最終PV 3.5021 kWhが残る一方、グラフのPVは欠損、消費は過去実績の代替推計になった。
- レビューとグラフのPV・消費は同じ日別集計・同じ選択済み時間別予想を使用する。別の計画結果や日別PVから片側だけを上書きしない。
- SQLite検証用読取モデルも予想の実行ID・当時の生成時刻を保存・同期・表示まで保持する。`app/operations/sync.py::TABLE_SPECS` と `app/dashboard/sqlite_repository.py::load_sqlite_query_snapshot` のlockを保護し、実データのbackend parityで出典の脱落を無視しない。
- 非制御の予想ジョブは実績・表示更新より前に、直近31日以内の欠損原本だけを保存済み計画から取り込む。有効な原本は保持し、当時の生成時刻・実行IDを維持する。過去日の再予想や後日再計算を当時の原本として保存しない。
- 時間別値の欠損・非有限値をゼロに偽装しない。モデル出力窓外の夜間PVゼロは維持し、24時間の数値検証に失敗した結果で有効な保存値を置き換えない。
- ダッシュボードGETからアーカイブを取得しない。初期bootstrap・履歴遅延読取・事前生成データの高速経路を維持する。実機制御の時刻所有権やDB非依存性は維持する。

変更時は `tests/test_forecast_history_contract.py` と
`tests/test_night_soc_protected_contract.py` を必須とする。コメント削除・契約変更は利用者の明示承認と、上記障害の回帰検証を伴う場合だけ許可する。

上表のコミットは、単なる設計案ではなく、このリポジトリで実際に行われた障害対応または再発防止修正の境界である。新しい設計へ移行する場合も、旧境界を先にテストで置き換え、保護対象の削除理由を同じ変更に記録する。

## 03時の実機確認基準

時刻境界の自動テストは、締切後に新しいHTTP要求・再試行・retry sleepを開始しないことを確認する。既にKP-NETへ送出済みの非同期要求をクライアントだけで取り消せる保証ではない。本番反映後は、03実行ログとKP-NETのread-backで、06:45以後にSOC realtime要求がないこと、06:50前に開始した最終standbyが候補5でread-backされること、07:00のeconomyが実機候補ラベルから選択され、`SocEconomyMode=0%` がread-backされることを確認する。その他の設定値は07時処理前の値を保持する。

ダッシュボードの朝SOC目標判定は、07:00の日中economy切替後に同時刻の放電が反映される競合を避けるため、切替前の最終30分実績（通常06:30）のみを使う。06:30実績がない場合に07:00以後のSOCで代用してはならない。変更時は、06:30で目標到達後に07:00で低下しても未達警告を出さないことと、06:30で真に未達なら警告することを回帰テストで固定する。

`docs/current/architecture/NIGHT_SOC_SINGLE_OWNER_IMPLEMENTATION_SPEC_JA.md` は退役済みの歴史的設計であり、現行指示として扱わない。現行の23/03/07責務と時刻境界は `app/runtime/night_soc_time_contract.py` のみを根拠とする。退役文書の旧ゲート、手動スロットskip、再適用を現行契約へ戻してはならない。

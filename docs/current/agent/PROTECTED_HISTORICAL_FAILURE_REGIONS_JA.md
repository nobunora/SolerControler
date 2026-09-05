# 過去の障害に基づく変更保護領域

この文書は、最近の障害と、その少し前までの修正履歴から、再変更で同じ事故を起こしやすい箇所を固定するための作業規約である。対象はファイル全体ではなく、記載したシンボル・定数・設定群に限る。

## 変更規則

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
| `app/runtime/night_soc_controller.py::build_device_soc_guard`、`app/kpnet/profile_builder.py::_build_dynamic_forced_profile` | `d1d7792`、2026-08-23実機確認 | 実機の `SocChargeMode` は最大50%までで、計画SOCは50%を超え得る。このとき50%候補で強制充電を開始し、03時監視が連続値の計画SOC到達で待機へ遷移する | 最大候補未満を強制開始エラーに戻さない。`SocChargeMode` を停止閾値に使わず、連続目標と停止閾値を03時監視に残す。変更時は50%強制開始・read-back・60秒後の復元と待機遷移を再検証する |
| `app/runtime/slot_orchestration.py::_run_night_23` / `_run_adjust_03` / `_run_day_07`、`app/runtime/night_soc_time_contract.py` | 2026-08-29 利用者承認による `d1d7792` / `f2cfa51` の cross-slot ownership 置換 | 03の失敗・再試行や遅い03書込みが07 greenを止め、または07後にgreenを上書きする | 23はstandby一回、07はgreen一回を無条件read-backし、03は06:45 realtime監視停止・06:50最終standby開始停止・06:55 I/O停止の単独所有とする。時刻は `night_soc_time_contract.py` を唯一の実行根拠とし、23/07へcross-slot依存を戻さない。|
| `app/runtime/plan_persistence.py::acquire_night_soc_lease` | `79361c4`, `f910b98`, `0a804f4` | Firestore transaction の呼び出し順・generator互換性・既存リース判定の不整合で、所有権取得を誤って拒否または上書きする | このlegacy persistence契約はslot controlの外側だけに適用する。23/03/07のdevice controlへlease判定を戻さない。transaction は read 前に開始し、plan_id・owner・有効期限を維持する。 |

| `scripts/deploy_production_from_env.ps1::Get-DeploymentStageRecord`、`Invoke-DeploymentStage` | `0bc046b`, `92c32d0`, `5e46ff8` | 実行済みCloud操作が状態ファイルへ保存されず、再実行で不要な再ビルド・再実行や手戻りが発生する | OrderedDictionary と PSCustomObject の両方を明示的に扱う。成功した段階だけを resume でスキップし、stage状態の動的メンバー解決に戻さない |
| `app/forecasting/pv_physical.py::HOURS`、`OUTPUT_HOURS`、補正スケール | `4af0d59`, `f35a74f` | 05:00の物理PV出力を追加する際に、既存の07:00以降のSOC計画・校正スケールまで変わる | 出力時間窓の拡張と、既存計画時間の校正を分離する。時間窓・EWMA/実績比率の意味を変更する場合は同一入力の前後比較を行う |
| `static/dashboard.js::estimateHourlyForecastSoc`、`static/dashboard_calculations.js::forecastSocFromLatestActual` | 2026-09-05 ローカル画面で計画SOC欠落時に予想SOC系列が全点null | 保存計画が一時的に欠けただけで時間別予測グラフの予想SOCが消える | 計画SOCが有効なら従来の07:00目標起点を維持する。計画SOCが欠けても時間別実測SOCがあれば、最新実測値を起点に以後を予測して系列を残す。実測SOCもない場合だけ表示不能とする。 |
| `app/energy_plan/monthly_projection.py::previous_billing_period_for_target`、本番の月次料金環境値 | `541cd60` | 当月前半の観測だけ、または根拠のない第3段階ペナルティを使い、SOC目標に余計なバイアスを加える | 前月の実績を基準にし、料金・売電・ペナルティの未確認契約値を発明しない。料金入力はCSV読込から目的関数まで通し、raw集計と計画値を比較する |
| `app/kpnet/settings_roundtrip.py::run_settings_roundtrip`、`scripts/deploy_gcp_jobs.ps1` の設定テストJob、`scripts/deploy_production_from_env.ps1` の `settings_roundtrip` stage | `ee84e43`, `bf48f42`, `5e46ff8`、2026-09-04 非制御scope境界 | 実機設定を変更したまま戻らない、実行済みテストがデプロイ状態に記録されない、またはdashboard/forecast-only検証のためだけに無関係な実機設定変更を発生させる | runner/fullではsettings round-tripを必須とし、実行時だけ有効、保持時間60秒固定、初期スナップショットへ復元してread-back確認、Cloud Run retryは0回、Schedulerは作らない。復元処理と状態記録を削除・省略しない。dashboard-onlyと、control Job revisionを変更しない明示`forecast` scopeでは実機round-tripを起動せず、stageを`skipped_not_applicable`として記録する。これらのscopeで`skipped_manual`へ緩めたり、逆に実機round-tripを追加したりしない。 |
| `app/settings/forced_charge.py::ForcedChargeSettings.from_env`、03 Job target | 2026-08-29 利用者承認 | 計画値と実機制御値の差を隠す | 0/30/50/80/100 を連続した実効目標とし、0は直ちにstandbyへ遷移する。|
| `app/kpnet/workflow.py::_preserve_night_soc_fields` (`EVIDENCE_20260829_SLOT23_PRESERVE`) | 2026-08-28 23:00実機読戻しでgreen=1のまま | `batteryOperatingMode`までpreserveするとstandby=5をgreen=1で上書きし、23:00成功ログ後も物理的にgreenのままになる | modeをpreserveへ追加せず、12個のSOC/時刻フィールドだけを維持する。局所lock検査とgreen1→standby5回帰テストを必須とする。 |
| `app/kpnet/profile_builder.py::_pick_battery_operating_mode_code` (`EVIDENCE_20260829_STANDBY_CANDIDATE`) | 2026-08-29実機候補値（0=economy, 1=green, 3=forced, 5=standby） | 旧standby=0 fallbackは経済モードを書き、待機read-backの意味を壊す | standbyは候補ラベルから5を選び、候補不在はfail closedする。実候補4値の回帰テストを必須とする。 |
| `scripts/deploy_gcp_jobs.ps1` 03 Job `--max-retries 0` (`EVIDENCE_20260829_JOB03_RETRY`) | 2026-08-29 Cloud Run再試行で別plan_id生成後にlease拒否 | retry=1以上は二回目のlease拒否で一次KP-NET read-back不一致を覆い隠し、07:00用終端を残さない | platform retryを0に固定し、内部KP retryは維持する。局所マーカーと03 deploy行のsemantic testを必須とする。 |

`app/runtime/cloud_job.py::_monitor_partial_forced_and_stop` は、03制御結果の監査用として、単独のJSON stdout行（`message="03-terminal-audit"`）を最大1件出力してよい。この出力はbest-effortで、失敗しても制御・exit code・07へ影響してはならない。Firestore、DB、lease、owner、cross-slot hand-off、terminal-state保存は引き続き禁止する。

## 履歴の扱い

上表のコミットは、単なる設計案ではなく、このリポジトリで実際に行われた障害対応または再発防止修正の境界である。新しい設計へ移行する場合も、旧境界を先にテストで置き換え、保護対象の削除理由を同じ変更に記録する。

## 03時の実機確認基準

時刻境界の自動テストは、締切後に新しいHTTP要求・再試行・retry sleepを開始しないことを確認する。既にKP-NETへ送出済みの非同期要求をクライアントだけで取り消せる保証ではない。本番反映後は、03実行ログとKP-NETのread-backで、06:45以後にSOC realtime要求がないこと、06:50前に開始した最終standbyが候補5でread-backされること、07:00のgreenが候補1でread-backされることを確認する。

ダッシュボードの朝SOC目標判定は、07:00のgreen切替後に同時刻の放電が反映される競合を避けるため、green切替前の最終30分実績（通常06:30）のみを使う。06:30実績がない場合に07:00以後のSOCで代用してはならない。変更時は、06:30で目標到達後に07:00で低下しても未達警告を出さないことと、06:30で真に未達なら警告することを回帰テストで固定する。

`docs/current/architecture/NIGHT_SOC_SINGLE_OWNER_IMPLEMENTATION_SPEC_JA.md` は退役済みの歴史的設計であり、現行指示として扱わない。現行の23/03/07責務と時刻境界は `app/runtime/night_soc_time_contract.py` のみを根拠とする。退役文書の旧ゲート、手動スロットskip、再適用を現行契約へ戻してはならない。

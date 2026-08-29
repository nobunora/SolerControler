# 過去の障害に基づく変更保護領域

この文書は、最近の障害と、その少し前までの修正履歴から、再変更で同じ事故を起こしやすい箇所を固定するための作業規約である。対象はファイル全体ではなく、記載したシンボル・定数・設定群に限る。

## 変更規則

`HISTORICAL_FAILURE_LOCK` コメントが付いた領域は、通常のリファクタリング、簡略化、命名変更、既定値変更の対象にしない。変更が必要な場合だけ、次を満たしてから編集する。

1. 変更理由、影響する過去の障害、現行の実機または保存データによる根拠を変更説明に記録する。
2. 先に過去の失敗を再現する回帰テストを追加または更新する。
3. 外部状態を使う場合は、実機の候補値・read-back・復元結果を確認する。モックだけで成功扱いにしない。
4. `code-quality-audit` の適用チェック、対象テスト、必要なら本番ゲートを実行する。
5. 本番反映は `docs/current/ops/PRODUCTION_DEPLOYMENT_RUNBOOK_JA.md` に従い、失敗状態を手動で成功に書き換えない。

`tests/test_night_soc_protected_contract.py` は2026-08-28のSOC 0%事故用の必須ガードである。夜間SOCに限らず、ソース・設定・テスト・デプロイ変更を行う全ての変更で通常のpytest/preflightに含めて実行し、lockコメント、production default、30%実行床、manual opt-in、23→03→07のmonitor・終端state・07 green gate契約を同時に確認する。このテストをskip、削除、warning化してはならない。

## 保護対象と根拠

| 保護対象 | 過去の根拠 | 再発防止する事故 | 拘束内容 |
| --- | --- | --- | --- |
| `app/runtime/night_soc_controller.py::build_device_soc_guard`、`app/kpnet/profile_builder.py::_build_dynamic_forced_profile` | `d1d7792`、2026-08-23実機確認 | 実機の `SocChargeMode` は最大50%までで、計画SOCは50%を超え得る。このとき50%候補で強制充電を開始し、03時監視が連続値の計画SOC到達で待機へ遷移する | 最大候補未満を強制開始エラーに戻さない。`SocChargeMode` を停止閾値に使わず、連続目標と停止閾値を03時監視に残す。変更時は50%強制開始・read-back・60秒後の復元と待機遷移を再検証する |
| `app/runtime/cloud_job.py::_monitor_partial_forced_and_stop`、`_RunnerMonitorDevicePort::apply_profile`、`_assert_day_transition_allowed` | `d1d7792` | 強制充電開始失敗時の安全待機、目標SOC到達後の待機、07:00遷移の所有者制御が崩れて朝SOC 0%になる | このジョブが強制開始からSOC監視、停止、最終待機、実行状態永続化を所有する。`readback_match` だけを強制開始成功の証拠にしない |
| `app/runtime/plan_persistence.py::acquire_night_soc_lease`、`app/runtime/cloud_job.py::_acquire_night_soc_lease` | `79361c4`, `f910b98`, `0a804f4` | Firestore transaction の呼び出し順・generator互換性・既存リース判定の不整合で、所有権取得を誤って拒否または上書きする | transaction は read 前に開始し、plan_id・owner・有効期限を維持する。リトライや lease 判定を変更する場合は Firestore fake テストを先に更新する |
| `scripts/deploy_production_from_env.ps1::Get-DeploymentStageRecord`、`Invoke-DeploymentStage` | `0bc046b`, `92c32d0`, `5e46ff8` | 実行済みCloud操作が状態ファイルへ保存されず、再実行で不要な再ビルド・再実行や手戻りが発生する | OrderedDictionary と PSCustomObject の両方を明示的に扱う。成功した段階だけを resume でスキップし、stage状態の動的メンバー解決に戻さない |
| `app/forecasting/pv_physical.py::HOURS`、`OUTPUT_HOURS`、補正スケール | `4af0d59`, `f35a74f` | 05:00の物理PV出力を追加する際に、既存の07:00以降のSOC計画・校正スケールまで変わる | 出力時間窓の拡張と、既存計画時間の校正を分離する。時間窓・EWMA/実績比率の意味を変更する場合は同一入力の前後比較を行う |
| `app/energy_plan/monthly_projection.py::previous_billing_period_for_target`、本番の月次料金環境値 | `541cd60` | 当月前半の観測だけ、または根拠のない第3段階ペナルティを使い、SOC目標に余計なバイアスを加える | 前月の実績を基準にし、料金・売電・ペナルティの未確認契約値を発明しない。料金入力はCSV読込から目的関数まで通し、raw集計と計画値を比較する |
| `app/kpnet/settings_roundtrip.py::run_settings_roundtrip`、`scripts/deploy_gcp_jobs.ps1` の設定テストJob | `ee84e43`, `bf48f42`, `5e46ff8` | 実機設定を変更したまま戻らない、または実行済みテストがデプロイ状態に記録されない | 実行時だけ有効、保持時間は60秒固定、初期スナップショットへ復元してread-back確認、Cloud Run retryは0回、Schedulerは作らない。復元処理と状態記録を削除・省略しない |
| `scripts/deploy_gcp_jobs.ps1::$commonEnv` の `NIGHT_SOC_MANUAL_OPERATION=false`、03:00 Job の `ADJUST03_MIN_TARGET_SOC_PERCENT=30`、`app/runtime/slot_orchestration.py::_manual_soc_operation_enabled` / `_run_adjust_03`、`scripts/kpnet_incident_validation.py::run_scheduled_auto_path_validation` | `d1d7792`, `1dd21ae`, 2026-08-28実績（plan=100%, actual=0%, charge=0kWh） | 手動運用を本番既定にして23:00 standby と03:00 lease・監視・強制充電・read-back・終端状態を全て迂回し、07:00だけが残る。又は実行下限を0にしてoptimizerのplan=0がSOC 0%のまま実効目標になる。実機round-tripだけでは定時分岐の迂回を検出できない | 手動運用は runtime の明示 override のみで、本番既定値を `false` から変更しない。03:00実行下限30%を削除・0化しない（plan=100は維持しplan=0だけ30へ上げる）。定時23→03→07 replay は検証の必須成功条件であり、03 monitorが1回、Firestore終端状態、07 green順序を確認してから実機round-tripを行う。real/fake Firestoreのmanual hand-offはowner・write-skipped・freshness・plan_idを同じ契約で満たす。変更時は全ケースの回帰試験と実機read-back/復元を再実施する。 |
| `app/kpnet/workflow.py::_preserve_night_soc_fields` (`EVIDENCE_20260829_SLOT23_PRESERVE`) | 2026-08-28 23:00実機読戻しでgreen=1のまま | `batteryOperatingMode`までpreserveするとstandby=5をgreen=1で上書きし、23:00成功ログ後も物理的にgreenのままになる | modeをpreserveへ追加せず、12個のSOC/時刻フィールドだけを維持する。局所lock検査とgreen1→standby5回帰テストを必須とする。 |
| `app/kpnet/profile_builder.py::_pick_battery_operating_mode_code` (`EVIDENCE_20260829_STANDBY_CANDIDATE`) | 2026-08-29実機候補値（0=economy, 1=green, 3=forced, 5=standby） | 旧standby=0 fallbackは経済モードを書き、待機read-backの意味を壊す | standbyは候補ラベルから5を選び、候補不在はfail closedする。実候補4値の回帰テストを必須とする。 |
| `app/runtime/cloud_job.py::_finalize_03_exception_with_fail_safe_standby` (`EVIDENCE_20260829_FAILSAFE_FINALIZER`) | 2026-08-29 03:00 forced reapply read-back mismatch | standby試行だけでACKする、又はstandby側例外で元の不一致を隠すと、07:00を誤許可するか一次原因を喪失する | standby apply/read-back成功時だけ`STANDBY_ACKED/failed_command`、失敗時は`STANDBY_UNCONFIRMED`とし、元例外を再送出する。終端行列テストを必須とする。 |
| `app/runtime/cloud_job.py::_apply_03_confirmed_standby` (`EVIDENCE_20260829_CONFIRMED_STANDBY`) | 2026-08-29 07:00 green未遷移の終端欠落 | ACKをapply/read-back前又はfinallyで書くと、実機がforced/unknownでも07:00 greenへ進む | KP-NET standby確認後にのみstop reasonとACKを永続化する。standby失敗時の非ACKテストを必須とする。 |
| `app/runtime/night_soc_operational_contract.py::DAY_TRANSITION_ALLOWED_STATES` (`EVIDENCE_20260829_DAY_GATE`) | 2026-08-29 standby未確認時の07:00 block | `STANDBY_UNCONFIRMED`や汎用失敗を許可集合へ加えると実機状態不明のままgreenへ遷移する | 許可集合を`STANDBY_ACKED/COMPLETED_NO_CHARGE/VERIFIED`だけに固定し、全状態行列でfail closedを確認する。 |
| `scripts/deploy_gcp_jobs.ps1` 03 Job `--max-retries 0` (`EVIDENCE_20260829_JOB03_RETRY`) | 2026-08-29 Cloud Run再試行で別plan_id生成後にlease拒否 | retry=1以上は二回目のlease拒否で一次KP-NET read-back不一致を覆い隠し、07:00用終端を残さない | platform retryを0に固定し、内部KP retryは維持する。局所マーカーと03 deploy行のsemantic testを必須とする。 |

## 履歴の扱い

上表のコミットは、単なる設計案ではなく、このリポジトリで実際に行われた障害対応または再発防止修正の境界である。新しい設計へ移行する場合も、旧境界を先にテストで置き換え、保護対象の削除理由を同じ変更に記録する。

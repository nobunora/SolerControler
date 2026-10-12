# S10 既存backup復元・通し検証・release引継ぎ

依存: S09。関連gate: G07/G08。coverage: R03/R07/R08/R10/R11/R14/R15、C05trace/C06backup/C07cache/C08save-before-control/C09freshness/C11readperf/C12protected-vars/C14logs/C15sourceunchanged/C16gates。

## 入口・参照範囲

- S09までのdiff/品質/実adapter/性能証拠を受け取る。文書パッケージ完成、導入前試験、production read-backを別の結果にする。
- AIルーティング: release判断/復元hash/未完gateはCodex、Lunaは承認済み差分の限定確認だけ。本番実行はcurrent conversation明示承認とsolar-production-deployment Skillが必要。
- [共通契約](../contracts.md)、[公開契約](../publication-contracts.md)、[gates](../gates.json)を正本にする。新backup形式・独立calibration currentの復元を作らない。
- CodebaseMemory親照会のimmutable archive経路を使い、Drive embed→offline restoreは下記実sourceで確認する。再index/daemonを本段で起動しない。

## 変更対象

| ファイル・完全修飾symbol | 本段の許可範囲 |
| --- | --- |
| [drive.py](../../../../../app/backup/drive.py) `app.backup.drive.build_firestore_snapshot` | **読取/検証のみ**。既存schema2/collection/embedded rawを通す。production code変更なし |
| [plan_snapshot.py](../../../../../app/backup/plan_snapshot.py) `app.backup.plan_snapshot.embed_plan_detail` | **読取/検証のみ**。traceを含むrawのhash検証 |
| 同上 `app.backup.plan_snapshot.restore_snapshot_payload` | **読取/検証のみ**。local temp fixtureへoffline復元。クラウド復元・control採用へ使わない |
| `tests/test_plan_snapshot_recovery.py` / **NEW LOCAL** `tests/test_pv_clear_ghi_backup_roundtrip.py` | 新trace fixtureと改竄/欠落/旧plan互換の局所回帰。実server中断は未実証 |

release/deploy/scripts/env/deps/scheduler/IAMは変更対象0。source修正が必要なら該当S01〜S09へ戻し、本段でcleanupしない。

## 変更禁止

| symbol・実source anchor | 保持変数/コード |
| --- | --- |
| `drive.PLAN_BACKUP_COLLECTIONS`: `("night_charge_plans", "night_plan_decisions", "forecast_plans", "forecast_hourly_snapshots")` | 既存collection範囲。traceだけを理由にcurrent coefficient/new collectionを足さない |
| `drive.build_firestore_snapshot`: `"schema_version": 2,` | `collections/counts/captured_at`、既存schema/embedded raw/`unavailable_legacy`意味 |
| `plan_snapshot.embed_plan_detail`: `raise ValueError("backup source checksum mismatch")` | `doc/text/raw/sha/allow_legacy_summary`、checksumを緩和しない。summaryのみからrawを捏造しない |
| `plan_snapshot.restore_snapshot_payload`: `raise ValueError("backup detail checksum mismatch")` | `snapshot/plans/targets/path/raw`、全部検証後write、既存destination conflict拒否 |
| `cloud_job._archive_generated_plan`: `if remaining < 60:` / `timeout_seconds=20` | `remaining/plan_path/source`、独立子process20秒・06:45前60秒reserve・失敗後制御継続。source編集禁止 |
| `night_soc_time_contract`: `FORCED_MONITOR_CUTOFF = time(6, 45)` / `FINAL_STANDBY_START_CUTOFF = time(6, 50)` / `CONTROL_HARD_CUTOFF = time(6, 55)` | cutoff/240秒operation/60秒reserve、23・07 DB非依存/単独所有を保持 |

[保護文書](../../../agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md)と `badd209` の03独立化・20秒保存回帰、`93e8c3c/7899423/76f1e91/a69146b` の原本/由来/SOC契約を保持する。Q01 legacy07 profile文言は別確認項目でPV変更へ混ぜない。

## クラス・変数差分

| 所在 | 差分 | 意味 |
| --- | --- | --- |
| production sourceの上記symbol/定数 | 追加/変更0 | 既存raw/detail_sha256/decision_id/source_revisionをそのまま検証 |
| NEW LOCAL backup test | local `original_raw/restored_raw/expected_sha/trace` | 同bytes/fulltrace照合、既存canonical pack object＋外部hashをデータ契約で再検証 |
| PLANNED release fixture | local `control_version/forecast_run_id/display_version` | 生成/採用/表示の別識別。backup成功をdevice appliedへ読み替えない |

NEW production function/classは本段では0。test fixtureのlocal値を新API/envへ昇格しない。

## 実装手順・分岐

1. S01〜S09の試験実装/実行証拠とG07実adapter/crash/retryを確認。fake/論理20・1,680順序を本番atomicityの保証へ読み替えない。
2. 既存Drive schema2へtraceを含むrawをembedするoffline fake fixtureを作る。少なくとも正常・同版retry・旧plan・改竄・欠落の5件を検査する。
3. offline temp destinationへ復元しbytes SHA256一致、fulltrace一箇所、pack canonical hash再生成一致、原本PV24/SOC/容量を照合する。現時点のcoef current配信参照は自動復元対象としない。
4. 破損hash/非dict/詳細なし新decision/既存destination違いはwrite前に拒否。旧summary missing-originalは既存 `unavailable_legacy` として他正常原本を阻害しない。
5. 訂正historyの再構築、同時writer/crash/同版再試行、control採用後projection/asset失敗復旧を通す。03/07へtransaction/backup tailを戻さない。
6. 全必須品質/保護回帰、秘密check、diffを確認。文書のみの作業でソース/設定/テストが変わっていないことも記録する。
7. 本番が未承認ならG08で止め、ローカル結果と残るlive確認を渡す。承認後はproduction runbook全文とSkillを読みcanonical wrapper/StatePathで実行する。本packetに直接cloud commandを生成しない。

## テスト・PASS条件

tests前にcode-quality-audit/QUALITY_GATE/pre_releaseを満たす。NEW試験は追加済み・ローカル実行済み。G07/G08未完を本番PASSへ置換しない。

| ID・状態 | fixture/条件 | command・厳密PASS |
| --- | --- | --- |
| RELEASE-01 EXISTING | 原本retry/旧summary/改竄/restore conflict/20秒期限 | `python -m pytest tests/test_plan_snapshot_recovery.py tests/test_drive_backup.py -q`。原本hash/旧形式/first provenance維持、無効fixture拒否、03失敗で制御継続 |
| RELEASE-02 NEW LOCAL `test_pv_clear_ghi_backup_roundtrip.py` | 正常/同版retry/改竄/欠落＋既存旧plan回帰 | `python -m pytest tests/test_pv_clear_ghi_backup_roundtrip.py tests/test_plan_snapshot_recovery.py -q`。正常bytes SHA一致/fulltrace復元、hash不一致write0、新decision欠落拒否、旧summary互換、独立calibration current自動復元0 |
| RELEASE-03 EXISTING | protected control/SOC/予想/表示 | `python -m pytest tests/test_night_soc_protected_contract.py tests/test_separated_runtime.py tests/test_soc_api.py tests/test_forecast_history_contract.py tests/test_dashboard_forecast_soc_contract.py -q`。skip/弱化0、23・07 DB非依存/時刻/API SOC/同原本容量・SOC PASS |
| RELEASE-04 PARTIAL 上記NEW＋公開系test | generated/archive/control/transaction/asset/Drive各境界のfixture | 上記NEW commandとS07〜S09 command。版/hash保持・拒否candidate mutable0・control再実行0。実adapter通しcrash/並行訂正復旧はG07未実証 |
| RELEASE-05 EXISTING gate | 必須lint/mypy/import/JS/security/全pytest、env未stage | `pwsh -NoProfile -File scripts/pre_release_local.ps1`。現行必須gate exit0、保護試験skip0。過去1008/143件を今回値へ転記しない |

G08 liveは本番mutation承認後のcanonical deployment validation task。SET/read-back/reconciliation、cache/request/bytesの実測がない状態でproduction PASSを出さない。

## 中断条件

- G07実adapter/race/recovery未完、復元hash/trace不一致、current calibration参照がDriveで戻るとの誤認。
- G08未承認でmerge/deploy/import/backup/job production mutationが必要、canonical wrapper不能、秘密/識別子漏えい。
- 03 archive時刻/23・07独立性/旧plan reader失敗、品質gate/保護test skip、過去訂正historyが更新されない。

## 出口・引継ぎ

結果を文書完備/ローカル実装検証/実adapter/productionの各根拠で分ける。未完gateはINCONCLUSIVE理由と必要証拠を記録。最小handoffはG07を完了した上で、G08のcanonical deploy直前成果物とlive検証項目を利用者へ渡すこと。

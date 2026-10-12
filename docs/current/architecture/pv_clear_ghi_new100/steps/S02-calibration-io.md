# S02 完成係数原本の読込・保存

ID: S02 / 前提: S01 / 状態: LOCAL_IMPLEMENTED_TEST_PENDING / ゲート: G02。

## 入口・参照範囲

[共通契約](../contracts.md)、[型とpack schema](../data-contracts.md)、[ゲート](../gates.json) を先に読む。
入力はS01の完成 `ClearGhiCalibration` または呼出元が選んだ一つの完成原本path。
出力は検証済み同原本、またはhash名保存Path。保存先/provider/updaterはG02の別結線判断。
このstepのlocal原本試験PASSをCloud Run/GCSの更新証明へ拡張しない。

## 変更対象

| path | qualified symbol |
| --- | --- |
| `app/forecasting/clear_ghi_calibration_io.py` NEW | `load_clear_ghi_calibration`, `write_clear_ghi_calibration` |
| `tests/test_clear_ghi_calibration_io.py` NEW | hash/read-back/crash/冪等・不正原本試験 |
| `app/forecasting/clear_ghi_store.py` NEW | `ClearGhiStore.read_current`, `publish`, `restore_current`, `check_deadline`, `remaining_timeout` |
| `tests/test_clear_ghi_store.py` NEW | local/store SDK mock・coldstart/CAS/deadline/明示復旧 |

非制御の原本I/Oだけ。storage結線の新env/IAM/job/schedulerは許可対象に含まれない。

## 変更禁止

| 既存qualified symbol / source | 保護する実コード・変数 |
| --- | --- |
| [pv_selection._build_selected_pv_forecast](../../../../../app/energy_plan/pv_selection.py) | `selected_days = {row["dt"].date().isoformat() for row in context.rows}` / `physical_history = {day: values for day, values in physical_history.items() if day in selected_days}`、7日lock保持 |
| [workflow._prepare_night_charge](../../../../../app/energy_plan/workflow.py) | `context.billing_rows if context.billing_rows is not None else context.rows`、請求履歴を晴天訓練集合へ置換しない |
| `clear_ghi_calibration_io` NEW | 学習/予報取得/control pointer/lease/GET同期再学習、別site/旧PV/全履歴探索禁止 |

## クラス・変数差分

新classなし。S01のimmutable `ClearGhiCalibration` を返す。
NEW signatures:

```python
load_clear_ghi_calibration(path, *, expected_site, expected_basis, target_date, as_of) -> ClearGhiCalibration
write_clear_ghi_calibration(calibration, *, directory) -> Path
```

`path,directory: Path` は呼出元所有、関数内暗黙defaultなし。
`raw_bytes: bytes`, `sha256: str` はcanonical原本由来、pack内自己hashなし。
run固有tmpを関数が所有し、他runのtmpを上書き/削除しない。
shared reference version/storage generationはadapter所有。JSON packと混同しない。

G02のbackground storeは `ClearGhiStore(directory: Path, *, fingerprint: str,
storage: Any = None, cloud_prefix: str | None = None, deadline: float | None = None)`。
localは `directory/clear_ghi/<sitehash>`、cloudは既存archive prefix下の同階層。
`read_current() -> (state | None, generation)`、`publish(calibration, records, *, expected_generation) -> None`。
成功publish/retry/restore後の `published_state_sha256: str | None` をupdater成功ログへ記録し、復旧manifest選択に使う。
`read_recovery_generation() -> int` は破損currentもrawのgenerationだけを明示読取し、不在だけ0を返す。
stateはschema1/model/basis/site/pack/pack SHA/training_as_of/clear_daysを固定し、実績・認定原本のhashを検証する。
`deadline` はmonotonic絶対時刻、未指定なら構築時+60秒。`check_deadline()` と
`remaining_timeout(*, cap=4.0)` で取得・保存処理へ同じ期限を共有する。SDK timeoutは残秒とcapの小さい方、retryなし。

## 実装手順・分岐

1. 選択済み一原本だけ読む。JSON/schema/hash/model/site/basis/terms/訓練日/有限性/時刻を検証。
   timezone比較は瞬間比較。usable_from/training_as_of≤as_of、訓練日はtarget_dateより前。
2. [data-contracts](../data-contracts.md) のcanonical bytesからhashを検証しimmutable型を作る。
   raw_bytesを再整形して別hashへ黙って置換しない。未完成tmpは原本として選ばない。
3. writeはhash名原本を新規作成→read-back/hash検証→run固有tmp→local参照replace。
   同hash再試行は同原本を返す。読返し不一致なら参照を更新しない。
   local配置は `<sha256>.json` とcanonical `current.json={"pack_sha256":"..."}`。
   loaderは選択済みhash名原本だけを読む。current参照自体を原本pathとして渡さない。
   原本はunique tmp/fsync→exclusive hard link、参照はunique tmp/fsync→replace。shared CASは別adapter。
4. 更新不正/破損なら同site/basisの直前検証済み有効原本を保持し、old usable pack使用をログへ。
   初回有効原本なしは生成失敗。旧PV・前日計画・別site原本を採用しない。
5. 非制御updaterは訓練日＋原本版で一意、訂正は集合再構築し後のtraining_as_ofを持つ。
   plan原本を新係数で書換えない。予測runは開始時選択packを固定、途中再読しない。
6. local writerは単一writerが前提。原子的replaceだけでは遅い旧updaterを防げない。
   shared adapterでは同profile/site/basisのtraining_as_of後退拒否＋参照version CAS/再読が必要。
   Cloud/GCSでlocal replaceをCASの代用にしない。adapterの結線/実証はG02/G07へ渡す。
7. G02 storeは実績・認定原本を `originals/<actual|certification>/<date>/<SHA>.json`、
   packを `calibrations/<SHA>.json`、復旧manifestを `states/<stateSHA>.json` にimmutable保存/read-back後、currentを最後にCAS更新。
   全candidate/previous stateを検証しexpected generation一致を確認した後、同日同source SHAの原本I/Oを省略する。
   非晴天への訂正でstateの晴天membershipを退役しても、過去原本と旧manifestは削除しない。
   新packで原本変更0ならpublishは9 RPC、変更source M個なら3M+9 RPC、coldstart N日なら6N+8 RPC。
   current先行読取は既存時2 RPC。各cloud RPC最大4秒に加え取得/fit/保存に共通60秒のcooperative deadlineを適用する。
   通常原本変更0の保存最悪36秒、先行読取込み44秒。SDK timeoutと処理境界で停止し、OS/DNSの強制cancelは保証しない。
   bootstrapはupdaterの1096日相当sample上限。超過は明示offline rebuildが必要、期限超過は旧有効を保持、初回は失敗。
   日照＋衛星実測供給はG01。既存7日履歴/予報shortwaveを実観測へ偽装しない。
8. plan内pack＋traceの復元とcurrent係数参照復旧を区別。
   backupのgenerated/rejected planを無条件currentへ昇格しない。site/model/usable_fromを検証する。
   current復旧は `restore_current(state_sha256, *, expected_generation, target_date, as_of)` の明示code入口だけ。
   成功ログのpublished_state_sha256（または以前に保存した検証済manifestのSHA）を選択し、hash名manifest一個を読む。
   manifest→同hash pack→各日実績/認定原本をread-back照合しsite/basis/target日/usable時刻を検証する。
   `read_recovery_generation()` でcurrent missingは0、破損時はgeneration-boundに取得した現行generationを指定する。
   local tokenは破損raw bytesのSHAを整数化した値。valid currentの別原本への置換は拒否する。
   不明hash/原本破損/未来利用/競合/期限超過は復旧失敗、listingやplanからの探索・自動昇格は行わない。
   既存破損currentの復旧はN日で4N+7 RPC、同60秒期限で停止する。実production実行は承認済canonical wrapperを別途必要とする。

## テスト・PASS条件

品質gate後に実施。本パケット作成時は未実行。
G02 store追加分は2026-10-12にmandatory品質PASS後、Ruff/mypy/py_compile PASS、
`python -m pytest -q tests/test_load_correction_equivalence.py tests/test_clear_ghi_store.py` で48 PASS
（load16/store32）。store32はcoldstart・crash・CASに加え原本再I/O0、local/cloud mockのmissing/corrupt current復旧、
manifest/pack/source/site/利用時刻/世代不正、valid current上書き拒否、共有期限の縮小timeout・中断、
晴天membership退役時の旧immutable原本保持を実行済み。実GCS・本番権限・live性能はG07の別証拠。
準備済command: `python -m pytest -q tests/test_clear_ghi_calibration_io.py`（品質gate通知待ち、まだ未実行）。

| ID / NEW test | fixture・経路 | PASS |
| --- | --- | --- |
| IO-001 `test_canonical_pack_roundtrip_and_hash` PLANNED | tmp_path完成pack、非ASCII、同hashretry | read-back同bytes/hash、二重原本生成なし |
| IO-002 `test_reject_invalid_calibration_pack` PLANNED | schema/site/basis/terms/hash/rank/非有限/未来日・aware違反 | 不正原本失敗、探索/fallback0 |
| IO-003 `test_failed_update_keeps_valid_reference` PLANNED | valid原本→破損write/read-back不一致 | 有効参照不変、old pack理由ログ、破損を成功にしない |
| IO-004 `test_cold_start_without_pack_fails` PLANNED | 原本なし/未完成tmpだけ | 新計画生成失敗、旧PV/別site探索0 |
| IO-005 `test_interrupted_tmp_is_not_current` PLANNED | 原本作成/読返し/tmp/replace各段階crash | currentは旧有効または新有効だけ、partial採用0 |
| IO-006 `test_late_updater_and_correction_order` PLANNED | local単一writerで遅い旧世代/同日訂正後世代 | training_as_of後退/同時刻異hash拒否、実shared CAS証明はG07で別途 |
| IO-007 `test_plan_restore_does_not_promote_current_pack` PLANNED | plan埋込pack・generated/rejected原本 | 再現hash一致、current参照へ暗黙昇格0 |

既存回帰command: `python -m pytest -q tests/test_forecast_history_contract.py`。
`test_failed_forecast_day_recovers_original_and_both_consumers_match` と
`test_source_locks_and_bounded_recovery_remain_present` の原本/範囲契約を維持する。
Drive実復元経路の追加検証は公開・復元stepへ渡し、ここで本番完了とはしない。

## 中断条件

G02未解決ならproduction loader/writer結線・current参照復旧は中断。
writer所有/CAS境界が不明、参照世代が後退、破損を成功扱い、承認外env/IAM/jobが必要なら停止。
原本維持は最新学習精度の保証ではない。係数鮮度を失敗ログから消さない。

## 出口・引継ぎ

S03/S04へ完成pack/type/hashと失敗結果の境界を渡す。
G02/G07には配送・updater所有・CAS/read-back・cold startの実証不足を列挙する。
計算runが一つのpackだけ固定できることが出口条件。control/publicationは変更しない。

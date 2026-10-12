# S09 dashboard asset公開と読取性能

依存: S08。関連gate: G04/G07。coverage: R07/R09/R10/R11/R14/R15、C05trace/C07cache/C09freshness/C11readperf/C12protected-vars/C14logs/C16gates。

## 入口・参照範囲

- S08の採用済みraw/hash・forecast run・target/生成時刻とprojection結果を受け取る。assetだけのgeneration CASをpayload鮮度の証明にしない。
- AIルーティング: actuals共通epoch/単一writer・実adapter/性能の判定はCodex、限定producer patchはLuna。G04/G07未完はLuna停止。
- [共通契約](../contracts.md)、[公開契約](../publication-contracts.md)、[gates](../gates.json)を読む。CodebaseMemoryは `write_dashboard_snapshots → load_dashboard_slice/_upload_artifact` とforecast/planner直接callerを経路図に使用する。
- page全体の経路: HTML shell→JS→bootstrap→precomputed GCS/local。assetありDB0、なし31日bounded。明示history操作→history asset、なし404。
- producer経路: import/plan→persist/project→write→slice repositoryの120秒cache→storage→gzip。readerのartifact cache30秒は別process。

## 変更対象

| ファイル・完全修飾symbol | 限定差分 |
| --- | --- |
| [snapshots.py](../../../../../app/dashboard/snapshots.py) `app.dashboard.snapshots.write_dashboard_snapshots` | producer fresh read、同世代payload固定・検証、local unique tmp→replace、既存結果keys保持 |
| 同上 `app.dashboard.snapshots._upload_artifact` | 固定URI object generation CAS＋承認済み内部4identity/source_epoch metadata、8KiB/24s/3試行。詳細は公開契約 |
| 同上 NEW `_read_dashboard_source_epoch/_check_source_epoch_order/_validate_publication_payload/_read_history_publication` | producer-only native source-map/保持範囲/同generation history読取/後退/原本24値SOC17検証。署名・例外は公開契約 |
| [slice_assembler.py](../../../../../app/dashboard/slice_assembler.py) `app.dashboard.slice_assembler.read_latest_pv_forecast_diagnostics` | 壊れたlocal JSON/非dictを理由付きログ。正常未配置と区別、{}互換・compact出力保持 |
| [data.py](../../../../../app/dashboard/data.py) `app.dashboard.data.clear_dashboard_cache` **既存・呼出のみ** | producerで呼びrepository cacheもclear。helper本体/TTLは変更対象外 |

新class/frontend/HTTP endpointを作らない。NEW内部helperは上表と公開契約へ限定し、`SnapshotArtifact`は既存型を使う。

## 変更禁止

| symbol・実source anchor | 変数/保持内容 |
| --- | --- |
| `snapshots._upload_artifact`: `blob.cache_control = "private, max-age=0, must-revalidate"` | `uri/blob/artifact.raw/artifact.gzip_bytes`、fixed URI/schema1/gzip/ETag/cacheを維持 |
| `snapshots.write_dashboard_snapshots`: `window_days=31,` / `history_requested = bool(update_history or full_history_rebuild)` | `update_history/full_history_rebuild/history_requested/payloads`、既定bootstrapのみ。historyを初期へ同期読取しない |
| `snapshots._cache_ttl_seconds`: `os.getenv("DASHBOARD_SNAPSHOT_CACHE_SECONDS", "30")` | `raw/_SNAPSHOT_CACHE/use_cache`、reader cacheを0/全GETclearへ変えない |
| `data.clear_dashboard_cache`: `clear_firestore_cache()` | `_FIRESTORE_SLICE_CACHE`とrepository側両方clearする既存helperを呼ぶ。新独自cache管理へ置換しない |
| `slice_assembler.read_latest_pv_forecast_diagnostics`: `os.getenv("NIGHT_CHARGE_PLAN_PATH", "artifacts/night_charge_plan.json")` | `path/data`、既存path名/{}互換保持。GETでDB追加読取/原本修復/新PV予測禁止 |
| `snapshots.write_dashboard_snapshots`: `clear_snapshot_cache()` | writerのローカルartifact cache invalidationを保持。別reader processのcacheを即clearしたとは説明しない |

[保護文書](../../../agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md)のSOC07起点/06:30判定・同vintage/GET履歴契約を保持。`static/dashboard.js::estimateHourlyForecastSoc`、`dashboard_calculations.js::forecastSocFromLatestActual`、`server._serve_snapshot`は編集対象外。

## クラス・変数差分

| 所在 | 既存/追加local | 型・制約 |
| --- | --- | --- |
| `write_dashboard_snapshots` | 既存 `payloads/kind/payload/artifact/path/results` | payload一回固定、原本PV24/load24/SOC/capacity/target同世代。raw/gzip bytes/etag結果保持 |
| 同上 | NEW local `temporary_path` | run固有Path→replace、中断時旧asset保持。共有 `.part`名禁止 |
| `_upload_artifact` | 既存 `blob/uri`、NEW local `expected_generation` | CAS書込対象object自身のgeneration。新世代別URI/manifestなし |
| 同上 | NEW local `candidate_order/existing_order` | [公開契約](../publication-contracts.md)の比較tuple、actuals鮮度binding未証明ならupload拒否。metadata keyを独断発明しない |
| `read_latest_pv_forecast_diagnostics` | 既存 `path/data`、NEW local `reason_code` | JSON破損/非dict/未配置を分け、body/token/rawをログへ出さない |

## 実装手順・分岐

1. G04の承認済みproducer-only source-mapでactuals/forecast全sourceのnative update_time/docid digestをbefore/after検証。単一writer/既存共有epochを断定せず、詳細・限界は公開契約へ従う。
2. G07でpayload原本識別・実績鮮度・metadata・有限I/O/deadlineを実adapterへ結線する。存在しないepoch/profile pointerを仮定しない。
3. producerだけ既存 `data.clear_dashboard_cache()` を呼ぶ。forecast/day/planを投影した直後にfresh sliceを一回固定し、PV/load/SOC/容量/日付を同世代検証する。
4. 診断local planとDB予測が一致しない/epoch不明/混合ならuploadしない。最後の整合assetを保ち理由/採用版/表示版を記録する。
5. GCS現generation/identity/source-mapと比較しCAS。競合時も同候補map再読、固定payload最大3試行・24秒予算。query3660+1/metadata8KiB超過や後退はoldretain。
6. localはunique tmp→replaceで中断安全。historyは同generationの旧payload/保持範囲mapを照合し、通常incremental＋進んだ境界だけrefresh。古訂正/legacy初回のみ既存bounded full rebuildへ渡す。bootstrap writerで全履歴を作らない。
7. reader 30秒遅延、producer 120秒cache、model誤差を分け測定。fulltraceは原本のみ、初期payloadは最小識別/時刻。GET予測計算/新HTTP endpoint0。

## テスト・PASS条件

code-quality-audit/必須gate後に実施。NEW試験追加済み・ローカル実行済み。G04/G07は実server競合/性能を残してOPEN。

| ID・状態 | fixture/条件 | command・厳密PASS |
| --- | --- | --- |
| DASH-01 EXISTING | bootstrap gzip/ETag、versioned static、historyなし | `python -m pytest tests/test_dashboard_server.py tests/test_dashboard_snapshots.py -q`。assetありDB0、If-None-Match304、historyなし404/全履歴read0、既定31日bootstrap、static immutable保持 |
| DASH-02 EXISTING | 同vintage SOC/歴史・旧plan reader | `python -m pytest tests/test_dashboard_forecast_soc_contract.py tests/test_dashboard_data.py tests/test_dashboard_history_contract.py -q`。SOC07起点/06:30判定・欠損保持・backend由来・同PV/load保持 |
| DASH-03 NEW LOCAL `test_dashboard_publication_generation.py` | producer cache順序、diagnostic非dict/破損、native nanos/旧cost/外側analog/history、既存reader同原本payload | `python -m pytest tests/test_dashboard_publication_generation.py -q`。clear後31日slice、理由のみログ、訂正map変化。正常24値/SOC通過、PV/load/run/target/capacity/SOC混合拒否。実server生成は未実証 |
| DASH-04 NEW LOCAL 同ファイル | CAS/source drift/delete/budget/legacyasset、通常新planのdefault history更新/古訂正/日境界/同generation history読取 | 同上。通常incremental成功、訂正/legacyのみfull rebuild、保持範囲へ新epoch偽stamp0、境界1日refresh、drift/予算超過で旧asset保持。実GCS SDK CAS request確認、server競合はINCONCLUSIVE |
| DASH-05 PARTIAL 既存＋NEW | HTTP bootstrap/history/cache、producer read計数/圧縮bytes | `python -m pytest tests/test_dashboard_server.py tests/test_dashboard_snapshots.py tests/test_dashboard_publication_generation.py -q`。assetありDB0/ETag/31日、revision小fixture9doc/回、local gzip bytes実測。browser/production転送/cache遅延は未測定 |

固定URIのCAS fakeだけではactuals freshness PASSにしない。実adapter未検証、bytes未測定はINCONCLUSIVE/未測定を明記する。

## 中断条件

- source-mapにpayload依存元が欠落、native版不明/訂正中/後退/budget超過はoldretain。実serverのsource-map/CAS競合未実証ならG04/G07 OPENで導入PASS停止。
- 新manifest/collection/env/flags、全GET無cache、画面側新PV再計算、history同期ロードが必要になった。
- mixed/古actuals upload、ETag/cache/lazy/read数回帰、producer cacheクリア漏れ、body/秘密ログ。

## 出口・引継ぎ

S10へ表示原本識別・actuals freshness根拠・race/復旧/HTTP性能測定・history訂正rebuild証拠を渡す。最小handoffは同rawのDriveオフライン復元。未完ならG04/G07理由と必要writer/metadataだけ返す。

# D01 小さなモデル要約と世代の結線

## 入口・参照範囲

[contracts C01〜C03](../contracts.md)、[data-path-audit](../data-path-audit.md)、inventory R02/R03/R18。
既存snapshot producerのpublished_planと採用済み原本、既存payloadだけを使用する。

## 変更対象

| path / symbol | 限定差分 |
| --- | --- |
| app/dashboard/slice_assembler.py::extract_pv_forecast_diagnostics | 既存戻り値keysを保持し、keyword-only producer_identity:dict[str,str]\|None=None / forecast_run_id:str\|None=Noneを末尾追加。pv_model keyだけ付加 |
| 同file NEW _build_pv_model_summary(data:dict[str,Any],*,producer_identity:dict[str,str]\|None=None,forecast_run_id:str\|None=None)->dict[str,Any] | C02の単一allowlist/型/shape/reasonを標準ライブラリだけで生成 |
| app/dashboard/snapshots.py::write_dashboard_snapshots | published_plan有のbootstrapだけ既存_validate_publication_payload成功後、固定rawから上記extractorへidentity/runを渡してdiagnosticsを上書き。新読取0 |

新class0。既存DashboardSlice/RawData/SnapshotArtifact型はそのまま使う。

## 変更禁止

write_dashboard_snapshotsの署名、published_plan/source_epoch/source_client、履歴incremental/full-rebuild、native epoch/CAS/budget/cache clearを変えない。
_validate_publication_payload、_read_dashboard_source_epoch、_upload_artifact、server._serve_snapshotを変更しない。
published_plan.raw_jsonの再serializeから新raw hashを作らない。full trace/pack storage再読/NumPy import/物理再計算は禁止。
元のextractorのrationale優先/daytime_soc_optimization fallback、plan_dateと旧4dictを保持する。

## クラス・変数差分

NEW helper localsはtrace/pack/summary/model_id/status/reason_code/training_dates/theta/totals。
producer追加localsはoriginal=json.loads(published_plan.raw_json)、producer_identity既存4keys、forecast_run_id。
runはpayloadの当日24rowのunique nonempty forecast_run_idから取得する。issued_atや更新時刻をrunの代用にしない。
外部契約は既存pv_forecast_diagnostics内のoptional pv_modelだけ。全dates・hourly trace・座標・site keyはコピーしない。

## 実装手順・分岐

1. 既存producerの原本/24値/17SOC検証を先に成功させる。失敗候補からvalid要約を作らない。
2. モデルsource/legacy集合/key pathはC02の列挙だけを使い、原本result.final_pv_forecast_source優先とphysical.selected_method/trace.model_idの矛盾を拒否する。未知をlegacyへ分類しない。
3. NEW+identity無/run無はunsupported、NEW+trace無/metadata矛盾はinvalid。既知旧式はlegacy。status別shape/reasonはC02に固定。
4. validでは学習日unique sorted/対象日前、samples==13*N、terms固定/finite signed theta4、C02のUTC時刻順序とkey path、sha形式、rank/condition有限、repaired_hours型を確認する。再fitしない。
5. generated_at/plan_dateはPublishedPlanと原本に一致、raw_plan_sha256/control_version_idは既存identityからcopy。trace.target_date/model/basis/packは矛盾を拒否。
6. JSON UTF-8 compact 4KiB超・invalidはcommon-only summaryへ縮退。common最大長/形式を先に確認し、超過値はNone、fallback自体も4KiB再検査。truncate identityは禁止。producerで有限reason/正規化済model/targetだけlog。正常PV/load/実績を消さない。
7. GET既定callerは新keywordを渡さない。追加DB/原本storeアクセス0。NEW unsupported時に旧PV説明へ戻さない。

## テスト・PASS条件

PLANNED `tests/test_dashboard_new100_diagnostics.py`: valid17日/221区間、全legacy集合、unknown巨大ID、trace欠落、producer無し、run無し、bool/nonfinite、対象日混入、重複日、13*N不一致、全時刻順序等号/逆転/offset同instant、retrievedがcreatedより前/後双方、有効0/signedtheta、common-only超過防止。
PASSは有限shapeと理由、旧keys保持、元データ不変、同原本retry同summary、None≠0、NumPy/観測/fit/store/追加DBcall0。
既存publication testsへproducer overrideの同日別run拒否とpayload size差計測を追加。raw identityはproducerだけの保証と明記。

## 中断条件

原本identityを別のhashで生成する必要、新DB/HTTP/env/依存、既存SOC/CAS/epochを改変する必要が出たら停止して仕様差分を報告する。

## 出口・引継ぎ

D02へC02 shapeのfixtureと日本語reason mapを渡す。D03へ当日public run binding、D05へ追加bytes/read0の証拠を渡す。

# R08 復元後SOC取得の共有期限付きretryと拒否診断

対象は実機往復後のread-only SOC取得と、API拒否時の秘密を含まない診断だけ。新production class・flag・env key・依存は0。物理操作、03制御、計画、経済式を変更しない。

## 入口・参照範囲

- 今回のlive probeは2回ともforced/greenのSET/readback/60秒保持と `restore_verified=true` を確認した。その直後の `scripts.kpnet_soc_api_probe.run_probe` → `latest_realtime_soc_reading` が `SocApiError: refresh was not accepted` で失敗し、約1分後のread-only取得は成功した。元endCdは未記録であり、provider側の一時状態・rate制約等の原因は未確定。
- 参照は [SOC probe](../../../../../../scripts/kpnet_soc_api_probe.py)::run_probe、[SOC API](../../../../../../app/kpnet/soc_api.py)::KpNetSocClient.read_soc、既存 [retry](../../../../../../app/runtime/soc_reading.py)::read_realtime_soc_with_retry、[R05](R05-release-acceptance.md)、[R06](R06-post-deploy-soc-probe.md)。retry helperはCALL ONLYで本stageから変更しない。
- baselineは `27755f5e8838f8486570b022c1223c354a4b017c`。編集前raw SHAはprobe=`cc6f63e1b212b3c83d15724f7fe5e6826583b0ed94a95e3f975fc47ff3676664`、API=`a64ef10cc2ea46f2b258f74ea949d7944dc54e78a005b5264bbe729c16166b1b`。実装hash・試験selectorはsource freezeで照合する。

## 変更対象

| 既存symbol | 限定変更 |
| --- | --- |
| `scripts.kpnet_soc_api_probe.run_probe` | 1操作のmonotonic絶対期限を作り、既存reader retryへ既存取得callableを渡す。新CLI引数・consumer・caller位置は追加しない |
| `app.kpnet.soc_api.KpNetSocClient.read_soc` | refresh拒否枝へbest-effort JSON診断を追加する。既存例外・成功応答・HTTP要求は保持する |

R05内のrun_probe呼出位置と前後順序は変更0。実機往復・復元が完了した後のreadだけを再試行し、SETや往復試験をretryしない。standalone SOC readに物理操作を追加しない。R06のdeployment complete/03 terminal/時刻条件も維持する。

## 変更禁止

- 03/23/07 control、`read_realtime_soc_with_retry` 本体、SOC API endpoint/認証/request body、SET・候補mode/SOC・60秒保持・復元・時刻所有権を変更しない。
- 古い・未来・不正日時のreadingや不正SOCを成功へ昇格しない。失敗を0%や最後の古い値へ置き換えない。
- 新env key/feature flag/class/logger/API/dependency、SDK retry増設、raw response・token・ID・message・localsのログを追加しない。物理復元未確認・running/unknownを新SETの許可へ変換しない。
- 未採用9/20校正pending例外はR03の承認条件のまま。R08でactual/pack/trace・金融・assetの条件を緩和しない。

## クラス・変数差分

既存 `KpNetSocClient` と取得済み `SocReading` を再利用し、新classは0。run_probeの追加localは `deadline` のみで、`time.monotonic()+SOC_OPERATION_MAX_SECONDS` を一度計算する（現契約60秒）。既存 `read_realtime_soc_with_retry` は失敗時にも `SocReading(value_percent=None,source='unavailable',error=...)` を返し、PythonのNoneそのものは返さない。run_probeの既存SOC/測定timestamp/取得timestampの3項目検査から従来のRuntimeError/非0でFAILとする。

callは `latest_realtime=lambda: latest_realtime_soc_reading(deadline_monotonic=deadline)`、`env_int=lambda _name,default:default`、`env_float=lambda _name,default:default`、`deadline_monotonic=deadline`。既存default最大3回/待機300秒を利用するが、待機は既存fair-share処理で同じ60秒以内へ制限される。試行ごとの期限再生成やenv読取経路を追加しない。
取得試行数は従来1回から最大3回へ増える。各試行の既存SOC HTTP sequenceは不変で、試行数をHTTP request数と同一視しない。期限不足では3回未満で停止することも正常であり、成功や全3回実行を保証しない。

read_soc拒否枝の追加localは `code` / `safe_code`。`safe_code` はstr・長さ5・ASCII・decimalを全て満たす場合だけ元codeを使い、他はliteral `unrecognized`。追加JSONは `message="kpnet-soc-api-rejected"` と `end_code` だけ。print失敗をbest-effortで無視し、その後の既存SocApiErrorを保持する。

## 実装手順・分岐

1. 1操作deadlineを生成し、既存SOC取得へ同じdeadlineを渡す。既存helperのdefault3回とfair-share待機を使い、全体60秒を試行ごとにリセットしない。
2. fresh readingなら従来の結果へ進む。期限・全試行失敗・不正/古い/未来日時はFAIL。現在の取得要件を最終成功だけ緩くしない。
3. refresh拒否だけsafe codeをJSON化する。予期しないcode型/Unicode数字/長さ違いは `unrecognized`、log障害でも元例外を発生させる。
4. 既存 `03-soc-read-attempt` event名は保持する。probe Job/呼出位置から今回のread-only試行と03監視を区別し、このeventだけで03動作や充電成功を宣言しない。
5. mandatory品質後に直接回帰を実行し、正式wrapperで同commit/imageのlive probeを検証する。read-only retryの成功だけでSET/readback/restore、全deployment成功を代替しない。

## テスト・PASS条件

対象はNEW [tests/test_soc_api_probe.py](../../../../../../tests/test_soc_api_probe.py)、既存 [tests/test_soc_api.py](../../../../../../tests/test_soc_api.py)、[tests/test_separated_runtime.py](../../../../../../tests/test_separated_runtime.py)。local fixtureと本番実証を分けて記録する。

| 実在selector | fixture/PASS条件 |
| --- | --- |
| `tests/test_soc_api_probe.py::test_probe_recovers_with_one_shared_deadline` | transient 0/1/2。同deadlineでfresh成功、最大3試行、待機は共有60秒内 |
| `tests/test_soc_api_probe.py::test_probe_exhaustion_remains_failure` | 3回拒否又は初回が60秒消費。非0/既存RuntimeError、余分な期限reset/取得0 |
| `tests/test_soc_api_probe.py::test_probe_incomplete_measurement_is_not_accepted` | SOC・測定timestamp・取得timestamp欠落を成功へ昇格しない |
| `tests/test_soc_api.py::test_rejected_refresh_logs_only_safe_provider_code` | decimal/private/int/fullwidth等5cases。追加JSONは2keys、5桁ASCII decimal以外はunrecognized、秘密非出力 |
| `tests/test_soc_api.py::test_rejected_refresh_log_failure_does_not_change_rejection` | log障害でも元SocApiErrorと拒否を保持 |
| `tests/test_separated_runtime.py::test_post_restore_soc_retry_does_not_repeat_device_mutation` | actual control.main/probe/sharedretryをlocal fixtureで接続。before1 read＋after3 reads、模擬40秒、共有deadline60、物理roundtrip一回。実device/Cloud合格へ昇格しない |

- 一回拒否後のfresh成功、全3回失敗、期限不足、古い/未来readingを独立fixtureで確認する。同deadlineが全callへ渡り、default300秒待機が共有60秒を超えず、試行数3以下・期限リセット0・全失敗非0。
- 正常5桁ASCII endCdと不正型/Unicode/長さをparameterizeし、追加JSONは2keysだけ、raw response・token・ID・秘密が出ない。log失敗でも元SocApiErrorのtype/内容と拒否動作を保持する。
- 従来のAPI成功pathとfreshness検査、物理往復/復元/時間帯保護の関連回帰を維持する。local fake合格を本番provider復旧・SET成功・原因確定へ昇格しない。

mandatory品質後の候補commandは `python -m pytest -q tests/test_soc_api_probe.py tests/test_soc_api.py tests/test_separated_runtime.py`。実行結果は親の正規gateで確認し、この文書登録時点のruntime完了を主張しない。

## 中断条件

全取得失敗・共有期限超過・freshness不足・秘密漏洩・新endpoint/異なるHTTP操作/物理操作・既存例外/保護条件の変更はFAIL/停止。read-only取得失敗後は物理復元証拠を保全し、同HEADのSET/D/buildをblind rerunしない。endCdを取得してもprovider原因はそのcodeと根拠を別評価し、観測だけで原因確定へ昇格しない。

## 出口・引継ぎ

限定2symbol/変数・新test参照・normalized source hashをfreezeして親へ渡す。R08はR00品質を前提としたread-only補助契約で、R05.completeを前提にせずcontrol内での循環を避ける。R05での受入は従来の物理往復/復元後SOC取得まで全て確認してから成功とし、他stage/最終deploymentの未完を残す。

# 2026-08-31 朝のSOCギャップ調査報告

## 結論

2026-08-31 07:00 JST のSOCは86%だった。2026-08-31の有効目標と停止理由は確認できていないため、目標未達を確定することはできない。ただし、目標が100%だった場合の差は14ポイントである。

最も整合する仮説は、03時制御の強制充電が05:38頃までに終了した、または継続条件を失ったことである（確度: 中）。05:30の充電実績は0.486 kWhで、その後の06:00と06:30は0 kWhであり、読み取り専用で確認した03時Cloud Run実行も05:38:54 JSTに完了している。一方、現行ソースの通常の強制監視カットオフは06:45 JSTである。停止の正確な理由は証跡からは判定できない。

## 調査範囲と固定条件

- 対象日: 2026-08-31（時刻はJST、Cloud Run生成・開始・完了時刻はUTCとJSTを併記）。
- Git基準: `master` / `1042b8e`。
- 調査は原因推定のみ。コード、設定、本番データ、実機、デプロイは変更していない。
- テストは実行していない。調査成果物の作成のみであり、挙動変更はないためである。
- 時系列の派生証跡: [soc_gap_evidence_2026-08-31.csv](./soc_gap_evidence_2026-08-31.csv)。
- ログの機微情報を除いた要約: [soc_gap_log_summary_2026-08-31.md](./soc_gap_log_summary_2026-08-31.md)。

## 確認済みの事実と数値

### KP-NET実績CSV

最新の公式取込は07:46 JSTに成功した。使用したコマンドは `pwsh -NoProfile -File scripts/run_kpnet_import_from_env.ps1 -SkipFirestoreIngest` であり、Firestoreへは投入せず、ローカルCSVを読み取り対象として扱った。実行成果物は `artifacts/20260831-074620` である。

SOCは00:00の0%から05:30に93%まで上昇した後、06:30まで93%で横ばいとなり、07:00には86%へ低下した。03:00〜07:00の充電量合計は8.557 kWhである。05:30の充電量は0.486 kWh、06:00と06:30は0 kWh、07:00は放電0.698 kWhだった。

|時刻|SOC|充電量|補足|
|---|---:|---:|---|
|00:00|0%|—|開始時点|
|03:00|14%|—|充電上昇中|
|04:00|50%|—|充電上昇中|
|05:00|85%|—|充電上昇中|
|05:30|93%|0.486 kWh|最後に確認できた充電量|
|06:00|93%|0 kWh|充電実績なし|
|06:30|93%|0 kWh|充電実績なし|
|07:00|86%|0 kWh、放電0.698 kWh|PV 0.088 kWh、負荷0.925 kWh、買電0.139 kWh|

07:00のエネルギーバランスは整合する。`PV 0.088 + 放電 0.698 + 買電 0.139 = 負荷 0.925 kWh` であり、SOC低下をデータ矛盾としては扱わない。

### Cloud Run 03時実行とログ

読み取り専用で確認した03時実行は、UTCで2026-08-30 18:00:01に作成、18:00:17に開始、20:38:54に完了した（JSTでは2026-08-31 03:00:01、03:00:17、05:38:54）。成功数は1で、`Completed=True`、`ResourcesAvailable=True`、`Started=True`、`ContainerReady=True`、コンテナdigestの存在を確認した。

実行に紐づくログは127件で、WARNING/ERROR severityは0件だった。CSVダウンロード、ローカル計画成果物生成、約3分周期のKP-NETログイン/ログアウトを確認した。フィルタ済みtext payloadには直接制御を表すキーワードのメッセージはなかった。ただし、text payloadにないことは「制御がなかった」ことの証明ではない。

### Firestoreと現行03経路

読み取り専用のFirestore確認では、`night_charge_plans`、`night_soc_execution`、`battery_daily_metrics`はいずれも2026-08-29までで、2026-08-30付の計画はなかった。これは単独では失敗を意味しない。現行03経路はFirestoreへのfallback/persistenceを意図的に持たず、ローカルで計画を再生成する設計である。

現行ソースの確認箇所は以下である。

- [`app/runtime/cloud_job.py`](../../../app/runtime/cloud_job.py): 03時はローカル計画を再生成し、強制充電を監視して時間フェンスまで実行する。目標到達または監視停止時にはstandbyへ遷移する。
- [`app/runtime/slot_orchestration.py`](../../../app/runtime/slot_orchestration.py): 03:00スロットはstandaloneで、03経路にFirestoreや他スロットの依存を追加しない契約である。
- [`docs/current/ops/SOC_GAP_ROOT_CAUSE_EVIDENCE_20260831_JA.md`](../../current/ops/SOC_GAP_ROOT_CAUSE_EVIDENCE_20260831_JA.md): 通常の強制監視カットオフは06:45、final standby開始は06:50、03時I/Oのハードカットオフは06:55 JSTと定義される。

既存の2026-08-29計画・実績には、target=100%、`required_night_charge=0.601 kWh`、実行の`latest_soc=0`、`readback_match=true`がある。ただし、これは今回朝の計画ではない。2026-08-31のtargetと停止理由は未確認である。

## 原因推定（仮説）

1. **03時制御の強制充電が05:38頃までに終了、または継続条件を失った**（確度: 中、最上位）
   - 05:30、06:00、06:30の充電実績が順に0.486、0、0 kWhであり、実行完了は05:38:54 JSTだった。通常の監視カットオフ06:45より約66分早い。
   - 充電量ゼロと早期完了は整合するが、ログの要約だけでは目標到達、SOC読出し、例外捕捉後のstandby、または他の終了分岐のどれかは特定できない。

2. **有効目標または停止判定が93〜94%程度だった**（確度: 中低）
   - 05:30から06:30までSOCが93%で横ばいであることとは両立する。
   - 当日targetと停止理由が未確認のため、事実としては扱わない。

3. **KP-NETの強制モードまたはread-backの不一致**（確度: 低中）
   - 強制充電の継続に影響し得るが、今回のフィルタ済みログに直接の現行証拠はない。

4. **機器側の充電受入れ・制限・物理的要因**（確度: 低）
   - 05:30以降に充電実績がなく93%で横ばいであることとは両立するが、機器状態や診断値を確認していないため未検証である。

## 現在の証拠と履歴の分離

2026-08-30の履歴では、計画SOC 94%に対して03時実績SOC 0%、必要充電量0.601 kWhに対して03時時点の必要量9.864 kWh、target=100%、07:30 SOC=73%が記録されている。これは旧時点の計画・実機SOCの不整合を示す歴史的証拠である。

ただし、旧split-brain/read-back不一致のログは1日ずれており、2026-08-30の直接証拠ではない。さらに現行03経路は旧split-brainをそのまま使用しない。したがって、この履歴を2026-08-31の直接原因としては用いない。

## 限界と未確定事項

- CSVは30分粒度で、停止時刻・停止分岐を分単位で確定できない。
- 当日の計画target、実行時のSOC読出し、停止理由、最終設定read-backは保存済みFirestoreから確認できない。
- Cloud Runログは機微情報と無制限の転記を避けた要約であり、payload欠落は不実行の証拠ではない。
- 実行が成功状態であることはコンテナ処理の成功を示すが、目標SOC到達の証明ではない。

## 実施コマンド・確認パス

- `pwsh -NoProfile -File scripts/run_kpnet_import_from_env.ps1 -SkipFirestoreIngest`
- [scripts/run_kpnet_import_from_env.ps1](../../../scripts/run_kpnet_import_from_env.ps1)
- [app/runtime/cloud_job.py](../../../app/runtime/cloud_job.py)
- [app/runtime/slot_orchestration.py](../../../app/runtime/slot_orchestration.py)
- [docs/current/ops/SOC_GAP_ROOT_CAUSE_EVIDENCE_20260831_JA.md](../../current/ops/SOC_GAP_ROOT_CAUSE_EVIDENCE_20260831_JA.md)

今回のCodebaseMemory MCPは`transport closed`のため利用不能だった。グラフ結果は使わず、上記の現行ソースを直接確認した。固定証拠ドキュメント内にある過去の`ready`確認は、今回の確認結果として扱わない。

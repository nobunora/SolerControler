# 夜間SOC単一所有者制御 実装詳細仕様書

## 1. 文書情報

| 項目 | 内容 |
| --- | --- |
| 状態 | 実装済み・本番反映済み（2026-08-21） |
| 対象 | 03:00〜07:00 の計画SOC達成、強制充電、停止、監査 |
| 非対象 | PV点予測の変更、物理PVへの通常EWMA適用、売電単価・売電ペナルティの有効化 |
| 関連ADR | `adr/0002-night-slot-orchestration.md`、`adr/0003-energy-plan-boundaries.md` |

本仕様の目的は、計画した翌朝SOCを安全に実現できる制御へ改め、設定競合・丸め・観測不足により「設定要求は成功したが翌朝SOCがずれる」状態を検出可能かつ再発しにくい状態にすることである。

## 2. 事実に基づく問題定義

### 2.1 観測された乖離

2026-08-01〜11 のローカル実績では、計画SOCが100%未満だった次の4日はすべて翌朝SOCが計画を超過した。

| 計画日 | 計画SOC | 翌朝SOC | 誤差 |
| --- | ---: | ---: | ---: |
| 2026-08-04 | 71% | 80% | +9pt |
| 2026-08-07 | 72% | 91% | +19pt |
| 2026-08-09 | 71% | 89% | +18pt |
| 2026-08-10 | 31% | 43% | +12pt |

この4日の平均超過は **+14.5pt** である。全11日の翌朝SOC誤差MAEは **6.27pt** だが、100%計画日の未達が平均を相殺するため、低目標時の超過リスクを表す指標としては不十分である。

### 2.2 現行経路と競合可能性

夜間には、同じSOC関連フィールドを含む設定を書き込む経路が複数ある。

| 時刻・契機 | 実行経路 | SOC関連設定 |
| --- | --- | --- |
| 23:00 | standbyプロファイル | `socChargeMode`、SOC各モード、充放電時刻 |
| 03:00 | forced適用、停滞時再適用 | 同上 |
| 03:00〜07:00 | 目標到達・cutoff・異常時のstandby | 同上 |
| 07:00 | greenプロファイル | `socChargeMode`、SOC各モード、充放電時刻 |

実績イベントには、03:00の`forced-monitor`設定の約70〜107秒後に`night-green`設定が続く例がある。これは競合の**可能性を示す事実**であり、機器read-backがない現状では、どちらが最終的に機器で有効だったかは確定できない。

### 2.3 現行仕様上の不整合

1. `target_soc_7_percent`は連続値の計画目標だが、KP-NETの`SocChargeMode`は候補値から計画値以上の最小コードを選ぶ離散値である。両者を同一値として扱ってはならない。
2. 必要充電量には「計画由来kWh」と「実測SOC差分」の二系統があり、矛盾時の優先順位が明文化されていない。
3. 03:00ジョブは計画再生成を許容しているため、充電開始後に参照計画が変わると、設定・監視・監査の対象が分裂し得る。
4. 03:00 cutoffと07:00ジョブの開始時刻は同一である。排他制御なしでは遅延・再試行による上書きを排除できない。
5. KP-NETのデバイス時刻設定は同日内に制限される。論理的な夜間窓（23:00〜07:00）をそのまま跨日設定として送ってはならない。
6. 現在の売電契約設定は中立（`SOC_EXPORT_CONTRACT_STATUS=inactive`、売電ペナルティ未設定）である。SOC実行精度の修正と、売電ペナルティの有効化を同一変更に混ぜてはならない。

## 3. 設計原則

### 3.1 指令値と安全ガードを分離する

| 用語 | 定義 | 用途 |
| --- | --- | --- |
| `raw_target_soc_percent` | 計画が要求する連続値の目的SOC | 監視停止・翌朝合否の基準 |
| `effective_target_soc_percent` | 最低SOC制約を適用後の目的SOC | 実行時の目的SOC |
| `device_soc_code` | KP-NET候補値から選んだコード | 機器側の上限ガード |
| `device_soc_ceiling_percent` | コードが表す実効上限SOC | read-back照合・安全性判定 |
| `charge_duration_seconds` | 実測SOC差分等から算出した開始時の最大充電時間 | タイムアウトガード。停止の主判定ではない |

`device_soc_code`は、`effective_target_soc_percent`を下回らない最小候補を選ぶ。これは不足充電を避ける**上限ガード**であり、目的SOCそのものではない。

監視処理は `effective_target_soc_percent - stop_margin_percent` に到達した時点で待機設定へ遷移する。したがって、例として目的71%、候補80%の場合、80%まで充電し続けるのではなく、70%（margin 1ptのとき）から停止判定を開始する。停止要求後に待機設定のread-backが成功した場合のみ停止完了とする。

`device_soc_ceiling_percent`が目的SOCより低い候補しか得られない、または候補コードと実効上限の対応を確認できない場合は、強制充電を開始してはならない。安全側の終了プロファイルへ遷移し、`INCONCLUSIVE`または`FAIL`として通知する。

### 3.2 単一所有者

計画日ごとに、03:00〜07:00のSOC制御フィールドの書込み所有者を `NightSocController` 一つに限定する。

所有対象フィールドは以下とする。

```text
batteryOperatingMode
socSafetyMode
socEconomyMode
socContactInput
socChargeMode
chargeStartTimeH / chargeStartTimeM / chargeEndTimeH / chargeEndTimeM
dischargeStartTimeH / dischargeStartTimeM / dischargeEndTimeH / dischargeEndTimeM
```

23:00は夜間に備えた非SOC設定のみを許可するか、夜間SOC制御フィールドを含む既存プロファイルを送らない。07:00は、03:00実行の排他リースが終了し、かつ終了read-backが記録済みの場合にのみgreenへ遷移できる。例外的な安全停止も `NightSocController` を経由して行う。

蓄電池をユーザーが手動操作する運用では `NIGHT_SOC_MANUAL_OPERATION=true` を指定する。この場合、23:00と03:00は蓄電池設定を書き込まず、03:00処理は `MANUAL_OPERATION` の引継ぎ状態だけを記録する。07:00のgreen設定は引き続き自動書込みの対象だが、KP-NETの設定後read-backが一致しない場合は成功扱いにせず失敗する。

#### 2026-08-28 SOC 0% インシデントの再発防止

2026-08-28朝、計画SOCが100%だったにもかかわらず、実績SOCが0%、充電量も0kWhとなった。`NIGHT_RESERVE_SOC_PERCENT=0` は経済計算上の予備率を表す設定であり、実行時に最低限確保すべきSOCではない。この値を0%のまま維持すると、計画生成または設定書込みが欠落した場合に0%が実効目標として通過し得る。

本番デプロイの03:00ジョブでは、実行時の最低目標SOCを `ADJUST03_MIN_TARGET_SOC_PERCENT=30` とする。これは計画値を30%へ固定するものではなく、計画値が低すぎる場合の実行安全床である。手動運用を行う場合は `NIGHT_SOC_MANUAL_OPERATION=true` を明示的に指定し、暗黙に自動運用を無効化してはならない。

### 3.3 計画を実行開始前に凍結する

03:00のCSV取込・計画再生成は、強制充電の最初の書込み前に完了しなければならない。実行に使う計画は次を持つ不変スナップショットとする。

```text
plan_id                 = JSTのplan_date + revision + content_hash
plan_revision
plan_hash
generated_at_utc
raw_target_soc_percent
effective_target_soc_percent
required_night_charge_kwh
capacity_kwh
charge_efficiency
source_data_freshness_seconds
```

強制充電を開始した後、同一`plan_id`の目標・必要kWh・候補コードを再読込みして変更してはならない。再計画が必要な場合は、旧実行を安全停止して終了記録を残した後に、別revisionとして新規実行する。暗黙の上書きは禁止する。

### 3.4 設定成功とSOC達成成功を分離する

KP-NETへのPOST成功、確認画面成功、設定read-back一致、停止read-back一致、翌朝SOC合格は別の状態である。前段の成功だけで`PASS`としてはならない。

## 4. 実行仕様

### 4.1 状態遷移

```text
PLAN_FROZEN
  -> LEASE_ACQUIRED
  -> SETTINGS_APPLIED
  -> SETTINGS_ACKED
  -> CHARGING
  -> TARGET_REACHED
  -> STANDBY_REQUESTED
  -> STANDBY_ACKED
  -> VERIFIED

各状態から、stale SOC / read-back不一致 / 競合 / cutoff / 通信失敗
  -> SAFE_TERMINATED
  -> FAIL または INCONCLUSIVE
```

- `SETTINGS_ACKED`は、書込み後の機器read-backで、所有対象フィールドが要求意図と一致した場合のみ遷移する。
- `TARGET_REACHED`は、新鮮で妥当なSOC観測値が停止閾値以上の場合のみ遷移する。
- `STANDBY_ACKED`は、待機指令のread-back一致を確認した場合のみ遷移する。
- `VERIFIED`は07:00の評価SOCが取得できた場合のみ遷移する。観測不能は成功ではない。

### 4.2 優先順位と計算

制御判断の優先順位は次の通りとする。

```text
安全制約・機器制約
  > effective_target_soc_percent
  > 最新の有効SOCとの差分
  > 必要kWh
  > 推定充電時間
```

1. `effective_target_soc_percent = max(raw_target_soc_percent, min_target_soc_percent)` とする。
2. 最新SOCが有効なら、`required_charge_percent = max(0, effective_target - latest_soc)` とする。
3. `required_night_charge_kwh`は時間算出・異常検知に使う。SOC目標を超えるための根拠には使わない。
4. `charge_duration_seconds`は、原則として実測SOC差分と検証済みの充電率から算出する。率が不十分ならkWh推定へフォールバックするが、フォールバック理由を記録する。
5. 最大時間に達しても目的SOCに未達なら、待機へ遷移し`FAIL`とする。再適用は所有者内で一回まで許容してよいが、毎回新しいread-backが必須である。
6. 停止判定は時間ではなくSOCを主とする。SOCが停止閾値へ達したら、残時間の有無にかかわらず待機へ遷移する。

### 4.3 SOC観測の品質ゲート

SOC値は次のすべてを満たすときのみ制御に使用できる。

- 値が0〜100%の範囲内である。
- 観測時刻が現在から `max_soc_age_seconds` 以下である。初期値はポーリング間隔180秒の2倍、360秒とする。
- 値と時刻を取得したデータ源を保存できる。
- 0%または前回値から不自然に大きく変化した値は、連続2観測または電力量整合で確認する。

SOC不明の強制開始は本番で禁止する。既存の許容フラグを残す場合も、検証環境専用とし、`SOC_UNKNOWN`実行は翌朝成功判定の母集団から除外する。

### 4.4 リースと競合検知

`night_soc_execution`に`plan_id`をキーとする排他リースを持たせる。リースには所有者、取得時刻、失効時刻、更新番号を含める。

- 同一`plan_date`で有効なリースは1件のみ。
- 書込み直前とread-back直後にリース所有者を照合する。
- 予定外の設定イベント、またはread-back値が所有者の直近意図と異なる場合は`WRITE_CONFLICT`とする。
- `WRITE_CONFLICT`時は追加充電を中止し、安全側の待機設定を一度だけ試行する。待機設定を確認できない場合は`FAIL`として運用アラートを発報する。
- 07:00ジョブは、リースが有効ならgreen書込みを待機または明示的に引き継ぐ。タイムアウトで無条件に書き込んではならない。

### 4.5 read-back契約

各設定書込みについて、少なくとも次を同じ相関IDで保存する。

```text
requested_fields
requested_at_utc
kpnet_request_result
device_readback_fields
device_readback_at_utc
readback_match
readback_mismatch_fields
writer_name
lease_id
plan_id
```

比較はコード値として厳密に行う。コードとSOC百分率を比較してはならない。コード対実効上限の対応表は、KP-NETの取得済み候補値と機器read-backで検証済みのマップを唯一のソースとする。未検証の仮定値（例: 50/80/100%）をコードの意味として実装に埋め込まない。

## 5. データモデルと監査結果

既存の計画保存・設定イベント・日次実績を置換せず、`plan_id`で相関可能に拡張する。保存先がFirestoreとSQLiteに分かれる場合でも、両方に同じ`plan_id`と`correlation_id`を持たせる。

| レコード | 必須項目 |
| --- | --- |
| `night_soc_execution` | plan snapshot、lease、状態、開始・終了時刻、要求値、read-back、異常理由 |
| `night_soc_observation` | plan_id、観測時刻、SOC、鮮度、データ源、品質判定 |
| `night_soc_audit` | plan_id、07:00 SOC、誤差、競合数、read-back成否、`PASS/FAIL/INCONCLUSIVE`、理由配列 |

監査結果の意味は以下で固定する。

| 結果 | 条件 |
| --- | --- |
| `PASS` | read-back一致、競合なし、停止確認済み、07:00の有効SOCが許容誤差内 |
| `FAIL` | 過充電・未達、競合、read-back不一致、停止不能、または安全停止失敗 |
| `INCONCLUSIVE` | 必須観測不足、SOC品質不良、相関不能、または検証不能な機器コード |

初期の暫定受入範囲は、停止時・06:30・07:00の各SOCが目的SOCの **-3pt〜+2pt** とする。ただし、有効な観測がない場合は範囲内とみなさない。14日以上の実運用データで分布を確認し、機器の分解能と計測誤差に基づき再校正する。

## 6. 変更対象と責務

| 対象 | 変更責務 |
| --- | --- |
| `app/runtime/cloud_job.py` | 03:00の開始・監視・停止を新コントローラへ委譲し、直接の多重プロファイル適用を除去する |
| 新規 `app/runtime/night_soc_controller.py` | 計画凍結、リース、状態遷移、SOC品質、停止、監査を一元化する |
| `app/forced_charge/state_machine.py` | 状態機械をコントローラの状態・失敗理由と整合させる |
| `app/kpnet/profile_builder.py` | raw目標、候補コード、実効上限、時間算出根拠を別フィールドで返す。丸め値を目的SOCとして返さない |
| `app/kpnet/workflow.py` | 設定intent・read-back・相関IDを返し、所有者照合を通さない書込みを禁止する |
| `app/runtime/plan_persistence.py` | 不変plan snapshot、実行・観測・監査記録を保存する |
| `app/runtime/slot_orchestration.py` | 23:00/07:00をリース認識型に変更し、SOCフィールドの直接書込みを防ぐ |
| `scripts/kpnet_soc_gap_report.py` | 表示だけでなく`PASS/FAIL/INCONCLUSIVE`と失敗理由を出力する |
| `scripts/deploy_gcp_jobs.ps1` | 提案フラグを明示してジョブ間の切替責務を設定する |

物理PVの通常EWMAスキップは本変更では維持する。物理PVの実績差分は、将来別フェーズで「物理PV専用のリスク倍率」としてSOC安全余裕にだけ作用させ、点予測そのものを通常EWMAで補正しない。

## 7. 段階導入

| 段階 | モード | 実施内容 | 進行条件 |
| --- | --- | --- | --- |
| 0 | observe | 既存制御を変えず、plan_id・writer・read-back・07:00監査を収集 | 連続14日、相関欠落なし |
| 1 | shadow | 新コントローラが意思決定だけ行い、既存指令との差分を記録 | 差分を全件説明可能 |
| 2 | enforce-canary | 単一所有者を限定期間で有効化。greenはリース引継ぎのみ | read-back 100%、競合0件 |
| 3 | enforce | 全対象日に適用 | 14日で`PASS`率・SOC誤差が受入基準を満たす |

提案する設定値は `NIGHT_SOC_CONTROL_MODE=observe|shadow|enforce` と `NIGHT_SOC_READBACK_REQUIRED=true` である。既存の環境変数や外部契約を変更するため、実装時に正式な設定名とデプロイ手順をレビューで確定する。

ロールバックは、既存プロファイルへの無条件復帰ではなく、最後に確認できた安全な待機状態を保持する方針とする。機器状態を確認できない場合の電池安全・非常時運用は、運用責任者が承認したフェイルセーフ設定を別途明文化する必要がある。

## 8. 受入試験

### 8.1 単体試験

- 71%などのraw目標と離散候補コードを混同せず、候補コードが目的以上の最小値になること。
- 停止はraw/effective目標で行い、候補コード到達を待たないこと。
- 最低SOC、SOC差分、必要kWh、時間の優先順位が本仕様どおりであること。
- stale・欠損・0%・急変SOCを安全に扱うこと。
- 計画凍結後の再読込み、二重リース、予定外writerを拒否すること。
- cutoff、通信失敗、read-back不一致、停止失敗がそれぞれ`FAIL`または`INCONCLUSIVE`になること。

### 8.2 結合試験

- 擬似KP-NETで、要求payloadとread-backの全所有対象フィールドを照合すること。
- forced初回適用、停滞時再適用、目標到達後standby、07:00 green引継ぎを一つの`plan_id`で追跡できること。
- 03:00終了と07:00開始を同時に発火しても、SOCフィールドの書込みが一意であること。
- 同一計画に別writerの設定イベントを注入すると、競合検知・安全停止・監査記録が行われること。

### 8.3 運用受入

- controlled fieldsの競合書込み：0件。
- 強制充電開始・停止のread-back成功率：100%。
- `plan_id`不一致、相関不能、必須観測欠落を`PASS`扱いしないこと。
- 14日間の有効サンプルで、停止時・06:30・07:00の各SOC誤差が暫定範囲内である割合を記録し、基準未達なら全体有効化しないこと。
- `kpnet_soc_gap_report`が日別の結果・誤差・競合・read-back・観測鮮度・失敗理由を出力すること。

## 9. 実装前に確定すべき事項

1. KP-NET候補コードと実効SOC上限の正式な対応表を、実機read-backを伴って取得すること。
2. 23:00、03:00、07:00の各プロファイルで、実際に書込みが必要なフィールドをフィールド単位で承認すること。
3. 機器が設定値をread-backできない場合の承認済みフェイルセーフと、運用アラートの受け手を定めること。
4. `ADJUST03_REGENERATE_PLAN=true`を残す場合のrevision切替の業務ルールを定めること。
5. SOCセンサー0%・急変値の正常範囲を、機器仕様と実績データで校正すること。
6. 売電ペナルティは、FIT年次、契約状態、昼間三段単価の確認後に、別の料金仕様・回帰試験として扱うこと。

## 10. 完了条件

本仕様の実装完了は、コードが存在することではなく、少なくとも次を満たした時点とする。

```text
要求SOC、実効候補コード、機器read-back、停止SOC、06:30 SOC、07:00 SOCが
同じ plan_id で追跡できる。
```

加えて、競合書込みゼロ、read-back確認済み、翌朝SOCの監査判定が可能であり、`PASS`を設定POSTの成功だけで付与しないことを必須とする。

## 11. 実装・本番反映記録

本仕様に対応する実装では、次を反映した。

- `app/runtime/night_soc_controller.py`でplan_id、候補値ガード、SOC鮮度、read-back比較、実効目標を共通化。
- 03:00処理でFirestoreの夜間SOC実行記録と排他リースを取得し、計画凍結・充電・停止状態を保存。
- KP-NET設定後のcontrolled fields read-back不一致をエラー化。
- 23:00はenforce時に夜間SOCフィールドを保持し、07:00は03:00完了記録がない場合にgreen書込みを拒否。
- `NIGHT_SOC_MANUAL_OPERATION=true` では23:00/03:00の蓄電池書込みを停止し、03:00の`MANUAL_OPERATION`引継ぎ後に07:00のgreen書込みとread-back確認を行う。
- SOC乖離レポートに`PASS/FAIL/INCONCLUSIVE`、07:00誤差、read-back、停止確認、競合数、理由を追加。
- 本番環境変数は`NIGHT_SOC_CONTROL_MODE=enforce`、`NIGHT_SOC_READBACK_REQUIRED=true`、リース18,000秒、SOC鮮度360秒で反映。

検証結果は、全体テスト **450 passed, 1 skipped**、Ruff成功、mypy成功、security check成功、Cloud Build成功、3ジョブ更新、Scheduler確認、Dashboard更新、KP-NET import成功、Drive backup成功、07:00 DryRun成功である。

なお、`competing_writes=0`は単一所有者経路が記録した制御イベントの値であり、外部プロセスが同時に機器へ書き込んでいないことを物理機器側の全履歴だけで証明する値ではない。初回14日間は`settings_events`、plan_id、writer、read-backを突合し、外部writerが検出された場合は監査を`FAIL`へ変更する運用検証を必須とする。

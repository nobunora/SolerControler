# KP-NET 実績データ差分同期 仕様書

## 1. 目的

KP-NET の監視CSVを取り込むたびに、既存データを無条件に再書込みし、Firestore の `monitoring_samples` 全履歴を繰り返し走査する現行方式を廃止する。

本仕様では、通常同期を **JST直近4暦日の差分照合** に限定し、KP-NET メンテナンス等で一時的に欠損したデータが1～2日後に補完される運用を吸収しながら、以下を実現する。

- KP-NET から取得する月次CSVを必要最小限にする。
- 同一 timestamp のデータを論理的に一意に保つ。
- 同一内容の既存レコードは再書込みしない。
- 遅れて追加された欠損データは後続同期で取り込む。
- KP-NET 側で訂正された値は更新する。
- 通常同期で `monitoring_samples` 全期間scanを行わない。
- 日次集計・PV充電終了SOC・料金集計も変更範囲だけを再計算する。
- ダッシュボードの bootstrap は必要な場合だけ更新する。
- 蓄電池制御、23/03/07 の制御契約、予報ロジックには触れない。
- 1時間ごとの自動取得は本PRの対象外とする。

## 2. 背景と現行課題

### 2.1 現行の監視CSV保存

Firestore は現在、概ね以下の構造を使用する。

```text
monitoring_samples/{ts}
```

`ts` 自体が document ID であるため、同一文字列の timestamp が複数documentとして保存される構造ではない。

SQLite / PostgreSQL も `ts` を一意キーとして扱う。

したがって主問題は「同一timestampの複数保存」ではなく、以下である。

1. CSVに含まれる既存timestampを毎回 `set(..., merge=True)` / UPSERT している。
2. 実績値が同一でも `source_csv` / `ingested_at` が更新され、不要なwriteが発生する。
3. Firestore の `recalc_battery_pv_charge_end_soc()` が `monitoring_samples` 全件をscanする。
4. Firestore の `recalc_dashboard_daily_metrics()` が `monitoring_samples` 全件をscanする。
5. `recalc_cost_daily()` も全 `monitoring_samples` を読み直す。
6. CSV取得のデフォルト対象月が前月＋当月であり、月途中でも前月CSVを毎回取得し得る。

### 2.2 KP-NET の遅延補完

KP-NET はメンテナンス等により、半日程度の監視データが欠けることがある。

例:

```text
9/18 10:00～20:00   KP-NET側で欠損
9/19                まだ欠損
9/20                9/18のデータが後から補完
```

このため「最新から過去へ見て、既存1件を見つけたら停止」では欠損補完を取り逃す。

通常同期では **今日を含む直近4暦日を必ず再照合する**。

## 3. スコープ

### 3.1 対象

- KP-NET CSV対象月の絞り込み
- CSV行の正規化
- CSV内部の重複排除
- 直近4日の差分照合
- Firestore / SQLite / PostgreSQL の不要write抑止
- 変更timestamp / 影響日付の追跡
- 変更日のみの日次集計
- PV充電終了SOCの部分再計算
- 料金集計の有界再計算
- bootstrap snapshot の必要時更新
- 既存データの重複・ID不整合を確認する一回限りの監査/整理手段
- ログと回帰テスト

### 3.2 対象外

- 1時間ごとのScheduler追加
- 手動「更新」ボタン
- 蓄電池制御
- 23/03/07 のモード/SOC設定変更
- 予報アルゴリズム変更
- 料金契約値変更
- history snapshot の更新頻度変更
- KP-NET の認証方式変更
- 本番データの自動削除・自動migration実行

## 4. 時刻と同期ウィンドウ

### 4.1 基準時刻

同期ウィンドウの判定は **Asia/Tokyo の暦日** を使用する。

`now` が 2026-09-20 JST の場合、通常同期対象は:

```text
2026-09-17 00:00:00
～
2026-09-20 23:59:59...
```

つまり:

```text
today
today - 1 day
today - 2 days
today - 3 days
```

の4暦日。

### 4.2 月境界

直近4日が2か月にまたがる場合だけ2か月分のCSVを取得する。

例:

```text
2026-10-02 実行
対象日: 09/29, 09/30, 10/01, 10/02
対象CSV月: 2026-09, 2026-10
```

月途中なら当月1ファイルだけを取得する。

例:

```text
2026-09-20 実行
対象CSV月: 2026-09
```

### 4.3 明示バックフィル

`KP_CSV_TARGET_MONTHS` は取得対象月の明示指定に使用する。4日windowを外して指定月全体をDB同期する **full backfill** は、これとは別に `DATA_MONITORING_FULL_BACKFILL=true` を明示した場合だけ有効とする。

過去月全体を同期する場合は `KP_CSV_TARGET_MONTHS=<対象月>` と `DATA_MONITORING_FULL_BACKFILL=true` を同時に指定する。対象月だけを設定しても通常4日windowを暗黙に解除してはならない。

通常実行が勝手に全履歴へ拡大してはならない。

## 5. CSV取得仕様

### 5.1 通常同期

KP-NET の監視CSVは月単位でしか取得できないため、HTTP転送自体を行単位にはできない。

そのため通常同期では:

1. JST直近4日の属する月集合を求める。
2. KP-NET の available months と交差を取る。
3. 必要な月だけダウンロードする。
4. CSVをローカルでparseした後、直近4日以外の行はDB照合対象にしない。

### 5.2 必要月がKP-NETに存在しない場合

メンテナンスや公開遅延により必要月が available months に存在しない場合:

- 既存DBデータを削除しない。
- 存在する対象月だけ処理してよい。
- 不足月を `source_month_unavailable` としてログに残す。
- 不足を「0件の実績」と解釈しない。
- snapshot / aggregate から既存値を消さない。

### 5.3 KP-NET自体が取得不能の場合

認証失敗、メンテナンス画面、HTTP失敗等でCSVを取得できない場合:

- 監視データを書き換えない。
- 既存データを削除しない。
- stale CSVを「最新」として自動採用しない。
- 現行の失敗処理/exit semanticsを維持し、原因をログへ残す。

## 6. 正規化と一意性

### 6.1 論理キー

監視実績の論理一意キーは:

```text
ts
```

とする。

新規取り込みでは:

```text
document.id == payload["ts"]
```

を満たすこと。

### 6.2 実績比較対象

同一データかどうかは以下の実績フィールドだけで比較する。

- `ts`
- `pv_kwh`
- `load_kwh`
- `sell_kwh`
- `buy_kwh`
- `charge_kwh`
- `discharge_kwh`
- `soc_percent`

以下は一致判定から除外する。

- `source_csv`
- `ingested_at`
- その他の取り込みメタデータ

したがって実績値が同じなら、別runで再取得してもDB writeしない。

## 7. CSV内部重複

### 7.1 完全一致重複

同じ `ts` かつ比較対象フィールドがすべて同一の行が複数存在する場合:

- 1件へcollapseする。
- DB writeは最大1回。
- `csv_duplicate_same` を増加させる。

### 7.2 内容不一致重複

同じ `ts` で実績値が異なる行が同一同期入力に存在する場合:

- そのtimestampを曖昧な競合として扱う。
- 自動で「最後の行が正しい」と決めない。
- 当該timestampの新規/更新writeを行わない。
- 既存DB値がある場合は保持する。
- `csv_duplicate_conflict` と timestamp をログに残す。
- 他timestampの正常な同期は継続してよい。

## 8. DB差分判定

直近4日の正規化済みsource rowsとDB rowsを比較する。

### 8.1 INSERT

```text
sourceあり
DBなし
```

→ INSERT

主用途: KP-NETが後日補完した欠損データ。

### 8.2 UPDATE

```text
sourceあり
DBあり
実績値が異なる
```

→ UPDATE

主用途: KP-NET側の後日訂正。

### 8.3 SKIP

```text
sourceあり
DBあり
実績値が同一
```

→ writeしない。

`source_csv` や `ingested_at` だけを更新することも禁止する。

### 8.4 sourceに存在しないDB行

```text
DBあり
sourceなし
```

→ DELETEしない。

KP-NETメンテナンス中の欠損をDB削除へ伝播させてはならない。

## 9. 最新から過去への走査

source rowsは timestamp 降順で処理してよいが、通常同期では **直近4日ウィンドウ内で既存レコードを見つけても停止しない**。

停止条件は:

```text
timestamp < JST today-3days 00:00
```

のみ。

つまり「既存1件/3件連続で停止」ではなく、4日間のreconciliation windowを最後まで確認する。

## 10. 変更範囲の追跡

同期結果は最低限以下を返す。

```text
rows_seen
rows_in_window
inserted
updated
unchanged
csv_duplicate_same
csv_duplicate_conflict
changed_timestamps
calendar_dates
dashboard_affected_dates
```

### 10.1 calendar_dates

INSERT / UPDATE されたtimestampの暦日。

### 10.2 dashboard_affected_dates

通常は変更timestampの暦日。

ただし 23:00 以降の充電データは翌日の `review_night_charge_kwh` にも影響するため、翌日も追加する。

例:

```text
2026-09-20T23:30:00 changed
→ affected:
  2026-09-20
  2026-09-21
```

## 11. 日次集計の再計算

### 11.1 全履歴scan禁止

通常同期経路から以下の無制限scanを除去する。

```python
client.collection("monitoring_samples").stream()
client.collection("monitoring_samples").order_by("ts").stream()
```

通常取り込みでは query 範囲が必ず timestamp / date で有界であること。

### 11.2 dashboard_daily_metrics

変更の影響を受ける日だけ再計算する。

日 D の `dashboard_daily_metrics` を計算する際は:

- D 00:00～24:00 の暦日データ
- D-1 23:00～24:00 の夜間充電レビュー用データ

が必要。

`review_night_charge_kwh(D)` は:

```text
D-1 23:00～24:00
+
D   00:00～07:00
```

を含む現行意味を維持する。

### 11.3 PV充電終了SOC

`pv_charge_end_soc_percent` / `pv_charge_end_at` は変更された暦日だけ再計算する。

当該日の:

- `pv_kwh > 0`
- `charge_kwh > 0`
- `soc_percent != null`

を満たす最後のtimestampを使用する現行契約を維持する。

### 11.4 読み取り回数

同じ日の monitoring rows を、dashboard metrics と PV充電終了SOCのために別々に2回取得しない。

可能な限り1つの bounded read を共有して両方を計算する。

## 12. cost_daily の有界再計算

`recalc_cost_daily()` も通常取り込みで全履歴を読んではならない。

### 12.1 再計算開始日

最古の変更日が属する月の1日を `cost_recalc_start` とする。

理由:

- `night8_tiered` は同一月内の累積買電量で段階料金が変化する。
- 月途中だけを独立再計算すると段階境界が壊れる。

### 12.2 再計算終了日

JSTの現在日まで、またはDBに存在する最新監視日までのうち必要な範囲。

直近4日同期から導かれるため、通常は最大でも「前月1日～当日」程度に限定される。

### 12.3 グローバル累積値

`cost_daily.cumulative_kwh` / `cumulative_yen` は全期間累積なので、`cost_recalc_start` の前日の `cost_daily` をbaselineとして取得する。

再計算結果へそのbaselineを加えて保存する。

全履歴 monitoring rows を再読込して累積値を作り直してはならない。

## 13. 変更なしの場合

直近4日の全source rowがDBと同一なら:

- `monitoring_samples` write = 0
- 日次monitoring集計再計算 = 0
- cost_daily再計算 = 0
- actual-only import で他入力変更もない場合、bootstrap snapshot の再生成も不要

ただし同一pipeline runの再実行、settings/forecast等の別入力が同時に変わる経路では、その入力に必要な既存処理を壊してはならない。

## 14. snapshot

通常のCSV actual-only同期で監視データがINSERT/UPDATEされた場合:

- bootstrap snapshot を更新する。
- history snapshot は更新しない。

現行の「23時のみhistory full rebuild」という契約を変更しない。

## 15. 既存データの重複監査・整理

### 15.1 前提

現行Firestoreは document ID が `ts` のため、通常生成されたデータは同じ文字列timestampで複数documentにならない。

ただし旧データや手動投入により以下が存在する可能性はある。

- `document.id != payload.ts`
- timezone表現差等により同一論理時刻が複数IDに存在
- 同一論理timestampで内容が同一のlegacy document
- 同一論理timestampで内容が異なる競合

### 15.2 一回限りの監査手段

実装では、全監視データを **dry-runで監査できる専用ツール** を用意する。

通常pipelineには組み込まない。

分類:

```text
canonical
duplicate_same
duplicate_conflict
id_payload_mismatch
invalid_timestamp
```

### 15.3 自動整理可能なもの

`duplicate_same` で、canonical documentを一意に決定できる場合のみ apply mode で余分なdocumentを削除可能とする。

### 15.4 自動削除禁止

以下は自動削除しない。

- 内容不一致の `duplicate_conflict`
- timestamp解釈が曖昧
- canonicalを一意に選べない
- KP-NET現在値との整合を確認できないもの

### 15.5 本番実行

重複監査ツールの `--apply` を production で自動実行しない。

本番データ削除は別途、利用者の現在の会話で明示承認が必要。

## 16. バックエンド互換性

本番最適化の主対象は Firestore だが、以下を維持する。

- SQLite: `ts PRIMARY KEY`
- PostgreSQL: `ts` UNIQUE/PRIMARY契約
- Firestore: document ID = `ts`

CSVの正規化、一致判定、changed timestamps / affected datesの計算は可能な限り共通ロジックとする。

各DBのquery / write部分だけadapter境界に残す。

## 17. ログ/可観測性

1同期につき最低限以下を構造化または検索可能なログとして出す。

```text
reconcile_days=4
source_months=...
source_month_unavailable=...
csv_rows=...
window_rows=...
inserted=...
updated=...
unchanged=...
csv_duplicate_same=...
csv_duplicate_conflict=...
changed_dates=...
dashboard_affected_dates=...
cost_recalc_start=...
monitoring_reads_scope=...
monitoring_writes=...
snapshot_updated=true|false
```

秘密情報、KP-NET認証情報、ローカル絶対パスは記録しない。

## 18. 性能契約

通常4日同期では、総履歴年数に対して処理量が増加しないこと。

### 必須

- monitoring_samples 全collection stream禁止
- 既存と同一の監視rowへのwrite禁止
- KP-NET取得月は直近4日が属する月のみ
- dashboard/PV集計readはaffected datesに限定
- cost readは最古変更月の月初以降に限定
- 変更0件ならmonitoring由来の再集計を行わない

### 目標

30分データの場合、通常の同一データ再同期は概ね:

```text
source comparison: 4日分 ≒ 最大192 interval程度
monitoring writes: 0
daily materialization writes: 0
```

を基準とし、1年/2年分の履歴量に比例してreadが増えないこと。

これは実測値ではなく設計上の上限目標であり、本番値はdeploy後に測定する。

## 19. 安全契約

この変更で以下を変更してはならない。

- `app/runtime/night_soc_time_contract.py`
- 23/03/07 の実機制御
- `BatteryOperatingMode`
- `SocEconomyMode`
- forced mode の既存契約
- KP-NET settings write
- Scheduler cadence
- production IAM
- Secret Manager権限

`tests/test_night_soc_protected_contract.py` は通常のquality gateで必ず維持する。

## 20. 完了条件

以下をすべて満たしたとき実装完了とする。

1. 通常同期で直近4日だけを必ず照合する。
2. 月途中は原則当月CSVだけを取得する。
3. 月境界は必要な場合だけ前月＋当月を取得する。
4. 遅延補完された欠損rowをINSERTできる。
5. 後日訂正されたrowをUPDATEできる。
6. 同一rowはwriteしない。
7. sourceに無いDB rowを削除しない。
8. CSV内部完全重複をcollapseする。
9. CSV内部競合を安全にskip/reportする。
10. 通常pipelineから monitoring_samples 全履歴scanが消える。
11. dashboard/PV日次集計はaffected datesだけ再計算する。
12. cost_dailyは月初baseline方式で有界再計算する。
13. 変更0件ではmonitoring由来の再集計を行わない。
14. bootstrap/historyの既存更新契約を維持する。
15. SQLite/PostgreSQL/Firestoreの一意性契約を維持する。
16. dry-run重複監査手段を用意する。
17. 本番削除は自動実行しない。
18. 詳細テスト仕様の必須ケースがすべて通る。

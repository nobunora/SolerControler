# 実績取り込みの成功位置からの差分照合

## 目的と固定する境界

前回成功後の追加・訂正・削除だけをFirestoreから受信し、既存の全範囲revisionと同じ結果を算出する。
30分更新で全履歴を再取得しない。観測日を24時間に切る方式ではなく、古い観測日の変更も対象とする。
PV予測、SOC、料金計算、CSVの値・観測時刻、採用計画、公開する値と出典は変更しない。

現経路は、ブラウザ → POST API → 専用Job → CSV取得 → 指定日保存 → 表示生成 → GCS公開 → 受領票GET。
この経路とHTTP回数は維持する。初期bootstrap、明示履歴、静的資源のcache/ETagも変更しない。

```text
① 前回成功時のGCS checkpoint（metadata・read_time・再開token）
   ↓ 世代固定取得・checksum・DB identity・schema・件数を検査
② Firestore Listenを再開 → 追加/訂正/削除/結果集合からの除外を適用
   ↓ 全12 targetのCURRENTと全体read_timeがそろって初めて確定
③ 固定時点のmetadata query adapter → 既存のrevision計算と同じscope/digest
   ↓ 保存前 / 保存後 / 公開前後で②へ戻り、新しい整合点を取得
④ CSV保存・表示公開・受領票とactive状態の成功確定
   ↓ 成功・partialだけ（failed/busy/保存結果不明では進めない）
⑤ checkpointをGCS世代CASで1回保存 → 次回の①
```

## 責任

| 所有者 | 責任 |
| --- | --- |
| `revision_changes` | 初回の固定read_time全取得、native変更stream、CURRENT/RESET/存在件数/削除、有限retry・期限・取消 |
| `revision_manifest` | 単一時点の読取り専用metadata query。DB fallback・業務値・書込みを持たない |
| `incremental_revisions` | checkpoint取得/検査、差分状態、段階の共有、既存revisionへの接続、成功後CAS保存、観測ログ |
| `snapshots` | 従来のscope・除外日・native update_time・digest・上限・公開順序検査 |
| `actual_import` | 既存の受付・保存・公開・受領票の状態遷移と、成功確定後だけのcheckpoint保存 |

全書込み経路へ独自journalを挿入せず、DBのnative変更通知を使う。
アプリ外の書込みや過去日の削除も、同じqueryの再開位置から取得する。
常駐watcherは設けず、Jobの検査時だけstreamを開き、確定・失敗・期限のいずれでも取消する。
Listenはselect非対応のため、受信後に日付・時刻・名前・analog参照とnative update_timeだけを保持する。
12 targetのうちsettingsは従来必要な最新40件の集合を追跡し、集合への補充・除外も反映する。
実SDKではこのlimit付きtargetが再開時にRESETされるため、上限40件の設定確認を固定補助読取りとして
`bounded_reset_targets`/`settings_document_reads`へ別計上する。履歴targetのRESETとは区別し、
通常モードで全履歴の再取得が発生していないことを検査する。

## 移行・失敗・競合

- 初回またはchecksum/schema/identity不一致では、固定時点の全取得から一度再構築する。通常の差分モードと区別して記録する。
- RESETは当該集合を空にしてからnative初期状態を再受信する。存在件数不一致・再開拒否は完全な再構築を要求する。
- 再開時の削除は個別通知ではなく、存在確認のBloom filterで通知される場合もある。全キャッシュ名を検査し、確実に集合外と判明した文書だけを除く。除外後の件数がserver件数に一致した場合だけ採用する。filter欠落・偽陽性・不正値で一致しない場合は従来どおり再構築する。
- 部分streamは破棄する。全targetのCURRENTだけでなく、target_ids空の整合read_timeまで待つ。
- 途中で失敗したJobはGCSの成功位置を進めない。実績保存済みの公開再開は従来のcheckpoint/receiptで扱う。
- cache/stream障害時は理由をログに残し、既存の期限付き全照合へ戻す。不完全なmetadataを正常扱いしない。
- 成功位置の保存は元のGCS世代へCASし、応答不明を再送しない。保存障害で既に成功した実績保存・公開を失敗へ戻さない。
- 公開前後およびGCS CASの各試行は新しいnative整合点を取得する。以前の段階を流用しない。
- 欠測日レビューのmetadataも差分状態を使用する。CSV値の同日比較と月内料金再計算の業務読取りは別に保持する。

## 機械的な受入条件

1. 同じnative時点の全取得と差分結果で、bootstrap/history/指定日除外の全epoch成分が一致する。
2. 前回位置から24時間の再開、古い日付の追加・訂正・削除、最後の文書削除、同時更新、settingsの集合交代を検出する。
3. 部分stream、CURRENT不足、RESET、存在件数不一致、通信断、期限切れで誤った成功位置を保存しない。
4. failed/busy/actuals_saved不明/受領票書込み失敗では保存0回、成功確定後は世代CASを1回だけ実行する。
5. 再起動後の通常差分モードで全履歴queryを発行しない。初期化・再構築・fallbackと通常モードを分けて計測する。
6. 初期HTTP件数・遅延履歴・原本SHA/出典/対象外日付・SOCを保持する。

文書readイベント、Listen target数、削除・除外数、初期全取得、圧縮checkpoint bytesを別々に記録する。
通知文書数を課金件数と同一視せず、空queryの最低読取りや課金exportは別に扱う。
99%という以前の見積もりを合格値へ自動変換せず、初回と再開後の本番実測を報告する。

仕様: [Firestore native protocol](https://cloud.google.com/firestore/docs/reference/rpc/google.firestore.v1)
および[listenerの課金](https://firebase.google.com/docs/firestore/pricing#listening_to_query_results)。

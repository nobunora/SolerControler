# 予報非依存の理想最大発電量台帳

## 計算・更新

1時間の最大量は、日付・時刻・設備形状による快晴POA基準電力量 B と、全時間で共有する係数 C の積。太陽高度・季節はBで変化する。気温補正・天気予報・忘却・時間帯別係数は使わない。

実際のh:00とh:30のPV電力量を合計する。現在の最大量との差が **0.01 kWhを超えた場合だけ** Cを上げる。欠測を0にしない。低日射の追加閾値・分母floor・時間帯別fallbackは作らない。B=0で0.01 kWhを超過する場合は明示エラーで、台帳更新を中止する。

過去の発行値は更新後の最大量へ置き換えない。実績到着時刻と実発行時刻を分ける。事前値なし・時刻証拠不足・実績待ち・未来区間を成功として数えない。既知期間の再現は物理的絶対上限や未知期間の精度の証明ではない。

## 自動更新の経路

既存の予測専用Jobは、保存済み予測の回復、CSV取得、実績DB取込、従来の予測計算の順に実行する。DB取込成功後、新台帳を更新する。rawの重複取込が冪等終了しても新台帳処理へ到達する。settings-only回・raw失敗時は呼ばない。

新台帳は通常forecastの成功条件に追加しない。台帳の通信・整合性・保存失敗は `pv_ideal_max_import` の `severity=ERROR/status=failed/error_type` として記録し、完了済みの実績取込と後続forecastは継続する。例外messageや保存先識別子は出さない。台帳自身のCAS・checksum検査は維持し、その実行での再書込み・古いcopyへのfallback・自動初期化は行わない。次の通常実行では空directoryへ本番currentを新規checkoutしてから処理する。実績取込本体の失敗は従来どおり伝播する。

予測Jobの子は旧計画アーカイブを無効化しているため、新台帳のprefixを `PV_IDEAL_MAX_GCS_PREFIX` で明示的に引き継ぐ。この値は既存prefixから実行時に導出する。通常は追加の `.env` 設定は不要。直接実行の既定は従来の `night_plan_archive_prefix()`。旧計画保存・予報PV・SOC制御へ新最大量を代入しない。

## 保存と復元

既存archive prefix内の別namespace `pv_ideal_max/<geometry fingerprint>/` を使う。旧 `clear_ghi` や計画原本を変更しない。

- 整合したSQLite backupをSHA256名のgzip snapshotとしてcreate-only保存。
- `current.json` を読んだ世代条件で更新し、競合時は終了。blind retryや古いcopyでの上書きはしない。
- 毎回空の一時directoryへ指定世代を復元し、snapshot SHA、model/geometry、C/revision、SQLite quick_checkを確認。
- pointerなしは `uninitialized`。自動初期化・暗黙リセットをしない。破損・snapshot欠落はエラー。
- cloud初期化実時刻を評価境界として保持し、区間開始と保存された発行時刻の両方がその境界以降の場合だけ運用評価する。研究用seedの過去時刻を本番成績に読み替えない。
- 共有deadline60秒、各SDK操作は最大4秒、SDK retryなし。圧縮snapshot bytesと更新結果を記録。

履歴増加に伴うsnapshot転送量は運用で測定する。通常状態は1行、明示的な時間別reportは31日以内の索引範囲だけを読む。HTTP/dashboard経路は追加しない。

## 正規ローカル入口

`scripts/run_pv_ideal_max_from_env.ps1` は既存の `production_env.ps1` から設定を読む。秘密や保存先識別子をコマンドへ書かない。

- `-Mode ValidateSeed -StateDirectory <local directory>`: seedのローカル検証。
- `-Mode InitializeCloud -StateDirectory <local directory>`: 明示承認後の初回保存。既存台帳は置換しない。
- `-Mode Report -FromDate YYYY-MM-DD -ThroughDate YYYY-MM-DD -AsOf <offset付き時刻> -OutputPath <新規JSON>`: 本番台帳からread-only復元して1時間別評価。

通常reportは部分集合を返す。全原本の復旧受入では、空directoryに復元したcurrent_state・observations・predictionsの全行と移行元を照合する。初期化の承認はJobデプロイ・merge・機器操作の承認と区別する。

訂正された同一実績は自動上書きせず、別の明示的な再構築を要求する。保存失敗後は正規経路で現在版を照合してから再開し、係数や原本を消してやり直さない。

# 電力量の区間とSOCの時点（ローカル検証中）

CSVの元ラベルは変更しない。電力量は区間積分、SOCは時点の状態であり、同じ行だから
同じ瞬時刻を表すという扱いは禁止する。

`E[h] = E_CSV[h:00] + E_CSV[h:30] = integral(h,h+1,power(t)dt)`。
2つの有効・非負・有限の30分値がそろう場合だけ1時間値を作る。欠損は0ではない。
予測も同じ[h,h+1)であり、保存予測のhourや元日射時刻を書き換えない。
Open-Meteoの終了ラベルは放射計算で1回だけ区間開始へ正規化する。

計画SOC[h]はh:00の状態で、E[h]を反映するとSOC[h+1]になる。
今回の11/3・4のAPI照合ではCSV SOC[label]はlabel+30分の状態と整合した。
この換算は`kpnet_energy_start_soc_end_inferred_api_20261103_04_v1`として明示し、
メーカーが正式に確定した契約とは扱わない。APIのdevice_measured_atには加算しない。
CSV SOCを正時境界hへ合わせるなら前の半時h-00:30の行が必要であり、
正時・半時SOCを加算/平均して作らない。

共通実装はapp/domain/measurement_time.py。元CSV readerの保存payload、予測原本抽出、
夜間予測の観測SOC時刻、PV誤差検証の境界SOC、表示read modelが同じ規則を使う。
画面はサーバーが示すSOC時刻をそのまま描き、独自の+30分/+1時間処理をしない。
元のtsとSOC値、予測run/issued-atは保持する。既存DBの一括修復は行っていない。
SQLiteは同じ半時SOCを読取りSQLで取得し、共通read modelを作る。

日次集計と過去の昼間SOC増加量評価も、07:00境界にはCSV06:30のSOCだけを使う。
欠損なら近傍の行で代用しない。日中SOCの時間帯判定にはSOCの有効時刻を使い、
買電量・料金区分には電力量の区間開始を使う。朝SOC欠損時は時刻もNULLに戻す。
互換フィールドactual_soc_at_hour_start_percentは元CSV正時ラベルの値であり、
その正時の状態を意味しない。時点の比較にはactual_soc_pointsのatを使う。

変更対象の保護は、夜間SOCの実測0・計画と実績の分離、原本の同一性・SQLite parity。
これらを維持した上で、2026-11-04利用者承認により時間解釈を明示する。
ブラウザ取得数・初期期間・遅延履歴・キャッシュ条件は変えず、DB読取りを追加しない。
当日プレビューはローカルに再生成し、本番や過去日のmaterialized historyを更新しない。

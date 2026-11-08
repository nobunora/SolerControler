# 保存原本のPV再評価と365日学習

2026-11-08の利用者承認: 過去原本再評価、PV学習と完全SOC検証の分離、各面1.08kW/傾斜19度、最低7日、365日窓、採用条件緩和、ローカル非適用経路を実装する。天候分類統合・PVセル温度補正・23時充電は含めない。電気料金最小の既存SOC目的関数は変更しない。

## 計算経路

計算Job -> 既存能力台帳/現在の気象予報 -> 過去365日の保存実績とimmutable night_plan_decisions -> 原本SHA/発行・取得時点検証 -> 保存気象の最新基準再評価 -> PV学習 -> 前向き検証/完全SOC・料金検証 -> 将来計画。ダッシュボードGET、実機制御23/03/07に履歴読取を追加しない。

各過去日・時刻の太陽高度/方位、面別の入射を既存の1分積分で計算し、当時保存した日射を適用する。現在の天候・実績日射へ置換しない。日射の終端ラベルと瞬時の気温/風/天候時刻を一律にずらさない。

学習には、その時刻の正時・半時PVの合計と取得時点を使用する。片側欠測/非有限/負/重複は対象時間を除外。有効0は採用。SOC/負荷/買売電/蓄電池応答の欠測だけではPV学習日を破棄しない。完全な蓄電池・料金replay用PvBiasDayは従来条件のまま別に収集する。

## 数値契約

- 窓は対象日より前の365日。同時刻・同天候分類で7つ以上の異なる日。
- 事前強度は20日のまま。`delta = sum(actual - reforecast)/(n+20)`。最低日数と平滑化強度を混同しない。
- `forecast = clip(weather_adjusted_maximum + delta, 0, maximum)`。独立した能力台帳は残差補正で変更しない。
- 前向き学習には評価日の発行時刻以前に取得済みの過去日だけを使う。係数も当該時点のnative更新履歴から選ぶ。同じ取得時刻のbatchでは最終の最大係数を使う。
- PV検証は最低5つの前向き評価日で、同じ取得済み時間区間のMAEが`baseline-1e-9`未満、平均符号付き誤差の絶対値が`baseline+1e-9`以下。
- 当時の蓄電池応答等が揃う全ての評価日で既存SOC/買電/料金誤差を再計算する。少なくとも1つの完全replayがあり、無効なreplayがなく、各平均誤差が基準`+1e-9`以下の場合に採用する。PV検証日数とは別に完全replayの日数を報告する。SOCは同点を許可し、負の誤差を要求する厳密改善は不要。
- 料金誤差は予測品質のguardであって、全日請求額の削減実績ではない。夜間充電を含むSOC最適化の料金目的、単価、残量価値、制約、候補SOCは従来通り。買電ゼロ/売電量を優先しない。
- 検証不足や悪化時はneutral、または同一model/geometry/能力stateに結び付く既存採用モデルのみ保持する。異なる設備や古い方式のモデルを移植しない。

## 入力予算

実績は既存の請求/学習用読取結果を再利用し、その先頭より古い365日内の範囲だけ追加する。上限17,520実績。消費学習や既存PV7日選択の契約は変更しない。

原本metadataは365*8件まで、朝版は日別8候補まで。GCS原本はgeneration固定・raw SHA照合、1件展開後2MiB、合計64MiB。最大8並列、読取全体90秒、SDK retryなし。日時順に最初の有効朝版を選ぶ。欠損/破損/日別予算超過/未読を日単位で可視化し、原本を捏造しない。毎回の読取数・bytes・採用時間/日・除外数はcoverageへ記録する。ブラウザ要求/転送/DB読取の増分は0。

## 設備と台帳の整合

config/pv_arrays.jsonを図面の1.08kW/19度へ整合する。性能比・影係数・温度係数は変更しない。新geometryは新fingerprintとなるため、既存台帳やstateのfingerprintを直接書換えない。

正規run_pv_ideal_max_from_env.ps1のPrepareGeometryで旧配置設定と旧台帳をread-only取得し、保存観測を取得順に既存update APIへ渡して別namespaceのseedを作る。最初に旧状態の係数/revisionを履歴から完全再現する。新seedは過去予測を発行しない。0.01kWh超過、C>=1、before-score、source SHA/CASの保護は維持する。

InitializeCloudは新namespaceが存在しない場合だけ許可。migration manifestとseed SHA、新旧fingerprint、元台帳のsnapshot/generationが一致することを確認し、正規publishだけで初期化する。旧台帳/元原本を保持。pointerのinitialized_atは新規公開時刻であり、過去観測のavailable_atと混同しない。timeout/既存namespace/世代不一致では再publishせずread-only照合する。

## ローカル非適用経路

replay_pv_bias_training.pyは退避済みSQLite・raw原本ディレクトリ・native台帳をread-onlyで読む。指定旧/新配置で同じ純粋計算核を使い、未補正とcandidate_shadowを新規ディレクトリへ出力する。入力の前後SHAを確認し、production_applied=false、remote_calls=0、DB_writes=0を記録する。

ローカルの料金replayを行わない場合はNOT_RUNとし、PV品質改善だけで運用採用済みにしない。既存出力先を上書きしない。原本・ダッシュボード・control_planを更新しない。

## 変更しない保護領域

forecast_run_id/元発行時刻、過去予測、forecast_recoveryの31日原本回復、実測0と欠測、電力量/SOC時刻、HGB、蓄電池温度・充放電モデル、請求期間/料金契約、23/03/07の制御とAPI待機、表示/取込ボタン、runtime layout、Drive除外は維持する。geometry fingerprintの不一致拒否とfallbackの可視化を緩和しない。

configと新しい計算用依存を正規calculation patch SHAに含め、同commit/digestのSDK proofと保存原本/表示照合を行う。forecast限定リリースをfull/controlへ拡大しない。実機SETは今回のテストへ追加しない。

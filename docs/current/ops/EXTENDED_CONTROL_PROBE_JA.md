# 専用03時制御の実機一貫試験

`scripts/run_extended_control_probe_from_env.ps1 -StatePath artifacts/deployment_state/extended-probe-<timestamp>.json` を使用する。実機変更の明示承認、cleanなcommit、そのcommitの本番ゲート成功を前提とする。通常23/03/07、Scheduler、runner:latestは更新せず、専用タグから既存の試験Jobだけを更新する。Cloud Run retryは0。

既存CSV取得・計画生成・60秒の設定往復試験を維持し、その後に拡張試験を行う。通常03時の監視関数と保存関数を直接再使用する。通常処理の06:45/06:50/06:55境界と本番用機器ポートは変更しない。専用入口に限って論理03時の時計と、実時間の期限を持つ実機ポートを注入する。

生成原本は変更せず保存する。試験計画は複製し、実SOC+1ポイント（上限100%）を明示的な試験目標とする。Firestoreはcontrol_live_probes/<run>/以下、GCSはlive-probes/<run>/以下だけに記録する。本番のlatest、日別計画、判断学習には混入させない。保存原本、索引、監視に渡すJSONのSHAを照合し、読戻した原本を圧縮した証拠からローカルへ復元する。

実SOCは本番と同じ取得・再試行処理を使い、CSV値で代用しない。監視開始時から300秒の試験予算と、最大30秒の試験用再確認間隔を設ける。通常制御が要求した待機時間と実際の試験待機時間を別々に記録する。この試験は通常のETA間隔やScheduler起動の証明にはならない。

合格には、原本と索引の照合、forced=3→standby=5の実SET/read-back、停止時の実SOC≧目標、全14設定の復元が必要。初めから100%なら即時停止経路として明示し、SOC上昇の証拠とは扱わない。予算内未達・取得不能は失敗として復元する。UNKNOWN_WRITEは既存の過去障害契約に従って読取りだけを行い、結果不明のまま追加SETを重ねない。通常処理の保護領域や既存60秒試験を緩和しない。

中断時は同じstateを確認する。runningは未確定であり再実行しない。明示的に成功したbuildだけをResumeで再利用する。取得できた実行応答・proof・原本をartifacts/deployment_stateへ保存し、実機の成功とローカル復元成功がそろってcompleteとする。

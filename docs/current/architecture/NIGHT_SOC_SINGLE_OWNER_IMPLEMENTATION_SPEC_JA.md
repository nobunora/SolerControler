# 夜間SOC単一所有者制御 実装詳細仕様書（退役）

## 状態

この文書は**退役した歴史的設計**であり、実装・運用の指示書ではない。
2026-08-29の利用者承認により、過去の単一所有者設計は独立した23:00、03:00、07:00スロットへ置換された。現行の唯一の時刻契約は [app/runtime/night_soc_time_contract.py](../../../app/runtime/night_soc_time_contract.py) と、その保護テストである。

以前の本文に記載されていた所有権の集中、状態遷移、スケジュール上の待機条件、実行下限、再適用、永続化を伴う制御手順は、いずれも現行の動作を規定しない。この履歴文書を根拠に実装または運用を変更してはならない。

## 現行の独立スロット契約

| スロット | 現行責務 |
| --- | --- |
| 23:00 | `slot_orchestration._run_night_23` がstandbyを一度だけ書込み、read-backする。|
| 03:00 | `slot_orchestration._run_adjust_03` が時刻境界を守る。Realtime SOC監視は06:45より前だけ開始でき、最終standbyは06:50より前に開始し、06:55以後は外部I/Oを開始しない。|
| 07:00 | `slot_orchestration._run_day_07` がgreenを一度だけ書込み、read-backする。03:00スロットの結果に依存しない。|

各境界、開始可否、残り時間の計算は `night_soc_time_contract.py` を参照すること。個別の環境変数、保存状態、過去の設計書で時刻境界を上書きしてはならない。

## 保持する制約と限界

- 03:00のRealtime SOC取得には絶対期限を一度だけ割り当て、HTTP要求・再試行・sleepは残時間を超えない。
- 自動テストは擬似KP-NETで時刻境界とread-back経路を検証する。本番の機器状態は、反映後にジョブログと実機read-backで別途確認が必要である。
- 本文書が退役する前の観測値、候補値、受入基準は履歴情報であり、現在の設定値や運用判断の根拠にはならない。

現行コードの保護境界と検証条件は [PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md](../agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md) を参照する。

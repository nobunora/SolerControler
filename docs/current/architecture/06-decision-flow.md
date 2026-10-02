# Decision Flow

この文書は蓄電池設定に関わる判断の全体順を示します。設定値の詳細は [運用条件ファイルガイド](../product/OPERATION_CONDITIONS_GUIDE.md)、予測モデルの詳細は `product/` の専門文書を正本とします。

```text
23:00: standby を適用し、外部取得はしない
  ↓
03:00: CSVと予報を取得する
  ↓
当日の night_charge_plan を再生成する
  ↓
必要充電量・目標SOC・現在SOCから充電の必要性を判断する
  ├─ 必要: 強制充電を開始し、実測目標到達・連続取得失敗・06:45まで監視する
  └─ 不要: 夜間プロファイルを反映し、待機を維持する
  ↓
03は外部保存を行わない。保存・ダッシュボード更新は別の非制御所有者が行う
  ↓
07:00: グリーンモードへ切り替える
```

## 判断の優先順位

1. 固定安全条件: 0時跨ぎ禁止、開始・終了同一禁止
2. 変動時刻条件: 夜間終了時刻、日中充電窓
3. KP-NET候補値から運転モードを選択（03の停止SOCは連続した計画目標で判定）
4. PV・消費予測と蓄電池制約に基づくSOC最適化

## 所有者

| 判断 | 主な所有パッケージ |
| --- | --- |
| スロットごとの実行順 | `runtime/` |
| KP-NETプロファイル生成 | `kpnet/` |
| PV・消費の予測 | `forecasting/` |
| SOCと夜間充電量の最適化 | `energy_plan/` |
| 固定・変動条件の定義 | `config/operation_conditions.json` |

変更前には [ADR 0002](adr/0002-night-slot-orchestration.md) と [ADR 0003](adr/0003-energy-plan-boundaries.md) を確認してください。

現行の時刻・設定保護は [歴史的障害保護仕様](../agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md) を優先します。新機能の実装前案は [SOC経済評価の詳細実装仕様](SOC_ECONOMIC_OPTIMIZATION_IMPLEMENTATION_SPEC_JA.md) を参照してください。

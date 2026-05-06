# 運用条件ファイルガイド（operation_conditions.json）

このシステムの蓄電池設定ルールは、`config/operation_conditions.json` で管理します。  
目的は「コードを触らずに条件を調整する」ことです。

## 1. 構造

- `fixed`: 常に満たす必要がある条件（最優先）
- `variable`: 外部条件に応じて調整する条件
- `priority`: 数値が大きいほど先に適用

## 2. 重要（最優先）

次の2条件は **必須** です。外すと蓄電池が正常動作しないため禁止です。

- `forbid_cross_midnight`: 0時をまたぐ設定の禁止
- `forbid_same_start_end`: 開始時刻と終了時刻が同一の設定禁止

## 3. 現在サポートしているルールID

### fixed

- `forbid_cross_midnight`
  - 対象: `target="charge"`
  - 補正: 0時跨ぎの場合、同日内に補正
- `forbid_same_start_end`
  - 対象: `target="charge"`
  - 補正: `min_duration_minutes` 分の幅を確保

### variable

- `night_charge_end_time`
  - `value: "HH:MM"`
  - 23時/03時ジョブで使う夜間充電終了時刻
- `day_charge_window`
  - `start: "HH:MM"`, `end: "HH:MM"`
  - 07時グリーン設定で使う充電時間帯

## 4. 編集時の注意

- 時刻は `HH:MM` 形式のみ
- JSONのカンマ忘れに注意
- 変更後はローカル実行で `kpnet_summary.json` の `fixed_condition_adjustments` を確認

## 5. 反映手順

1. `config/operation_conditions.json` を編集  
2. ローカルで `python kpnet_main.py` を実行  
3. `artifacts/<run_id>/kpnet_summary.json` の条件適用結果を確認  
4. 問題なければ Cloud Run Jobs を再デプロイ


# Solar Controller アーキテクチャ概要

この文書群は、システムを上から下へ読むための地図です。最初にここを読み、目的に応じて次の層へ進んでください。

| 読む目的 | 文書 |
| --- | --- |
| 目的と外部サービス | [01-system-context.md](01-system-context.md) |
| 実行基盤と時刻別ジョブ | [02-runtime-and-deployment.md](02-runtime-and-deployment.md) |
| Pythonコードの責務 | [03-code-map.md](03-code-map.md) |
| データと保存先 | [04-data-and-backends.md](04-data-and-backends.md) |
| 実行入口 | [05-entrypoints.md](05-entrypoints.md) |
| 業務判断 | [06-decision-flow.md](06-decision-flow.md) |
| 複数バックエンドを選ぶ理由 | [ADR 0001](adr/0001-multi-backend-storage.md) |
| 夜間スロットを分ける理由 | [ADR 0002](adr/0002-night-slot-orchestration.md) |
| 予測・計画・実機設定を分ける理由 | [ADR 0003](adr/0003-energy-plan-boundaries.md) |

## システムの目的

太陽光発電・消費・蓄電池の実績と予報を用いて、夜間充電量と蓄電池モードを毎日調整し、その結果を保存・可視化します。運用の時刻境界は23:00、03:00、07:00（JST）です。

## C4の使い方

C4は Context、Container、Component、Code の順に視点を深める方法です。このリポジトリでは、まず Context（外部サービスとの関係）と Container（実行基盤・データ保存先）を固定し、必要なときだけコードの責務表へ進みます。全レベルの図を作る必要はありません。

- C4の公式説明: <https://c4model.com/diagrams>
- 初めて読む人: `01` → `02` → `05` → `03` の順
- 判断ロジックを変更する人: `06` と `adr/0002`、`adr/0003` を先に読む
- DBやダッシュボードを変更する人: `04` と `adr/0001` を先に読む

## 文書の正本

このディレクトリは構造と責務の正本です。詳細な設定値・運用手順・予測モデルの仕様は、各ページからリンクする `product/` と `ops/` の専門文書を正本とします。

# 新100％PV予測 実装詳細書 revision 8

長い単一文書を、実装責務ごとの11パッケージへ置き換えた。
正本の入口は [実装パッケージ索引](pv_clear_ghi_new100/README.md)。
この入口に個別の型・分岐・禁止コードを複製しない。

| 必要な情報 | 参照先 |
| --- | --- |
| 担当step・読む順・依存出口 | [README](pv_clear_ghi_new100/README.md)、[index.json](pv_clear_ghi_new100/index.json) |
| 不変条件と変更禁止field | [contracts](pv_clear_ghi_new100/contracts.md) |
| クラス/変数/内部入力/係数/trace | [data-contracts](pv_clear_ghi_new100/data-contracts.md) |
| 保存→採用→projection、世代/失敗境界 | [publication-contracts](pv_clear_ghi_new100/publication-contracts.md) |
| 実sourceと照合するsymbol/保護コード | [boundaries.json](pv_clear_ghi_new100/boundaries.json) |
| 未確定条件・承認・必要証拠 | [gates.json](pv_clear_ghi_new100/gates.json) |
| 実装済み経路・実行証拠・導入前の残件 | [implementation-status](pv_clear_ghi_new100/implementation-status.md) |

AI担当は `python docs/current/architecture/pv_clear_ghi_new100/tools/validate_package.py --step S04`
の出力から必要部分だけを参照する。全step本文を毎回読み込まない。

文書の構造・参照検査PASSは、新方式実装のPASSではない。
承認されたS00〜S10のコードとローカル検証を実装した。全体の実行結果と未完gateは上記状況正本へ分離する。
本番導入・mergeの承認や実クラウド競合実証を、ファイル変更承認・ローカル合格から推測しない。
依存・envキー・公開API・23/03/07制御契約を追加変更しない。

今回の再構造化と再検証の詳細は [報告](../../../artifacts/analysis/pv-new100-package-20261011/report.md)。
実装と比較検証の詳細は [実装報告](../../../artifacts/analysis/pv-new100-implementation-20261011/report.md)。
revision6の原文はローカル検証artifactとして保存しているが、実装指示の正本ではない。

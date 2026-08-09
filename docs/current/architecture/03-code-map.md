# Code Map

## 依存方向

```text
runtime / dashboard / energy_plan / kpnet
                 ↓
        forecasting / operations
                 ↓
 domain / configuration / parsing
```

上位パッケージは下位の共通規則へ依存してよい一方、`domain`、`configuration`、`parsing` は外部サービス・画面・実行基盤へ依存しません。

| パッケージ | 所有する責務 | 最初に読む場所 |
| --- | --- | --- |
| `runtime/` | 時刻スロット、リトライ、実機制御の実行順 | `cloud_job.py` |
| `energy_plan/` | SOC最適化、夜間充電計画、出力組立 | `workflow.py` |
| `forecasting/` | 気象、PV、消費、補正 | `correction.py`, `pv_array.py` |
| `kpnet/` | CSV取得、設定プロファイル、KP-NET通信 | `workflow.py`, `profile_builder.py` |
| `operations/` | CSV取込、DB保存、複数バックエンド | `workflow.py` |
| `dashboard/` | 表示用スライス、取得アダプタ、HTTP | `data.py`, `server.py` |
| `domain/` | SOC・料金・時刻の規則 | 各小モジュール |
| `configuration/` | 環境変数の型変換 | `environment.py` |
| `parsing/` | 数値・CSVの低レベル変換 | `numbers.py` |

## 互換モジュール

`app/` 直下の薄いモジュールは旧import経路との互換性を保つための再公開です。新しいコードは、表に示した所有パッケージから直接importしてください。互換モジュールを削除する前には、リポジトリ内・外部利用者・テストの参照を確認します。

## 変更時の選び方

- 新しい業務規則: `domain/`
- 新しい外部I/O: 対応する領域のアダプタ
- 新しい計画判断: `energy_plan/` または `forecasting/`
- 新しい実行手順: `runtime/`
- 新しい保存形式: `operations/` と必要な `dashboard/`

次: [データとバックエンド](04-data-and-backends.md)

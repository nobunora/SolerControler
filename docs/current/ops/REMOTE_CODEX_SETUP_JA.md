# リモートCodex安全運用セットアップ

この文書は、VSCode拡張からリモートCodexへ開発作業を委譲し、本番デプロイや外部サービス操作はローカルVSCode環境で実行するための運用ルールです。

## 結論

このプロジェクトでは、次の分担を標準にします。

```text
リモートCodex:
  コード修正
  リファクタ
  近傍テスト
  過去データを使ったシミュレーション
  ドキュメント更新
  PRまたは通常コミット

ローカルVSCode/Codex:
  GCPデプロイ
  Cloud Run Job実行
  KP-NETログイン
  KP-NET設定変更
  Secret Manager操作
  本番CSV取得
```

理由は、リモートCodexへ本番認証情報を渡さずに済み、外部サービスのログイン状態や `gcloud` 認証を現在のローカル環境に閉じ込められるためです。

## リモートCodexに許可すること

- GitHub上の `nobunora/SolerControler` を読む。
- `work` ブランチで作業する。
- コードを編集する。
- テストを実行する。
- ローカルにあるテスト用データ、または認証不要のサンプルデータでシミュレーションする。
- Firestore読み取り検証は、リモート環境に明示的な読み取り認証がある場合のみ実行する。
- 日本語で詳しいコミットメッセージを書く。
- PRを作成する。

## リモートCodexに禁止すること

- KP-NETへログインする。
- KP-NETの設定を変更する。
- `scripts/deploy_gcp_jobs.ps1` などの本番デプロイを実行する。
- `gcloud run jobs execute` で本番ジョブを実行する。
- Secret Managerを作成、更新、表示する。
- `.env` の秘密値を表示する。
- Secret値、トークン、パスワード、秘密鍵を出力する。
- `git push --force` または履歴書き換えを行う。
- 本番データを破壊する可能性があるコマンドを実行する。

## Codex Environmentの推奨設定

対象リポジトリ:

```text
nobunora/SolerControler
```

対象ブランチ:

```text
work
```

セットアップスクリプト例:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m pip install -r requirements-runner.txt
```

最初に実行する確認:

```bash
python -m pytest tests/test_soc_cost_optimizer.py -q
python -m pytest tests/test_energy_model.py -q
bash scripts/remote_codex_smoke_test.sh
```

## Secretの方針

原則として、リモートCodexには本番Secretを入れません。

入れないもの:

```text
KP_MONITOR_USERNAME
KP_MONITOR_PASSWORD
GITHUB_TOKEN
SSH_PRIVATE_KEY
サービスアカウント秘密鍵JSON
Google Workspace認証情報
DASHBOARD_BASIC_PASSWORD
```

例外的に入れてよい可能性があるもの:

```text
FIRESTORE_PROJECT_ID=codrivernavi-web-20260510
FIRESTORE_DATABASE_ID=(default)
DATA_BACKEND=sqlite
```

FirestoreをリモートCodexで読む場合は、読み取り専用に近い権限のサービスアカウントを別途用意し、ローカル本番デプロイ用の権限とは分けます。

## VSCodeからの依頼テンプレート

リモートCodexへ作業を投げるときは、次のように依頼します。

```text
workブランチで作業してください。
AGENTS.mdを最初に読み、必要なdocsだけ参照してください。
docs/current/ops/REMOTE_CODEX_SETUP_JA.mdの安全運用ルールに従ってください。

許可:
- コード修正
- 近傍テスト
- 必要なシミュレーション
- 日本語で詳しいコミットメッセージを書くこと

禁止:
- デプロイ
- KP-NETログイン
- KP-NET設定変更
- Secret Manager操作
- .env秘密値の表示
- force push

作業完了時は、変更内容、テスト結果、未確認事項、ローカルVSCode側で実行すべき本番コマンドを報告してください。
```

## ローカルVSCode側の標準手順

リモートCodexの作業が終わったら、ローカルで取り込みます。

```powershell
git fetch origin
git pull --ff-only origin work
python -m pytest
```

問題なければ、ローカルで本番デプロイを実行します。

```powershell
scripts\deploy_gcp_jobs.ps1 -ProjectId codrivernavi-web-20260510
```

必要な場合だけ、本番ジョブを手動実行します。

```powershell
gcloud run jobs execute solar-battery-23 --project codrivernavi-web-20260510 --region us-central1
gcloud run jobs execute solar-battery-03 --project codrivernavi-web-20260510 --region us-central1
gcloud run jobs execute solar-battery-07 --project codrivernavi-web-20260510 --region us-central1
```

## リモートCodexが詰まったときの判断

リモートCodexが次の状態になったら、無理に進めずローカルへ引き継ぎます。

- 認証情報がなくてGCPまたはFirestoreにアクセスできない。
- KP-NETログインが必要になった。
- Secretの値が必要になった。
- Windows専用スクリプトが必要になった。
- デプロイ判断が必要になった。
- 本番データの更新が必要になった。

この場合、リモートCodexは「必要なローカル実行コマンド」と「なぜローカル実行が必要か」だけを報告します。

## 期待する効果

- 本番Secretをリモートに置かない。
- 本番操作ミスを減らす。
- リモートCodexの作業範囲が狭まり、トークン消費が安定する。
- デプロイや外部ログイン失敗の調査をローカルに閉じ込められる。
- コード変更と本番操作の責任境界が明確になる。

# Codex Rules Split Version

このzipは、Codex向けの開発ルールを分割した版です。

## ファイル構成

```txt
AGENTS.md
docs/
  design_intent_rules.md
  code_review.md
  bad_patterns.md
  report_template.md
```

## 使い方

プロジェクトのルートにAGENTS.mdを置く。

docsディレクトリも同じルートに置く。

Codexには、まずAGENTS.mdを読ませる。

実装前に意図や設計思想が不明な場合は、docs/design_intent_rules.mdを参照させる。

実装後の自己レビューでは、docs/code_review.mdを参照させる。

AI生成コードにありがちな悪いパターンを潰したい場合は、docs/bad_patterns.mdを参照させる。

最終報告やPR説明には、docs/report_template.mdを使わせる。

## 狙い

このルールは、AIが単にコードを生成するだけでなく、設計意図・既存文脈・レビュー可能性を残すためのものです。

特に次を重視しています。

- 不明点を推測で埋めない。
- 設計思想が不明なら人間に質問する。
- 過去チャット履歴やメモリを参照できるなら、利用可能な範囲で参照する。
- 参照できない場合は、参照できないことを明示する。
- 動くコードではなく、保守できるコードを書く。
- 「なぜそうしたか」を残す。
- AI生成コードにありがちな悪いパターンを個別に潰す。
- 公開API名、DBカラム名、設定キー、環境変数名などの外部契約名は、命名改善だけを理由に変更しない。

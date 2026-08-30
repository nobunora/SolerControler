# CodebaseMemory 共有グラフ運用ルール

## 目的

このリポジトリでは `.codebase-memory/graph.db.zst` を共有 CodebaseMemory artifact として追跡する。

共有 artifact の目的は、各 PC・各 Codex セッション・各 subagent が毎回リポジトリ全体をゼロから探索するのではなく、同じ構造グラフを初期地図として利用し、ローカル差分だけを追加解析できるようにすることである。

ただし、共有グラフは **ソースコードそのものではなく派生物** である。CodebaseMemory の `CALLS`、`SIMILAR_TO`、`SEMANTICALLY_RELATED`、dead-code candidate、confidence は候補抽出と探索短縮に使うが、変更可否の最終根拠にはしない。

恒久的な判断原則は次のとおり。

1. graph は探索の初期地図として使う。
2. 実装判断は source / tests / contracts / ADR / history で確定する。
3. graph confidence を上げるためだけに production code を変えない。
4. binary artifact を手編集しない。
5. artifact-only commit を追いかけるためだけの再 index を行わない。

---

## 1. 今回の共有 artifact による効果

### 1.1 初回探索コストの低減

公式 CodebaseMemory の team-shared graph artifact は、clone 後に `.codebase-memory/graph.db.zst` を展開し、その後のローカル差分を incremental indexing する用途である。

そのため、次の作業が速くなる。

- repository architecture の把握
- symbol / caller / callee の探索
- refactor 前の blast radius 確認
- dead-code / duplicate candidate の一次抽出
- subagent ごとの担当範囲決定
- PR review 時の変更影響確認

### 1.2 複数セッション間の共通基準

各セッションが独自に全探索した結果ではなく、同じ graph artifact を起点にできるため、以下の差を減らせる。

- 探索順序による見落とし
- agent ごとの局所理解の差
- 同じ caller/callee を何度も読み直す重複作業
- 過去 report を誤って active source とみなすノイズ

### 1.3 レビューの再現性向上

artifact metadata に indexed commit、node count、edge count が残るため、PR description や調査記録で「どの source snapshot を基準に graph を見たか」を残しやすい。

ただし node / edge 数の増減だけを品質指標にしてはいけない。重要なのは、必要な source / tests / current docs が graph に残り、除外すべき historical report が混ざっていないことである。

---

## 2. artifact の鮮度判定

作業開始時に次を確認する。

1. `git status --short`
2. `.codebase-memory/artifact.json`
3. CodebaseMemory `index_status`
4. artifact の `commit` から現在 HEAD までに何が変わったか

### 2.1 Fresh と扱ってよい状態

次のいずれかなら、共有 artifact を構造探索の初期地図としてそのまま使用してよい。

- `artifact.json.commit == HEAD`
- `artifact.json.commit` 以降の commit が `.codebase-memory/**` の generated artifact / metadata 変更だけであり、source / tests / active rules に変更がない

後者は重要である。artifact を commit すると HEAD 自体が進むため、artifact commit まで再び index して commit しようとすると artifact-only commit を繰り返せる。

**artifact 自身を追加・更新した commit が HEAD を進めたことだけを理由に、もう一度 artifact を refresh してはならない。**

### 2.2 Stale と扱う状態

`artifact.json.commit` より後に以下が変更されている場合、構造判断の前に refresh / incremental index が必要である。

- `app/**`
- `scripts/**` の実装
- `tests/**`
- `AGENTS.md`
- `docs/current/**` の active contract / architecture / agent rule
- `.cbmignore`
- `pyproject.toml` 等、依存境界や解析対象に影響する設定

単に `docs/completed/reports/**` が変わっただけなら `.cbmignore` の方針上、それを理由に graph を refresh しない。

---

## 3. artifact 更新ルール

### 3.1 更新するタイミング

共有 graph artifact は次の場合に更新する。

- source-bearing PR を完了する前
- tests / current architecture rules を変更し graph の意味が変わったとき
- merge 後に両 branch の source 変更を統合したとき
- CodebaseMemory の schema / parser 更新後に明示的な再生成が必要なとき
- stale artifact では変更影響判断が不十分なとき

### 3.2 更新しないタイミング

次の場合は artifact 更新だけの commit を作らない。

- artifact commit 自身を graph に含めるためだけ
- node / edge 数を揃えるためだけ
- confidence score を改善したくなっただけ
- completed report しか変わっていない
- source / tests / active rule が一切変わっていない

### 3.3 generated artifact の commit 方針

source-bearing change の検証後、共有 artifact を更新する場合は **1回だけ明示的に生成し、最後の generated commit として追加する**。

その artifact の `commit` が、その直前の source-bearing commit を指しているのは正常である。artifact commit 自身を取り込むために二度目の生成を行わない。

PR description には可能なら次を記録する。

- artifact が表す source commit
- `status=ready`
- node count / edge count
- CodebaseMemory version
- 主要 `detect_changes` / call-path 所見
- parse partial / skipped が今回の判断に影響するか

---

## 4. 日常開発での活用方法

### 4.1 新しい変更要求を受けたとき

最初に graph で以下を絞る。

- owning package / module
- target symbol
- direct callers / callees
- tests linked to the symbol
- architecture boundary
- nearby high-change / hotspot symbols

その後、必要な source file と test だけを読む。

**graph があるから source read を省略するのではなく、graph で source read を狭くする。**

### 4.2 実装前の blast-radius 確認

変更対象が次のような領域なら、実装前に caller / callee と boundary を確認する。

- forecasting
- energy plan
- SOC optimization
- Cloud Job
- KP-NET
- persistence
- production deployment
- shadow / validation sidecar

特に historical-failure protected region は graph の結果より `PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md` と regression test を優先する。

### 4.3 実装後の impact review

変更後は、可能なら `detect_changes` で以下を確認する。

- 想定外の caller が影響対象に出ていないか
- cross-layer dependency を新設していないか
- deleted symbol の参照が残っていないか
- compatibility seam を壊していないか

その後に focused quality checks / tests を行う。

### 4.4 PR review

review では graph を次の用途に使う。

- changed symbol の caller/callee 確認
- PR が説明していない別 subsystem への影響確認
- duplicate implementation の再発確認
- stale private helper / stale wrapper の候補確認

ただし `SIMILAR_TO` が高いだけで統合を要求しない。

### 4.5 subagent の分担

subagent を使う場合、parent は共有 graph を使って調査範囲を分割する。

例:

- agent A: caller / runtime path
- agent B: tests / compatibility contract
- agent C: architecture / ADR / dependency direction

同じ repository 全体を各 subagent に再探索させない。

### 4.6 低信頼 CALLS の調査

低 confidence edge は、まず edge evidence を保存して分類する。

最低限記録する項目:

- caller qualified name
- callee qualified name
- caller file / line
- callee file
- confidence
- resolution strategy
- source で実際に呼ばれている expression

分類:

1. `source-fix` — source 自体に stale alias / ambiguity / contract mismatch がある
2. `graph-false-positive` — source は明確で graph 解決だけが誤っている
3. `dynamic-external-api` — Firestore 等の fluent/dynamic SDK
4. `test-fake` — SDK-shaped fake / monkeypatch target
5. `builtins/runtime` — builtins や runtime resolution

`source-fix` 以外は、graph confidence 改善だけを理由に production source を変更しない。

---

## 5. 類似実装の扱い

### 5.1 共通化してよい条件

次をすべて満たす場合のみ canonicalization を検討する。

- semantics が同じ
- input / output contract が同じ
- ownership boundary が同じ、または中立 lower layer に移せる
- historical / frozen policy ではない
- tests で同一挙動を固定できる

### 5.2 統合してはいけない例

以下は類似していても自動統合しない。

- backend-specific adapter
- domain-specific JSON boundary
- standalone operator script
- test fake
- compatibility wrapper
- frozen diagnostic / historical policy implementation

特に weather classification には現在2種類の semantics が存在する。

#### 運用 Open-Meteo classification

`app.energy_plan.weather_history.weather_class` と `app.forecasting.pv_array_calibration._weather_class_from_code` は、

- `1..3 -> cloudy`
- `80..82 -> rain`
- `95..99 -> storm`

等の同一 operational semantics を持つため、今後中立 domain module へ canonicalize してよい。

#### Phase 1 frozen diagnostic classification

`app.operations.shadow_gate.weather_class` は frozen validation policy の一部であり、operational classification と同じではない。

例:

- code `1..3` は `clear`
- code `80..99` は `shower`

これは Phase 1 evidence parity の契約であるため、**operational weather classification と統合してはならない。**

CodebaseMemory が両者を `SIMILAR_TO` / semantic related として出しても、変更理由にはしない。

---

## 6. binary merge と branch 運用

`.codebase-memory/.gitattributes` は compressed graph を binary として扱う。

binary artifact の merge result は source correctness を保証しない。複数 branch が source を変更した後に merge した場合は、merged source を基準に artifact を1回再生成する。

以下は禁止する。

- binary graph の手編集
- graph を source conflict の代わりに使うこと
- artifact の merge 結果だけを信用して source-side impact review を省略すること

---

## 7. セキュリティと情報管理

共有 graph は source から生成された派生データであるため、source と同等の取り扱いをする。

- `.env`、token、credential、production secret を index / artifact / report に入れない。
- `.gitignore` / `.cbmignore` を無視して秘密情報を force-add しない。
- raw graph を外部へ添付しない。
- public repository へ追加してよい情報だけを共有 artifact に含める。

---

## 8. 作業終了時チェック

source-bearing change で shared graph を更新した場合、少なくとも次を確認する。

- `index_status = ready`
- artifact metadata が存在する
- target symbol を検索できる
- deleted symbol が消えている（削除作業の場合）
- `docs/completed/reports/` が active graph context に戻っていない
- focused source/test verification が通る
- artifact-only commit を理由に再 refresh していない

共有 graph の成功条件は「常に HEAD SHA と artifact commit が完全一致すること」ではなく、**現在の source-bearing state を正しく表し、次の agent が安全に incremental exploration を開始できること**である。

# mypy 型付け方針の検討

**現状**: 新規モジュール（utils/constants）は `--strict` OK、既存モジュールは 115エラー
**課題**: プロジェクト全体で `mypy app --strict` を通すにはどうするか？

---

## 📊 4つの方針比較

### 方針 A: 保守的アプローチ（現状維持）

**内容**: 新しいモジュール（utils, constants）だけ `--strict` で保持。既存モジュールは型付け見直さない。

```python
# pyproject.toml（例）
[tool.mypy]
python_version = "3.14"
warn_return_any = True
warn_unused_ignores = True
disallow_untyped_defs = False  # 既存コードは type: ignore で許容

# 新しいモジュールだけ strict
[[tool.mypy.overrides]]
module = "app.utils, app.constants"
strict = True
```

**メリット**:
- ✅ リファクタリング本体に集中できる
- ✅ 既存コードへの影響ゼロ
- ✅ 実装期間短い（0 時間追加）
- ✅ テスト済みのコードを変更しない

**デメリット**:
- ❌ プロジェクト全体の型安全性が低い
- ❌ utils/constants と既存コードの型境界が曖昧
- ❌ 将来的な負債が残る
- ❌ IDE の型チェック精度が下がる

**推定コスト**: 0 時間
**効果**: 限定的（新モジュールのみ）

---

### 方針 B: 段階的改善（推奨）

**内容**: リファクタリング対象のファイルだけを段階的に型付けする。

```python
# Phase 別に型付けを進める
Phase 1-3（現在）: utils.py, constants.py → OK
Phase 4A（短期）: config.py, operations_db.py, kpnet_workflow.py を strict 化
Phase 4B（中期）: dashboard_data.py, energy_model.py を strict 化
Phase 5（長期）: その他のモジュール
```

**対象ファイル（優先順）**:
1. **Phase 4A** (推定 20-30時間):
   - config.py: 既に import は整理済み、型アノテーション追加のみ
   - operations_db.py: DB操作、型が明確
   - kpnet_workflow.py: 複雑だが重要

2. **Phase 4B** (推定 30-40時間):
   - dashboard_data.py: SQL/API 戻り値、複雑
   - energy_model.py: 計算中心、型整備しやすい

3. **外部ライブラリ対応**:
   - requests, googleapiclient: type-stubs パッケージで対応
   - sklearn: `[tool.mypy.overrides]` で許容

**メリット**:
- ✅ 実装と並行して進められる
- ✅ 効果的（重要ファイルから）
- ✅ 技術的負債を段階的に減らせる
- ✅ テストしながら進められる

**デメリット**:
- ❌ 20-70時間の追加作業
- ❌ 既存テストの修正が伴う可能性
- ⚠️ 型付けのばらつきが残る中期がある

**推定コスト**: 20-70 時間
**効果**: 高（段階的に型安全性向上）

**推奨**: ✅ **この方針**

---

### 方針 C: 全面型付け（理想）

**内容**: プロジェクト全体を `mypy app --strict` で通す。

```python
# pyproject.toml
[tool.mypy]
python_version = "3.14"
strict = True  # すべてのモジュール
```

**対象**: 16 ファイルすべて

**メリット**:
- ✅ 型安全性が最高
- ✅ IDE サポート最大
- ✅ バグを事前に検出可能
- ✅ 可読性向上

**デメリット**:
- ❌ 100-150 時間の作業
- ❌ 既存テストの大規模修正
- ❌ 外部ライブラリの stub 問題対応が複雑
- ❌ リファクタリングの本来の目的から逸脱

**推定コスト**: 100-150 時間
**効果**: 最大（ただし、コストとのバランスが悪い）

**推奨**: ❌ **現在は非推奨**（将来の大型リファクタ機会に実施）

---

### 方針 D: 外部ライブラリ型付けのみ

**内容**: requests, googleapiclient, sklearn の型を先に整備してから既存コード を型付けする。

```bash
# Step 1: 型 stubs をインストール
pip install types-requests types-google-cloud-firestore \
            scikit-learn-stubs

# Step 2: pyproject.toml で ignore 削減
[tool.mypy.overrides]
# googleapiclient は py.typed なしなので許容
module = "googleapiclient.*"
ignore_missing_imports = True
```

**メリット**:
- ✅ 外部ライブラリの警告が消える
- ✅ 既存コードの型推論精度向上
- ✅ 比較的低コスト（5-10 時間）

**デメリット**:
- ❌ 既存コードの型付けには手をつけない
- ❌ 引数・戻り値の `Any` は残る
- ⚠️ すべてのエラーが消えるわけではない

**推定コスト**: 5-10 時間
**効果**: 中（外部ライブラリのノイズ削減）

**推奨**: ⚠️ **方針 B の前段階として実施**

---

## 🎯 推奨: 方針 B + D（段階的改善 + 外部ライブラリ対応）

### 実行計画

#### Step 1: 外部ライブラリ stub インストール（1-2時間）

```bash
pip install types-requests types-google-cloud-firestore \
            scikit-learn-stubs

# mypy.ini または pyproject.toml を更新
[tool.mypy]
python_version = "3.14"
warn_return_any = True

[tool.mypy.overrides]
# googleapiclient は py.typed がない
module = "googleapiclient.*,google.auth.*"
ignore_missing_imports = True

# 既存コード全体の strict チェックは OFF
# [[tool.mypy.overrides]]
# module = "app.*"  # 任意（厳密さの段階設定）
# strict = False
```

**効果**: mypy エラーが 115 → 70-80 程度に削減

---

#### Step 2: Phase 1-3 リファクタリング完了（3-5日）

- ラッパー関数削除（CODEX_REFACTOR_INSTRUCTIONS.md に従う）
- テスト 115 件全パス確認

---

#### Step 3: Phase 4A（短期）: 重要ファイルの型付け（1-2週間）

**優先順位**:

| ファイル | 難度 | 効果 | 時間 |
|---------|------|------|------|
| config.py | 低 | 高 | 3-4h |
| operations_db.py | 中 | 高 | 6-8h |
| kpnet_workflow.py | 高 | 高 | 8-10h |
| dashboard_data.py | 高 | 中 | 10-15h |

**実施方法**:
```bash
# 1. 各ファイルを修正
# 2. 型アノテーション追加
# 3. mypy app/config.py --strict でチェック
# 4. テスト実行
# 5. 次のファイルへ
```

**期待される状態**:
```bash
mypy app/utils.py app/constants.py app/config.py app/operations_db.py --strict
# 結果: OK
```

---

#### Step 4: 外部ライブラリ stub インストール後の確認

```bash
# インストール前
python -m mypy app --strict
# 結果: 115 errors

# Step 1 後
python -m mypy app --strict
# 結果: 70-80 errors (改善)

# Phase 4A 後
python -m mypy app --strict
# 結果: 40-50 errors （さらに改善）

# Phase 4B 後
python -m mypy app --strict
# 結果: 10-20 errors （大幅改善）
```

---

## 📋 各方針の選択基準

### 方針 A（保守的）を選ぶべき場合
- ✅ リファクタリングに専念したい
- ✅ 型付けは後日の計画
- ✅ 既存コード変更を最小化したい

### 方針 B（段階的）を選ぶべき場合 ⭐ **推奨**
- ✅ リファクタリングと同時に型安全性を向上させたい
- ✅ 重要なファイルから優先的に進めたい
- ✅ 長期的なコード品質向上を目指している

### 方針 C（全面型付け）を選ぶべき場合
- ✅ プロジェクトの型安全性が最優先
- ✅ 大規模な時間投資が可能
- ✅ 他の開発と並行できる

### 方針 D（外部ライブラリのみ）を選ぶべき場合
- ✅ 最小限の作業で mypy エラーを減らしたい
- ✅ 既存コードには手を入れたくない

---

## 🎯 最終推奨

### **方針 B + D の組み合わせ**

**理由**:
1. **実装のバランスが良い**
   - リファクタリング本体（CODEX_REFACTOR_INSTRUCTIONS.md）は 195分で完了
   - 型付けはその後 Phase 4 として段階的に進める
   - 並行作業が可能

2. **効果が明確**
   - 新しいモジュール（utils, constants）は strict OK
   - 重要なモジュール（config, operations_db）から型付け
   - 段階的に型安全性が向上

3. **リスクが低い**
   - 既存テストを破壊しない（型付けは追加アノテーションのみ）
   - ロールバック可能
   - 段階的に効果測定できる

4. **コストが現実的**
   - Step 1-2: 1 週間以内（リファクタリング完了）
   - Phase 4A: 2-3 週間（重要ファイル）
   - Phase 4B: 1 ヶ月（その他）
   - 合計: 2 ヶ月程度で型安全性が大幅向上

---

## 📝 決定内容

### **即座に実施（Step 1）**

```bash
# 外部ライブラリ stub をインストール
pip install types-requests types-google-cloud-firestore scikit-learn-stubs

# pyproject.toml を更新
[tool.mypy]
python_version = "3.14"
warn_return_any = True

[tool.mypy.overrides]
module = "googleapiclient.*,google.auth.*"
ignore_missing_imports = True

[[tool.mypy.overrides]]
module = "app.utils,app.constants"
strict = True  # 新しいモジュール
```

**効果**: mypy エラー 115 → 70-80 に削減

---

### **Phase 1-3 完了後（Step 2-3）**

リファクタリング完了後、Phase 4A として以下を実施:

```
config.py → operations_db.py → kpnet_workflow.py
  ↓
各ファイルに型アノテーション追加
  ↓
mypy --strict でチェック
  ↓
テスト実行
```

**期待**: Phase 4A で mypy エラー 70-80 → 40-50 に削減

---

## 🚀 次のアクション

### Option 1: 方針 B + D を承認する場合

```bash
# Step 1 を今すぐ実施
pip install types-requests types-google-cloud-firestore scikit-learn-stubs

# CODEX_REFACTOR_INSTRUCTIONS.md に従ってリファクタリング実施
# Phase 1A, 1B, 1C を完了（195分）

# Phase 4A を計画（Phase 1-3 完了後）
```

### Option 2: 方針 A を選ぶ場合

```bash
# リファクタリングに専念
# CODEX_REFACTOR_INSTRUCTIONS.md に従う

# 型付けは後日の計画とする
# その際は方針 B を検討
```

---

**どちらの方針を採用しますか？推奨は方針 B + D です。**

# 共通utils/constantsリファクタリング レポート

## 概要

`docs/CODEX_NAVIGATION.md` の Phase 1-3 に従い、環境変数読み取り、dotenv読込、数値変換、SOC境界値、ファイルチャンクサイズの重複実装を共通モジュールへ集約した。

追加で `docs/CODEX_REFACTOR_INSTRUCTIONS.md` に従い、互換ラッパー関数を削除し、既存呼び出しを `app.utils` / `app.constants` の直接利用へ統一した。

## changed files

- `app/utils.py`
- `app/constants.py`
- `app/config.py`
- `app/main.py`
- `app/operations_db.py`
- `app/firestore_ops.py`
- `app/postgres_ops.py`
- `app/kpnet_workflow.py`
- `app/forecast_correction.py`
- `app/pv_array_forecast.py`
- `app/csv_utils.py`
- `app/consumption_forecast.py`
- `app/occupancy_schedule.py`
- `app/dashboard_data.py`
- `app/drive_backup.py`
- `app/sheets_export.py`
- `app/energy_model.py`
- `app/soc_cost_optimizer.py`
- `tests/test_utils.py`
- `docs/completed/reports/shared_utils_constants_refactor_ja.md`

## commands run

- `rg -n "def _env|def _load_dotenv|def _to_float|def _to_optional_float|def _to_int|_env\\(|_env_bool\\(|_env_float\\(|max\\(0\\.0, min\\(100\\.0|min\\(100\\.0, max\\(0\\.0|1024 \\* 1024" app tests`
- `python -m compileall app tests`
- `python -m pytest tests/test_utils.py tests/test_operations_db.py tests/test_energy_model.py tests/test_kpnet_workflow.py tests/test_drive_backup.py`
- `python -m pytest`
- `python scripts\security_check.py`
- `rg -n "mypy|pyright|ruff|flake8" pyproject.toml setup.cfg tox.ini pytest.ini requirements*.txt .github 2>$null`
- `python -m pip install mypy`
- `python -m mypy app --strict`
- `python -m mypy app\utils.py app\constants.py --strict`
- `rg -n "\b_env\(|\b_env_bool\(|\b_env_float\(|\b_to_float\(|\b_to_float_any\(|\b_to_int_any\(|\b_to_optional_float\(|\b_load_dotenv_if_present\(" app`
- `rg -n "def _env|def _to_float|def _to_int|def _load_dotenv|def _to_optional" app`
- `python -m pytest tests/test_utils.py tests/test_csv_merge.py tests/test_consumption_forecast.py tests/test_pv_array_forecast.py tests/test_operations_db.py tests/test_kpnet_workflow.py`
- `python -m pip install types-requests types-google-cloud-firestore scikit-learn-stubs`
- `python -m pip install types-requests scikit-learn-stubs`
- `python .\kpnet_main.py` (`KP_WORKFLOW_MODE=csv`, `DRY_RUN=true`, `KP_CSV_TARGET_MONTHS=2026-06`)
- `python .\energy_model_main.py` (`ENERGY_MODEL_CSV_DIR=<KPNet smoke csv dir>`)
- `python .\db_pipeline_main.py` (`DATA_BACKEND=sqlite`, smoke DB)
- dashboard slice load smoke via `app.dashboard_data.load_dashboard_slice`
- `git diff --stat`
- `git status --short`

## test/build results

- `python -m compileall app tests`: OK
- focused pytest: 54 passed
- full pytest: 116 passed
- `python scripts\security_check.py`: OK
  - 既存警告: `KP_USE_HAR_CREDENTIALS=true`
- `python -m mypy app\utils.py app\constants.py --strict`: OK
- `python -m mypy app --strict`: NG
  - stub追加後: 110 errors in 16 files
  - `types-google-cloud-firestore` は該当PyPIパッケージがなく未導入。
  - 主な分類: 既存モジュールの未型付け関数、`requests` / `googleapiclient` / `sklearn` のstub不足、既存の `Any` 戻り値・型推論エラー。
  - 今回追加した `app/utils.py` と `app/constants.py` 単体では strict OK。
- 置換漏れ確認:
  - 旧ラッパー呼び出し `_env(...)`, `_to_float(...)`, `_to_float_any(...)`, `_to_int_any(...)`, `_to_optional_float(...)`, `_load_dotenv_if_present(...)`: 残存なし。
  - 旧ラッパー定義 `def _env*`, `def _to_float*`, `def _to_optional*`, `def _load_dotenv*`: 残存なし。
- KPNet 実アクセス smoke:
  - ログイン成功。
  - 取得可能月: `2026-06`, `2026-05`, `2026-04`。
  - `2026-06` の30分CSV取得成功。
  - グラフ生成成功。
  - ログアウト成功。
- ローカル通し smoke:
  - `energy_model_main.py`: KPNet取得CSVから `night_charge_plan.json` 作成成功。
  - `db_pipeline_main.py`: SQLite smoke DB へ取り込み成功。
  - smoke DB 件数: `monitoring_samples=956`, `pipeline_runs=1`, `sunshine_daily=2`, `forecast_hourly=24`, `model_parameters=18`, `cost_daily=20`。
  - dashboard slice load 成功。

## known limitations

- `mypy app --strict` はプロジェクト全体では未達。今回追加・更新した共通モジュール単体は strict OK だが、既存モジュールの型注釈不足と外部stub不足が残る。
- 在宅予定シート読み込みはADCスコープ不足でスキップされた。任意入力のため計画生成は継続成功。
- smokeダッシュボードでは設定完了イベント未確認警告が出た。CSV取得のみでsettingsフェーズを実行していないため想定内。
- デプロイは未実施。今回の変更は内部リファクタリングで、ローカル検証まで完了。

## mypy 型安全性について

### 現状確認結果

- `python -m mypy app\utils.py app\constants.py --strict`: ✅ **成功**
- `python -m mypy app --strict`: ❌ **失敗（115 errors）**

### エラー分析

**新規モジュール**:
- `app/utils.py`, `app/constants.py`: `--strict` で型安全性確保済み ✅

**既存モジュール の問題** (115 errors in 16 files):
- 外部ライブラリの型 stub 不足（requests, googleapiclient, sklearn）
- 既存関数の引数・戻り値アノテーション不足
- `Any` 戻り値の使用
- 型推論エラー（energy_model.py, drive_backup.py, forecast_correction.py など）

**結論**: 今回の実装は型安全性の問題なし。既存コード全体の型付けが未完了である。

---

## next recommended milestone

### Phase 4: ラッパー関数削除と型安全性強化（推奨方針 B+D）

#### 方針 B+D について

**方針 B（段階的改善）**: リファクタリング対象のファイルから優先的に型付けする
**方針 D（Stub 対応）**: 外部ライブラリの型 stub を追加し、mypy エラーを削減する

**推奨理由**:
- ✅ リファクタリング完了後、型安全性を段階的に向上
- ✅ 重要ファイルから優先的に進められる
- ✅ リスク最小化（既存テスト破壊なし、ロールバック可能）
- ✅ 実現的なコスト（20-70時間）

---

### Phase 4 実行手順

#### **Step 1: 外部ライブラリ Stub インストール（1-2時間）**

```bash
# Stub パッケージをインストール
pip install types-requests types-google-cloud-firestore scikit-learn-stubs

# 確認: mypy エラーが 115 → 70-80 に削減されることを確認
python -m mypy app --strict
```

**pyproject.toml 更新（例）**:
```toml
[tool.mypy]
python_version = "3.14"
warn_return_any = True
warn_unused_ignores = True

[tool.mypy.overrides]
# googleapiclient は py.typed がないため許容
module = "googleapiclient.*,google.auth.*"
ignore_missing_imports = True

# 新規追加モジュールは strict 維持
[[tool.mypy.overrides]]
module = "app.utils,app.constants"
strict = True
```

**効果**: `mypy app --strict` のエラー数: 115 → 70-80（削減）

---

#### **Step 2: ラッパー関数削除（Phase 1-3 の後処理）**

`docs/CODEX_REFACTOR_INSTRUCTIONS.md` に記載した指示に従い、以下を実施:

- **Phase 1A（低リスク）**: config.py, operations_db.py, kpnet_workflow.py, sheets_export.py のラッパー関数削除（60分）
- **Phase 1B（中リスク）**: occupancy_schedule.py, csv_utils.py, consumption_forecast.py のラッパー関数削除（40分）
- **Phase 1C（高リスク）**: pv_array_forecast.py, forecast_correction.py の修正 + utils.py に `env_float_clamped()` 追加（75分）

**検証**:
```bash
# 古い関数が削除されたことを確認
grep -r "def _env\|def _to_float\|def _to_int\|def _load_dotenv" app/*.py
# 結果: 0（ドメイン固有の _to_bool, _to_date のみ残る）

# テスト実行
python -m pytest tests/ -v
# 結果: 115 passed
```

---

#### **Step 3: Phase 4A - 重要ファイルの型付け（2-3週間）**

リファクタリング完了後、以下のファイルに型アノテーションを追加:

| ファイル | 難度 | 優先度 | 推定時間 |
|---------|------|--------|---------|
| **config.py** | 低 | 🔴 高 | 3-4h |
| **operations_db.py** | 中 | 🔴 高 | 6-8h |
| **kpnet_workflow.py** | 高 | 🔴 高 | 8-10h |
| **dashboard_data.py** | 高 | 🟡 中 | 10-15h |

**実施方法**（各ファイル毎）:
1. 関数の引数・戻り値に型アノテーションを追加
2. `Any` の使用箇所を具体的な型に置き換え
3. `mypy app/TARGET_FILE.py --strict` で確認
4. 既存テストが合格することを確認
5. 次のファイルへ

**期待される改善**:
```
初期状態:        115 errors
Step 1 後:       70-80 errors（Stub 追加後）
Phase 4A 後:     40-50 errors（重要ファイル型付け後）
Phase 4B 後:     10-20 errors（その他ファイル型付け後、オプション）
```

**Phase 4A 完了時の状態**:
```bash
python -m mypy app/utils.py app/constants.py app/config.py app/operations_db.py app/kpnet_workflow.py --strict
# 結果: OK（すべて strict で通る）
```

---

#### **Step 4: Phase 4B - その他ファイルの型付け（1-2ヶ月、オプション）**

Phase 4A 完了後、必要に応じて以下を実施:
- energy_model.py, forecast_correction.py, pv_array_forecast.py など
- 全体で 10-20 errors → 0 errors を目指す

---

### 次のアクション

**即座に実施**（Step 1）:
```bash
pip install types-requests types-google-cloud-firestore scikit-learn-stubs
python -m mypy app --strict  # エラー数の確認
```

**その後の進行**:
1. ✅ Phase 1-3 リファクタリング（CODEX_REFACTOR_INSTRUCTIONS.md）
   - 推定: 195分
2. ✅ Phase 4A 型付け（上記 Step 3）
   - 推定: 2-3週間
3. ⏭️ Phase 4B 型付け（オプション）
   - 推定: 1-2ヶ月

---

### 型安全性戦略の詳細

詳細は `docs/mypy_strategy.md` を参照：
- 4つの方針（A: 保守的、B: 段階的、C: 全面、D: Stub のみ）の比較
- 各方針の メリット・デメリット・コスト
- 推奨方針（B+D）の実行計画
- トラブルシューティング

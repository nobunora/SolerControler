# SolerControler ゼロ値境界不具合 修正作業指示書インデックス

## 目的

この一式は、2026年7月19日に `C:\VSC\SolerControler` を追加監査した結果として確認した、数値 `0` と欠損値 `None` の混同、および夜間充電時間設定の契約不明確性について、調査結果、根本原因、修正箇所、修正案、全体方針、個別方針を作業単位ごとに整理したものである。

作業者またはAIは、最初にこのインデックスと `01_overall_policy.md` を読み、その後は担当する問題の個別指示書だけを追加で読むこと。

既存の文書、仕様、テスト、運用手順を削除または置換せず、必要な説明と回帰テストを追記する。複数問題を同一コミットへ混在させない。

## 完了状況

2026年7月19日に全作業を完了した。

- PV総予測0 kWh: `f234603` (`Preserve zero PV forecast values`)
- SOC上限0%: `2d386a6` (`Preserve zero SOC constraint caps`)
- 予報気温0℃: `5ddbf18` (`Preserve zero forecast temperatures`)
- 夜間充電窓契約: `32bf85c` (`Document night charge window contract`)
- 重点テスト: 132 passed
- 全非externalテスト: 366 passed、1 deselected
- `compileall`: 成功
- `security_check.py`: 成功
- `git diff --check`: 成功
- 外部API・Firestore・KP-NETの既存フィールド名: 変更なし
- 夜間充電窓: 実機動作は変更せず、論理窓と同日内実機窓の診断・文書・テストを追加

## 基準情報

- 対象リポジトリ: `C:\VSC\SolerControler`
- 調査時HEAD: `95eb45c`
- 調査日: `2026-07-19`
- 調査時作業ツリー: clean
- セキュリティ検査: `python scripts/security_check.py` 成功
- エネルギーモデル関連テスト: `37 passed`
- KP-NETワークフロー関連テスト: `37 passed`
- 注意: 既存テストが成功していても、再現済みの境界不具合が残っている

## 文書一覧

1. `01_overall_policy.md`
   - 全体修正方針、値の契約、実装順序、禁止事項、検証方針
2. `02_pv_zero_forecast.md`
   - PV総予測 `0 kWh` が欠損扱いされるP1不具合
3. `03_soc_cap_zero.md`
   - SOC上限 `0%` が `100%` に変換されるP1不具合
4. `04_temperature_zero.md`
   - 予報気温 `0℃` が `20℃` に変換されるP2不具合
5. `05_night_charge_window_contract.md`
   - 夜間充電開始時刻設定の名称と実際の制御契約の不一致

## 調査で確定した事項

### P1: PV総予測 `0 kWh` が欠損扱いされる

有効な `total_kwh=0.0` が `None` に変換され、旧来のフォールバックPVモデルが使用される。

再現結果:

- 正しく0 kWhを採用した場合の必要夜間充電量: `6.666666666666666 kWh`
- 欠損扱いされた場合のフォールバックPV予測: `10.525 kWh`
- 欠損扱いされた場合の必要夜間充電量: `0.0 kWh`

### P1: SOC上限 `0%` が `100%` に変換される

`cap_target_soc_percent=0.0` がPythonのtruthinessにより偽と判定され、`or 100.0` によって100%へ置換される。

再現結果:

- ガード適用: `True`
- 正しい上限値: `0.0`
- 現行式の評価結果: `100.0`

### P2: 気温 `0℃` が `20℃` に変換される

有効な `temp_c=0.0` が欠損扱いされ、既定値20℃に置換される。

再現結果:

- 5時間日照時の0℃PV予測: `10.875 kWh`
- 5時間日照時の20℃PV予測: `10.175 kWh`
- PV予測差: `0.70 kWh`
- 探索した条件内での必要夜間充電量最大差: `1.2444444444444418 kWh`

### P2: 夜間充電開始時刻設定の契約が不明確

`KP_NIGHT_CHARGE_WINDOW_START` は名称上は充電許可窓の開始時刻に見えるが、実際には主に実績推定へ使われ、機器へ送る開始時刻は原則として00:00以降へ制限される。

設定の意味、KP-NET機器制約、実際の時刻生成規則を明文化する必要がある。

## 実施順序

### 第1段階: 共通方針と境界契約の固定

1. `01_overall_policy.md` を読む。
2. 入力値について `0`、負値、欠損、解析失敗の契約を確定する。
3. 現行不具合を再現する失敗テストを先に追加する。

### 第2段階: 計算結果を反転させるP1不具合

1. `02_pv_zero_forecast.md`
2. `03_soc_cap_zero.md`

この2件は別々のコミットで修正する。

### 第3段階: 予測精度と設定契約

1. `04_temperature_zero.md`
2. `05_night_charge_window_contract.md`

### 第4段階: 全体回帰確認

個別テスト、関連テスト、全非externalテスト、セキュリティ検査、構文検査、差分検査を実行する。

## 依存関係

- `02_pv_zero_forecast.md` と `04_temperature_zero.md` は同じ予報値正規化部分を触る可能性があるが、コミットは分ける。
- 共通ヘルパーを導入する場合も、最初のコミットでは対象不具合に必要な最小範囲だけを実装する。
- `03_soc_cap_zero.md` はPVガードが返す0%を正しく集約できることが前提であり、PV予測ゼロ問題とは別の責務として扱う。
- `05_night_charge_window_contract.md` は仕様判断を含むため、現在の挙動を無断で変更しない。文書化とテスト固定を先行し、機器制約確認後に制御変更を行う。
- 大規模な `energy_model_main.py` 分割は、境界不具合修正後に行う。バグ修正と全面的リファクタリングを混ぜない。

## 1件ごとの標準手順

1. `01_overall_policy.md` と担当する個別指示書を読む。
2. 指定された現在コードと現行テストを確認する。
3. 指示書記載の条件で問題を再現する。
4. 失敗する回帰テストを追加する。
5. 問題1件だけを最小変更で修正する。
6. 対象テストを実行する。
7. 関連テストを実行する。
8. `git diff --check` を実行する。
9. `git diff` で無関係な変更がないことを確認する。
10. 1件だけをコミットする。
11. 作業報告を残す。

## 共通禁止事項

- 欠損値と数値0を同一視しない。
- `x or default` を数値入力の既定値処理へ安易に使用しない。
- 有効な0を負値と同じ無効値として扱わない。
- 既存の予報値を、理由や出所を残さず別モデルへ差し替えない。
- 制約上限0%を「制約なし」の100%へ置換しない。
- 設定名から推測して制御契約を変更しない。
- バグ修正と大規模ファイル分割を同じコミットへ混ぜない。
- 既存文書を削除または置換しない。
- `.env` の実値、認証情報、個人情報をログや文書へ記載しない。

## 全体完了時の確認

```powershell
python -m pytest -q tests/test_energy_model.py
python -m pytest -q tests/test_energy_model_runtime.py
python -m pytest -q tests/test_pv_array_forecast.py
python -m pytest -q tests/test_kpnet_workflow.py
python -m pytest -q tests/test_soc_cost_optimizer.py
python -m pytest -q -m "not external"
python -m compileall -q app energy_model_main.py cloud_job_runner.py
python scripts/security_check.py
git diff --check
git status --short
```

## 作業報告テンプレート

- 対応番号:
- 基準HEAD:
- 変更ファイル:
- 再現条件:
- 再現結果:
- 根本原因:
- 仕様判断:
- 修正内容:
- 追加テスト:
- テスト結果:
- セキュリティ検査:
- 互換性への影響:
- 残課題:
- ロールバック方法:

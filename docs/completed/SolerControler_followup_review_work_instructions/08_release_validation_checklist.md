# SolerControler リリース前総合確認手順

> 対象HEADのP0/P1修正後に実施する。

## 1. 静的検査

```powershell
python -m compileall -q app cloud_job_runner.py dashboard_server.py energy_model_main.py
```

```powershell
git diff --check
```

## 2. 関連テスト

```powershell
python -m pytest -q tests/test_cloud_job_runner.py tests/test_kpnet_workflow.py tests/test_dashboard_data.py tests/test_dashboard_server.py tests/test_energy_model.py
```

## 3. 全テスト

```powershell
python -m pytest -q
```

再レビュー時の基準は次の通り。

```text
199 passed in 8.78s
```

修正後は追加テスト分だけ件数が増えることを確認する。

## 4. JavaScript時刻解析確認
Nodeがある場合、実時刻がマッチすることを確認する。

```powershell
node -e "const re=/^(\d{1,2}):(\d{2})$/; console.log(re.test('02:43'), re.test('07:00'), re.test('24:00'))"
```

期待値:

```text
true true false
```

さらに自動テストで、夜間充電量の配分合計が元の`night_charge_kwh`と一致することを確認する。

## 5. Dashboardコンテナ確認
Dashboard用Dockerイメージをビルドし、コンテナ内に次があることを確認する。

```text
/app/templates/dashboard.html
/app/static/dashboard.css
/app/static/dashboard.js
```

起動後に次を確認する。

- `/` → HTTP 200
- `/static/dashboard.css` → HTTP 200
- `/static/dashboard.js` → HTTP 200
- ブラウザconsoleにJavaScript構文エラーなし
- CSS適用済み
- 充電開始・終了時刻が実データ通り表示・計算される

## 6. SOC異常値確認
テストfixtureまたはモックで次を投入する。

- `-1`
- `101`
- `NaN`
- `Infinity`
- stale正常値
- fresh正常値

期待:
- 異常値は有効SOCにならない。
- stale値はfallbackにならない。
- fresh正常値だけがCSV SOCとして採用される。

## 7. 初回SOC取得不能確認
正式仕様に応じて確認する。

### 安全側停止仕様
- forcedが一度も適用されない。
- standbyが明示適用される。
- `initial_soc_unavailable`等のreasonが保存される。

### 明示許可仕様
- 許可フラグfalseではforced開始しない。
- 許可フラグtrueでのみforced開始する。
- 連続取得失敗でstandbyへ戻る。

## 8. 最新スケジュール順序不変性
同じイベント集合を次の順序で渡し、結果が一致することを確認する。

- 新しい順
- 古い順
- ランダム順

確認対象:
- `charge_start_time`
- `charge_end_time`
- `recorded_at`
- `settings_completed_run_id`
- `settings_completed_at`

## 9. Backend parity
同じ論理fixtureをSQLite/PostgreSQL/Firestoreへ与え、主要出力を比較する。

- 日付範囲
- energy daily
- battery daily
- latest schedule
- cost monthly
- daily review

## 10. 最終Git確認

```powershell
git status --short
```

```powershell
git diff --stat
```

```powershell
git diff
```

確認事項:
- 無関係なファイル変更がない。
- 一時生成物、認証情報、ログ、DBをコミットしない。
- 各問題が別コミットになっている。
- Dockerfile修正とJavaScript修正が最初に反映されている。

## リリース判定
次をすべて満たした場合のみリリース可。

- P0 2件が修正済み。
- SOC異常値を受理しない。
- SOC不明時の開始仕様が明文化・テスト済み。
- 全テスト成功。
- Dashboardコンテナ実動作成功。
- 実時刻区間で夜間充電量が配分される。

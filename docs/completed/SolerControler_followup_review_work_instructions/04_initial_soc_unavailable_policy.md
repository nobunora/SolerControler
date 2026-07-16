# SolerControler 追加修正作業指示書

> 対象: `C:\VSC\SolerControler`  
> 基準HEAD: `7681fe2`  
> 原則: この指示書の問題だけを修正し、無関係な変更を混ぜない。

## 04. 初回SOC取得不能でも強制充電を開始する

### 優先度
**P1 / 高・仕様判断必須**

### 対象
- `cloud_job_runner.py`
- `_monitor_partial_forced_and_stop()`
- `_read_soc_with_fallback()`
- 03:00強制充電開始フロー

### 現在の動作
1. リアルタイムSOCを規定回数取得する。
2. 取得失敗時、新鮮なCSVへfallbackする。
3. 両方取得不能なら`SocReading(None, "unavailable", ...)`を返す。
4. それでも計画値を使って処理を継続する。
5. `_run_03_settings_profile_with_db(profile="forced", ...)`を実行する。
6. 強制充電開始後の監視ループでSOC失敗が規定回数続くとstandbyへ戻す。

### 調査結果
監視中のfail-safeは実装済みだが、制御開始前のfail-safeではない。
初回SOCが不明でも、一度は強制充電が開始される。

### 影響
- 実SOCが既に高い場合でも、取得不能時に強制充電を開始する。
- `ADJUST03_MAX_CONSECUTIVE_SOC_FAILURES`回の追加失敗までforced状態が継続する。
- ポーリング間隔・完了推定により、standby移行までの時間が変動する。
- 「データ不明時は充電停止」なのか「計画達成を優先して充電開始」なのかがコード上で暗黙になっている。

### 必須仕様判断
次のどちらを正式仕様とするか決める。

#### 方針A: 安全側停止
初回SOCがunavailableならforcedを開始せずstandbyを維持する。

推奨フロー:
1. 異常イベントを永続化する。
2. `standby`を明示適用する。
3. stop reasonを`initial_soc_unavailable`等で保存する。
4. 処理を終了する。

#### 方針B: 計画達成優先
SOC不明でも計画値を信頼してforcedを開始する。

この場合は暗黙動作にせず、環境変数で明示する。

```text
ADJUST03_ALLOW_FORCED_START_WITHOUT_SOC=true
```

既定値は安全側の`false`を推奨する。

### 修正方針
- 方針Aを採る場合、forced開始前に`latest_soc is None`を判定して終了する。
- 方針Bを採る場合、許可フラグが明示的にtrueの場合だけforced開始を許可する。
- どちらでもFirestoreにSOC source、error、停止または開始理由を保存する。

### 必須テスト
- 初回realtime失敗、CSVなし。
- 初回realtime失敗、CSV stale。
- 初回realtime失敗、CSV異常値。
- 安全側設定ではforcedが一度も呼ばれずstandbyのみ。
- 許可設定ではforced開始後、連続失敗でstandbyへ戻る。
- stop reasonまたはstart reasonが期待値で永続化される。
- 初回SOC正常時の既存フローを壊さない。

### 完了条件
- SOC不明時の制御方針が設定・コード・テストで明示される。
- デフォルト動作を運用担当者が説明できる。
- forced開始前後の両方にfail-safeが存在する。

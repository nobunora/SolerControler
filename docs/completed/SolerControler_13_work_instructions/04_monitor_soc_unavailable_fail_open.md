# SolerControler 修正作業指示書

> 対象: `C:\VSC\SolerControler`  
> 基準HEAD: `5430f40`  
> 原則: この指示書の問題だけを修正し、無関係な変更を混ぜない。


## 04. SOC監視不能時に強制充電を継続するフェイルオープン

### 優先度
**P0**

### 対象
- `cloud_job_runner.py`
- `_monitor_partial_forced_and_stop()`

### 調査結果
監視中の取得例外は `latest_soc=None` として処理を続ける。停止条件を評価できないまま、強制充電がカットオフまで継続する。

### 根本原因
- SOC不能時の安全仕様がない。
- 連続失敗回数と最終有効値の鮮度を保持していない。
- CSV fallbackが監視ループから外れている。
- 強制充電開始後の予期しない例外に対するfail-safeが弱い。

### 影響
- 目標SOC到達後も充電継続。
- 不要な買電。
- 計画と実運転の乖離。
- 通信断時の動作を説明できない。

### 修正方針
推奨ポリシー:
1. realtime失敗時は02のCSV fallbackを使う。
2. 両方失敗したら連続失敗回数を増やす。
3. 閾値未満は短時間後に再試行。
4. 閾値以上はstandbyへ安全停止。
5. 最終有効SOCを使う場合は鮮度上限を設ける。
6. 停止理由を保存する。

推奨状態:
```python
@dataclass
class MonitorState:
    consecutive_soc_failures: int = 0
    last_valid_soc: float | None = None
    last_valid_soc_at: datetime | None = None
```

### 実装手順
1. 最大連続失敗回数を設定化する。
2. 有効SOC取得時は失敗数を0に戻す。
3. last valid SOCの最大鮮度を設定する。
4. 閾値超過時にstandbyを適用する。
5. 停止理由を以下から保存する。
   - `target_reached`
   - `cutoff_reached`
   - `soc_unavailable_fail_safe`
   - `monitor_timeout`
6. 強制充電開始後の外側にfail-safe cleanupを設ける。
7. standby適用失敗も別途記録する。

### 必須テスト
- 1回失敗後に回復。
- 連続失敗閾値でstandby。
- CSV fallback成功なら継続。
- 古いlast valid値を使わない。
- 予期しない例外後にもstandbyを試行。
- 停止理由が保存される。

### 完了条件
- SOCが見えない状態でカットオフまで無条件継続しない。
- 安全停止理由が追跡可能。
- 異常系テストが存在する。

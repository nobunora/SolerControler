# SolerControler 修正作業指示書

> 対象: `C:\VSC\SolerControler`  
> 基準HEAD: `5430f40`  
> 原則: この指示書の問題だけを修正し、無関係な変更を混ぜない。


## 02. 初回リアルタイムSOC取得失敗で03時ジョブが中断する

### 優先度
**P0**

### 対象
- `cloud_job_runner.py`
- `_monitor_partial_forced_and_stop()`
- `_latest_realtime_soc_percent()`
- `_latest_soc_percent()`

### 調査結果
監視開始前の初回取得は例外処理されていない。

```python
latest_soc = _latest_realtime_soc_percent()
```

監視ループ内には例外処理があるが、初回だけ無保護である。

### 根本原因
- realtimeへの置換時にCSV SOC経路をフォールバックとして残さなかった。
- SOC取得元の優先順位、リトライ、鮮度条件が仕様化されていない。
- 戻り値が `float | None` だけで、出所・失敗理由を保持できない。

### 影響
以下で03時処理全体が停止する。
- login失敗
- HTTP timeout
- HTML変更
- SOC解析失敗
- logout失敗
- 一時通信断

### 修正方針
SOC取得を共通関数へまとめる。

取得順:
1. realtimeを規定回数リトライ
2. 失敗後、最新CSV SOCへフォールバック
3. 両方失敗ならunavailable
4. 値・出所・エラーを返す

推奨データ:
```python
@dataclass(frozen=True)
class SocReading:
    value_percent: float | None
    source: str
    error: str | None
    observed_at: datetime | None
```

### 実装手順
1. `_read_soc_with_fallback(csv_paths)` を作る。
2. realtimeを2～3回リトライする。
3. 全失敗時に `_latest_soc_percent(csv_paths)` を呼ぶ。
4. CSVの最終時刻を調べ、古すぎる値は使わない。
5. 初回と監視ループの両方を共通関数へ置換する。
6. `soc_source` をログと保存データへ記録する。
7. 両方失敗時は04の安全停止方針へ接続する。

### 必須テスト
- realtime成功。
- realtime失敗・CSV成功。
- 両方失敗。
- 古いCSVを不採用。
- 初回realtime例外でジョブが即クラッシュしない。
- sourceが保存される。

### 完了条件
- 初回SOC取得例外で03時ジョブが即中断しない。
- 使用したSOCの出所を追跡できる。
- CSV fallbackが実際に機能する。

### テスト
```powershell
python -m pytest -q tests/test_cloud_job_runner.py
```

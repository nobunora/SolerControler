# SolerControler 修正作業指示書

> 対象: `C:\VSC\SolerControler`  
> 基準HEAD: `5430f40`  
> 原則: この指示書の問題だけを修正し、無関係な変更を混ぜない。


## 03. logout失敗が正常なSOC取得結果を上書きする

### 優先度
**P0**

### 対象
- `cloud_job_runner.py`
- `_latest_realtime_soc_percent()`
- `KpNetClient.logout()`

### 調査結果
現在:
```python
client.login()
try:
    return client.read_realtime_soc_percent()
finally:
    client.logout()
```

SOC読取成功後にlogoutが例外を投げると、正常な戻り値が失われる。

### 根本原因
cleanup例外と本処理結果を分離していない。`finally` 内の例外が戻り値を上書きする。

### 影響
- SOC取得成功が失敗扱いになる。
- 初回なら03時ジョブ停止。
- 監視中ならSOC unavailable扱い。
- 不要な再ログインやfallbackが発生する。

### 修正方針
logout失敗は記録するが、正常なSOC値を破棄しない。

推奨:
```python
client.login()
try:
    return client.read_realtime_soc_percent()
finally:
    try:
        client.logout()
    except Exception:
        LOGGER.exception("KP-NET logout failed")
```

読取自体も失敗した場合は、読取例外を主原因とし、logout失敗は補助ログにする。

### 実装手順
1. logoutを内側try/exceptへ入れる。
2. SOC読取失敗とlogout失敗を別メッセージで記録する。
3. read成功・logout失敗時は値を返す。
4. login失敗時は無理にlogoutしない。
5. 必要なら `session.close()` を別cleanupとして実行する。

### 必須テスト
- read成功/logout成功。
- read成功/logout失敗でも値を返す。
- read失敗/logout成功。
- read失敗/logout失敗で読取失敗を主原因にする。
- login失敗。

### 完了条件
- logout障害で正常SOC値が失われない。
- 例外の主原因をログから区別できる。

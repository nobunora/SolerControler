# SolerControler 追加修正作業指示書

> 対象: `C:\VSC\SolerControler`  
> 基準HEAD: `7681fe2`  
> 原則: この指示書の問題だけを修正し、無関係な変更を混ぜない。

## 01. Dashboardコンテナへtemplates/staticが含まれない

### 優先度
**P0 / 最優先**

### 対象
- `Dockerfile.dashboard`
- `dashboard_server.py`
- `templates/dashboard.html`
- `static/dashboard.css`
- `static/dashboard.js`

### 調査結果
`dashboard_server.py` は実行時に次を参照する。

```python
(Path(__file__).parent / "templates" / "dashboard.html").read_text(...)
(Path(__file__).parent / "static" / filename).read_bytes()
```

一方、`Dockerfile.dashboard` は現在次しかCOPYしていない。

```dockerfile
COPY app ./app
COPY dashboard_server.py ./
```

したがってCloud Buildで生成されるDashboardイメージには、`/app/templates/dashboard.html`、`/app/static/dashboard.css`、`/app/static/dashboard.js` が存在しない。

### 再現結果
コンテナ内でルート `/` を表示すると、テンプレート読込み時に次相当で失敗する。

```text
FileNotFoundError: /app/templates/dashboard.html
```

静的アセットURLも同様に読込み不能になる。

### 根本原因
HTML/CSS/JSを外部ファイルへ分離したが、Dashboard用DockerfileのCOPY対象を更新していない。

### 影響
- Cloud Run上でダッシュボードのルート表示が失敗する。
- CSS/JavaScriptが配信されない。
- ローカルではリポジトリ上のファイルを直接読めるため、デプロイ前テストだけでは見逃しやすい。

### 修正方針
`Dockerfile.dashboard` に次を追加する。

```dockerfile
COPY app ./app
COPY templates ./templates
COPY static ./static
COPY dashboard_server.py ./
```

### 実装上の注意
- `COPY . .` へ安易に広げず、必要ファイルを明示する。
- `.dockerignore` がある場合、`templates/`、`static/` を除外していないことも確認する。
- ファイル所有権やWORKDIRは現行のままでよい。
- Runner用の通常`Dockerfile`へはDashboard資産が不要なら追加しない。

### 必須テスト
1. Dashboardイメージをローカルビルドする。
2. コンテナ内に次が存在することを確認する。
   - `/app/templates/dashboard.html`
   - `/app/static/dashboard.css`
   - `/app/static/dashboard.js`
3. コンテナを起動し、次を確認する。
   - `/` がHTTP 200
   - `/static/dashboard.css` がHTTP 200
   - `/static/dashboard.js` がHTTP 200
4. HTMLにCSS/JS参照が含まれていることを確認する。

### 推奨自動テスト
Docker buildを常時テストできない場合でも、最低限Dockerfile本文に必要COPYがあることを確認する軽量テストを追加する。

### 完了条件
- Cloud Buildで作成したイメージに新設資産が含まれる。
- Cloud Run相当環境でルート、CSS、JSがすべてHTTP 200になる。
- ローカル実行とコンテナ実行の表示結果が一致する。

from __future__ import annotations

import gzip
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import re
import shutil
import subprocess
from threading import Thread

import pytest

from app.dashboard.server import Handler, _html, _static_asset, _static_version
from app.dashboard.snapshots import BOOTSTRAP_KIND, artifact_from_payload, clear_snapshot_cache


def test_dashboard_template_keeps_critical_dom_and_nonce() -> None:
    payload = {"latest_schedule": {"charge_start_time": "02:43"}, "pv_daily": [{"date": "2026-07-15"}]}
    html = _html(payload, script_nonce="test-nonce")

    for element_id in (
        "statusMsg",
        "dashboardWarnings",
        "dailyReviewPrevBtn",
        "dailyReviewNextBtn",
        "learningParamsTable",
    ):
        assert f'id="{element_id}"' in html
    assert 'nonce="test-nonce"' in html
    static_version = _static_version()
    assert f'src="/static/dashboard.js?v={static_version}"' in html
    assert f'href="/static/dashboard.css?v={static_version}"' in html
    assert "__DASHBOARD_DATA_PLACEHOLDER__" not in html
    assert "1. 予実レビュー（昨日まで）" in html
    assert f"window.__DASHBOARD_DATA__ = {json.dumps(payload, ensure_ascii=False)};" in html
    assert html.index("window.__DASHBOARD_DATA__") < html.index(
        f'src="/static/dashboard.js?v={static_version}"'
    )
    for dependency in ("dashboard_calculations.js", "dashboard_dates.js", "dashboard_api.js", "dashboard_store.js"):
        assert html.index(f'src="/static/{dependency}?v={static_version}"') < html.index(
            f'src="/static/dashboard.js?v={static_version}"'
        )


def test_dashboard_static_assets_are_available() -> None:
    css = _static_asset("/static/dashboard.css")
    javascript = _static_asset("/static/dashboard.js")

    assert css is not None and css[0].startswith("text/css") and b":root" in css[1]
    assert javascript is not None and javascript[0].startswith("text/javascript")
    assert b"function estimateHourlyNightGridCharge" in javascript[1]
    assert b"main();" in javascript[1]
    assert b"__DASHBOARD_DATA_PLACEHOLDER__" not in javascript[1]
    assert b"window.__DASHBOARD_DATA__ || {}" in javascript[1]
    assert "PV予測モデル".encode() not in javascript[1]
    assert "第1 ${arrival".encode() not in javascript[1]
    assert "日中最大SOC".encode() not in javascript[1]
    for path in ("dashboard_dates.js", "dashboard_api.js", "dashboard_store.js"):
        asset = _static_asset(f"/static/{path}")
        assert asset is not None and asset[0].startswith("text/javascript")
    assert _static_asset("/static/missing.js") is None


def test_dashboard_bootstrap_payload_is_available_to_external_javascript() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed")
    payload = {"pv_daily": [{"date": "2026-07-15", "actual_kwh": 4.2}]}
    html = _html(payload, script_nonce="test-nonce")
    match = re.search(r"<script nonce=\"test-nonce\">\s*(window\.__DASHBOARD_DATA__ = .*?;)\s*</script>", html)
    assert match is not None

    script = (
        "global.window = {};\n"
        f"{match.group(1)}\n"
        "const initialPayload = window.__DASHBOARD_DATA__ || {};\n"
        "if (initialPayload.pv_daily[0].actual_kwh !== 4.2) process.exit(1);\n"
    )
    subprocess.run([node, "-e", script], check=True)


def test_dashboard_dockerfile_copies_runtime_assets() -> None:
    root = Path(__file__).parents[1]
    dockerfile = (root / "Dockerfile.dashboard").read_text(encoding="utf-8")

    for source, destination in (
        ("templates", "./templates"),
        ("static", "./static"),
        ("dashboard_server.py", "./"),
    ):
        assert f"COPY {source} {destination}" in dockerfile
        assert (root / source).exists()

def _serve_dashboard_for_test() -> tuple[ThreadingHTTPServer, Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_bootstrap_http_uses_precomputed_gzip_etag_without_db(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("DASHBOARD_BASIC_USER", raising=False)
    monkeypatch.delenv("DASHBOARD_BASIC_PASSWORD", raising=False)
    monkeypatch.delenv("DASHBOARD_SNAPSHOT_GCS_PREFIX", raising=False)
    monkeypatch.delenv("NIGHT_PLAN_ARCHIVE_GCS_PREFIX", raising=False)
    monkeypatch.setenv("DASHBOARD_SNAPSHOT_LOCAL_DIR", str(tmp_path))
    clear_snapshot_cache()

    payload = {
        "pv_daily": [{"date": "2026-09-20", "forecast_pv_total_kwh": 12.3}],
        "forecast_hourly": [],
        "energy_daily": [],
        "cost_daily": [],
        "cost_monthly": [],
        "battery_daily": [],
        "battery_flow_daily": [],
        "model_parameters": [],
        "latest_schedule": {},
        "dashboard_warnings": [],
        "pv_forecast_diagnostics": {},
        "daily_review": {},
        "daily_reviews": [],
        "meta": {"snapshot_kind": BOOTSTRAP_KIND, "snapshot_schema_version": 1},
    }
    artifact = artifact_from_payload(BOOTSTRAP_KIND, payload)
    (tmp_path / "bootstrap.json.gz").write_bytes(artifact.gzip_bytes)

    def _db_must_not_be_called(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("bootstrap snapshot hit the database")

    monkeypatch.setattr("app.dashboard.server.load_dashboard_slice", _db_must_not_be_called)

    server, thread = _serve_dashboard_for_test()
    try:
        host, port = server.server_address
        conn = HTTPConnection(host, port)
        conn.request("GET", "/api/dashboard/bootstrap", headers={"Accept-Encoding": "gzip"})
        response = conn.getresponse()
        body = response.read()
        etag = response.getheader("ETag")
        assert response.status == 200
        assert response.getheader("Content-Encoding") == "gzip"
        assert response.getheader("Cache-Control") == "private, max-age=0, must-revalidate"
        assert etag == artifact.etag
        assert body == artifact.gzip_bytes
        assert json.loads(gzip.decompress(body).decode("utf-8")) == payload
        conn.close()

        conn = HTTPConnection(host, port)
        conn.request(
            "GET",
            "/api/dashboard/bootstrap",
            headers={"Accept-Encoding": "gzip", "If-None-Match": str(etag)},
        )
        response = conn.getresponse()
        assert response.status == 304
        assert response.read() == b""
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        clear_snapshot_cache()


def test_versioned_static_asset_is_precompressed_immutable_and_revalidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DASHBOARD_BASIC_USER", raising=False)
    monkeypatch.delenv("DASHBOARD_BASIC_PASSWORD", raising=False)

    server, thread = _serve_dashboard_for_test()
    try:
        host, port = server.server_address
        path = f"/static/dashboard.js?v={_static_version()}"
        conn = HTTPConnection(host, port)
        conn.request("GET", path, headers={"Accept-Encoding": "gzip"})
        response = conn.getresponse()
        body = response.read()
        etag = response.getheader("ETag")
        assert response.status == 200
        assert response.getheader("Content-Encoding") == "gzip"
        assert response.getheader("Cache-Control") == "private, max-age=31536000, immutable"
        assert etag
        assert b"main();" in gzip.decompress(body)
        conn.close()

        conn = HTTPConnection(host, port)
        conn.request(
            "GET",
            path,
            headers={"Accept-Encoding": "gzip", "If-None-Match": str(etag)},
        )
        response = conn.getresponse()
        assert response.status == 304
        assert response.read() == b""
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


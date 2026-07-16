from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from dashboard_server import _html, _static_asset


def test_dashboard_template_keeps_critical_dom_and_nonce() -> None:
    payload = {"latest_schedule": {"charge_start_time": "02:43"}, "pv_daily": [{"date": "2026-07-15"}]}
    html = _html(payload, script_nonce="test-nonce")

    for element_id in (
        "statusMsg",
        "dashboardWarnings",
        "learningParamsTable",
    ):
        assert f'id="{element_id}"' in html
    assert 'nonce="test-nonce"' in html
    assert 'src="/static/dashboard.js"' in html
    assert 'href="/static/dashboard.css"' in html
    assert "__DASHBOARD_DATA_PLACEHOLDER__" not in html
    assert f"window.__DASHBOARD_DATA__ = {json.dumps(payload, ensure_ascii=False)};" in html
    assert html.index("window.__DASHBOARD_DATA__") < html.index('src="/static/dashboard.js"')
    for dependency in ("dashboard_calculations.js", "dashboard_dates.js", "dashboard_api.js", "dashboard_store.js"):
        assert html.index(f'src="/static/{dependency}"') < html.index('src="/static/dashboard.js"')


def test_dashboard_static_assets_are_available() -> None:
    css = _static_asset("/static/dashboard.css")
    javascript = _static_asset("/static/dashboard.js")

    assert css is not None and css[0].startswith("text/css") and b":root" in css[1]
    assert javascript is not None and javascript[0].startswith("text/javascript")
    assert b"function estimateHourlyNightGridCharge" in javascript[1]
    assert b"main();" in javascript[1]
    assert b"__DASHBOARD_DATA_PLACEHOLDER__" not in javascript[1]
    assert b"window.__DASHBOARD_DATA__ || {}" in javascript[1]
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

from __future__ import annotations

from pathlib import Path

from dashboard_server import _html, _static_asset


def test_dashboard_template_keeps_critical_dom_and_nonce() -> None:
    html = _html({"latest_schedule": {}}, script_nonce="test-nonce")

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


def test_dashboard_static_assets_are_available() -> None:
    css = _static_asset("/static/dashboard.css")
    javascript = _static_asset("/static/dashboard.js")

    assert css is not None and css[0].startswith("text/css") and b":root" in css[1]
    assert javascript is not None and javascript[0].startswith("text/javascript")
    assert b"function estimateHourlyNightGridCharge" in javascript[1]
    assert b"main();" in javascript[1]
    assert _static_asset("/static/missing.js") is None


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

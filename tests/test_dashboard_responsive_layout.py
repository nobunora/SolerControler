"""Render the real template/CSS with long production-shaped values, without network."""
from pathlib import Path
import re

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_content_fits_mobile_without_clipping_gantt() -> None:
    html = (ROOT / "templates/dashboard.html").read_text(encoding="utf-8")
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S)
    html = re.sub(r"<link\b[^>]*>", "", html)
    css = (ROOT / "static/dashboard.css").read_text(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.abort())
            page.set_content(html)
            page.add_style_tag(content=css)
            page.locator("#pvModelProvenance").evaluate("e => e.textContent = 'モデル: ' + 'a'.repeat(64)")
            page.locator("#pvModelExplanation").evaluate("e => e.textContent = 'PV(h)=' + 'a'.repeat(120)")
            page.locator(".battery-life-value").evaluate_all("els => els.forEach(e => e.textContent = '8.42 kWh / 100.0%')")
            page.locator("#constraintGantt").evaluate("e => e.textContent = '00:00 — 24:00'")
            for width in (320, 390, 768, 1440):
                page.set_viewport_size({"width": width, "height": 900})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), width
                for selector in ("#pvModelProvenance", ".equation", ".battery-life-kpi"):
                    assert page.locator(selector).evaluate_all("els => els.every(e => e.scrollWidth <= e.clientWidth + 1)"), (width, selector)
                assert page.locator("#pvModelProvenance").inner_text().endswith("a" * 64)
                if width < 980:
                    assert page.locator(".gantt-wrap").evaluate("e => e.scrollWidth > e.clientWidth")
                    assert page.locator(".gantt-wrap").evaluate("e => { e.scrollLeft = 100; return e.scrollLeft > 0; }")
        finally:
            browser.close()

"""Read-only browser acceptance; artifacts remain private and failures exit nonzero."""
from __future__ import annotations

import argparse
import base64
import importlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def assess_snapshot(snapshot: dict[str, Any]) -> dict[str, bool]:
    ranges = re.findall(r"(\d{4}-\d{2}-\d{2})\s*[〜～~]\s*(\d{4}-\d{2}-\d{2})", snapshot["text"])
    return {
        "period_order": bool(ranges) and all(start <= end for start, end in ranges),
        "charts_rendered": snapshot["rendered_charts"] >= 8,
        "no_page_overflow": snapshot["document_width"] <= snapshot["viewport_width"] + 1,
    }


def run_browser(url: str, output: Path) -> dict[str, Any]:
    # Optional operator tool; absence is INCONCLUSIVE, never a skipped PASS.
    sync_playwright = importlib.import_module("playwright.sync_api").sync_playwright

    origin = urlsplit(url)
    if origin.scheme != "https" or not origin.netloc or origin.username or origin.password:
        raise ValueError("Invalid dashboard origin")
    base = f"{origin.scheme}://{origin.netloc}"
    authorization = "Basic " + base64.b64encode(
        f"{os.environ['DASHBOARD_BASIC_USER']}:{os.environ['DASHBOARD_BASIC_PASSWORD']}".encode()
    ).decode()
    checks: dict[str, bool] = {}
    error_count = 0
    history_requests = 0

    def record_error(*_: Any) -> None:
        nonlocal error_count
        error_count += 1

    def record_request(request: Any) -> None:
        nonlocal history_requests
        parsed = urlsplit(request.url)
        if parsed.netloc == origin.netloc and parsed.path == "/api/dashboard/history":
            history_requests += 1

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.route(base + "/**", lambda route: route.continue_(
                headers={**route.request.headers, "authorization": authorization}))
            page.on("pageerror", record_error)
            page.on("console", lambda message: record_error() if message.type == "error" else None)
            page.on("request", record_request)
            response = page.goto(base, wait_until="domcontentloaded", timeout=45000)
            checks["http_ok"] = response is not None and response.status == 200
            if not checks["http_ok"]:
                return {"status": "FAIL", "checks": checks}
            page.wait_for_function("document.querySelectorAll('canvas').length >= 8", timeout=30000)

            def capture(name: str) -> None:
                page.wait_for_load_state("networkidle", timeout=30000)
                page.screenshot(path=str(output / f"{name}.png"), full_page=True)
                snapshot = page.evaluate("""() => ({
                    text: document.body.innerText,
                    rendered_charts: [...document.querySelectorAll('canvas')].filter(e => {
                        const chart = window.Chart?.getChart(e);
                        return e.width > 0 && e.height > 0 && chart?.data.datasets.some(
                            d => d.data.some(v => v !== null && Number.isFinite(typeof v === 'object' ? v?.y : v)));
                    }).length,
                    document_width: document.documentElement.scrollWidth,
                    viewport_width: innerWidth
                })""")
                checks.update({f"{name}.{key}": value for key, value in assess_snapshot(snapshot).items()})

            capture("initial")
            checks["lazy_history"] = history_requests == 0
            page.get_by_role("button", name="全て(日)", exact=True).click()
            capture("all")
            checks["history_loaded_once"] = history_requests == 1
            page.get_by_role("button", name="1ヶ月(日)", exact=True).click()
            capture("month_after_all")
            checks["history_reused"] = history_requests == 1
            page.reload(wait_until="domcontentloaded")
            page.set_viewport_size({"width": 390, "height": 844})
            capture("mobile")
            checks["no_browser_errors"] = error_count == 0
        finally:
            browser.close()
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_directory.resolve()
    if not output.is_relative_to(ROOT / "artifacts"):
        parser.error("Output must be under repository artifacts")
    output.mkdir(parents=True, exist_ok=False)
    result: dict[str, Any]
    try:
        inventory = json.loads(args.inventory.read_text(encoding="utf-8-sig"))
        result = run_browser(inventory["web"]["status"]["url"], output)
    except Exception as error:
        # Browser exceptions can contain URLs/credentials; expose only the type.
        result = {"status": "INCONCLUSIVE", "error_type": type(error).__name__}
    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    result["scope"] = "current dashboard rendering, navigation and request counts; not source/device acceptance"
    (output / "receipt.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 2}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())

"""Check the restored local environment using frozen originals, never cloud APIs."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib
import json
import os
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.local_recovery import PROJECT, checksum, local_environment, write_json


class FrozenStorage:
    def __init__(self, snapshot: Path) -> None:
        self.snapshot = snapshot
        self.entries: dict[tuple[str, str], list[dict[str, Any]]] = {}
        buckets = json.loads((snapshot / "storage/buckets.json").read_text(encoding="utf-8"))
        for index, bucket in enumerate(buckets):
            rows = json.loads((snapshot / f"storage/bucket-{index}-objects.json").read_text(encoding="utf-8"))
            for row in rows:
                self.entries.setdefault((bucket["name"], row["name"]), []).append(row)

    def bucket(self, name: str) -> Any:
        store = self

        class Bucket:
            def blob(self, key: str, generation: int | None = None) -> Any:
                candidates = store.entries.get((name, key), [])
                if generation is not None:
                    candidates = [item for item in candidates if int(item["generation"]) == generation
                                  or str(generation) in item.get("required_generation_aliases", {})]
                elif any(not item.get("timeDeleted") for item in candidates):
                    candidates = [item for item in candidates if not item.get("timeDeleted")]
                if not candidates:
                    raise FileNotFoundError("Original object is absent from the local snapshot")
                metadata = max(candidates, key=lambda item: int(item["generation"]))
                return FrozenBlob(store.snapshot, metadata, requested_generation=generation)

        return Bucket()


class FrozenBlob:
    def __init__(self, snapshot: Path, metadata: dict[str, Any], *, requested_generation: int | None = None) -> None:
        self.path = (snapshot / metadata["local_file"]).resolve()
        if not self.path.is_relative_to(snapshot.resolve()):
            raise ValueError("Original object path escapes the snapshot")
        self.generation = int(metadata["generation"])
        self.source_generation = self.generation
        if requested_generation is not None and requested_generation != self.generation:
            expected = metadata.get("required_generation_aliases", {}).get(str(requested_generation))
            raw = self.path.read_bytes()
            original = gzip.decompress(raw) if metadata["name"].endswith(".gz") else raw
            if not expected or hashlib.sha256(original).hexdigest() != expected:
                raise ValueError("Recovery generation alias lacks exact original-content proof")
            self.generation = requested_generation
        self.expected = metadata["local_sha256"]

    def reload(self, **_kwargs: Any) -> None:
        if checksum(self.path) != self.expected:
            raise ValueError("Frozen original checksum mismatch")

    def download_as_bytes(self, *, if_generation_match: int, start: int = 0, end: int | None = None, **_kwargs: Any) -> bytes:
        if if_generation_match != self.generation:
            raise ValueError("Frozen original generation mismatch")
        with self.path.open("rb") as stream:
            stream.seek(start)
            return cast(bytes, stream.read() if end is None else stream.read(end - start + 1))


def deployed_source_check(snapshot: Path, source: Path) -> dict[str, Any]:
    services = json.loads((snapshot / "configuration/cloud_run_services.json").read_text(encoding="utf-8"))["services"]
    if len(services) != 1:
        raise ValueError("Identify the captured dashboard service before comparing application files")
    digest = services[0]["template"]["containers"][0]["image"].rsplit("@", 1)[1]
    blobs = snapshot / "registry/oci/blobs/sha256"

    def verified_blob(value: str) -> Path:
        if not value.startswith("sha256:") or len(value) != 71:
            raise ValueError("Invalid image digest")
        path = blobs / value.split(":", 1)[1]
        if checksum(path) != value.split(":", 1)[1]:
            raise ValueError("Captured image checksum mismatch")
        return path

    manifest = json.loads(verified_blob(digest).read_bytes())
    if "manifests" in manifest:
        descriptor = next(item for item in manifest["manifests"] if item.get("platform", {}).get("architecture") == "amd64")
        manifest = json.loads(verified_blob(descriptor["digest"]).read_bytes())
    expected = {path.relative_to(source).as_posix(): checksum(path) for path in (source / "app").rglob("*.py")}
    for path in (source / "static").iterdir():
        if path.suffix in {".js", ".css", ".html"}:
            expected[path.relative_to(source).as_posix()] = checksum(path)
    expected["dashboard_server.py"] = checksum(source / "dashboard_server.py")
    found: dict[str, str] = {}
    for layer in reversed(manifest["layers"]):
        if len(found) == len(expected):
            break
        with tarfile.open(verified_blob(layer["digest"]), mode="r|*") as archive:
            for member in archive:
                name = member.name.removeprefix("./").removeprefix("app/")
                target = Path(name)
                if target.name.startswith(".wh."):
                    deleted = (target.parent / target.name.removeprefix(".wh.")).as_posix()
                    if deleted in expected and deleted not in found:
                        found[deleted] = "deleted"
                if member.isfile() and name in expected and name not in found:
                    stream = archive.extractfile(member)
                    if stream is None or member.size > 5_000_000:
                        raise ValueError("Unexpected application image entry")
                    found[name] = hashlib.sha256(stream.read(5_000_001)).hexdigest()
    differences = [name for name, value in expected.items() if found.get(name) != value]
    return {"status": "PASS" if not differences else "FAIL", "image_digest": digest,
            "application_files": len(expected), "found": len(found), "differences": differences}


def browser_acceptance(url: str, output: Path) -> dict[str, Any]:
    sync_playwright = importlib.import_module("playwright.sync_api").sync_playwright
    from scripts.check_dashboard_browser import assess_snapshot

    checks: dict[str, bool] = {}
    errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda _error: errors.append("pageerror"))
            response = page.goto(url, wait_until="networkidle", timeout=60000)
            checks["http_ok"] = response is not None and response.status == 200
            page.wait_for_function("window.Chart && Chart.getChart(document.querySelector('canvas'))", timeout=30000)
            for name, width in (("desktop", 1440), ("mobile", 390)):
                page.set_viewport_size({"width": width, "height": 1000})
                page.wait_for_timeout(500)
                page.screenshot(path=str(output / (name + ".png")), full_page=True)
                value = page.evaluate("""() => {
                    const legend=Chart.getChart(document.getElementById('hourlyForecastChart')).legend;
                    return {text:document.body.innerText,
                      rendered_charts:[...document.querySelectorAll('canvas')].filter(e=>
                        Chart.getChart(e)?.data.datasets.some(d=>d.data.some(v=>v!==null&&
                        Number.isFinite(typeof v==='object'?v?.y:v)))).length,
                      document_width:document.documentElement.scrollWidth,viewport_width:innerWidth,
                      hourly_legend:{top:legend.top,bottom:legend.bottom,boxes:legend.legendHitBoxes}};
                }""")
                checks.update({name + "." + key: value for key, value in assess_snapshot(value).items()})
            result = page.request.post(url + "api/dashboard/actual-import", data={"target_date": "2026-11-01"})
            checks["writes_rejected"] = result.status == 403 and result.json().get("error") == "local_recovery_read_only"
            checks["no_page_errors"] = not errors
        finally:
            browser.close()
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks}


def check(root: Path, *, emulator_port: int, port: int) -> dict[str, Any]:
    runtime = root / "runtime"
    source = runtime / "source"
    environment = local_environment(runtime, emulator_port, port)
    os.environ.clear()
    os.environ.update(environment)
    os.chdir(source)
    sys.path.insert(0, str(source))
    output = runtime / "acceptance"
    output.mkdir(exist_ok=True)
    from google.auth.credentials import AnonymousCredentials
    from google.cloud.firestore import Client
    from app.dashboard.data import clear_dashboard_cache, load_dashboard_slice
    from scripts.validate_dashboard_backend_parity import DATASETS, IGNORED_FIELDS, compare_rows, partition_forecast_metadata
    from scripts.validate_forecast_display_evidence import load_forecast_evidence_for_window, validate_forecast_window_evidence

    log = output / "sqlite-sync.private.log"
    with log.open("wb") as stream:
        subprocess.run([sys.executable, "scripts/sync_validation_state.py", "--sqlite", environment["DATA_DB_PATH"]],
                       env=environment, stdout=stream, stderr=stream, check=True)
    credential_factory: Any = AnonymousCredentials
    client = Client(project=PROJECT, credentials=credential_factory())
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    start, end = (today - timedelta(days=30)).isoformat(), today.isoformat()
    sqlite_path = runtime / "solar_monitor.db"
    os.environ["DATA_BACKEND"] = "firestore"
    firestore_data = load_dashboard_slice(sqlite_path, end_date=end, window_days=31, include_static=False).data
    runtime_receipt = json.loads((runtime / "receipt.safe.json").read_text(encoding="utf-8"))
    snapshot_directory = runtime_receipt["snapshot_directory"]
    if snapshot_directory not in {"latest", "refresh.pending"}:
        raise ValueError("Invalid snapshot selection")
    image_source = deployed_source_check(root / snapshot_directory, source)
    write_json(output / "deployed-source.safe.json", image_source)
    evidence = load_forecast_evidence_for_window(client=client, storage=FrozenStorage(root / snapshot_directory),
               start_date=start, end_date=end, selected_hourly=firestore_data.forecast_hourly, deadline=time.monotonic() + 120)
    result = validate_forecast_window_evidence(start_date=start, end_date=end, evidence_by_date=evidence,
             displayed_hourly=firestore_data.forecast_hourly, displayed_battery_daily=firestore_data.battery_daily)
    write_json(output / "forecast-evidence.private.json", result)
    os.environ["DATA_BACKEND"] = "sqlite"
    clear_dashboard_cache()
    sqlite_data = load_dashboard_slice(sqlite_path, end_date=end, window_days=31, include_static=False).data
    errors: dict[str, list[str]] = {}
    for dataset in DATASETS:
        left, right = getattr(sqlite_data, dataset), getattr(firestore_data, dataset)
        extra: list[str] = []
        if dataset == "forecast_hourly":
            left, right, extra = partition_forecast_metadata(sqlite_rows=left, firestore_rows=right, day_results=result["days"])
        errors[dataset] = extra + compare_rows(left, right, ignored_fields=IGNORED_FIELDS.get(dataset))
    write_json(output / "backend-parity.private.json", errors)
    try:
        browser = browser_acceptance(f"http://127.0.0.1:{port}/", output)
    except Exception as error:
        browser = {"status": "INCONCLUSIVE", "reason": type(error).__name__}
    write_json(output / "browser.safe.json", browser)
    summary = {"backend_parity": "PASS" if not any(errors.values()) else "FAIL",
               "deployed_application_source": image_source["status"],
               "parity_error_counts": {key: len(value) for key, value in errors.items()},
               "forecast_saved_input_verification": result["status"], "forecast_coverage": result["coverage"],
               "browser_acceptance": browser["status"], "fresh_weather_prediction": "NOT_RUN", "remote_writes": 0}
    write_json(output / "summary.safe.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--emulator-port", type=int, default=8781)
    parser.add_argument("--port", type=int, default=8780)
    args = parser.parse_args()
    result = check(args.root.resolve(), emulator_port=args.emulator_port, port=args.port)
    print(json.dumps(result))
    return 0 if all(result[key] == "PASS" for key in ("deployed_application_source", "backend_parity", "forecast_saved_input_verification", "browser_acceptance")) else 2


if __name__ == "__main__":
    raise SystemExit(main())

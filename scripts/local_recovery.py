"""Latest-only backup publication and isolated local Firestore recovery.

Cloud capture belongs exclusively to backup_operational_state_from_env.ps1.
This module can write only owned local files and the loopback emulator.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, cast

import requests

OWNER = "solar-local-recovery-v1"
PROJECT = "demo-solar-local-recovery"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def checksum(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def owned_root(root: Path) -> Path:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    marker = root / "owner.json"
    if marker.exists():
        if json.loads(marker.read_text(encoding="utf-8")) != {"owner": OWNER}:
            raise ValueError("Recovery directory belongs to another operation")
    else:
        if any(p.name not in {"refresh.pending", "operation.lock"} for p in root.iterdir()):
            raise ValueError("Refusing to adopt a nonempty recovery directory")
        write_json(marker, {"owner": OWNER})
    return root


def remove_owned(root: Path, name: str) -> None:
    if name not in {"latest.previous", "runtime"}:
        raise ValueError("Deletion is outside managed temporary directories")
    path = root / name
    if path.is_symlink() or path.resolve().parent != root.resolve():
        raise ValueError("Recovery path escapes its owned directory")
    if path.exists():
        def remove_readonly(function: Any, filename: str, error: BaseException) -> None:
            mode = Path(filename).stat().st_mode
            if not isinstance(error, PermissionError) or mode & stat.S_IWRITE:
                raise error
            # Git marks pack files read-only on Windows. Only this owned
            # temporary tree may have that attribute cleared for removal.
            os.chmod(filename, mode | stat.S_IWRITE)
            function(filename)

        shutil.rmtree(path, onexc=remove_readonly)


def verify_snapshot(path: Path) -> dict[str, Any]:
    path = path.resolve()
    manifest = cast(dict[str, Any], json.loads((path / "manifest.private.json").read_text(encoding="utf-8")))
    stages = manifest.get("stages", {})
    required = {"local", "firestore", "firestore_settings", "storage", "configuration", "secrets", "drive", "logs", "registry"}
    if manifest.get("selection_policy", {}).get("drive") == "excluded_by_user":
        if stages.get("drive", {}).get("status") != "excluded" or stages["drive"].get("reason") != "user_scope_exclusion":
            raise ValueError("Drive exclusion lacks an explicit scoped receipt")
        required.remove("drive")
    if (manifest.get("status") != "complete" or manifest.get("gaps") != []
            or not required.issubset(stages)
            or any(stages[key].get("status") != "success" for key in required)):
        raise ValueError("Incomplete backup cannot replace latest or start recovery")
    if not manifest.get("files"):
        raise ValueError("Backup file inventory is empty")
    for relative, metadata in manifest["files"].items():
        candidate = (path / relative).resolve()
        if not candidate.is_relative_to(path) or not candidate.is_file() or checksum(candidate) != metadata["sha256"]:
            raise ValueError("Backup checksum/path verification failed")
    summary = json.loads((path / "summary.safe.json").read_text(encoding="utf-8"))
    if summary.get("status") != "complete" or summary.get("gaps") != []:
        raise ValueError("Backup summary does not confirm complete acquisition")
    return manifest


def verify_preview(path: Path) -> dict[str, Any]:
    """Verify completed source/DB stages without claiming a full backup passed."""
    manifest = cast(dict[str, Any], json.loads((path / "manifest.private.json").read_text(encoding="utf-8")))
    for name in ("local", "firestore", "firestore_settings", "configuration", "secrets"):
        stage = manifest["stages"].get(name, {})
        if stage.get("status") != "success":
            raise ValueError("Local preview requires a completed database/source capture")
        if not stage.get("files"):
            raise ValueError("Preview stage file inventory is empty")
        for relative, digest in stage.get("files", {}).items():
            target = (path / relative).resolve()
            if not target.is_relative_to(path.resolve()) or checksum(target) != digest:
                raise ValueError("Preview source checksum mismatch")
    return manifest


def reconcile_publication(root: Path) -> None:
    """Recover a directory rename interrupted before the new latest was visible."""
    root = owned_root(root)
    latest, previous = root / "latest", root / "latest.previous"
    if previous.exists():
        if latest.exists():
            verify_snapshot(latest)
            remove_owned(root, "latest.previous")
        else:
            verify_snapshot(previous)
            previous.rename(latest)


def promote(root: Path) -> dict[str, Any]:
    root = owned_root(root)
    reconcile_publication(root)
    pending, latest, previous = root / "refresh.pending", root / "latest", root / "latest.previous"
    manifest = verify_snapshot(pending)
    if latest.exists():
        verify_snapshot(latest)
        latest.rename(previous)
    try:
        pending.rename(latest)
    except OSError:
        if previous.exists() and not latest.exists():
            previous.rename(latest)
        raise
    # The old copy exists only during replacement, never as a retained generation.
    remove_owned(root, "latest.previous")
    return {"status": "PASS", "retained_complete_backups": 1,
            "snapshot_read_time": manifest["stages"]["firestore"]["result"].get("read_time")}


def local_environment(runtime: Path, emulator_port: int, dashboard_port: int) -> dict[str, str]:
    keep = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "USERPROFILE",
            "APPDATA", "LOCALAPPDATA", "HOME", "LANG", "LC_ALL"}
    result = {key: value for key, value in os.environ.items() if key.upper() in keep}
    result.update(FIRESTORE_EMULATOR_HOST=f"127.0.0.1:{emulator_port}",
                  FIRESTORE_PROJECT_ID=PROJECT, GOOGLE_CLOUD_PROJECT=PROJECT,
                  FIRESTORE_DATABASE_ID="(default)", DATA_BACKEND="firestore",
                  DATA_DB_PATH=str(runtime / "solar_monitor.db"), DRY_RUN="true",
                  DASHBOARD_HOST="127.0.0.1", DASHBOARD_PORT=str(dashboard_port),
                  DASHBOARD_ACTUAL_IMPORT_JOB="", DASHBOARD_SNAPSHOT_GCS_PREFIX="",
                  NIGHT_PLAN_ARCHIVE_GCS_PREFIX="", DASHBOARD_AUTH_ENABLED="false",
                  DASHBOARD_SNAPSHOT_LOCAL_DIR=str(runtime / "dashboard_snapshots"),
                  CLOUDSDK_CONFIG=str(runtime / "empty-cloud-config"), PYTHONUTF8="1")
    return result


def remap_references(value: Any, original_project: str) -> Any:
    if isinstance(value, list):
        return [remap_references(item, original_project) for item in value]
    if isinstance(value, dict):
        return {key: (item.replace(f"projects/{original_project}/", f"projects/{PROJECT}/", 1)
                      if key == "referenceValue" and isinstance(item, str)
                      and item.startswith(f"projects/{original_project}/")
                      else remap_references(item, original_project)) for key, item in value.items()}
    return value


def canonical_document(fields: dict[str, Any]) -> bytes:
    from google.cloud.firestore_v1.types import Document
    document = Document.from_json(json.dumps({"fields": fields}))
    return cast(bytes, document._pb.SerializeToString(deterministic=True))


def restore_documents(snapshot: Path, emulator_port: int, manifest: dict[str, Any]) -> dict[str, int]:
    """Commit and read back every field; transport is fixed to numeric loopback."""
    if not 1024 <= emulator_port <= 65535:
        raise ValueError("Invalid local emulator port")
    session = requests.Session()
    session.trust_env = False
    host = f"http://127.0.0.1:{emulator_port}/v1/"
    counts: dict[str, int] = {}
    databases = json.loads((snapshot / "firestore/databases.json").read_text(encoding="utf-8"))
    for index, database in enumerate(databases):
        database_id = database["name"].rsplit("/", 1)[1]
        prefix = f"projects/{PROJECT}/databases/{database_id}/documents/"
        endpoint = host + prefix.rstrip("/")
        batch: list[dict[str, Any]] = []
        seen: set[str] = set()
        count = 0

        def flush() -> None:
            response = session.post(endpoint + ":commit", json={"writes": [{"update": item} for item in batch]}, timeout=60)
            response.raise_for_status()
            result = session.post(endpoint + ":batchGet", json={"documents": [item["name"] for item in batch]}, timeout=60)
            result.raise_for_status()
            rows = result.json()
            rows = rows if isinstance(rows, list) else [rows]
            found = {item["found"]["name"]: item["found"].get("fields", {}) for item in rows if "found" in item}
            if len(found) != len(batch) or any(canonical_document(found.get(item["name"], {}))
                                              != canonical_document(item["fields"]) for item in batch):
                raise ValueError("Local Firestore document readback mismatch")
            batch.clear()

        with gzip.open(snapshot / f"firestore/database-{index}-documents.jsonl.gz", "rt", encoding="utf-8") as stream:
            size = 0
            for line in stream:
                original = json.loads(line)
                name = prefix + original["name"].split("/documents/", 1)[1]
                if name in seen:
                    raise ValueError("Duplicate original document path")
                seen.add(name)
                row = {"name": name, "fields": remap_references(original.get("fields", {}), manifest["project"])}
                row_size = len(json.dumps(row).encode("utf-8"))
                if batch and (len(batch) >= 100 or size + row_size > 4_000_000):
                    flush()
                    size = 0
                batch.append(row)
                size += row_size
                count += 1
            if batch:
                flush()
        counts[database_id] = count
    expected = manifest["stages"]["firestore"]["result"]["documents"]
    if sum(counts.values()) != expected:
        raise ValueError("Local Firestore total count mismatch")
    session.close()
    return counts


def stop_runtime(root: Path) -> None:
    state = root / "runtime/processes.private.json"
    if not state.exists():
        return
    processes = json.loads(state.read_text(encoding="utf-8"))
    token = processes["instance"]
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ValueError("Invalid owned process identity")
    recorded = ",".join(str(int(pid)) for pid in processes["pids"])
    # A Java launcher may spawn another java.exe with the same instance option.
    # Match that exact option, including when the recorded launcher has exited.
    # Recycled recorded PIDs must still be rejected before stopping anything.
    command = (f"$recorded=@({recorded}); $all=@(Get-CimInstance Win32_Process -ErrorAction Stop); "
               "foreach($id in $recorded) { $p=$all | Where-Object ProcessId -eq $id; "
               f"if($p -and (-not $p.CommandLine -or -not $p.CommandLine.Contains('{token}'))) "
               "{ throw 'Process identity differs; refusing to stop it' } }; "
               "$owned=@($all | Where-Object { $_.CommandLine -and "
               f"(($_.ProcessId -in $recorded -and $_.CommandLine.Contains('{token}')) -or "
               f"($_.Name -eq 'java.exe' -and $_.CommandLine.Contains('-Dsolar.recovery.instance={token} '))) }}); "
               "foreach($p in $owned) { Stop-Process -Id $p.ProcessId -ErrorAction Stop }; "
               "foreach($p in $owned) { Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue | "
               "Wait-Process -Timeout 15 -ErrorAction Stop }; exit 0")
    subprocess.run(["pwsh", "-NoProfile", "-Command", command], check=True, capture_output=True)


def require_free(port: int) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))


def start_process(command: list[str], *, runtime: Path, name: str, env: dict[str, str], cwd: Path) -> subprocess.Popen[bytes]:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with (runtime / (name + ".private.log")).open("ab") as stream:
        return subprocess.Popen(command, stdout=stream, stderr=stream, cwd=cwd, env=env, creationflags=flags)


def restore(root: Path, *, jar: Path, emulator_port: int, dashboard_port: int, preview_pending: bool = False) -> dict[str, Any]:
    root = owned_root(root)
    reconcile_publication(root)
    snapshot = root / ("refresh.pending" if preview_pending else "latest")
    manifest = verify_preview(snapshot) if preview_pending else verify_snapshot(snapshot)
    if not jar.is_file():
        raise ValueError("Install the official Firestore emulator before restoring")
    stop_runtime(root)
    remove_owned(root, "runtime")
    runtime = root / "runtime"
    runtime.mkdir()
    (runtime / "empty-cloud-config").mkdir()
    (runtime / "dashboard_snapshots").mkdir()
    require_free(emulator_port)
    require_free(dashboard_port)
    revision = manifest["stages"]["local"]["result"]["source_revision"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Invalid backup source revision")
    source = runtime / "source"
    subprocess.run(["git", "clone", "--no-checkout", str(snapshot / "local/source.bundle"), str(source)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(source), "checkout", "--detach", revision], check=True, capture_output=True)
    env = local_environment(runtime, emulator_port, dashboard_port)
    instance = uuid.uuid4().hex
    state: dict[str, Any] = {"instance": instance, "pids": []}
    java = start_process(["java", f"-Dsolar.recovery.instance={instance}", "-jar", str(jar.resolve()),
                          "--host", "127.0.0.1", "--port", str(emulator_port), "--project_id", PROJECT],
                         runtime=runtime, name="emulator", env=env, cwd=runtime)
    state["pids"].append(java.pid)
    write_json(runtime / "processes.private.json", state)
    deadline = time.monotonic() + 60
    while True:
        if java.poll() is not None:
            raise ValueError("Local emulator failed; inspect its private log")
        try:
            with socket.create_connection(("127.0.0.1", emulator_port), timeout=1):
                break
        except OSError:
            if time.monotonic() >= deadline:
                raise ValueError("Local emulator startup timed out") from None
            time.sleep(0.5)
    try:
        counts = restore_documents(snapshot, emulator_port, manifest)
        command = [sys.executable, str(Path(__file__).resolve()), "dashboard", "--root", str(root),
                   "--emulator-port", str(emulator_port), "--port", str(dashboard_port), "--instance", instance]
        dashboard = start_process(command, runtime=runtime, name="dashboard", env=env, cwd=source)
        state["pids"].append(dashboard.pid)
        write_json(runtime / "processes.private.json", state)
        url = f"http://127.0.0.1:{dashboard_port}/"
        session = requests.Session()
        session.trust_env = False
        deadline = time.monotonic() + 90
        while True:
            if dashboard.poll() is not None:
                raise ValueError("Local dashboard failed; inspect its private log")
            try:
                response = session.get(url, timeout=10)
                response.raise_for_status()
                break
            except requests.RequestException:
                if time.monotonic() >= deadline:
                    raise ValueError("Local dashboard startup timed out") from None
                time.sleep(0.5)
        session.close()
        receipt = {"status": "PARTIAL" if preview_pending else "PASS", "preview_only": preview_pending,
                   "backup_capture": manifest["status"], "snapshot_directory": snapshot.name,
                   "source_revision": revision, "firestore_documents": counts,
                   "original_fields_readback": "PASS", "server_timestamps_recreated": True,
                   "reference_project_mapping": "original project -> local demo project",
                   "emulator_sha256": checksum(jar), "dashboard_url": url,
                   "remote_writes": 0, "production_deployment": "NOT_RUN", "device_control": "NOT_RUN",
                   "forecast_replay": "NOT_RUN", "browser_acceptance": "NOT_RUN"}
        write_json(runtime / "receipt.safe.json", receipt)
        return receipt
    except Exception:
        stop_runtime(root)
        raise


def dashboard(root: Path, *, emulator_port: int, port: int) -> None:
    """Run the recovered application with no production credentials or actions."""
    runtime = root.resolve() / "runtime"
    source = runtime / "source"
    environment = local_environment(runtime, emulator_port, port)
    os.environ.clear()
    os.environ.update(environment)
    os.chdir(source)
    sys.path.insert(0, str(source))

    def local_network_only(event: str, arguments: tuple[Any, ...]) -> None:
        if event == "socket.connect":
            address = arguments[1]
            if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1", "localhost"}:
                raise PermissionError("Local recovery forbids outbound connections")
        if event == "socket.getaddrinfo" and arguments[0] not in {"127.0.0.1", "::1", "localhost", None}:
            raise PermissionError("Local recovery forbids external DNS")

    sys.addaudithook(local_network_only)
    from http.server import ThreadingHTTPServer
    from app.dashboard.server import Handler

    class ReadOnlyHandler(Handler):
        def do_POST(self) -> None:  # noqa: N802
            self._import_response(403, {"error": "local_recovery_read_only"})

    ThreadingHTTPServer(("127.0.0.1", port), ReadOnlyHandler).serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify", "promote", "restore", "preview-pending", "dashboard", "stop"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--jar", type=Path, default=Path.home() / ".cache/firebase/emulators/cloud-firestore-emulator-v1.21.0.jar")
    parser.add_argument("--emulator-port", type=int, default=8781)
    parser.add_argument("--port", type=int, default=8780)
    parser.add_argument("--instance", default="")
    args = parser.parse_args()
    if args.action == "prepare":
        reconcile_publication(args.root)
    elif args.action == "verify":
        verify_snapshot(args.root / "latest")
    elif args.action == "promote":
        print(json.dumps(promote(args.root)))
    elif args.action in {"restore", "preview-pending"}:
        print(json.dumps(restore(args.root, jar=args.jar, emulator_port=args.emulator_port, dashboard_port=args.port,
                                 preview_pending=args.action == "preview-pending")))
    elif args.action == "dashboard":
        dashboard(args.root, emulator_port=args.emulator_port, port=args.port)
    else:
        stop_runtime(owned_root(args.root))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        if "--root" in sys.argv:
            failure_root = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
            if (failure_root / "owner.json").is_file():
                write_json(failure_root / "failure.private.json", {"traceback": traceback.format_exc()})
        # Exception text from subprocesses/HTTP may contain private identifiers.
        print("Local recovery failed:", type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None

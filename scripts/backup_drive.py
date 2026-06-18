from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.drive_backup import (
    export_data_backup,
    export_source_backup,
    build_drive_service,
)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and ((value[0] == '"' and value[-1] == '"') or (value[0] == "'" and value[-1] == "'")):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Back up source and Firestore data to Google Drive.")
    parser.add_argument("--mode", choices=("source", "data", "all"), default=_env("DRIVE_BACKUP_MODE", "all") or "all")
    parser.add_argument("--folder-id", default=_env("DRIVE_BACKUP_FOLDER_ID"))
    parser.add_argument("--repo-root", default=_env("DRIVE_BACKUP_REPO_ROOT"))
    parser.add_argument("--skip-drive", action="store_true", help="Create local backup artifacts without uploading to Drive.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print the JSON result.")
    return parser


def main() -> int:
    _load_dotenv()
    args = build_parser().parse_args()
    repo_root = Path(args.repo_root).resolve() if args.repo_root else None
    drive_service = None if args.skip_drive else build_drive_service()
    folder_id = args.folder_id or None

    results: dict[str, Any] = {
        "mode": args.mode,
        "folder_id": folder_id,
        "source": None,
        "data": None,
    }

    if args.mode in {"source", "all"}:
        created, payload = export_source_backup(service=drive_service, folder_id=folder_id, repo_root_path=repo_root)
        results["source"] = {"created": created, **payload}

    if args.mode in {"data", "all"}:
        results["data"] = export_data_backup(service=drive_service, folder_id=folder_id)

    text = json.dumps(results, ensure_ascii=False, indent=2 if args.pretty else None)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

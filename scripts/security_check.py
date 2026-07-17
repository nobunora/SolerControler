from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _read_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _require_line(path: Path, needle: str, failures: list[str]) -> None:
    if not path.exists():
        failures.append(f"missing file: {path}")
        return
    content = path.read_text(encoding="utf-8")
    if needle not in content:
        failures.append(f"{path} does not contain required entry: {needle}")


def _check_sensitive_env_values(
    root: Path, env_map: dict[str, str], failures: list[str]
) -> None:
    credential_keys = {
        "DASHBOARD_BASIC_PASSWORD",
        "DASHBOARD_BASIC_USER",
        "DASHBOARD_SESSION_SECRET",
        "KP_MONITOR_PASSWORD",
        "KP_MONITOR_USERNAME",
        "MONITOR_PASSWORD",
        "MONITOR_USERNAME",
        "PGPASSWORD",
    }
    sensitive_keys = {
        key
        for key in env_map
        if key in credential_keys
        or any(marker in key.upper() for marker in ("API_KEY", "PRIVATE_KEY", "TOKEN"))
    }
    sensitive_keys.update(
        {
            "DRIVE_BACKUP_FOLDER_ID",
            "GCP_BILLING_ACCOUNT_ID",
            "SHEETS_SPREADSHEET_ID",
            "SHEETS_SHARE_EMAIL",
        }
    )
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout.split(b"\0")
    for raw_path in tracked:
        if not raw_path:
            continue
        relative = raw_path.decode("utf-8", errors="surrogateescape")
        path = root / relative
        try:
            content = path.read_bytes()
        except OSError as exc:
            failures.append(f"could not inspect tracked file {relative}: {exc}")
            continue
        for key in sensitive_keys:
            value = env_map.get(key, "").strip()
            normalized = value.lower().strip("<>{}[]")
            is_placeholder = normalized.startswith(("your_", "example_")) or normalized in {
                "changeme",
                "change_me",
                "password",
            }
            if (
                len(value) >= 8
                and not is_placeholder
                and value.encode("utf-8") in content
            ):
                failures.append(
                    f"tracked file contains the local .env value for {key}: {relative}"
                )


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    failures: list[str] = []
    warnings: list[str] = []

    _require_line(root / ".gitignore", ".env", failures)
    _require_line(root / ".gitignore", "artifacts/", failures)
    _require_line(root / ".dockerignore", ".env", failures)
    _require_line(root / ".dockerignore", "artifacts/", failures)

    env_map = _read_dotenv(root / ".env")
    _check_sensitive_env_values(root, env_map, failures)
    base_url = env_map.get("KP_BASE_URL", "")
    if base_url and not base_url.lower().startswith("https://"):
        failures.append("KP_BASE_URL must start with https:// for production safety")

    use_har = env_map.get("KP_USE_HAR_CREDENTIALS", "").strip().lower()
    if use_har in {"1", "true", "yes", "on"}:
        warnings.append("KP_USE_HAR_CREDENTIALS=true (disable for production if possible)")

    deploy_script = (root / "scripts" / "deploy_gcp_jobs.ps1").read_text(encoding="utf-8")
    for line in deploy_script.splitlines():
        if "--set-env-vars" in line and (
            "KP_MONITOR_PASSWORD=" in line or "KP_MONITOR_USERNAME=" in line
        ):
            failures.append("deploy script appears to pass monitor credentials via env vars")
            break
    if "--set-secrets" not in deploy_script:
        failures.append("deploy script must use Secret Manager via --set-secrets")

    if warnings:
        for line in warnings:
            print(f"[WARN] {line}")

    if failures:
        for line in failures:
            print(f"[FAIL] {line}")
        return 1

    print("[OK] security_check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

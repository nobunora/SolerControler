"""Prune only backed-up, unreferenced images after a completed role migration."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any
import requests


class PruneBlocked(RuntimeError):
    """Safe failure without cloud identifiers or credentials."""


def verify_oci(root: Path, digest: str, seen: set[str] | None = None) -> set[str]:
    seen = set() if seen is None else seen
    if digest in seen:
        return seen
    if not digest.startswith("sha256:") or len(digest) != 71:
        raise PruneBlocked("Invalid OCI digest")
    path = root / digest[7:]
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest[7:]:
        raise PruneBlocked("OCI backup missing or corrupt")
    seen.add(digest)
    try:
        document = json.loads(path.read_bytes())
    except (ValueError, UnicodeDecodeError):
        return seen  # Compressed layer, already byte-verified.
    if not isinstance(document, dict):
        return seen
    children = document.get("manifests", []) + document.get("layers", [])
    if document.get("config"):
        children.append(document["config"])
    for child in children:
        verify_oci(root, child["digest"], seen)
    return seen


class Pruner:
    def __init__(self, backup: Path, state_path: Path, acceptance_path: Path, output: Path) -> None:
        self.project = os.environ["GCP_PROJECT_ID"]
        self.region = os.environ["GCP_REGION"]
        self.backup, self.output = backup.resolve(), output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        manifest = json.loads((backup / "manifest.private.json").read_text(encoding="utf-8"))
        if manifest["project"] != self.project:
            raise PruneBlocked("Backup project mismatch")
        if self.state.get("kind") != "separated_runtime_deployment" or self.state.get("status") != "complete":
            raise PruneBlocked("Role migration is not complete")
        for stage in self.state["stages"].values():
            if stage.get("status") != "success":
                raise PruneBlocked("A required release stage did not pass")
        acceptance = json.loads(acceptance_path.read_text(encoding="utf-8-sig"))
        if acceptance.get("repository_commit") != self.state["repository_commit"] or any(
            acceptance.get(key) != "passed" for key in ("browser_status", "current_forecast_status")):
            raise PruneBlocked("Current production display/data acceptance is missing")
        for required in ("local", "firestore", "configuration", "secrets", "registry"):
            if manifest["stages"].get(required, {}).get("status") != "success":
                raise PruneBlocked("Current operational recovery coverage is incomplete")
        self.session = requests.Session()
        self.session.headers["Authorization"] = "Bearer " + os.environ["SOLAR_BACKUP_READ_TOKEN"]

    def get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.session.get(url, params=params, timeout=(10, 60))
        if response.status_code != 200:
            raise PruneBlocked("Fresh cloud reference query failed")
        result: dict[str, Any] = response.json()
        return result

    def pages(self, url: str, key: str, params: dict[str, Any] | None = None) -> list[Any]:
        params = dict(params or {})
        rows = []
        while True:
            page = self.get(url, params)
            rows.extend(page.get(key, []))
            if not page.get("nextPageToken"):
                return rows
            params["pageToken"] = page["nextPageToken"]

    def cloud(self, args: list[str]) -> str:
        result = subprocess.run(["pwsh", "-NoProfile", "-File", "scripts/gcloud.ps1", *args],
                                capture_output=True, text=True, encoding="utf-8")
        if result.returncode:
            raise PruneBlocked("Cloud operation failed; deletion stopped")
        return result.stdout.strip()

    def references(self) -> tuple[set[str], list[dict[str, Any]], list[dict[str, Any]]]:
        identity = self.get("https://cloudresourcemanager.googleapis.com/v1/projects/" + self.project)
        if identity.get("projectId") != self.project:
            raise PruneBlocked("Authenticated project identity does not match configuration")
        number = identity["projectNumber"]
        assets = self.pages(f"https://cloudasset.googleapis.com/v1/projects/{self.project}:searchAllResources", "results",
                            {"pageSize": 500, "assetTypes": ["run.googleapis.com/Job", "run.googleapis.com/Service"]})
        refs: set[str] = set()
        jobs: list[dict[str, Any]] = []
        snapshots = []
        def collect(value: Any) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == "image" and isinstance(item, str):
                        refs.add(item)
                    else:
                        collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)
        for asset in assets:
            name = asset["name"].removeprefix("//run.googleapis.com/")
            if not any(name.startswith(f"projects/{project}/locations/") for project in (self.project, number)):
                raise PruneBlocked("Cloud reference belongs to another project")
            resource = self.get("https://run.googleapis.com/v2/" + name)
            snapshots.append(resource)
            collect(resource)
            if asset["assetType"] == "run.googleapis.com/Job":
                jobs.append(resource)
            else:
                revisions = self.pages("https://run.googleapis.com/v2/" + name + "/revisions", "revisions", {"pageSize": 1000})
                # Keep traffic destinations and one immediate previous revision.
                ordered = sorted(revisions, key=lambda row: row.get("createTime", ""), reverse=True)
                routed = {row.get("revision", "").rsplit("/", 1)[-1] for row in resource.get("traffic", []) + resource.get("trafficStatuses", [])}
                for revision in ordered[:2] + [r for r in ordered if r["name"].rsplit("/", 1)[-1] in routed]:
                    collect(revision)
        if not jobs or not refs:
            raise PruneBlocked("Empty reference inventory cannot authorize deletion")
        resolved = set()
        for ref in refs:
            if "@sha256:" not in ref:
                digest = self.cloud(["artifacts", "docker", "images", "describe", ref, "--project", self.project,
                                     "--format", "value(image_summary.digest)"])
                if not digest.startswith("sha256:"):
                    raise PruneBlocked("Mutable reference cannot be resolved")
                ref = ref.rsplit(":", 1)[0] + "@" + digest
            resolved.add(ref)
        return resolved, jobs, snapshots

    def run(self, *, apply: bool = False, retire_manual_backups: bool = False) -> dict[str, Any]:
        refs, jobs, snapshots = self.references()
        (self.output / "references.private.json").write_text(json.dumps(snapshots, indent=2), encoding="utf-8")
        retired = 0
        if retire_manual_backups:
            assets = self.pages(f"https://cloudasset.googleapis.com/v1/projects/{self.project}:searchAllResources", "results",
                                {"pageSize": 500, "assetTypes": ["cloudscheduler.googleapis.com/Job"]})
            schedules = [self.get("https://cloudscheduler.googleapis.com/v1/" + asset["name"].removeprefix("//cloudscheduler.googleapis.com/")) for asset in assets]
            if not schedules:
                raise PruneBlocked("Scheduler reference inventory is empty")
            targets = [row.get("httpTarget", {}).get("uri", "") for row in schedules]
            for job in jobs:
                name = job["name"].rsplit("/", 1)[-1]
                if not name.startswith("solar-drive-backup-manual"):
                    continue
                containers = job["template"]["template"]["containers"]
                if len(containers) != 1 or "scripts/backup_drive.py" not in containers[0].get("args", []):
                    raise PruneBlocked("Manual job purpose is not the retained backup command")
                if any(name in target for target in targets):
                    raise PruneBlocked("Manual backup job still has a scheduled reference")
                executions = self.pages("https://run.googleapis.com/v2/" + job["name"] + "/executions", "executions", {"pageSize": 1000})
                if any(not row.get("completionTime") for row in executions):
                    raise PruneBlocked("Manual backup job still has an active execution")
                if apply:
                    region = job["name"].split("/locations/", 1)[1].split("/", 1)[0]
                    self.cloud(["run", "jobs", "delete", name, "--project", self.project, "--region", region, "--quiet"])
                retired += 1
            if apply and retired:
                refs, _, _ = self.references()
        repos = self.pages(f"https://artifactregistry.googleapis.com/v1/projects/{self.project}/locations/-/repositories", "repositories", {"pageSize": 1000})
        images = []
        for repo in repos:
            if repo.get("format") == "DOCKER":
                images.extend(self.pages("https://artifactregistry.googleapis.com/v1/" + repo["name"] + "/dockerImages", "dockerImages", {"pageSize": 1000}))
        # One complete legacy runner is retained for an explicit rollback/rebuild;
        # retained web revisions were protected above. No age-based deletion.
        legacy = f"{self.region}-docker.pkg.dev/{self.project}/{os.environ['GCP_RUNNER_REPOSITORY']}/{os.environ['GCP_RUNNER_IMAGE_NAME']}@"
        old = sorted([row for row in images if row["uri"].startswith(legacy)], key=lambda row: row.get("uploadTime", ""), reverse=True)
        if old:
            refs.add(old[0]["uri"])
        blobs = self.backup / "registry/oci/blobs/sha256"
        for digest in self.state["image_digests"].values():
            verify_oci(blobs, digest)
        packages = {(os.environ["GCP_RUNNER_REPOSITORY"], os.environ["GCP_RUNNER_IMAGE_NAME"] + suffix)
                    for suffix in ("", "-planner", "-control")}
        packages.add((os.environ["GCP_DASHBOARD_REPOSITORY"], os.environ["GCP_DASHBOARD_IMAGE_NAME"]))
        def in_scope(uri: str) -> bool:
            parts = uri.split("/", 3)
            return len(parts) == 4 and parts[1] == self.project and (parts[2], parts[3].split("@", 1)[0]) in packages
        candidates = [row for row in images if in_scope(row["uri"]) and row["uri"] not in refs]
        verified: set[str] = set()
        for row in candidates:
            verify_oci(blobs, row["uri"].rsplit("@", 1)[1], verified)
        (self.output / "candidates.private.json").write_text(json.dumps(candidates, indent=2), encoding="utf-8")
        deleted = 0
        for row in candidates:
            if apply:
                fresh, _, _ = self.references()
                if row["uri"] in fresh:
                    raise PruneBlocked("Reference changed during cleanup")
                self.cloud(["artifacts", "docker", "images", "delete", row["uri"], "--project", self.project, "--delete-tags", "--quiet"])
                deleted += 1
        summary = {"status": "passed", "applied": apply, "candidates": len(candidates), "deleted": deleted,
                   "protected_references": len(refs), "verified_candidate_blobs": len(verified),
                   "manual_backups_retired": retired if apply else 0, "at": datetime.now(timezone.utc).isoformat()}
        (self.output / "summary.safe.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--release-state", type=Path, required=True)
    parser.add_argument("--acceptance", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--retire-manual-backups", action="store_true")
    args = parser.parse_args()
    print(json.dumps(Pruner(args.backup, args.release_state, args.acceptance, args.output).run(apply=args.apply,
                     retire_manual_backups=args.retire_manual_backups)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

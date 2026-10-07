import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


@pytest.fixture
def module():
    path = Path(__file__).resolve().parents[1] / "scripts/prune_separated_images.py"
    spec = importlib.util.spec_from_file_location("safe_image_prune", path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_oci_verification_requires_every_layer_and_rejects_corruption(module, tmp_path):
    raw = b"compressed image layer"
    layer = "sha256:" + hashlib.sha256(raw).hexdigest()
    manifest = json.dumps({"schemaVersion": 2, "layers": [{"digest": layer}]}).encode()
    digest = "sha256:" + hashlib.sha256(manifest).hexdigest()
    (tmp_path / digest[7:]).write_bytes(manifest)
    with pytest.raises(module.PruneBlocked, match="missing or corrupt"):
        module.verify_oci(tmp_path, digest)
    (tmp_path / layer[7:]).write_bytes(raw)
    assert module.verify_oci(tmp_path, digest) == {layer, digest}
    (tmp_path / layer[7:]).write_bytes(b"changed")
    with pytest.raises(module.PruneBlocked, match="missing or corrupt"):
        module.verify_oci(tmp_path, digest)


def test_partial_release_cannot_authorize_pruning(module, tmp_path, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "unit-test")
    monkeypatch.setenv("GCP_REGION", "unit-test")
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"kind": "separated_runtime_deployment", "status": "running"}))
    (tmp_path / "manifest.private.json").write_text(json.dumps({"project": "unit-test"}))
    with pytest.raises(module.PruneBlocked, match="not complete"):
        module.Pruner(tmp_path, state, tmp_path / "missing-acceptance.json", tmp_path / "review")


def test_currently_referenced_image_and_rollback_are_never_delete_candidates(module, tmp_path, monkeypatch):
    pruner = module.Pruner.__new__(module.Pruner)
    pruner.project = "unit-test"
    pruner.region = "region"
    pruner.backup = tmp_path
    pruner.output = tmp_path
    pruner.state = {"image_digests": {}}
    for key, value in {"GCP_RUNNER_REPOSITORY": "controller", "GCP_RUNNER_IMAGE_NAME": "runner",
                       "GCP_DASHBOARD_REPOSITORY": "web", "GCP_DASHBOARD_IMAGE_NAME": "dashboard"}.items():
        monkeypatch.setenv(key, value)
    prefix = "region-docker.pkg.dev/unit-test/controller/runner@sha256:"
    active, rollback, retired = prefix + "a" * 64, prefix + "b" * 64, prefix + "c" * 64
    rows = [{"uri": uri, "uploadTime": time} for uri, time in [(active, "1"), (rollback, "3"), (retired, "0")]]
    monkeypatch.setattr(pruner, "references", lambda: ({active}, [], []))
    monkeypatch.setattr(pruner, "pages", lambda url, key, params: [{"name": "repo", "format": "DOCKER"}] if key == "repositories" else rows)
    monkeypatch.setattr(pruner, "cloud", lambda *args: pytest.fail("read-only review must not delete"))
    verified = []
    monkeypatch.setattr(module, "verify_oci", lambda root, digest, seen=None: verified.append(digest))
    result = pruner.run()
    assert result["candidates"] == 1 and result["deleted"] == 0
    assert verified == ["sha256:" + "c" * 64]


def test_scheduler_reference_inventory_queries_every_advertised_location(module, monkeypatch):
    pruner = module.Pruner.__new__(module.Pruner)
    pruner.project = "unit-test"
    queries = []

    def pages(url, key, params):
        queries.append(url)
        if key == "locations":
            return [{"locationId": "us-central1"}, {"locationId": "europe-west1"}]
        return [{"httpTarget": {"uri": "scheduled-reference"}}] if url.endswith("europe-west1/jobs") else []

    monkeypatch.setattr(pruner, "pages", pages)
    assert len(pruner.scheduler_jobs()) == 1
    assert len(queries) == 3 and queries[1].endswith("us-central1/jobs") and queries[2].endswith("europe-west1/jobs")


def test_failed_scheduler_region_query_prevents_any_manual_job_deletion(module, tmp_path, monkeypatch):
    pruner = module.Pruner.__new__(module.Pruner)
    pruner.project = "unit-test"
    pruner.output = tmp_path
    monkeypatch.setattr(pruner, "references", lambda: ({"active"}, [], []))

    def pages(url, key, params):
        if key == "locations":
            return [{"locationId": "us-central1"}]
        raise module.PruneBlocked("Fresh cloud reference query failed")

    monkeypatch.setattr(pruner, "pages", pages)
    monkeypatch.setattr(pruner, "cloud", lambda *args: pytest.fail("incomplete reference inventory must not delete"))
    with pytest.raises(module.PruneBlocked, match="reference query"):
        pruner.run(apply=True, retire_manual_backups=True)


@pytest.mark.parametrize("change,allowed", [("same", True), ("operator", True), ("runtime", False), ("dirty", False)])
def test_real_git_source_guard_allows_only_committed_cleanup_fixes(tmp_path, change, allowed):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

    git("init")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    (repo / "app").mkdir()
    (repo / "app/runtime.py").write_text("value = 1\n")
    (repo / "scripts").mkdir()
    operator = repo / "scripts/prune_separated_images.py"
    operator.write_text("# initial operator\n")
    git("add", ".")
    git("commit", "-m", "baseline")
    release = git("rev-parse", "HEAD")
    if change != "same":
        target = repo / "app/runtime.py" if change == "runtime" else operator
        target.write_text("# changed\n")
        if change != "dirty":
            git("add", ".")
            git("commit", "-m", "candidate")
    harness = tmp_path / "guard.ps1"
    harness.write_text("""param([string]$Source, [string]$Release)
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Source, [ref]$tokens, [ref]$errors)
$fn = $ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Assert-PruneSourceCompatibility'}, $true)
Invoke-Expression $fn.Extent.Text
Assert-PruneSourceCompatibility $Release
""", encoding="utf-8")
    source = Path(__file__).resolve().parents[1] / "scripts/prune_separated_images_from_env.ps1"
    result = subprocess.run(["pwsh", "-NoProfile", "-File", str(harness), str(source), release],
                            cwd=repo, capture_output=True, text=True, encoding="utf-8")
    assert (result.returncode == 0) is allowed, result.stderr

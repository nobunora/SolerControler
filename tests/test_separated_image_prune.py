import hashlib
import importlib.util
import json
from pathlib import Path

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

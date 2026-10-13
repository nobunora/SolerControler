from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import socket
import subprocess

import pytest

from scripts import prepare_server_adapter_fixture as preparation


@pytest.fixture
def repository(tmp_path, monkeypatch):
    repo = tmp_path / "repository"
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=repo, text=True, encoding="utf-8").strip()
    git("init", "-q")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "user.name", "Fixture")
    (repo / ".gitignore").write_text("artifacts/\n", encoding="utf-8")
    git("add", ".gitignore")
    git("commit", "-qm", "baseline")
    monkeypatch.setattr(preparation, "BASELINE", git("rev-parse", "HEAD"))
    source = repo / "app/energy_model/model.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "calculation source")
    for key, value in {"DATA_BACKEND": "firestore", "FIRESTORE_PROJECT_ID": "owned-test-project",
                       "FIRESTORE_DATABASE_ID": "owned-test-database",
                       "NIGHT_PLAN_ARCHIVE_GCS_PREFIX": "gs://owned-test-bucket/archive"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("FIRESTORE_EMULATOR_HOST", raising=False)
    def forbid_network(*args, **kwargs):
        raise AssertionError("Local preparation must not connect to a server")
    monkeypatch.setattr(socket, "create_connection", forbid_network)
    return repo, git("rev-parse", "HEAD"), git


def test_prepares_exact_owned_future_fixture_and_canonical_patch_without_cloud(repository):
    repo, commit, git = repository
    result = preparation.prepare(repo, commit, "explicit test authorization")
    fixture_path = Path(result["fixture_path"])
    fixture = json.loads(fixture_path.read_text())
    approval = json.loads((fixture_path.parent / "approval.private.json").read_text())
    assert result["cloud_writes"] == result["physical_setting_writes"] == 0
    assert fixture["approval_receipt_sha256"] == hashlib.sha256(preparation.canonical(approval)).hexdigest()
    assert fixture["fixture_sha256"] == hashlib.sha256(preparation.canonical(
        {key: value for key, value in fixture.items() if key != "fixture_sha256"})).hexdigest()
    assert fixture["source_commit"] == approval["source_commit"] == commit
    assert date.fromisoformat(fixture["target_date"]) > date.today() + timedelta(days=366)
    assert len(set(fixture["owned_document_paths"])) == len(set(fixture["owned_object_names"])) == 2
    assert all(path.startswith("forecast_hourly_snapshots/" + fixture["operation_id"] + "-")
               for path in fixture["owned_document_paths"])
    assert all(name.startswith(fixture["prefix"]) for name in fixture["owned_object_names"])
    patch = subprocess.check_output(["git", "diff", "--no-ext-diff", "--no-color", "--binary",
                                    preparation.BASELINE, "--", *preparation.CALCULATION_PATHS], cwd=repo,
                                   text=True, encoding="utf-8")
    assert result["calculation_patch_sha256"] == hashlib.sha256(patch.encode()).hexdigest()
    assert git("status", "--porcelain", "--untracked-files=all") == ""
    assert "owned-test-project" not in json.dumps(result)
    assert "explicit test authorization" not in json.dumps(result)
    second = preparation.prepare(repo, commit, "explicit test authorization")
    assert second["fixture_path"] != result["fixture_path"]


def test_refuses_overwrite_and_path_escape_before_writing(repository, tmp_path):
    repo, commit, _ = repository
    result = preparation.prepare(repo, commit, "approved")
    destination = Path(result["fixture_path"]).parent
    original = Path(result["fixture_path"]).read_bytes()
    with pytest.raises(FileExistsError):
        preparation.prepare(repo, commit, "approved", destination)
    assert Path(result["fixture_path"]).read_bytes() == original
    outside = tmp_path / "outside"
    with pytest.raises(ValueError, match="below artifacts"):
        preparation.prepare(repo, commit, "approved", outside)
    assert not outside.exists()


@pytest.mark.parametrize("defect", ["commit", "dirty", "approval", "emulator", "backend", "identity", "not_ignored"])
def test_invalid_preparation_stops_without_output(repository, monkeypatch, defect):
    repo, commit, git = repository
    note = "approved"
    if defect == "commit":
        commit = "0" * 40
    elif defect == "dirty":
        (repo / "untracked.txt").write_text("changed")
    elif defect == "approval":
        note = " "
    elif defect == "emulator":
        monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "localhost:8080")
    elif defect == "backend":
        monkeypatch.setenv("DATA_BACKEND", "sqlite")
    elif defect == "identity":
        monkeypatch.setenv("NIGHT_PLAN_ARCHIVE_GCS_PREFIX", "gs://owned-test-bucket/archive?token=PRIVATE")
    else:
        (repo / ".gitignore").write_text("")
        git("add", ".gitignore")
        git("commit", "-qm", "remove ignore")
        commit = git("rev-parse", "HEAD")
    with pytest.raises(ValueError):
        preparation.prepare(repo, commit, note)
    assert not (repo / "artifacts").exists()


@pytest.mark.parametrize("mismatch", [None, "source_commit", "backend", "project_id", "database_id", "bucket"])
def test_actual_deployment_guard_rejects_stale_fixture_before_build(tmp_path, mismatch):
    # Execute the actual pre-build guard, excluding every cloud/build command.
    source = (preparation.ROOT / "scripts/deploy_separated_runtime.ps1").read_text(encoding="utf-8-sig")
    guard = source[source.index("$preparedFixture ="):source.index("$fixtureSha =")]
    assert source.index("$preparedFixture =") < source.index("function Save-State")
    fixture = {"source_commit": "a" * 40, "backend": "firestore", "project_id": "owned-test-project",
               "database_id": "owned-test-database", "bucket": "owned-test-bucket"}
    if mismatch:
        fixture[mismatch] = "mismatch"
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    script = tmp_path / "guard.ps1"
    script.write_text("param($AdapterProofFixturePath)\n$ErrorActionPreference='Stop'\n"
        "$commit='" + "a" * 40 + "'\nfunction Get-RequiredProductionEnv($name) {\n"
        "$values=@{FIRESTORE_PROJECT_ID='owned-test-project'; FIRESTORE_DATABASE_ID='owned-test-database';"
        " NIGHT_PLAN_ARCHIVE_GCS_PREFIX='gs://owned-test-bucket/archive'}\nreturn $values[$name]\n}\n"
        + guard + "Write-Output 'GUARD_PASS'\n", encoding="utf-8")
    completed = subprocess.run(["pwsh", "-NoProfile", "-File", str(script), str(path)],
                               capture_output=True, text=True, encoding="utf-8")
    assert (completed.returncode == 0) == (mismatch is None)
    assert ("GUARD_PASS" in completed.stdout) == (mismatch is None)


@pytest.mark.parametrize("stale_revision", [False, True])
def test_actual_sdk_execution_revision_override_and_receipt_guard(tmp_path, stale_revision):
    source = (preparation.ROOT / "scripts/run_server_adapter_proof_from_env.ps1").read_text(encoding="utf-8-sig")
    execute = next(line for line in source.splitlines() if "$executionText =" in line)
    guard = source[source.index("    if ($proof.image_digest"):source.index("} catch { $proofError")]
    script = tmp_path / "proof.ps1"
    script.write_text("$ErrorActionPreference='Stop'\n"
        "$fixture=@{source_commit='" + "a" * 40 + "';fixture_sha256='" + "b" * 64 + "'}\n"
        "$imageDigest='sha256:" + "c" * 64 + "'\n"
        "$ProbeJobName='owned-test';$project='owned-project';$region='test-region'\n"
        "function MockCloud { ConvertTo-Json -InputObject @($args) -Compress }\n$gcloud='MockCloud'\n"
        + execute + "\n$passed=@($executionText | ConvertFrom-Json)\n"
        "$index=[Array]::IndexOf($passed,'--update-env-vars')\n"
        "if ($index -lt 0 -or $passed[$index+1] -ne ('PLAN_SOURCE_REVISION='+$fixture.source_commit+"
        "',PLAN_IMAGE_DIGEST='+$imageDigest)) {throw 'execution identity missing'}\n"
        "$proof=@{image_digest=$imageDigest;fixture_sha256=$fixture.fixture_sha256;status='PASS';"
        "source_commit=$fixture.source_commit;runtime_plan_source_revision='" + ("d" if stale_revision else "a") * 40 + "'}\n"
        "$executionExit=0\n" + guard + "\nWrite-Output 'PROOF_PASS'\n", encoding="utf-8")
    completed = subprocess.run(["pwsh", "-NoProfile", "-File", str(script)], capture_output=True, text=True)
    assert (completed.returncode == 0) == (not stale_revision)
    assert ("PROOF_PASS" in completed.stdout) == (not stale_revision)

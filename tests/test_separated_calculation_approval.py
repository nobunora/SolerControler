import hashlib
import subprocess

import pytest

from scripts.verify_separated_runtime_parity import verify_calculation_source


@pytest.fixture
def source_repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "core.autocrlf", "false"], check=True)
    source = tmp_path / "app/forecasting/correction_model.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 0\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "baseline"], check=True)
    baseline = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()
    monkeypatch.chdir(tmp_path)
    return source, baseline


def test_unchanged_calculation_needs_no_exception(source_repo):
    _, baseline = source_repo
    assert verify_calculation_source(baseline)["canonical_calculation_source_unchanged"] is True


def test_changed_calculation_requires_exact_reviewed_patch_and_rejects_later_edits(source_repo):
    source, baseline = source_repo
    source.write_text("value = 1\n", encoding="utf-8")
    with pytest.raises(AssertionError):
        verify_calculation_source(baseline)
    patch = subprocess.check_output(["git", "diff", "--no-ext-diff", "--no-color", "--binary", baseline,
                                     "--", "app/forecasting"], text=True, encoding="utf-8")
    digest = hashlib.sha256(patch.encode("utf-8")).hexdigest()
    proof = verify_calculation_source(baseline, digest)
    assert proof["canonical_calculation_source_unchanged"] is False
    assert proof["approved_calculation_patch_sha256"] == digest
    source.write_text("value = 2\n", encoding="utf-8")
    with pytest.raises(AssertionError):
        verify_calculation_source(baseline, digest)


def test_malformed_calculation_approval_is_rejected(source_repo):
    _, baseline = source_repo
    with pytest.raises(ValueError):
        verify_calculation_source(baseline, "not-a-digest")


def test_energy_plan_modules_cannot_bypass_calculation_approval(source_repo):
    source, baseline = source_repo
    history = source.parents[1] / "energy_plan" / "monitoring_history.py"
    history.parent.mkdir(parents=True)
    history.write_text("history_days = 7\n", encoding="utf-8")
    subprocess.run(["git", "add", "-N", str(history)], check=True)
    with pytest.raises(AssertionError):
        verify_calculation_source(baseline)

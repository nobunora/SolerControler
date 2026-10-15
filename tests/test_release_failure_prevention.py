from pathlib import Path
import shutil
import subprocess

import pytest

from scripts.check_dashboard_browser import assess_snapshot


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("text,width,expected", [
    ("2026-09-15〜2026-10-14", 390, True),
    ("2026-10-15〜2026-10-14", 390, False),
    ("2026-09-15〜2026-10-14", 565, False),
    ("Loading", 390, False),
])
def test_browser_rejects_observed_failures(text: str, width: int, expected: bool) -> None:
    checks = assess_snapshot({"text": text, "document_width": width,
                              "viewport_width": 390, "rendered_charts": 8})
    assert all(checks.values()) is expected


def run_pwsh(script: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("pwsh")
    if not executable:
        pytest.skip("PowerShell is required for release wrapper execution")
    return subprocess.run([executable, "-NoProfile", "-Command", script], cwd=cwd,
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")


@pytest.mark.parametrize("exit_code", [0, 1])
def test_isolated_timezone_prerequisites(tmp_path: Path, exit_code: int) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    rules = tmp_path / "docs/current/ops"
    rules.mkdir(parents=True)
    (rules / "DEPLOYMENT_FAILURE_RULES_JA.md").write_text("rules", encoding="utf-8")
    shutil.copy(ROOT / "scripts/check_release_prerequisites.ps1", scripts)
    result = run_pwsh(f"function python {{ $global:LASTEXITCODE = {exit_code} }}; & ./scripts/check_release_prerequisites.ps1", tmp_path)
    assert (result.returncode == 0) is (exit_code == 0)
    if exit_code:
        assert "Isolated Python cannot load Asia/Tokyo" in result.stderr


@pytest.mark.parametrize("partial", [True, False])
def test_gate_requires_every_check(tmp_path: Path, partial: bool) -> None:
    import json

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(ROOT / "scripts/production_deployment_gate.ps1", scripts)
    (scripts / "check_release_prerequisites.ps1").write_text("$global:LASTEXITCODE = 0", encoding="utf-8")
    for name in ("backup_local.ps1", "deploy_production_from_env.ps1", "pre_release_integration.ps1"):
        (scripts / name).write_text("$global:LASTEXITCODE = 0", encoding="utf-8")
    rules = tmp_path / "docs/current/ops"
    rules.mkdir(parents=True)
    (rules / "DEPLOYMENT_FAILURE_RULES_JA.md").write_text("rules", encoding="utf-8")
    (tmp_path / ".env").write_text("", encoding="utf-8")
    command = """
function git {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'rev-parse') { return ('a' * 40) }
    if ($args[0] -eq 'branch') { return 'test' }
}
function python { $global:LASTEXITCODE = 0 }
"""
    options = "-SkipLocalBackup -SkipCloudValidation" if partial else "-RunPreRelease"
    command += f"& ./scripts/production_deployment_gate.ps1 {options} -StatePath artifacts/deployment_state/test.json"
    result = run_pwsh(command, tmp_path)
    state = json.loads((tmp_path / "artifacts/deployment_state/test.json").read_text(encoding="utf-8-sig"))
    assert (result.returncode != 0) is partial
    assert state["status"] == ("failed" if partial else "passed")
    assert len(state["failure_rules_sha256"]) == 64
    assert any(check["status"] == "not_run" for check in state["checks"]) is partial


def test_canonical_pytest_preserves_native_handles() -> None:
    for name in ("pre_release_local.ps1", "pre_release_integration.ps1"):
        commands = [line for line in (ROOT / "scripts" / name).read_text(encoding="utf-8").splitlines()
                    if line.startswith("python -m pytest")]
        assert commands
        assert all("--capture=sys" in command for command in commands)

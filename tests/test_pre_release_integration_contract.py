from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_quality_gate_precedes_any_pytest() -> None:
    script = (ROOT / "scripts/pre_release_integration.ps1").read_text(encoding="utf-8")
    assert script.count("& .\\scripts\\pre_release_local.ps1") == 1
    assert script.index("& .\\scripts\\pre_release_local.ps1") < script.index("python -m pytest")
    assert "-SkipInstall:$SkipInstall" in script


def test_each_failure_stops_integration() -> None:
    script = (ROOT / "scripts/pre_release_integration.ps1").read_text(encoding="utf-8")
    commands = [line for line in script.splitlines() if line.startswith(("python ", "& ."))]
    assert len(commands) == 5
    lines = script.splitlines()
    for command in commands:
        assert "if ($LASTEXITCODE -ne 0) { throw" in lines[lines.index(command) + 1]


def test_protected_commands_preserved() -> None:
    script = (ROOT / "scripts/pre_release_integration.ps1").read_text(encoding="utf-8")
    for filename in ("test_dashboard_history_contract.py", "test_historical_forecast_replay_contract.py",
                     "test_dashboard_forecast_soc_contract.py", "test_dashboard_only_deployment_contract.py"):
        assert filename in script
    assert "--direction firestore-to-sqlite" in script


def test_validation_has_one_coordinator() -> None:
    script = (ROOT / "scripts/pre_release_integration.ps1").read_text(encoding="utf-8")
    assert script.count("python .\\scripts\\validate_dashboard_backend_parity.py") == 1
    assert "validate_forecast_display_evidence.py" not in script

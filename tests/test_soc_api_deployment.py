import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from scripts.security_check import _check_sensitive_env_values


ROOT = Path(__file__).resolve().parents[1]
CREDS = {"authorization": "Basic local-test-only", "api_key": "private-api-key-for-test",
         "user": {"id": "private-user-id", "password": "private-user-password"},
         "gateway": {"id": "private-gateway-id", "password": "private-gateway-password"}}


@pytest.mark.parametrize("secret", [CREDS["authorization"], CREDS["api_key"], CREDS["user"]["password"], CREDS["gateway"]["password"]])
def test_security_gate_detects_individual_api_secrets(tmp_path, monkeypatch, secret):
    (tmp_path / "source.py").write_text(secret, encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=b"source.py\0"))
    failures = []
    _check_sensitive_env_values(tmp_path, {"KP_SOC_API_CREDENTIALS_JSON": json.dumps(CREDS)}, failures)
    assert failures
    assert all(secret not in failure for failure in failures)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
def test_soc_secret_validate_only_is_local_and_redacted(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("configure_soc_api_from_env.ps1", "production_env.ps1"):
        shutil.copyfile(ROOT / "scripts" / name, scripts / name)
    (scripts / "gcloud.ps1").write_text("throw 'cloud must not be called'", encoding="utf-8")
    (tmp_path / ".env").write_text("KP_SOC_API_CREDENTIALS_JSON=" + json.dumps(CREDS) +
                                 "\nKP_SOC_API_CREDENTIALS_SECRET=test-soc-api\n", encoding="utf-8")
    result = subprocess.run(["pwsh", "-NoProfile", "-File", str(scripts / "configure_soc_api_from_env.ps1"), "-ValidateOnly"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "No mutation performed" in result.stdout
    assert "private-" not in result.stdout + result.stderr


def test_separated_release_requires_soc_credentials_and_live_readback():
    release = (ROOT / "scripts/deploy_separated_runtime.ps1").read_text(encoding="utf-8")
    assert release.index("Invoke-Stage 'soc_api_credentials'") < release.index("Invoke-Stage 'control'")
    binding = (ROOT / "scripts/configure_soc_api_from_env.ps1").read_text(encoding="utf-8")
    assert "@('solar-battery-03', 'solar-battery-settings-roundtrip')" in binding
    assert "--data-file" in binding
    assert "--update-secrets" in binding
    assert "Remove-Item -LiteralPath $temporary.FullName" in binding

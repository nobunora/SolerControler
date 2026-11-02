from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.kpnet.workflow as workflow


@pytest.mark.parametrize("profile,mode", [("forced", "3"), ("standby", "5"), ("green", "1")])
def test_unchanged_mode_is_explicitly_confirmed_without_another_write(
    monkeypatch, tmp_path: Path, capsys, profile: str, mode: str,
) -> None:
    class Client:
        csrf_setting = "csrf"
        pcsid = "pcs"

        def __init__(self, cfg, *, deadline_monotonic):
            self.deadline_monotonic = deadline_monotonic

        def login(self):
            pass

        def open_settings_page(self):
            pass

        def read_current_settings(self):
            return {"batteryOperatingMode": mode}

        def candidate_map(self, field, path):
            assert field == "BatteryOperatingMode"
            return {"1": "green", "3": "forced", "5": "standby"}

        def confirm_setting(self, payload):
            raise AssertionError("unchanged settings must not be submitted")

        def write_setting(self, html):
            raise AssertionError("unchanged settings must not be written")

        def logout(self):
            pass

    monkeypatch.setattr(workflow, "KpNetClient", Client)
    monkeypatch.setattr(workflow.KpNetConfig, "from_env", staticmethod(
        lambda: SimpleNamespace(dry_run=False, artifacts_dir=tmp_path)
    ))
    monkeypatch.setattr(workflow, "_mode_only_profile_from_current_settings", lambda current, name:
                        replace(workflow.FORCED_CHARGE_PROFILE, name=name))
    monkeypatch.setattr(workflow, "_build_payload", lambda **kwargs: ({"batteryOperatingMode": mode}, []))
    assert workflow.run_kpnet_mode_only_profile(profile=profile) == 0
    output = capsys.readouterr().out
    audit = next(json.loads(line) for line in output.splitlines() if '"kpnet-settings-readback"' in line)
    assert audit["status"] == "skipped-no-change"
    assert audit["readback_fields"] == ["batteryOperatingMode"]
    assert audit["requested"] == audit["observed"] == {"batteryOperatingMode": mode}
    assert audit["readback_match"] is True


@pytest.mark.parametrize("current", [{"batteryOperatingMode": "1"}, {}])
def test_unchanged_intent_cannot_accept_missing_or_mismatched_required_mode(
    monkeypatch, tmp_path: Path, current,
) -> None:
    monkeypatch.setenv("NIGHT_SOC_READBACK_REQUIRED", "false")
    monkeypatch.setattr(workflow, "_build_payload", lambda **kwargs: ({"batteryOperatingMode": "5"}, []))
    summary = {"setting_results": []}
    with pytest.raises(RuntimeError, match="read-back mismatch"):
        workflow._apply_settings_profile(
            client=SimpleNamespace(csrf_setting="csrf", pcsid="pcs"),
            cfg=SimpleNamespace(dry_run=False), run_dir=tmp_path, summary=summary,
            current=current, value_maps={}, profile=workflow.FORCED_CHARGE_PROFILE,
            required_readback_fields=("batteryOperatingMode",),
        )
    assert summary["setting_results"][0]["readback_match"] is False
    assert summary["setting_results"][0]["readback_mismatch_fields"] == ["batteryOperatingMode"]


def test_changed_mode_mismatch_remains_mandatory_when_optional_readback_disabled(
    monkeypatch, tmp_path: Path,
) -> None:
    class Client:
        csrf_setting = "csrf"
        pcsid = "pcs"
        writes = 0

        def confirm_setting(self, payload):
            return True, "confirmed", "", "<html>confirmed</html>"

        def write_setting(self, html):
            self.writes += 1
            return {"changed": True}

        def read_current_settings(self):
            return {"batteryOperatingMode": "1"}

    monkeypatch.setenv("NIGHT_SOC_READBACK_REQUIRED", "false")
    monkeypatch.setattr(workflow, "_build_payload", lambda **kwargs:
                        ({"batteryOperatingMode": "5"}, ["batteryOperatingMode"]))
    client = Client()
    with pytest.raises(RuntimeError, match="read-back mismatch"):
        workflow._apply_settings_profile(
            client=client, cfg=SimpleNamespace(dry_run=False), run_dir=tmp_path,
            summary={"setting_results": []}, current={"batteryOperatingMode": "1"},
            value_maps={}, profile=workflow.FORCED_CHARGE_PROFILE,
            required_readback_fields=("batteryOperatingMode",),
        )
    assert client.writes == 1

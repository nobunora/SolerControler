from __future__ import annotations

from app.backup.device import build_device_settings_snapshot


class _FakeConfig:
    pass


class _FakeClient:
    def __init__(self, _config: _FakeConfig) -> None:
        self.calls: list[str] = []

    def login(self) -> None:
        self.calls.append("login")

    def open_settings_page(self) -> None:
        self.calls.append("open_settings_page")

    def read_current_settings(self) -> dict[str, str]:
        self.calls.append("read_current_settings")
        return {"socChargeMode": "50", "batteryOperatingMode": "3"}

    def logout(self) -> None:
        self.calls.append("logout")


def test_build_device_settings_snapshot_is_read_only(monkeypatch) -> None:
    client = _FakeClient(_FakeConfig())
    monkeypatch.setattr("app.backup.device.KpNetConfig.from_env", staticmethod(lambda: _FakeConfig()))
    monkeypatch.setattr("app.backup.device.KpNetClient", lambda config: client)

    snapshot = build_device_settings_snapshot()

    assert snapshot["source"] == "kpnet_readback"
    assert snapshot["settings"]["socChargeMode"] == "50"
    assert client.calls == ["login", "open_settings_page", "read_current_settings", "logout"]

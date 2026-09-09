import asyncio
from pathlib import Path

import pytest

from app.echonet.models import DeviceCapabilities, DeviceIdentity
from app.echonet.service import EchonetReadService, TopologyError, UnsupportedPropertyError


class FakeAdapter:
    def __init__(self, devices, caps=None, values=None):
        self.devices = devices
        self.caps = caps or DeviceCapabilities(frozenset(), frozenset(), frozenset())
        self.values = values or {}
        self.read_calls = []

    async def discover(self, host):
        return self.devices

    async def capabilities(self, identity):
        return self.caps

    async def read_properties(self, identity, epcs):
        self.read_calls.append((identity, epcs))
        return {epc: self.values[epc] for epc in epcs}


def run(coro):
    return asyncio.run(coro)


def test_discovers_non_default_instance_ids():
    pv = DeviceIdentity("192.0.2.10", 0x02, 0x79, 0x02)
    battery = DeviceIdentity("192.0.2.10", 0x02, 0x7D, 0x03)
    topology = run(EchonetReadService(FakeAdapter([pv, battery])).discover_topology(pv.host))
    assert topology.pv.eoj == "027902"
    assert topology.battery.eoj == "027D03"


@pytest.mark.parametrize(
    "devices",
    [
        [DeviceIdentity("192.0.2.10", 0x02, 0x79, 1)],
        [DeviceIdentity("192.0.2.10", 0x02, 0x7D, 1)],
    ],
)
def test_missing_required_class_fails_closed(devices):
    with pytest.raises(TopologyError):
        run(EchonetReadService(FakeAdapter(devices)).discover_topology("192.0.2.10"))


def test_duplicate_target_class_is_ambiguous():
    devices = [
        DeviceIdentity("192.0.2.10", 0x02, 0x79, 1),
        DeviceIdentity("192.0.2.10", 0x02, 0x79, 2),
        DeviceIdentity("192.0.2.10", 0x02, 0x7D, 1),
    ]
    with pytest.raises(TopologyError):
        run(EchonetReadService(FakeAdapter(devices)).discover_topology("192.0.2.10"))


def test_unsupported_epc_is_rejected_before_network_read():
    identity = DeviceIdentity("192.0.2.10", 0x02, 0x7D, 1)
    fake = FakeAdapter([identity], DeviceCapabilities(frozenset({0x80}), frozenset(), frozenset()))
    service = EchonetReadService(fake)
    with pytest.raises(UnsupportedPropertyError):
        run(service.read_supported(identity, [0x80, 0xE0]))
    assert fake.read_calls == []


def test_zero_is_preserved_as_data():
    identity = DeviceIdentity("192.0.2.10", 0x02, 0x7D, 1)
    fake = FakeAdapter(
        [identity],
        DeviceCapabilities(frozenset({0xE0}), frozenset(), frozenset()),
        {0xE0: 0},
    )
    result = run(EchonetReadService(fake).read_supported(identity, [0xE0]))
    assert result[0xE0] == 0


def test_pychonet_import_is_isolated_to_adapter():
    echonet_dir = Path(__file__).parents[1] / "app" / "echonet"
    offenders = []
    for path in echonet_dir.glob("*.py"):
        if path.name == "adapter.py":
            continue
        if "pychonet" in path.read_text(encoding="utf-8"):
            offenders.append(path.name)
    assert offenders == []


def test_read_boundary_exposes_no_write_method():
    public_names = set(dir(EchonetReadService))
    assert not {"set", "set_epc", "write", "write_property"}.intersection(public_names)

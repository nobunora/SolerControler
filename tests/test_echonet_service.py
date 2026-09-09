import asyncio

import pytest

from app.echonet.adapter import GatewayCommandError, GatewayTimeoutError
from app.echonet.control import (
    ControlDisabledError,
    SafeWriteService,
    SafetyInterlockError,
    VerifiedWriteCommand,
)
from app.echonet.models import DeviceIdentity, WriteOutcome
from app.echonet.service import EchonetReadService, TopologyError


class FakeGateway:
    def __init__(self, devices=None, read_response=None):
        self.devices = devices or []
        self.read_response = read_response or {}
        self.set_effect = None
        self.set_calls = []
        self.read_calls = []
        self.active_sets = 0
        self.max_active_sets = 0
        self.set_delay = 0.0

    async def list_devices(self):
        return self.devices

    def discovered_identities(self):
        result = []
        for device in self.devices:
            identity = DeviceIdentity.from_gateway_device(device)
            if identity is not None:
                result.append(identity)
        return result

    async def get_properties(self, identity, epcs):
        self.read_calls.append((identity, epcs))
        effect = self.read_response
        if isinstance(effect, Exception):
            raise effect
        return effect

    async def set_properties(self, identity, properties):
        self.set_calls.append((identity, properties))
        self.active_sets += 1
        self.max_active_sets = max(self.max_active_sets, self.active_sets)
        try:
            if self.set_delay:
                await asyncio.sleep(self.set_delay)
            if isinstance(self.set_effect, Exception):
                raise self.set_effect
            return {}
        finally:
            self.active_sets -= 1


class MemoryAudit:
    def __init__(self):
        self.results = []

    def record(self, result):
        self.results.append(result)


def run(coro):
    return asyncio.run(coro)


def devices():
    return [
        {"ip": "192.0.2.10", "eoj": "0279:2", "properties": {}},
        {"ip": "192.0.2.10", "eoj": "027D:3", "properties": {}},
    ]


def command(identity=None, value=50):
    return VerifiedWriteCommand(
        name="verified-test-command",
        target=identity or DeviceIdentity("192.0.2.10", 0x027D, 1),
        epc=0xE0,
        payload={"number": value},
        expected_number=value,
    )


def test_discovers_non_default_instance_ids():
    topology = run(EchonetReadService(FakeGateway(devices())).discover_topology())
    assert topology.pv.eoj == "0279:2"
    assert topology.battery.eoj == "027D:3"


def test_missing_required_class_fails_closed():
    gateway = FakeGateway([{"ip": "192.0.2.10", "eoj": "0279:1"}])
    with pytest.raises(TopologyError):
        run(EchonetReadService(gateway).discover_topology())


def test_duplicate_target_class_is_ambiguous():
    gateway = FakeGateway(
        [
            {"ip": "192.0.2.10", "eoj": "0279:1"},
            {"ip": "192.0.2.10", "eoj": "0279:2"},
            {"ip": "192.0.2.10", "eoj": "027D:1"},
        ]
    )
    with pytest.raises(TopologyError):
        run(EchonetReadService(gateway).discover_topology())


def test_invalid_epc_rejected_before_gateway_io():
    identity = DeviceIdentity("192.0.2.10", 0x027D, 1)
    gateway = FakeGateway()
    with pytest.raises(ValueError):
        run(EchonetReadService(gateway).read_properties(identity, [0x100]))
    assert gateway.read_calls == []


def test_writes_are_disabled_by_default():
    gateway = FakeGateway()
    with pytest.raises(ControlDisabledError):
        run(SafeWriteService(gateway).apply(command()))
    assert gateway.set_calls == []


def test_stale_interlock_blocks_before_set():
    gateway = FakeGateway()
    service = SafeWriteService(gateway, enabled=True, ready_for_write=lambda: False)
    with pytest.raises(SafetyInterlockError):
        run(service.apply(command()))
    assert gateway.set_calls == []


def test_accepted_write_requires_matching_readback_and_audit():
    identity = DeviceIdentity("192.0.2.10", 0x027D, 1)
    gateway = FakeGateway(read_response={"E0": {"number": 50}})
    audit = MemoryAudit()
    result = run(SafeWriteService(gateway, enabled=True, audit=audit).apply(command(identity)))
    assert result.outcome is WriteOutcome.APPLIED
    assert len(gateway.set_calls) == 1
    assert len(gateway.read_calls) == 1
    assert audit.results == [result]


def test_set_timeout_reconciles_without_blind_retry():
    identity = DeviceIdentity("192.0.2.10", 0x027D, 1)
    gateway = FakeGateway(read_response={"E0": {"number": 50}})
    gateway.set_effect = GatewayTimeoutError("timeout")
    result = run(SafeWriteService(gateway, enabled=True).apply(command(identity)))
    assert result.outcome is WriteOutcome.APPLIED
    assert len(gateway.set_calls) == 1
    assert len(gateway.read_calls) == 1


def test_set_rejection_is_not_reported_as_success():
    identity = DeviceIdentity("192.0.2.10", 0x027D, 1)
    gateway = FakeGateway()
    gateway.set_effect = GatewayCommandError("ECHONET_DEVICE_ERROR", "rejected")
    result = run(SafeWriteService(gateway, enabled=True).apply(command(identity)))
    assert result.outcome is WriteOutcome.REJECTED
    assert gateway.read_calls == []


def test_same_target_writes_are_serialized():
    async def scenario():
        identity = DeviceIdentity("192.0.2.10", 0x027D, 1)
        gateway = FakeGateway(read_response={"E0": {"number": 50}})
        gateway.set_delay = 0.02
        service = SafeWriteService(gateway, enabled=True)
        await asyncio.gather(service.apply(command(identity)), service.apply(command(identity)))
        return gateway

    gateway = run(scenario())
    assert gateway.max_active_sets == 1
    assert len(gateway.set_calls) == 2

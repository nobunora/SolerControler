import asyncio
import time

from app.echonet.adapter import GatewayError
from app.echonet.runtime import GatewayRuntime, RuntimeState


class FakeRuntimeGateway:
    def __init__(self):
        self._connected = False
        self._last_message_at = None
        self.connect_failures = 0
        self.list_failures = 0
        self.connect_calls = 0
        self.close_calls = 0
        self.list_calls = 0

    @property
    def connected(self):
        return self._connected

    @property
    def last_message_at(self):
        return self._last_message_at

    async def connect(self):
        self.connect_calls += 1
        if self.connect_failures:
            self.connect_failures -= 1
            raise GatewayError("connect failed")
        self._connected = True
        self._last_message_at = time.monotonic()

    async def close(self):
        self.close_calls += 1
        self._connected = False

    async def list_devices(self):
        self.list_calls += 1
        if self.list_failures:
            self.list_failures -= 1
            raise GatewayError("health failed")
        self._last_message_at = time.monotonic()
        return []


def run(coro):
    return asyncio.run(coro)


def test_runtime_connects_and_becomes_write_healthy():
    async def scenario():
        gateway = FakeRuntimeGateway()
        runtime = GatewayRuntime(
            gateway,
            health_interval=0.01,
            stale_after=0.1,
            reconnect_min=0.01,
            reconnect_max=0.02,
        )
        await runtime.start()
        await asyncio.sleep(0.03)
        healthy = runtime.healthy_for_write
        state = runtime.state
        await runtime.stop()
        return gateway, healthy, state, runtime.state

    gateway, healthy, state, final_state = run(scenario())
    assert gateway.connect_calls >= 1
    assert healthy is True
    assert state is RuntimeState.ONLINE
    assert final_state is RuntimeState.STOPPED


def test_runtime_recovers_after_initial_connect_failure():
    async def scenario():
        gateway = FakeRuntimeGateway()
        gateway.connect_failures = 1
        runtime = GatewayRuntime(
            gateway,
            health_interval=0.01,
            stale_after=0.1,
            reconnect_min=0.01,
            reconnect_max=0.02,
        )
        await runtime.start()
        await asyncio.sleep(0.05)
        result = (gateway.connect_calls, runtime.state, runtime.healthy_for_write)
        await runtime.stop()
        return result

    calls, state, healthy = run(scenario())
    assert calls >= 2
    assert state is RuntimeState.ONLINE
    assert healthy is True


def test_runtime_closes_and_reconnects_after_health_failure():
    async def scenario():
        gateway = FakeRuntimeGateway()
        gateway.list_failures = 1
        runtime = GatewayRuntime(
            gateway,
            health_interval=0.01,
            stale_after=0.1,
            reconnect_min=0.01,
            reconnect_max=0.02,
        )
        await runtime.start()
        await asyncio.sleep(0.06)
        result = (gateway.connect_calls, gateway.close_calls, runtime.state)
        await runtime.stop()
        return result

    connect_calls, close_calls, state = run(scenario())
    assert connect_calls >= 2
    assert close_calls >= 1
    assert state is RuntimeState.ONLINE

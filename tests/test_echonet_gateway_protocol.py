from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable
from typing import Any, TypeVar

from app.echonet.adapter import EchonetListGateway
from app.echonet.models import DeviceIdentity

T = TypeVar("T")


def run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)


def test_gateway_websocket_contract_without_hardware() -> None:
    async def scenario() -> tuple[
        list[dict[str, Any]],
        list[DeviceIdentity],
        DeviceIdentity,
        dict[str, Any],
        dict[str, Any],
        bool,
    ]:
        from websockets.asyncio.server import ServerConnection, serve

        async def handler(socket: ServerConnection) -> None:
            devices: dict[str, dict[str, Any]] = {
                "192.0.2.10 0279:1": {
                    "ip": "192.0.2.10",
                    "eoj": "0279:1",
                    "properties": {"E0": {"number": 321}},
                },
                "192.0.2.10 027D:1": {
                    "ip": "192.0.2.10",
                    "eoj": "027D:1",
                    "properties": {"E0": {"number": 50}},
                },
            }
            await socket.send(json.dumps({"type": "initial_state", "payload": {"devices": devices}}))
            async for raw in socket:
                request: dict[str, Any] = json.loads(raw)
                request_id = str(request["requestId"])
                message_type = str(request["type"])
                payload = request["payload"]
                if not isinstance(payload, dict):
                    payload = {}
                if message_type == "list_devices":
                    data: dict[str, Any] = {"devices": devices}
                elif message_type == "get_properties":
                    targets = payload.get("targets", [])
                    epcs = payload.get("epcs", [])
                    target = str(targets[0])
                    epc = str(epcs[0])
                    data = {"devices": {target: {"properties": {epc: devices[target]["properties"][epc]}}}}
                elif message_type == "set_properties":
                    target = str(payload["target"])
                    properties = payload.get("properties", {})
                    if isinstance(properties, dict):
                        for epc, value in properties.items():
                            devices[target]["properties"][str(epc)] = value
                    data = {}
                else:
                    data = {}
                await socket.send(
                    json.dumps(
                        {
                            "type": "command_result",
                            "requestId": request_id,
                            "payload": {"success": True, "data": data},
                        }
                    )
                )

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            gateway = EchonetListGateway(f"ws://127.0.0.1:{port}", request_timeout=1.0)
            await gateway.connect()
            device_list = [dict(device) for device in await gateway.list_devices()]
            identities = gateway.discovered_identities()
            battery = next(identity for identity in identities if identity.class_code == 0x027D)
            before = dict(await gateway.get_properties(battery, (0xE0,)))
            await gateway.set_properties(battery, {0xE0: {"number": 60}})
            after = dict(await gateway.get_properties(battery, (0xE0,)))
            await gateway.close()
            return device_list, identities, battery, before, after, gateway.connected

    devices, identities, battery, before, after, connected = run(scenario())
    assert len(devices) == 2
    assert {identity.eoj for identity in identities} == {"0279:1", "027D:1"}
    assert battery == DeviceIdentity("192.0.2.10", 0x027D, 1)
    assert before["devices"][battery.target]["properties"]["E0"]["number"] == 50
    assert after["devices"][battery.target]["properties"]["E0"]["number"] == 60
    assert connected is False

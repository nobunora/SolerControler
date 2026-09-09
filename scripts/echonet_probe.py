"""Read-only RC-307A ECHONET topology/property-map probe.

Usage: python scripts/echonet_probe.py 192.168.x.x
No SET/SETC operation is exposed by this script.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from app.echonet.adapter import PychonetReadAdapter
from app.echonet.service import EchonetReadService


def _hex_set(values: frozenset[int]) -> list[str]:
    return [f"0x{value:02X}" for value in sorted(values)]


async def _run(host: str, listen_address: str, timeout: float) -> None:
    adapter = PychonetReadAdapter(listen_address=listen_address, timeout_seconds=timeout)
    service = EchonetReadService(adapter)
    topology = await service.discover_topology(host)
    result = {"host": host, "devices": {}}
    for label, identity in (("pv", topology.pv), ("battery", topology.battery)):
        caps = await service.capabilities(identity)
        result["devices"][label] = {
            "eoj": identity.eoj,
            "get": _hex_set(caps.gettable),
            "set": _hex_set(caps.settable),
            "notify": _hex_set(caps.notify),
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only RC-307A ECHONET probe")
    parser.add_argument("host", help="RC-307A IPv4 address")
    parser.add_argument("--listen-address", default="0.0.0.0")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()
    asyncio.run(_run(args.host, args.listen_address, args.timeout))


if __name__ == "__main__":
    main()

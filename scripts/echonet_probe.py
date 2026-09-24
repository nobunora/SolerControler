"""Read-only echonet-list probe for the RC-307A node exposed to SolarControler."""

from __future__ import annotations

import argparse
import asyncio
import json
import ssl

from app.echonet.adapter import EchonetListGateway
from app.echonet.service import EchonetReadService


async def _run(url: str, timeout: float, insecure_tls: bool) -> None:
    ssl_context = None
    if url.startswith("wss://"):
        ssl_context = ssl.create_default_context()
        if insecure_tls:
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
    gateway = EchonetListGateway(url, request_timeout=timeout, ssl=ssl_context)
    await gateway.connect()
    try:
        service = EchonetReadService(gateway)
        topology = await service.discover_topology()
        print(
            json.dumps(
                {
                    "pv": {"host": topology.pv.host, "eoj": topology.pv.eoj},
                    "battery": {"host": topology.battery.host, "eoj": topology.battery.eoj},
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        await gateway.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only RC-307A probe via echonet-list")
    parser.add_argument("url", help="echonet-list WebSocket URL, e.g. ws://127.0.0.1:8080/ws")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--insecure-tls",
        action="store_true",
        help="development only: disable TLS certificate verification",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.url, args.timeout, args.insecure_tls))


if __name__ == "__main__":
    main()

"""tools/latency_probe.py against the local relay: every frame arrives, well inside the network budget."""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import latency_probe  # noqa: E402
from tests.test_e2e_roles import relay_server  # noqa: E402


def test_probe_through_the_local_relay():
    async def main():
        async with relay_server() as port:
            return await latency_probe.probe(f"ws://127.0.0.1:{port}/ws", "e2e", n=60, rate_hz=100)

    res = asyncio.run(asyncio.wait_for(main(), 30))
    assert res["lost"] == 0 and res["received"] == 60
    assert res["p95_ms"] < latency_probe.BUDGET_MS

"""Relay latency from this machine: laptop -> relay -> client, on the local relay or Vultr.

    python tools/latency_probe.py                               # relay from laptop/config.yaml / laptop/.env
    python tools/latency_probe.py --relay wss://<host>/ws -n 400
    python tools/latency_probe.py --json

Two clients on the same machine (one clock, so no sync needed), in their own session (`latency-probe`), so a
real phone or dashboard never sees the probe. The sender sends `sources` frames at 20 Hz like the laptop does;
the receiver (role dashboard) timestamps each on arrival. Reports one-way relay latency (p50 / p95 / max) and
lost frames. Run it on campus Wi-Fi and on the hotspot; the network budget is <= 100 ms (docs/INTEGRATION.md §7).
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote

from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from contracts.python import here_contract as hc  # noqa: E402

SESSION = "latency-probe"
RATE_HZ = 20.0
BUDGET_MS = 100.0


def url_for(base, role, token):
    return f"{base}?session={SESSION}&role={role}&token={quote(token)}"


async def probe(base, token, n=200, rate_hz=RATE_HZ):
    lags, got = [], set()
    async with connect(url_for(base, "dashboard", token), open_timeout=10) as rx, \
            connect(url_for(base, "tool", token), open_timeout=10) as tx:
        await rx.send(hc.Hello(src="dashboard", t=time.time(), role="dashboard", subscribe=["sources"]).to_json())
        await tx.send(hc.Hello(src="tool", t=time.time(), role="tool", subscribe=[]).to_json())
        await asyncio.sleep(0.3)

        async def receive():
            while len(got) < n:
                m = json.loads(await rx.recv())
                if m.get("type") == "sources" and m.get("seq") not in got:
                    got.add(m["seq"])
                    lags.append((time.time() - m["t"]) * 1000)

        reader = asyncio.create_task(receive())
        for i in range(n):
            await tx.send(hc.Sources(src="tool", t=time.time(), seq=i, sources=[
                hc.Source(id=0, bearing_deg=0.0, energy=0.5, speech=True, confidence=0.8)]).to_json())
            await asyncio.sleep(1.0 / rate_hz)
        try:
            await asyncio.wait_for(reader, 2.0)
        except asyncio.TimeoutError:
            pass
    lags.sort()
    q = (lambda p: round(lags[int(p * (len(lags) - 1))], 1)) if lags else (lambda p: None)
    return {"relay": base.split("?")[0], "sent": n, "received": len(lags), "lost": n - len(lags),
            "p50_ms": q(0.5), "p95_ms": q(0.95), "max_ms": round(lags[-1], 1) if lags else None,
            "budget_ms": BUDGET_MS}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--relay", help="relay base URL (default: laptop/config.yaml or HERE_RELAY_URL)")
    p.add_argument("-n", type=int, default=200, help="frames to send (20 Hz)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    from laptop import settings
    r = settings.load()["relay"]
    res = asyncio.run(probe(args.relay or r["url"], r["token"], args.n))
    if args.json:
        print(json.dumps(res))
    else:
        ok = res["p95_ms"] is not None and res["p95_ms"] <= BUDGET_MS and res["lost"] == 0
        print(f"{res['relay']}: p50 {res['p50_ms']} ms, p95 {res['p95_ms']} ms, max {res['max_ms']} ms, "
              f"lost {res['lost']}/{res['sent']}  [{'OK' if ok else 'OVER BUDGET / LOSS'}: budget {BUDGET_MS:.0f} ms]")
    sys.exit(0 if res["received"] else 1)


if __name__ == "__main__":
    main()

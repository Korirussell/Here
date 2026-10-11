"""Booth drill: the ReSpeaker drops out mid-demo (unplugged, laptop slept) and HERE recovers with no restart.

Watches a running `--real` laptop through the relay (role tool): the laptop heartbeat must turn `degraded`
("ReSpeaker lost ...; reconnecting"), then `ok` again, and `sources` must flow again after that.

    python tools/doa_usb_drill.py --inject              # the laptop drops the array itself (audio.drill_file)
    python tools/doa_usb_drill.py --unplug              # a human pulls the USB cable, then plugs it back
    python tools/doa_usb_drill.py --inject --relay ws://localhost:18082/ws --session k1gate

`--inject` needs the laptop to run from this checkout (it watches `audio.drill_file` relative to its working
directory). Exit 0 = recovered within --budget seconds. K3: `run_drill(...)` returns the same numbers as a dict
for tools/drills.py.
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from laptop import settings  # noqa: E402
from laptop.client.relay import redact, relay_url  # noqa: E402


async def run_drill(relay=None, session=None, mode="inject", budget_s=20.0, prompt=print):
    """mode inject | unplug. Returns {ok, lost_s, back_s, sources_s, detail, reason}: seconds from the trigger to
    the degraded heartbeat, the ok heartbeat, and the first `sources` after it."""
    cfg = settings.load()
    url = relay_url(relay or cfg["relay"]["url"], session or cfg["relay"]["session"], "tool", cfg["relay"]["token"])
    out = {"ok": False, "mode": mode, "lost_s": None, "back_s": None, "sources_s": None, "detail": None, "reason": ""}
    async with connect(url, open_timeout=5) as ws:
        await ws.send(json.dumps({"type": "hello", "v": 3, "src": "tool", "t": time.time(), "role": "tool",
                                  "subscribe": ["heartbeat", "sources"]}))
        prompt(f"[drill] watching {redact(url)}")

        async def next_msg(deadline):
            raw = await asyncio.wait_for(ws.recv(), max(0.01, deadline - time.monotonic()))
            m = json.loads(raw)
            return m if m.get("src") == "laptop" else None

        # 1. a healthy laptop first
        deadline = time.monotonic() + 10
        try:
            while True:
                m = await next_msg(deadline)
                if m and m["type"] == "heartbeat" and m["status"] == "ok":
                    break
        except asyncio.TimeoutError:
            out["reason"] = "no ok heartbeat from the laptop in 10 s (is `python -m laptop --real` running?)"
            return out
        # 2. trigger
        if mode == "inject":
            drill = Path(cfg["audio"].get("drill_file") or "logs/drill_array_drop")
            drill.parent.mkdir(parents=True, exist_ok=True)
            drill.write_text("drill\n")
        else:
            prompt("[drill] UNPLUG the ReSpeaker's USB cable now; plug it back in when the drill says so.")
        t0 = time.monotonic()
        deadline = t0 + budget_s
        try:
            while out["lost_s"] is None:
                m = await next_msg(deadline)
                if m and m["type"] == "heartbeat" and m["status"] != "ok" and "ReSpeaker lost" in (m.get("detail") or ""):
                    out["lost_s"], out["detail"] = round(time.monotonic() - t0, 1), m.get("detail")
                    prompt(f"[drill] laptop says: {m['status']} - {m.get('detail')}")
            if mode == "unplug":
                prompt("[drill] PLUG the ReSpeaker back in now.")
            while out["back_s"] is None:
                m = await next_msg(deadline)
                if m and m["type"] == "heartbeat" and m["status"] == "ok":
                    out["back_s"] = round(time.monotonic() - t0, 1)
            while out["sources_s"] is None:
                m = await next_msg(deadline)
                if m and m["type"] == "sources":
                    out["sources_s"] = round(time.monotonic() - t0, 1)
        except asyncio.TimeoutError:
            missing = "degraded heartbeat" if out["lost_s"] is None else "ok heartbeat" if out["back_s"] is None \
                else "sources"
            out["reason"] = f"no {missing} within {budget_s:.0f} s"
            return out
    out["ok"] = True
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--inject", action="store_true", help="the laptop drops the array itself (no hands)")
    g.add_argument("--unplug", action="store_true", help="a human pulls and replugs the USB cable")
    p.add_argument("--relay")
    p.add_argument("--session")
    p.add_argument("--budget", type=float, default=None, help="seconds to recover (default 20 inject, 60 unplug)")
    a = p.parse_args()
    mode = "inject" if a.inject else "unplug"
    r = asyncio.run(run_drill(a.relay, a.session, mode, a.budget or (20.0 if a.inject else 60.0)))
    print(json.dumps(r))
    print("[drill] PASS" if r["ok"] else f"[drill] FAIL: {r['reason']}")
    sys.exit(0 if r["ok"] else 1)


if __name__ == "__main__":
    main()

"""Print every message in a relay session, one line each (README §22).

    python tools/relay_tap.py                         # session from laptop/config.yaml / HERE_SESSION
    python tools/relay_tap.py --types utterance,alert
    python tools/relay_tap.py --raw                   # full JSON
    python tools/relay_tap.py --validate              # also flag messages that break the contract
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

SKIP_BY_DEFAULT = {"heartbeat"}


def summary(m):
    t = m.get("type")
    if t == "sources":
        return " ".join(f"#{s['id']} {s['bearing_deg']:+6.1f}{' SPEECH' if s['speech'] else ''} "
                        f"e{s['energy']:.2f} c{s['confidence']:.2f}" for s in m.get("sources", []))
    if t == "speech_start":
        return f"{m['utt_id']} at {m['bearing_deg']:+.0f} (conf {m['confidence']:.2f})"
    if t == "utterance":
        flag = "FINAL" if m["final"] else "     "
        return (f"{m['utt_id']} {flag} {m['bearing_deg']:+5.0f}±{m['bearing_spread_deg']:.0f} "
                f"[{m.get('tone') or '-'}] {m['text']!r} + {m['pending']!r}" + (" OVERLAP" if m["overlap"] else ""))
    if t == "alert":
        b = "?" if m["bearing_deg"] is None else f"{m['bearing_deg']:+.0f}"
        return f"{m['emoji']} {m['label']} at {b} {'ACTIVE' if m['active'] else 'ended'} ({m['alert_id']})"
    if t == "sound_event":
        b = "?" if m["bearing_deg"] is None else f"{m['bearing_deg']:+.0f}"
        return f"{m['emoji']} {m['text']} x{m['count']} at {b} ({m['confidence']:.2f})"
    if t == "heartbeat":
        return f"{m['src']} {m['status']} {m.get('detail', '')} {m.get('engine', '')} {m.get('thermal', '')}"
    if t == "config":
        return f"user {m.get('user_name')!r} focus={m.get('focus_mode')} belt_only={m.get('belt_only_mode')}"
    return json.dumps({k: v for k, v in m.items() if k not in ("type", "v", "src", "t", "seq", "session")},
                      ensure_ascii=False)[:160]


async def main(args):
    cfg = settings.load()["relay"]
    url = relay_url(args.relay or cfg["url"], args.session or cfg["session"], "tool", cfg["token"])
    types = set(args.types.split(",")) if args.types else None
    validate = None
    if args.validate:
        from contracts.python.here_contract import errors as validate
    while True:
        try:
            async with connect(url, open_timeout=5) as ws:
                await ws.send(json.dumps({"type": "hello", "v": 3, "src": "tool", "t": time.time(), "role": "tool",
                                          "subscribe": ["*"]}))
                print(f"tapping {redact(url)}  (Ctrl+C to stop)")
                async for raw in ws:
                    m = json.loads(raw)
                    t = m.get("type")
                    if types is not None and t not in types:
                        continue
                    if types is None and t in SKIP_BY_DEFAULT and not args.all:
                        continue
                    stamp = time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
                    if args.raw:
                        print(f"{stamp} {raw}")
                    else:
                        print(f"{stamp} {m.get('src', '?'):6s} {t:13s} {summary(m)}")
                    if validate:
                        problems = validate(m)
                        if problems:
                            print(f"      CONTRACT: {'; '.join(problems)}")
        except (OSError, Exception) as e:  # noqa: BLE001
            print(f"[tap] {type(e).__name__}: {e}; retrying in 2 s")
            await asyncio.sleep(2)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--relay")
    p.add_argument("--session")
    p.add_argument("--types", help="comma-separated message types to show")
    p.add_argument("--all", action="store_true", help="include heartbeats")
    p.add_argument("--raw", action="store_true")
    p.add_argument("--validate", action="store_true")
    try:
        asyncio.run(main(p.parse_args()))
    except KeyboardInterrupt:
        pass

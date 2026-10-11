"""Sound -> client latency on the real array, measured where the phone sits: a relay client on this machine.

    python tools/doa_latency.py --relay ws://localhost:18082/ws --session k1gate --seconds 90
    python tools/doa_latency.py --json

Joins the session as a `dashboard` (gets every type) and stamps each laptop message on arrival. Same machine and
clock as the laptop, so no sync. Against the README's sound -> phone target (<= 250 ms for direction / alerts):

- direction: arrival - `sources.t` (the DoA sample's time: USB poll, tracker, throttle, serialize, relay);
- speech_start: arrival - the utterance's first audio (`t_start` of the utterance it starts, so it includes the
  VAD/array onset delay; pre-roll / look-back push t_start a little earlier than the voice);
- first words: arrival of the first partial with text - the same `t_start`;
- doorbell alert: arrival of the first active alert for a sound event - the sound's onset (the event's `t_start`
  + `sounds.onset_pre_s`, the window's pre-roll before the onset candidate).
Not included: the firmware's own acoustic -> DoA lag and the phone's Wi-Fi hop (measure with latency_probe.py).
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from laptop import settings  # noqa: E402
from laptop.client.relay import redact, relay_url  # noqa: E402

TARGET_MS = 250.0
MAX_SOUND_MS = 5000.0   # a doorbell alert later than this after its event start is an upgraded unknown


def summarize(rows, onset_pre_s=0.1):
    """rows: [(arrival, message dict)] -> {metric: [ms, ...]}."""
    out = {"direction": [], "speech_start": [], "first_words": [], "final_after_end": [], "doorbell_alert": []}
    starts, words_seen, events, alerted = {}, set(), {}, set()
    for arr, m in rows:
        t = m.get("type")
        if t == "sources" and m.get("sources"):
            out["direction"].append((arr - m["t"]) * 1000)
        elif t == "speech_start":
            starts[m["utt_id"]] = arr
        elif t == "utterance":
            u = m["utt_id"]
            if u in starts:
                out["speech_start"].append((starts.pop(u) - m["t_start"]) * 1000)
            if (m.get("text") or m.get("pending")) and u not in words_seen:
                words_seen.add(u)
                out["first_words"].append((arr - m["t_start"]) * 1000)
            if m.get("final") and m.get("text"):
                out["final_after_end"].append((arr - m["t_end"]) * 1000)
        elif t == "sound_event":
            events.setdefault(m["event_id"], m)
        elif t == "alert" and m.get("active") and m.get("kind") == "doorbell":
            ev = events.get((m.get("ref") or {}).get("event_id"))
            if ev is not None and ev["event_id"] not in alerted:
                alerted.add(ev["event_id"])
                ms = (arr - (ev["t_start"] + onset_pre_s)) * 1000
                if ms <= MAX_SOUND_MS:   # longer = an upgraded `unknown` kept its own (older) t_start: not measurable
                    out["doorbell_alert"].append(ms)
    return out


def table(stats):
    lines = ["| metric | n | p50 ms | p95 ms | max ms |", "|---|---|---|---|---|"]
    for k, xs in stats.items():
        if xs:
            a = np.array(xs)
            lines.append(f"| {k} | {len(a)} | {np.percentile(a, 50):.0f} | {np.percentile(a, 95):.0f} | {a.max():.0f} |")
        else:
            lines.append(f"| {k} | 0 | - | - | - |")
    return "\n".join(lines)


async def collect(url, seconds):
    rows = []
    async with connect(url, open_timeout=5) as ws:
        await ws.send(json.dumps({"type": "hello", "v": 3, "src": "dashboard", "t": time.time(), "role": "dashboard",
                                  "subscribe": ["*"]}))
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), max(0.01, end - time.monotonic()))
            except asyncio.TimeoutError:
                break
            arr = time.time()
            m = json.loads(raw)
            if m.get("src") == "laptop":
                rows.append((arr, m))
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--relay")
    p.add_argument("--session")
    p.add_argument("--seconds", type=float, default=60.0)
    p.add_argument("--json", action="store_true")
    a = p.parse_args()
    cfg = settings.load()
    url = relay_url(a.relay or cfg["relay"]["url"], a.session or cfg["relay"]["session"], "dashboard",
                    cfg["relay"]["token"])
    print(f"collecting {a.seconds:.0f} s from {redact(url)} ...", file=sys.stderr)
    rows = asyncio.run(collect(url, a.seconds))
    stats = summarize(rows, float(cfg.get("sounds", {}).get("onset_pre_s", 0.1)))
    if a.json:
        print(json.dumps({k: [round(x, 1) for x in v] for k, v in stats.items()}))
    else:
        print(table(stats))
        print(f"\ntarget: sound -> phone <= {TARGET_MS:.0f} ms (direction, alert); this is laptop -> local client")


if __name__ == "__main__":
    main()

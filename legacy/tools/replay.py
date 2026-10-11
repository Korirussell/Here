"""Replay a fixture into the hub in real time (HERE_PLAN.md §17), as if the sensors were live.

    python tools/replay.py two_people                  # name under fixtures/, or a path
    python tools/replay.py two_people --loop
    python tools/replay.py two_people --speed 2 --start 30
    python tools/replay.py two_people --types all      # also send the recorded utterances/detections

By default only the sensor streams (audio_chunk, doa, video_frame, heading) are sent, so the
running services (speech_here, vision_here, world_here fusion) recompute everything from them.
Stop the live producers first (audio_here, the phone / fake_phone), or the hub gets both.

Each original sender gets its own hub connection with its own role, so the hub sees the same
clients as it did live. Pacing follows the hub's original receive times; every timestamp
(t, t_start, t_end, name_mentions[].t) is moved to "now" so time-based logic behaves as live.
"""

import argparse
import asyncio
import json
import sys
import time
import wave
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from here_common.client import DEFAULT_URL, HubClient  # noqa: E402

SENSOR_TYPES = {"audio_chunk", "doa", "video_frame", "heading"}
NOT_REPLAYED = {"config", "calibrate", "hello", "heartbeat"}  # hub state, not part of the stream
NOT_FIELDS = {"type", "v", "src", "t", "seq", "rx", "_audio_offset", "_jpeg"}
CONNECT_TIMEOUT_S = 10.0


def find_fixture(arg):
    path = Path(arg)
    if not path.exists():
        path = ROOT / "fixtures" / arg
    if not (path / "messages.jsonl").exists():
        raise SystemExit(f"no fixture at {path} (expected messages.jsonl inside)")
    return path


def load(path, types):
    with open(path / "messages.jsonl", encoding="utf-8") as f:
        msgs = [json.loads(line) for line in f if line.strip()]
    msgs = [m for m in msgs if m["type"] not in NOT_REPLAYED and (types is None or m["type"] in types)]
    with wave.open(str(path / "audio.wav")) as w:
        audio = w.readframes(w.getnframes())
    return msgs, audio


class Replay:
    def __init__(self, path, msgs, audio, url, speed):
        self.path = path
        self.msgs = msgs
        self.audio = audio
        self.speed = speed
        self.sent = Counter()
        self.dropped = 0
        self.clients = {}
        for src in sorted({m["src"] for m in msgs}):
            c = HubClient(src, [], url)
            c.detail = f"replaying fixture {path.name}"
            self.clients[src] = c

    async def connect(self):
        tasks = [asyncio.create_task(c.run()) for c in self.clients.values()]
        deadline = time.time() + CONNECT_TIMEOUT_S
        while not all(c.connected for c in self.clients.values()):
            if time.time() > deadline:
                missing = [s for s, c in self.clients.items() if not c.connected]
                raise SystemExit(f"couldn't connect to the hub as {missing}; is world_here running?")
            await asyncio.sleep(0.1)
        return tasks

    async def play(self, start_s):
        rx0 = self.msgs[0]["rx"] + start_s
        msgs = [m for m in self.msgs if m["rx"] >= rx0]
        t0 = time.time() + 0.2

        def now_of(x):  # recorded laptop time -> replay laptop time
            return t0 + (x - rx0) / self.speed

        for m in msgs:
            await asyncio.sleep(max(0.0, now_of(m["rx"]) - time.time()))
            fields = {k: v for k, v in m.items() if k not in NOT_FIELDS}
            for k in ("t_start", "t_end"):
                if k in fields:
                    fields[k] = round(now_of(fields[k]), 3)
            if fields.get("name_mentions"):
                fields["name_mentions"] = [{**n, "t": round(now_of(n["t"]), 3)} for n in fields["name_mentions"]]
            client = self.clients[m["src"]]
            t = now_of(m["t"])

            if m["type"] == "audio_chunk":
                off = m["_audio_offset"] * 2
                payload = self.audio[off:off + m["samples"] * 2]
                ok = await client.send_with_binary("audio_chunk", payload, t=t, drop_if_busy=False, **fields)
            elif m["type"] == "video_frame":
                payload = (self.path / m["_jpeg"]).read_bytes()
                ok = await client.send_with_binary("video_frame", payload, t=t, drop_if_busy=False, **fields)
            else:
                ok = await client.send(m["type"], t=t, **fields)
            if ok:
                self.sent[m["type"]] += 1
            else:
                self.dropped += 1

    async def status(self, total_s):
        t0 = time.time()
        while True:
            await asyncio.sleep(0.5)
            s = self.sent
            print(f"\r  {(time.time() - t0) * self.speed:6.1f} / {total_s:.1f} s  audio {s['audio_chunk']:5d}  "
                  f"doa {s['doa']:5d}  frames {s['video_frame']:5d}  heading {s['heading']:5d}  "
                  f"other {sum(s.values()) - s['audio_chunk'] - s['doa'] - s['video_frame'] - s['heading']:4d}"
                  + (f"  dropped {self.dropped}" if self.dropped else "") + "  ", end="", flush=True)


async def main(args):
    path = find_fixture(args.fixture)
    types = None if args.types == "all" else set(args.types.split(",")) if args.types else SENSOR_TYPES
    msgs, audio = load(path, types)
    if not msgs:
        raise SystemExit("nothing to replay with those --types")
    total = msgs[-1]["rx"] - msgs[0]["rx"] - args.start
    counts = Counter(m["type"] for m in msgs)
    print(f"replaying {path.name}: {total:.1f} s, {dict(counts)} as {sorted({m['src'] for m in msgs})}")

    r = Replay(path, msgs, audio, args.url, args.speed)
    tasks = await r.connect()
    status = asyncio.create_task(r.status(total))
    try:
        while True:
            await r.play(args.start)
            if not args.loop:
                break
            print("\n  looping")
    finally:
        status.cancel()
        for t in tasks:
            t.cancel()
    print(f"\ndone: sent {sum(r.sent.values())} messages" + (f", {r.dropped} dropped" if r.dropped else ""))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("fixture", help="fixture name under fixtures/, or a path to one")
    p.add_argument("--types", help="comma-separated message types to send, or 'all' (default: sensor streams)")
    p.add_argument("--speed", type=float, default=1.0, help="playback speed (default 1.0)")
    p.add_argument("--start", type=float, default=0.0, help="skip this many seconds from the beginning")
    p.add_argument("--loop", action="store_true", help="repeat until Ctrl+C")
    p.add_argument("--url", default=DEFAULT_URL)
    args = p.parse_args()
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        print()

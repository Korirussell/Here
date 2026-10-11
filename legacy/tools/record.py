"""Record a fixture from the hub (HERE_PLAN.md §18 M2): everything the sensors and services send, in sync.

Taps the running hub, so whatever is connected gets recorded: audio_here (audio + DoA),
the phone or fake_phone (video + heading), and any perception output. Ctrl+C to stop.

    python tools/record.py two_people --notes "Maya left, Sam right, 1.5 m"
    python tools/record.py doorbell_back --seconds 60

Writes fixtures/<name>/:
    messages.jsonl   every JSON message in arrival order, plus "rx" (hub-side receive time);
                     audio_chunk lines carry "_audio_offset" (sample index into audio.wav),
                     video_frame lines carry "_jpeg" (path of the frame, relative to the fixture)
    audio.wav        16 kHz mono s16le, time-aligned to the first chunk (gaps filled with silence)
    video/           one JPEG per video_frame
    meta.json        name, notes, start/end, counts, array calibration at record time
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
from audio_here import config as AUDIO  # noqa: E402
from here_common.client import DEFAULT_URL, HubClient  # noqa: E402

FIXTURES = ROOT / "fixtures"
SR = 16000
# What a fixture holds: sensor streams (replayed by default) and service outputs (for comparison).
SUBSCRIBE = ["audio_chunk", "doa", "video_frame", "heading",
             "utterance", "detections", "sound_event", "sound_doa", "config", "calibrate"]
GAP_FILL_S = 0.15  # a chunk arriving this much later than expected means chunks were dropped


class Recorder:
    def __init__(self, path, url, notes):
        self.path = path
        self.notes = notes
        (path / "video").mkdir(parents=True)
        self.messages = open(path / "messages.jsonl", "w", encoding="utf-8")
        self.wav = wave.open(str(path / "audio.wav"), "wb")
        self.wav.setnchannels(1)
        self.wav.setsampwidth(2)
        self.wav.setframerate(SR)
        self.audio_t0 = None
        self.audio_written = 0
        self.frames = 0
        self.counts = Counter()
        self.t_first = self.t_last = None
        self.hub = HubClient("recorder", SUBSCRIBE, url)  # a tool, not a contract service: listens only
        self.hub.detail = f"recording fixture {path.name}"
        self.hub.on_message = self.on_message

    async def on_message(self, msg, payload):
        rx = time.time()
        kind = msg["type"]
        line = {"rx": rx, **msg}
        if kind == "audio_chunk" and payload is not None:
            line["_audio_offset"] = self.write_audio(msg["t"], payload)
        elif kind == "video_frame" and payload is not None:
            self.frames += 1
            rel = f"video/{self.frames:06d}.jpg"
            (self.path / rel).write_bytes(payload)
            line["_jpeg"] = rel
        elif kind in ("audio_chunk", "video_frame"):
            return  # header without its payload
        self.messages.write(json.dumps(line) + "\n")
        self.counts[kind] += 1
        if kind not in ("config", "calibrate"):
            self.t_first = self.t_first or msg["t"]
            self.t_last = msg["t"]

    def write_audio(self, t, payload):
        if self.audio_t0 is None:
            self.audio_t0 = t
        expected = round((t - self.audio_t0) * SR)
        if expected - self.audio_written > GAP_FILL_S * SR:
            gap = expected - self.audio_written
            self.wav.writeframes(b"\0\0" * gap)
            self.audio_written += gap
            print(f"\n  audio gap of {gap / SR:.2f} s filled with silence")
        offset = self.audio_written
        self.wav.writeframes(payload)
        self.audio_written += len(payload) // 2
        return offset

    async def status(self, stop_at):
        t0 = time.time()
        while stop_at is None or time.time() < stop_at:
            await asyncio.sleep(0.5)
            c = self.counts
            hub = "" if self.hub.connected else "  (hub not reachable, retrying)"
            print(f"\r  {time.time() - t0:6.1f} s  audio {self.audio_written / SR:6.1f} s  doa {c['doa']:5d}  "
                  f"frames {c['video_frame']:5d}  heading {c['heading']:5d}  utterances {c['utterance']:3d}{hub}  ",
                  end="", flush=True)

    def close(self):
        self.messages.close()
        self.wav.close()
        meta = {
            "name": self.path.name, "notes": self.notes,
            "t_start": self.t_first, "t_end": self.t_last,
            "duration_s": round(self.t_last - self.t_first, 2) if self.t_first else 0.0,
            "audio_t0": self.audio_t0, "audio_s": round(self.audio_written / SR, 2),
            "counts": dict(self.counts),
            "array_calibration": {"array_offset_deg": AUDIO.ARRAY_OFFSET_DEG,
                                  "array_clockwise": AUDIO.ARRAY_CLOCKWISE},
            "recorded": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        (self.path / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return meta


async def main(args):
    path = FIXTURES / args.name
    if path.exists():
        raise SystemExit(f"{path} already exists; pick another name or delete it yourself")
    rec = Recorder(path, args.url, args.notes)
    print(f"recording to {path} — Ctrl+C to stop")
    hub_task = asyncio.create_task(rec.hub.run())
    try:
        while not rec.hub.connected:  # --seconds counts from when recording actually starts
            await asyncio.sleep(0.05)
        stop_at = time.time() + args.seconds if args.seconds else None
        await rec.status(stop_at)
    finally:
        hub_task.cancel()
        meta = rec.close()
        print(f"\nsaved {path}: {meta['duration_s']} s, {meta['counts']}")
        if not meta["counts"].get("audio_chunk"):
            print("  no audio recorded: is audio_here (or tools/fake_audio.py) connected to the hub?")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("name", help="fixture name, e.g. two_people (folder under fixtures/)")
    p.add_argument("--notes", default="", help="who stood where, what happened")
    p.add_argument("--seconds", type=float, help="stop automatically after this long")
    p.add_argument("--url", default=DEFAULT_URL)
    args = p.parse_args()
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        pass

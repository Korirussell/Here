"""Would-be early directional cues from the laptop's onset candidates.

    python tools/doa_cues.py fixtures/k1_positions      # real chatter: how many cues a rule would send per minute
    python tools/doa_cues.py fixtures/array_tts         # a scene with a doorbell: is it cued, how fast

Replays the fixture in real time through the app with speech and sounds off, collects every onset candidate
(`app.onsets`, with its publish time), and applies the candidate cue rules: per rule, cues per minute and, when the
fixture's meta.json has a doorbell line, whether (and how many ms after its start) the doorbell was cued.
"""

import argparse
import asyncio
import sys
import time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from laptop import settings  # noqa: E402
from laptop.app import Laptop  # noqa: E402
from laptop.audio.mock import FixturePlayer  # noqa: E402

AMBIENCE_N, AMBIENCE_S = 4, 30.0
RULES = [  # (name, min rise dB, ambience counts speech-flagged onsets too)
    ("every onset, not ambience", 0.0, True),
    ("rise >= 20 dB, not ambience", 20.0, True),
    ("rise >= 20 dB, ambience = non-speech onsets", 20.0, False),
]


class Sink:
    connected = True
    on_config = on_message = None
    heartbeat_fields = None

    def send(self, msg):
        return True

    async def run(self):
        await asyncio.sleep(3600)


def collect(folder):
    p = FixturePlayer(folder)
    app = Laptop(settings.load(), (p, p), Sink())
    cands = []
    app.onsets.subscribe(lambda c: cands.append((time.time(), c)))

    async def main():
        task = asyncio.create_task(app.run())
        await asyncio.sleep(p.duration + 2)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(main())
    return p, cands


def apply(cands, rise_min, speech_counts):
    recent, out = {}, []
    for arr, c in cands:
        q = recent.setdefault(c.source_id, deque())
        while q and c.t - q[0] > AMBIENCE_S:
            q.popleft()
        ambience = len(q) >= AMBIENCE_N
        if speech_counts or not c.speech:
            q.append(c.t)
        if c.rise_db >= rise_min and not ambience:
            out.append((arr, c))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fixture")
    a = ap.parse_args()
    p, cands = collect(Path(a.fixture))
    minutes = p.duration / 60
    door = next((l for l in p.meta.get("lines", []) if l["who"] == "doorbell"), None)
    delays = sorted(round((arr - c.t) * 1000) for arr, c in cands)
    print(f"{p.folder.name}: {len(cands)} onset candidates in {minutes:.1f} min "
          f"({sum(not c.speech for _, c in cands)} not speech-flagged); publish delay p50 "
          f"{delays[len(delays) // 2] if delays else '-'} ms")
    for name, rise_min, speech_counts in RULES:
        cues = apply(cands, rise_min, speech_counts)
        line = f"  [{name}] {len(cues) / minutes:.1f} cues/min"
        if door:
            hit = [round((arr - p.t0 - door["t_start"]) * 1000) for arr, c in cues
                   if -0.3 <= c.t - p.t0 - door["t_start"] <= 0.5]
            line += f"; doorbell cued at {hit[0]} ms" if hit else "; doorbell MISSED"
        print(line)


if __name__ == "__main__":
    main()

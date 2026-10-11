"""Speech latency + failure-injection bench on a fixture, in real time, through the real SpeechPipeline.

    python -m laptop.speech.bench fixtures/array_tts                         # speech.engine from config.yaml
    python -m laptop.speech.bench fixtures/array_tts --engine scribe_realtime
    python -m laptop.speech.bench fixtures/array_tts --engine scribe_realtime --unplug 12:20
    python -m laptop.speech.bench fixtures/mock_scene --primary fake --unplug 6:14   # no key: the switch itself

Per scripted line of the fixture's meta.json: the engine that captioned it, first words (seconds after the line
started), final (seconds after it ended), WER of the final. --unplug A:B makes the primary engine fail from
fixture second A to B (its sessions are closed, as if the network dropped mid-utterance) to show whisper taking
over and the primary coming back. --primary fake: a FakeTranscriber reading the fixture's own lines stands in
for Scribe, so the switching can be checked without an API key (its captions are scripted, not heard).
"""

import torch  # noqa: F401  (first: cuBLAS/cuDNN DLLs for faster-whisper on Windows)

import argparse
import asyncio
import re
import sys
import time

from laptop import settings
from laptop.app import fixture_path
from laptop.audio.base import DirectionHistory
from laptop.audio.mock import FixturePlayer
from laptop.speech import transcriber as T
from laptop.speech.pipeline import SpeechPipeline


def words(text):
    return [w for w in re.sub(r"[^\w' ]", " ", text.lower()).split() if w]


def wer(ref, hyp):
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
    return d[len(h)] / max(1, len(r))


class Unplug:
    """Wraps an engine: between fixture seconds a and b every call raises, like a dropped network."""

    def __init__(self, engine, window, clock):
        self.inner, self.window, self.clock = engine, window, clock
        self.engine, self.streaming = engine.engine, getattr(engine, "streaming", False)
        self.hinting = getattr(engine, "hinting", False)
        self.was_down = False

    def down(self):
        rel = self.clock()
        is_down = self.window is not None and rel is not None and self.window[0] <= rel < self.window[1]
        if is_down and not self.was_down and hasattr(self.inner, "close"):
            self.inner.close()                       # live sessions die mid-utterance
        self.was_down = is_down
        return is_down

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def warm(self):
        if self.down():
            raise ConnectionError("unplugged")
        return self.inner.warm()

    def decode(self, audio, final, use_hint=True, utt_id=None):
        if self.down():
            raise ConnectionError("unplugged")
        return T.decode_call(self.inner, audio, final, use_hint, utt_id)


def build(cfg, args, lines, clock):
    if args.primary == "fake" or cfg.get("engine") == "scribe_realtime":
        whisper, _ = T.make_transcriber(dict(cfg, engine="faster_whisper"))
        if args.primary == "fake":
            primary = T.FakeTranscriber([l["text"] for l in lines], engine="scribe_realtime")
        else:
            primary = T.ScribeRealtimeTranscriber(**(cfg.get("scribe") or {}))
        fb = T.FallbackTranscriber(Unplug(primary, args.unplug, clock), whisper,
                                   cfg.get("fallback_max_latency_s", 1.5), args.retry_s)
        fb.warm()
        return fb
    return T.make_transcriber(cfg)[0]


async def run(args):
    cfg = settings.load()
    sp = dict(cfg["speech"])
    if args.engine:
        sp["engine"] = args.engine
    player = FixturePlayer(fixture_path(args.fixture))
    lines = [l for l in player.meta.get("lines", []) if l.get("text") and l.get("who") != "doorbell"]
    clock = lambda: None if player.t0 is None else time.time() - player.t0   # noqa: E731
    engine = build(sp, args, lines, clock)
    history = DirectionHistory()
    history.reports_speech = player.reports_speech
    got = []                                          # (wall time, message)
    pipe = SpeechPipeline(sp, engine, history, lambda m: got.append((time.time(), m)), vad=None,
                          names=[player.meta.get("user_name") or "Kori"],
                          wearer_bearing=cfg["audio"]["wearer_bearing_deg"],
                          wearer_ignore_deg=cfg["audio"]["wearer_ignore_deg"])
    q = asyncio.Queue(maxsize=400)
    loop = asyncio.get_running_loop()
    player.on_sources = history.add
    player.on_audio = lambda t, pcm: loop.call_soon(q.put_nowait, (t, pcm))
    task = asyncio.create_task(pipe.run(q))
    await player.run()
    await asyncio.sleep(3.0)                          # last final
    task.cancel()
    report(args, player, lines, got, engine)


def report(args, player, lines, got, engine):
    t0 = player.t0
    utts = [(w, m) for w, m in got if m.TYPE == "utterance"]
    print(f"\n{player.folder.name}: {len(lines)} lines, engine now {engine.engine}")
    print(f"{'line':>4} {'start':>6} {'engine':<16} {'first':>6} {'final':>6} {'wer':>5}  text")
    firsts, finals, wers = [], [], []
    for k, l in enumerate(lines):
        a, b = l["t_start"], l["t_end"]
        mine = [(w, m) for w, m in utts if m.t_end - t0 > a - 0.3 and m.t_start - t0 < b + 0.3]
        shown = [(w, m) for w, m in mine if m.text or m.pending]
        finals_ = [(w, m) for w, m in mine if m.final and m.text]
        first = shown[0][0] - t0 - a if shown else None
        fin = max(w for w, _ in finals_) - t0 - b if finals_ else None
        text = " ".join(m.text for _, m in sorted(finals_, key=lambda x: x[1].t_start))
        e = wer(l["text"], text)
        eng = ",".join(dict.fromkeys(m.engine for _, m in finals_)) or "-"
        firsts += [first] if first is not None else []
        finals += [fin] if fin is not None else []
        wers.append(e)
        print(f"{k:>4} {a:>6.1f} {eng:<16} {first if first is not None else float('nan'):>6.2f} "
              f"{fin if fin is not None else float('nan'):>6.2f} {e:>5.2f}  {text[:60]!r}")
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else float("nan")   # noqa: E731
    finals_per_utt = {}
    for _, m in utts:
        if m.final:
            finals_per_utt[m.utt_id] = finals_per_utt.get(m.utt_id, 0) + 1
    dupes = [u for u, n in finals_per_utt.items() if n > 1]
    print(f"median first words {med(firsts):.2f} s after the line starts, final {med(finals):.2f} s after it ends, "
          f"mean WER {sum(wers) / max(1, len(wers)):.2f}, utterances {len(finals_per_utt)}, duplicate finals {len(dupes)}")
    for t, e in getattr(engine, "switches", []):
        print(f"switch at fixture {t - t0:5.1f} s -> {e}")
    if getattr(engine, "reason", None):
        print(f"heartbeat detail now: {engine.reason}")


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("fixture")
    p.add_argument("--engine", choices=["faster_whisper", "scribe_realtime", "fake"])
    p.add_argument("--primary", choices=["scribe", "fake"], default="scribe",
                   help="fake: a scripted stand-in for Scribe (no key needed), whisper as the fallback")
    p.add_argument("--unplug", type=lambda s: tuple(float(x) for x in s.split(":")), metavar="A:B",
                   help="primary engine fails from fixture second A to B")
    p.add_argument("--retry-s", type=float, default=3.0, help="probe the primary this often while it's down")
    asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    main()

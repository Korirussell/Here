"""Scripted laptop events through the relay, for building the phone, the dashboard and the cloud without any
audio hardware (SPEC §14, docs/INTEGRATION.md §9).

Two kinds of script:

* **Moments** (SPEC §4's three core moments, with realistic timing): directions (`sources`) at 20 Hz while
  someone talks, `speech_start` at onset, word-by-word `utterance` partials, then the final.

      Person 1   front-right  +35 deg   "Hey, did you see the email about the internship fair?"
      Person 2   front-left   -35 deg   "Yeah! It's on Friday. Are you going?"
      Speaker B  behind       180 deg   the turn-around line
      doorbell   behind-right +165 deg  sound_event + alert
      Speaker B  behind       180 deg   "Hey Kori, ..." + name_called alert

* **Scenes** (`fixtures/events/<name>.jsonl`): one canonical message per line, `t` (and `t_start` / `t_end`)
  in seconds from the scene start, no `seq` / `session`. They mirror what `python -m laptop --real` sends in
  beams mode (fixed beams at world 0 / 180: `sources` id 0 = front beam, id 1 = rear beam), so anything built
  against a scene works unchanged on the real array. Playing one rebases the times onto the laptop clock and
  makes the ids unique per play (`-<run>-<loop>-<scene>`), so the phone never dedupes a replay into an old event.

      python tools/fake_events.py                       # the three moments, relay from laptop/config.yaml
      python tools/fake_events.py --loop --speed 1.5
      python tools/fake_events.py --relay ws://localhost:8080/ws --once
      python tools/fake_events.py --moment 2            # only the turn-around
      python tools/fake_events.py --scene rear_speech [--loop]
      python tools/fake_events.py --scene all           # every scene, in SCENES order
      python tools/fake_events.py --list
      python tools/fake_events.py --build-scenes        # rewrite the scripted fixtures/events/*.jsonl
      python tools/fake_events.py --from-log logs/laptop.jsonl --start T0 --end T1 --out fixtures/events/x.jsonl
"""

import argparse
import asyncio
import heapq
import itertools
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from contracts.python import here_contract as hc  # noqa: E402
from contracts.python.here_contract import (Alert, PerceptionEnrichment, Ref, SoundEvent, Source,  # noqa: E402
                                            Sources, SpeechStart, Utterance)
from laptop import settings  # noqa: E402
from laptop.audio.base import SourcesThrottle  # noqa: E402
from laptop.events.tracker import SourceTracker  # noqa: E402

SCENE_DIR = ROOT / "fixtures" / "events"
P1, P2, BEHIND, DOOR = 35.0, -35.0, 180.0, 165.0
FRONT, REAR = 0.0, 180.0                 # the demo's fixed beams (laptop/config.yaml audio.fixed_beams)
TICK = 0.05                              # 20 Hz, audio.doa_rate_hz
AMBIENT = 0.3                            # a quiet room's level on the processed audio (0..1)
ONSET_S, WORD_S, TAIL_S = 0.12, 0.28, 0.6   # VAD onset, one word, end-of-speech silence
LAPTOP_TYPES = {"sources", "speech_start", "utterance", "sound_event", "alert", "perception_enrichment"}
TIME_FIELDS = ("t", "t_start", "t_end")

MOMENTS = {
    1: [("say", P1, "Hey, did you see the email about the internship fair?", "questioning"),
        ("pause", 0.8),
        ("say", P2, "Yeah! It's on Friday. Are you going?", "excited"),
        ("pause", 0.8),
        ("say", P1, "Definitely. I'm bringing twenty copies of my resume.", "neutral"),
        ("pause", 1.5)],
    2: [("say", BEHIND, "Why is nobody looking at the person holding all the pizza?", "questioning"),
        ("pause", 2.0)],
    3: [("doorbell", DOOR),
        ("pause", 3.0),
        ("say", BEHIND, "Hey Kori, the pizza is here!", "excited", "name"),
        ("pause", 3.0)],
}


class RealClock:
    """Wall time; script seconds pass `speed` times faster."""

    def __init__(self, speed=1.0):
        self.speed = speed

    def now(self):
        return time.time()

    async def sleep(self, s):
        await asyncio.sleep(s / self.speed)


class VirtualClock:
    """Scene time from 0, no waiting: building fixtures/events/ is instant and deterministic."""

    def __init__(self, t0=0.0):
        self.t = t0

    def now(self):
        return round(self.t, 3)

    async def sleep(self, s):
        self.t += s


class Collector:
    """A client stand-in that keeps what was sent (scene building)."""

    connected = True

    def __init__(self):
        self.messages = []
        self.sent = 0

    def send(self, msg):
        self.sent += 1
        self.messages.append(msg)
        return True


class Talker:
    def __init__(self, sid, bearing, text, tone, start=0.0, name=False, energy=0.6, conf=0.8):
        self.sid, self.bearing, self.text, self.tone, self.start, self.name = sid, bearing, text, tone, start, name
        self.energy, self.conf = energy, conf
        self.words = text.split()
        self.phase = "wait"              # wait -> onset -> speaking -> tail -> done
        self.phase_st = 0.0
        self.shown = 0
        self.utt = None
        self.t_start = 0.0
        self.overlap = False

    @property
    def audible(self):
        return self.phase in ("onset", "speaking")


class Script:
    """Plays moments and scene steps into `client.send`. Times come from `clock` (real or virtual); timers
    (alerts ending, later enrichments) and idle `sources` keepalives run inside `sleep`, so the virtual clock
    produces exactly what a real run sends.

    Like the real array, the script produces raw readings every tick (`frame`): in beams mode both beams, the
    quiet one at room level. They go through the laptop's own SourceTracker and SourcesThrottle, so the
    `sources` sent (rate, holds, expiry down to an empty list) are the ones `python -m laptop` would send."""

    def __init__(self, client, speed=1.0, seed=1, clock=None, jitter=2.5, beams=None):
        self.c = client
        self.clock = clock or RealClock(speed)
        self.speed = speed
        self.rng = random.Random(seed)
        self.jitter = jitter             # deg of noise on bearings (0 = fixed beams: the real beams-mode output)
        self.st = 0.0                    # script time (seconds at speed 1)
        self.timers = []                 # heap of (script time, n, fn)
        self.timer_n = itertools.count()
        self.n = 0
        self.alert_n = 0
        self.event_n = 0
        self.beams = beams               # {sid: bearing} of the fixed beams (scenes), None = one source (moments)
        self.ambient = [(0, 0.0)]        # moments: where the quiet reading points (the last talker)
        self.last_frame_st = -1e9
        a = settings.load()["audio"]
        self.tracker = SourceTracker.from_config(a)
        self.throttle = SourcesThrottle(a["sources_idle_change_deg"], a["sources_keepalive_s"])

    # ---- time -------------------------------------------------------------------

    def now(self):
        return self.clock.now()

    async def sleep(self, s):
        end = self.st + s
        while self.st < end - 1e-9:
            dt = min(TICK, end - self.st)
            await self.clock.sleep(dt)
            self.st += dt
            self._tick()

    def _tick(self):
        while self.timers and self.timers[0][0] <= self.st + 1e-9:
            heapq.heappop(self.timers)[2]()
        if self.st - self.last_frame_st >= 2 * TICK - 1e-9:      # nothing said this tick: a quiet reading
            self.idle()

    def later(self, delay, fn):
        heapq.heappush(self.timers, (self.st + delay, next(self.timer_n), fn))

    async def drain(self):
        """Run the remaining timers (alerts ending) before stopping."""
        while self.timers:
            await self.sleep(max(TICK, self.timers[0][0] - self.st))

    # ---- messages ---------------------------------------------------------------

    def bearing(self, b):
        b = round(((b + (self.rng.gauss(0, self.jitter) if self.jitter else 0.0) + 180) % 360) - 180, 1)
        return 180.0 if b <= -180 else b

    def frame(self, entries):
        """One tick of raw readings, entries: (sid, bearing, speech, energy, confidence). Beams mode fills in
        the quiet beam. Sends `sources` when the throttle would."""
        if self.beams is not None:
            have = {e[0] for e in entries}
            entries = sorted(list(entries) + [(sid, b, False, AMBIENT, 0.0) for sid, b in self.beams.items()
                                              if sid not in have])
        elif entries:
            self.ambient = [(sid, b) for sid, b, *_ in entries]
        raw = [Source(id=sid, bearing_deg=self.bearing(b), energy=round(e, 2), speech=sp,
                      confidence=round(cf if sp else 0.0, 2)) for sid, b, sp, e, cf in entries]
        self.last_frame_st = self.st
        t = self.now()
        wire = self.tracker.update(t, raw)
        if self.throttle.should_send(t, wire, active=self.tracker.active):
            self.c.send(Sources(src="laptop", t=t, sources=wire))

    def idle(self):
        self.frame([] if self.beams is not None else [(sid, b, False, 0.03, 0.0) for sid, b in self.ambient])

    def utterance(self, tk, committed, pending, final):
        return Utterance(src="laptop", t=self.now(), utt_id=tk.utt, t_start=tk.t_start, t_end=self.now(),
                         text=" ".join(committed), pending=" ".join(pending), final=final,
                         bearing_deg=tk.bearing, bearing_spread_deg=3.0, source_id=tk.sid, overlap=tk.overlap,
                         tone=tk.tone, tone_confidence=0.75 if final else 0.4,
                         loudness=round(self.rng.uniform(0.45, 0.65), 2), engine="faster_whisper",
                         stage_ms={"asr": 120.0, "lag": 180.0})

    def alert(self, kind, label, emoji, bearing, conf, ref, active=True, alert_id=None, priority=2):
        if alert_id is None:
            self.alert_n += 1
            alert_id = f"fake-a{self.alert_n}"
        self.c.send(Alert(src="laptop", t=self.now(), alert_id=alert_id, kind=kind, label=label, emoji=emoji,
                          priority=priority, bearing_deg=bearing, active=active, confidence=conf, ref=ref))
        return alert_id

    def alert_for(self, seconds, kind, label, emoji, bearing, conf, ref, priority=2):
        """An alert that ends (active: false) `seconds` later, like laptop/alerts.py's expiry."""
        aid = self.alert(kind, label, emoji, bearing, conf, ref, priority=priority)
        self.later(seconds, lambda: self.alert(kind, label, emoji, bearing, conf, ref, active=False, alert_id=aid,
                                               priority=priority))
        return aid

    def enrich(self, ref, attention, conf, directed=None):
        """perception_enrichment as the rules fallback produces it (the classifier replaces the model id)."""
        self.c.send(PerceptionEnrichment(src="laptop", t=self.now(), ref=ref, attention=attention,
                                         attention_confidence=conf, attention_model="rules",
                                         directed_at_user=directed))

    # ---- speech -----------------------------------------------------------------

    async def say(self, bearing, text, tone, name=False, sid=0):
        await self.talk([Talker(sid, bearing, text, tone, name=name)])

    async def talk(self, talkers, background=None):
        """Several talkers on one 20 Hz loop (each Talker starts `start` s in). background(): extra `sources`
        entries per frame (crowd noise). Returns when every talker has said their final."""
        t0 = self.st
        while any(tk.phase != "done" for tk in talkers):
            rel = self.st - t0
            for tk in talkers:
                self._advance(tk, rel)
            live = [tk for tk in talkers if tk.audible]
            if len(live) > 1:
                for tk in live:
                    tk.overlap = True
            entries = [(tk.sid, tk.bearing, True, tk.energy, tk.conf) for tk in live]
            if background is not None:
                taken = {e[0] for e in entries}
                entries += [e for e in background() if e[0] not in taken]
            if entries:
                self.frame(sorted(entries))
            await self.sleep(TICK)

    def _advance(self, tk, rel):
        since = self.st - tk.phase_st
        if tk.phase == "wait" and rel >= tk.start - 1e-9:
            self.n += 1
            tk.utt = f"fake-u{self.n}"
            tk.t_start, tk.phase, tk.phase_st = self.now(), "onset", self.st
        elif tk.phase == "onset" and since >= ONSET_S - 1e-9:
            self.c.send(SpeechStart(src="laptop", t=self.now(), utt_id=tk.utt, bearing_deg=tk.bearing,
                                    source_id=tk.sid, confidence=round(tk.conf + 0.02, 2)))
            tk.phase, tk.phase_st = "speaking", self.st
        elif tk.phase == "speaking" and since >= WORD_S * (tk.shown + 1) - 1e-9:
            tk.shown += 1
            if tk.shown % 2 == 0 or tk.shown == len(tk.words):          # a partial every ~0.5 s
                committed = max(0, tk.shown - 2)
                self.c.send(self.utterance(tk, tk.words[:committed], tk.words[committed:tk.shown], False))
            if tk.shown == len(tk.words):
                tk.phase, tk.phase_st = "tail", self.st
        elif tk.phase == "tail" and since >= TAIL_S - 1e-9:
            self.c.send(self.utterance(tk, tk.words, [], True))
            tk.phase = "done"
            if tk.name:
                ref = Ref(utt_id=tk.utt)
                self.alert_for(6.0, "name_called", "Name called", "🗣️", tk.bearing, 0.9, ref)
                self.later(0.2, lambda: self.enrich(ref, "important", 0.9, directed=0.95))

    # ---- sounds -----------------------------------------------------------------

    async def sound(self, label, bearing, seconds, sid=1, beam=REAR, energy=0.7, conf=0.71, alert=None,
                    attention=None, model="panns_cnn14"):
        """A non-speech sound heard on beam `sid` (DoA is speech-gated: energy, no speech flag), then its
        sound_event at the SRP bearing, an update when it ends, the alert, and the enrichment."""
        category, emoji, text = SOUND_LABELS[label]
        t0 = self.now()
        self.event_n += 1
        ev = f"fake-s{self.event_n}"
        self.tracker.note_onset(sid, t0)                          # the onset detector fires on that beam
        for i in range(int(round(seconds / TICK))):
            self.frame([(sid, beam, False, energy, 0.0)])
            if i == int(round(0.6 / TICK)):                       # one classifier window in: onset event
                self.c.send(SoundEvent(src="laptop", t=self.now(), event_id=ev, label=label, category=category,
                                       emoji=emoji, text=text, confidence=conf, bearing_deg=bearing,
                                       t_start=t0, t_end=self.now(), count=1, source_id=sid, model=model))
                ref = Ref(event_id=ev)
                if alert:
                    self.alert_for(6.0, label, text, emoji, bearing, conf, ref, priority=alert)
                if attention:
                    self.later(0.3, lambda: self.enrich(ref, attention, 0.8))
            await self.sleep(TICK)
        self.c.send(SoundEvent(src="laptop", t=self.now(), event_id=ev, label=label, category=category,
                               emoji=emoji, text=text, confidence=conf, bearing_deg=bearing, t_start=t0,
                               t_end=self.now(), count=1, source_id=sid, model=model))

    async def doorbell(self, bearing):
        """The SPEC §4 moment-3 doorbell (one source, id 0), kept as it was for --moment 3."""
        await self.sound("doorbell", bearing, 0.6 + TICK, sid=0, beam=bearing, alert=2, model="enrolled")

    # ---- moments ----------------------------------------------------------------

    async def play(self, moments):
        for m in moments:
            print(f"[fake_events] moment {m}")
            for step in MOMENTS[m]:
                if step[0] == "pause":
                    await self.sleep(step[1])
                elif step[0] == "doorbell":
                    await self.doorbell(step[1])
                else:
                    await self.say(step[1], step[2], step[3], name=len(step) > 4)
        await self.drain()


SOUND_LABELS = {"doorbell": ("alert", "🔔", "Doorbell"), "unknown": ("other", "❔", "Unknown sound"),
                "baby_crying": ("alert", "👶", "Baby crying")}


# ---- scenes (docs/INTEGRATION.md §9; beams mode, fixed beams 0 / 180) ------------------------------------

async def scene_front_speech(s):
    await s.sleep(0.5)
    await s.talk([Talker(0, FRONT, "Hey, did you see the email about the internship fair?", "questioning")])
    await s.sleep(0.8)
    await s.talk([Talker(0, FRONT, "I'm bringing twenty copies of my resume.", "neutral")])
    await s.sleep(1.5)


async def scene_rear_speech(s):
    await s.sleep(0.5)
    await s.talk([Talker(1, REAR, "Why is nobody looking at the person holding all the pizza?", "questioning")])
    await s.sleep(2.0)


async def scene_doorbell_rear(s):
    await s.sleep(0.5)
    await s.sound("doorbell", 165.0, 1.5, alert=2, attention="urgent", model="enrolled")
    await s.sleep(2.0)


async def scene_name_called_rear(s):
    await s.sleep(0.5)
    await s.talk([Talker(1, REAR, "Hey Kori, the pizza is here!", "excited", name=True)])
    await s.sleep(3.0)


async def scene_unknown_sound(s):
    await s.sleep(0.5)
    await s.sound("unknown", -120.0, 1.2, energy=0.55, conf=0.32, attention="notice")
    await s.sleep(2.0)


async def scene_two_speakers(s):
    await s.sleep(0.5)
    await s.talk([Talker(0, FRONT, "So the demo starts with the caption in front of you.", "neutral"),
                  Talker(1, REAR, "Wait, can someone help me with these boxes?", "questioning", start=1.2,
                         energy=0.55, conf=0.7)])
    await s.sleep(2.0)


async def scene_crowd_noise(s):
    rng = random.Random(7)

    def crowd():                     # babble on both beams: loud, speech flickering, low confidence
        return [(sid, b, rng.random() < 0.35, rng.uniform(0.55, 0.8), rng.uniform(0.1, 0.3))
                for sid, b in ((0, FRONT), (1, REAR))]

    for _ in range(int(3.0 / TICK)):
        s.frame(crowd())
        await s.sleep(TICK)
    await s.talk([Talker(0, FRONT, "Can you hear me over all this noise?", "questioning", energy=0.8)],
                 background=crowd)
    for _ in range(int(2.0 / TICK)):
        s.frame(crowd())
        await s.sleep(TICK)
    await s.sleep(1.0)


# Scenes whose fixtures/events file was cut from a real run (--from-log), so --build-scenes leaves them alone.
RECORDED: set[str] = set()

SCENES = {"front_speech": scene_front_speech, "rear_speech": scene_rear_speech,
          "doorbell_rear": scene_doorbell_rear, "name_called_rear": scene_name_called_rear,
          "unknown_sound": scene_unknown_sound, "two_speakers": scene_two_speakers,
          "crowd_noise": scene_crowd_noise}


def build_scene(name):
    """Run a scripted scene on the virtual clock: its messages as JSONL-ready dicts (t from 0)."""
    sink = Collector()
    s = Script(sink, clock=VirtualClock(), jitter=0.0, beams={0: FRONT, 1: REAR})
    s.idle()
    asyncio.run(_build(s, SCENES[name]))
    return [m.to_dict() for m in sink.messages]


async def _build(s, fn):
    await fn(s)
    await s.drain()


def write_scene(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for d in rows:
            f.write(json.dumps(d, ensure_ascii=False, separators=(",", ":")) + "\n")


def build_scenes(names=None):
    for name in names or [n for n in SCENES if n not in RECORDED]:
        rows = build_scene(name)
        write_scene(SCENE_DIR / f"{name}.jsonl", rows)
        print(f"[fake_events] {name}: {len(rows)} messages, {rows[-1]['t']:.1f} s")


def load_scene(name):
    path = Path(name) if Path(name).suffix == ".jsonl" else SCENE_DIR / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def scene_names():
    return sorted(p.stem for p in SCENE_DIR.glob("*.jsonl"))


def rebase(d, base, speed=1.0, tag=""):
    """A scene line -> a message for now: times onto the laptop clock, ids made unique with `tag`."""
    d = json.loads(json.dumps(d))
    for k in TIME_FIELDS:
        if k in d:
            d[k] = round(base + d[k] / speed, 3)
    if tag:
        for k in ("utt_id", "event_id", "alert_id"):
            if k in d:
                d[k] = f"{d[k]}-{tag}"
        ref = d.get("ref") or {}
        for k in ("utt_id", "event_id"):
            if k in ref:
                ref[k] = f"{ref[k]}-{tag}"
    d.pop("seq", None)
    d.pop("session", None)
    return hc.parse(d)


async def play_scene(client, rows, speed=1.0, tag=""):
    """Send a scene's messages at their recorded times (relative to now)."""
    base = time.time() + 0.05
    for d in rows:
        delay = base + d["t"] / speed - time.time()
        if delay > 0:
            await asyncio.sleep(delay)
        client.send(rebase(d, base, speed, tag))


def from_log(log, start, end, out=None):
    """Cut [start, end] (laptop clock) of a laptop JSON-lines log into a scene: laptop messages only,
    times relative to `start`, no seq / session / _sent."""
    rows = []
    for line in Path(log).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if d.get("type") not in LAPTOP_TYPES or d.get("src") != "laptop" or not start <= d["t"] <= end:
            continue
        for k in ("seq", "session", "_sent"):
            d.pop(k, None)
        for k in TIME_FIELDS:
            if k in d:
                d[k] = round(d[k] - start, 3)
        rows.append(d)
    rows.sort(key=lambda d: d["t"])
    if out:
        write_scene(Path(out), rows)
        print(f"[fake_events] wrote {out}: {len(rows)} messages")
    return rows


async def main(args):
    from laptop.client.fanout import FanoutClient
    from laptop.client.relay import RelayClient
    cfg = settings.load()
    r = cfg["relay"]
    url = args.relay or r["url"]
    mirrors = [u for u in dict.fromkeys([*r["mirrors"], *args.mirror]) if u != url]
    link = (lambda u, s, t, **kw: FanoutClient([u, *mirrors], s, t, **kw)) if mirrors else RelayClient
    client = link(url, args.session or r["session"], r["token"], role="tool", subscribe=[])
    runner = asyncio.create_task(client.run())
    while not client.connected:
        await asyncio.sleep(0.05)
    try:
        if args.scene:
            names = list(SCENES) if args.scene == "all" else [args.scene]
            run = f"{int(time.time()) % 100000}"
            for n in itertools.count():
                for k, name in enumerate(names):
                    print(f"[fake_events] scene {name}")
                    await play_scene(client, load_scene(name), args.speed, tag=f"{run}-{n}-{k}")
                    await asyncio.sleep(1.0 / args.speed)
                if not args.loop:
                    break
        else:
            script = Script(client, args.speed)
            moments = [args.moment] if args.moment else [1, 2, 3]
            while True:
                await script.play(moments)
                if not args.loop:
                    break
        await asyncio.sleep(0.5)                                  # let the writer flush
    finally:
        runner.cancel()
    print(f"[fake_events] done ({client.sent} messages sent)")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--relay", help="relay base URL (default: laptop/config.yaml or HERE_RELAY_URL)")
    p.add_argument("--mirror", action="append", default=[], metavar="URL",
                   help="also publish to this relay (repeatable; like python -m laptop --mirror)")
    p.add_argument("--session")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--loop", action="store_true")
    p.add_argument("--once", action="store_true", help="play once and exit (default)")
    p.add_argument("--moment", type=int, choices=[1, 2, 3])
    p.add_argument("--scene", help="a fixtures/events scene name (or a .jsonl path), or 'all'")
    p.add_argument("--list", action="store_true", help="list the scenes")
    p.add_argument("--build-scenes", nargs="*", metavar="NAME", help="rewrite scripted scenes (default: all)")
    p.add_argument("--from-log", metavar="LOG", help="cut a laptop log into a scene (with --start/--end/--out)")
    p.add_argument("--start", type=float)
    p.add_argument("--end", type=float)
    p.add_argument("--out")
    a = p.parse_args()
    if a.list:
        print("\n".join(scene_names()))
    elif a.build_scenes is not None:
        build_scenes(a.build_scenes or None)
    elif a.from_log:
        if a.start is None or a.end is None or not a.out:
            p.error("--from-log needs --start, --end and --out")
        from_log(a.from_log, a.start, a.end, a.out)
    else:
        try:
            asyncio.run(main(a))
        except KeyboardInterrupt:
            pass

"""Operator view in the terminal: python -m laptop --real --dashboard (optional; any mode works).

One screen with what the hearing module is doing right now: a compass with the array's direction (and the
wearer's mark and alert directions), the input level, VAD / utterance state, the current caption, active
alerts and sound events, and the relay link with heartbeat problems. It only reads: it taps the messages
the laptop sends and polls the app's parts; it never changes what goes to the phone.

Uses `rich` when installed (`pip install rich`), otherwise prints a one-line status twice a second.
"""

import asyncio
import math
import time
from collections import deque

ROWS, COLS = 9, 19                   # compass grid (characters are ~2x taller than wide)
TONE_EMOJI = {"neutral": "", "excited": "!", "laughing": "haha", "shouting": "LOUD", "whispering": "soft",
              "questioning": "?", "sad": "sad"}


def compass(bearing=None, wearer=None, alerts=(), speech=False, others=()):
    """Top-down ring, world frame: front (0) up, clockwise. Returns a list of text lines."""
    grid = [[" "] * COLS for _ in range(ROWS)]
    cy, cx = ROWS // 2, COLS // 2
    ry, rx = ROWS // 2, COLS // 2

    def put(deg, ch, scale=1.0):
        a = math.radians(deg)
        y = int(round(cy - math.cos(a) * ry * scale))
        x = int(round(cx + math.sin(a) * rx * scale))
        if 0 <= y < ROWS and 0 <= x < COLS:
            grid[y][x] = ch

    for d in range(0, 360, 15):
        put(d, ".")
    for d, ch in ((0, "F"), (90, "R"), (180, "B"), (-90, "L")):
        put(d, ch)
    grid[cy][cx] = "+"
    if wearer is not None:
        put(wearer, "w", 0.45)
    for a in alerts:
        if a is not None:
            put(a, "!", 0.75)
    for b, ch in others:                 # beams mode: the second beam's talker / sound
        put(b, ch, 0.75)
    if bearing is not None:
        put(bearing, "@" if speech else "o", 0.75)
    return ["".join(r) for r in grid]


def bar(value, width=20):
    n = int(round(max(0.0, min(1.0, value)) * width))
    return "#" * n + "-" * (width - n)


class Dashboard:
    def __init__(self, app, client, wearer_bearing=None):
        self.app, self.client = app, client
        self.wearer = wearer_bearing
        self.source = None                 # newest sources[0]
        self.others = []                   # sources[1:] (the second beam in beams mode)
        self.source_t = 0.0
        self.captions = deque(maxlen=4)    # (utt_id, Utterance) newest last
        self.started = {}                  # utt_id -> SpeechStart (typing dots)
        self.alerts = {}                   # alert_id -> Alert (active ones)
        self.sounds = deque(maxlen=3)
        self.heartbeat = None
        self.counts = {"captions": 0, "retracted": 0, "alerts": 0}

    # ---- input: every message the laptop sends --------------------------------------------

    def tap(self, msg):
        kind = msg.TYPE
        if kind == "sources" and msg.sources:
            self.source, self.source_t = msg.sources[0], time.time()
            self.others = msg.sources[1:]
        elif kind == "speech_start":
            self.started[msg.utt_id] = msg
        elif kind == "utterance":
            self.started.pop(msg.utt_id, None)
            for i, (uid, _) in enumerate(self.captions):
                if uid == msg.utt_id:
                    del self.captions[i]
                    break
            if msg.text or msg.pending:
                self.captions.append((msg.utt_id, msg))
            if msg.final:
                self.counts["captions" if msg.text else "retracted"] += 1
        elif kind == "alert":
            if msg.active:
                if msg.alert_id not in self.alerts:
                    self.counts["alerts"] += 1
                self.alerts[msg.alert_id] = msg
            else:
                self.alerts.pop(msg.alert_id, None)
        elif kind == "sound_event":
            self.sounds.append(msg)
        elif kind == "heartbeat":
            self.heartbeat = msg

    def wrap(self, client):
        """Make client.send also feed the dashboard."""
        send = client.send

        def tapped(msg):
            ok = send(msg)
            try:
                self.tap(msg)
            except Exception:  # noqa: BLE001 - the display must never break sending
                pass
            return ok
        client.send = tapped
        return client

    # ---- state polled from the app ---------------------------------------------------------

    def level_db(self):
        return getattr(self.app.audio, "level_db", None)

    def vad_state(self):
        sp = self.app.speech
        if sp is None:
            return "speech off"
        seg = sp.seg
        if seg is None:
            return "listening"
        who = "wearer?" if not seg.started else "talker"
        return f"utterance {seg.utt_id[-4:]} {seg.duration:4.1f} s ({who}, array {seg.doa_speech_s:.1f} s)"

    def lines(self):
        """Plain-text rendering (the rich view uses the same pieces)."""
        s = self.source
        stale = s is None or time.time() - self.source_t > 3.0
        bearing = None if stale else s.bearing_deg
        speech = bool(s and s.speech and s.confidence >= 0.3 and not stale)
        others = [] if stale else [(o.bearing_deg, "#" if o.speech else ":") for o in self.others]
        ring = compass(bearing, self.wearer, [a.bearing_deg for a in self.alerts.values()], speech, others)
        lvl = self.level_db()
        info = [
            f"direction  {'-' if bearing is None else f'{bearing:+6.1f} deg'}  "
            f"{'SPEECH' if speech else '      '} conf {0 if s is None else s.confidence:.2f}"
            + "".join(f"   | beam {o.id + 1} {o.bearing_deg:+6.1f} deg {'SPEECH' if o.speech else ''}"
                      for o in ([] if stale else self.others)),
            (f"input      {lvl:6.1f} dBFS [{bar((lvl + 70) / 60)}]" if lvl is not None else
             f"input      energy {0 if s is None else s.energy:.2f} [{bar(0 if s is None else s.energy)}]"),
            f"speech     {self.vad_state()}",
            f"wearer     {'not calibrated' if self.wearer is None else f'{self.wearer:+.0f} deg (ignored)'}",
            "",
        ]
        for uid in list(self.started)[-1:]:
            info.append(f"... typing  from {self.started[uid].bearing_deg:+.0f} deg")
        for _, u in list(self.captions)[-2:]:
            tone = TONE_EMOJI.get(u.tone or "", "") if u.tone else ""
            info.append(f"{'>' if u.final else '~'} {u.bearing_deg:+4.0f} deg [{u.tone or '-'}{' ' + tone if tone else ''}] "
                        f"{u.text} {('(' + u.pending + ')') if u.pending else ''}"[:100])
        info.append("")
        for a in self.alerts.values():
            where = "?" if a.bearing_deg is None else f"{a.bearing_deg:+.0f} deg"
            info.append(f"ALERT {a.label} at {where} ({a.confidence:.2f})")
        for e in list(self.sounds)[-2:]:
            info.append(f"sound {e.text} x{e.count} at {'?' if e.bearing_deg is None else f'{e.bearing_deg:+.0f}'} deg")
        hb = self.heartbeat
        link = getattr(self.client, "connected", False)
        sent = getattr(self.client, "sent", 0)
        dropped = getattr(self.client, "dropped", 0)
        status = "-" if hb is None else f"{hb.status} ({hb.engine}, doa {hb.doa_source})"
        info.append(f"relay      {'connected' if link else 'DOWN'}  sent {sent} dropped {dropped}  heartbeat {status}")
        if hb is not None and hb.detail:
            info.append(f"           {hb.detail[:90]}")
        info.append(f"session    captions {self.counts['captions']}  retracted {self.counts['retracted']}  "
                    f"alerts {self.counts['alerts']}")
        info += self.pipeline_lines()
        return ring, info

    def pipeline_lines(self):
        """Stage latency p50/p95 (ms), dropped / suppressed counts, queue depth, onsets (K1 D)."""
        from .events.stats import STATS
        out = [f"latency    {STATS.line()}  (ms p50/p95)"]
        app = self.app
        th = getattr(app, "throttle", None)
        audio_q, out_q = app.queue_depth() if hasattr(app, "queue_depth") else (0, 0)
        dropped = getattr(self.client, "dropped", 0)
        audio_dropped = app.audio_dropped() if hasattr(app, "audio_dropped") else 0
        out.append(f"pipeline   dropped {dropped} (audio {audio_dropped})  sources suppressed "
                   f"{0 if th is None else th.suppressed}/{0 if th is None else th.sent + th.suppressed}  "
                   f"queue audio {audio_q} relay {out_q} (max {getattr(self.client, 'max_queue', 0)})")
        on = getattr(app, "onsets", None)
        tr = getattr(app, "tracker", None)
        if on is not None or tr is not None:
            last = on.recent[-1] if on is not None and on.recent else None
            out.append(f"events     onsets {0 if on is None else on.count}"
                       + ("" if last is None else f" (last: id {last.source_id} at "
                          f"{'?' if last.bearing_deg is None else f'{last.bearing_deg:+.0f}'} deg, +{last.rise_db:.0f} dB)")
                       + ("" if tr is None else f"  tracks {[s.id for s in tr.live(tr.last_t)]}"
                          f"{'  OVERLAP' if tr.overlap else ''}"))
        return out

    # ---- output ------------------------------------------------------------------------------

    def renderable(self):
        from rich.columns import Columns
        from rich.panel import Panel
        from rich.text import Text

        ring, info = self.lines()
        ring_text = Text("\n".join(ring), style="bold cyan")
        body = Text()
        for line in info:
            style = "bold red" if line.startswith("ALERT") else ("bold" if line.startswith(">") else "")
            if line.startswith("~"):
                style = "dim"
            if "DOWN" in line:
                style = "bold yellow"
            body.append(line + "\n", style=style)
        return Panel(Columns([Panel(ring_text, title="world (F = array front)"), body]),
                     title="HERE hearing module", subtitle="Ctrl+C to stop")

    async def run(self):
        try:
            from rich.live import Live
        except ImportError:
            await self.run_plain()
            return
        with Live(self.renderable(), refresh_per_second=8, redirect_stdout=True, redirect_stderr=True) as live:
            while True:
                await asyncio.sleep(0.125)
                live.update(self.renderable())

    async def run_plain(self):
        while True:
            await asyncio.sleep(0.5)
            _, info = self.lines()
            print("\r" + " | ".join(x for x in info[:3] if x)[:150], end="", flush=True)


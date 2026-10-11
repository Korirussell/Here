"""Sound classification: audio in, `sound_event` (+ `alert`) out (SPEC §10, §12.7, §15).

Two triggers, both cheap to skip (SPEC §10, docs/INTEGRATION.md §5.1, §6):
- the sliding window: the newest WINDOW_S every HOP_S (drop, don't queue);
- the laptop's onset candidates (`app.onsets`): the window starting just before the onset, from a bounded queue that
  drops the oldest.

The model is `sounds.model` (a Hugging Face AudioSet tagger, chosen by tools/sound_bench.py) with PANNs CNN14 as
the fallback, at load and if it fails mid-session (models.py). Scores map onto contract SoundLabels; repeats merge
(events.py). Labels in `sounds.publish` become `sound_event`s with a bearing; alerting labels also raise an `alert`.

`unknown` (docs/INTEGRATION.md §4): an onset with real energy that lasts, where no label (published or not) reaches
its threshold, becomes `unknown` with the onset's bearing. It is never forced into a known label. If a known label
crosses its threshold within `unknown.upgrade_s`, the same `event_id` is re-sent with that label. Steady ambience
(the same stream firing again and again) merges into the open unknown instead of spamming new ones.

`source_id` (the `sources` track the sound came from): the onset's stream when an onset triggered the window,
this pipeline's beam when there are several streams (fixed beams: 0 front, 1 rear), kept for later updates of the
same event and across an unknown -> label upgrade; omitted when nothing says which stream it was.

Offline check of a recording (prints what it would detect):

    python -m laptop.sounds.classifier --wav doorbell.wav [--model panns_cnn14] [--threshold 0.2]
"""

import argparse
import asyncio
import time
from collections import deque

import numpy as np

from contracts.python.here_contract import SoundEvent

from . import config as C
from .enrolled import EnrolledMatcher
from .events import Events
from .labels import ALERTING_LABELS, OVERRULING_LABELS, SOUND_DISPLAY
from .models import PANNS_ID, ensure_panns_files, load_backend  # noqa: F401  (ensure_*: tools import it from here)

ensure_model_files = ensure_panns_files


class Classifier:
    """The configured model with PANNs as the fallback. `classify(pcm16k) -> (scores, genre)`.

    `model_id` names whoever answered (for `sound_event.model`); `thresholds` / `default_threshold` / `alert_min`
    are that model's (score scales differ between models); `problem` is set when we fell back."""

    def __init__(self, model=None, device=None, fallback=None):
        self.wanted = model or getattr(C, "MODEL", PANNS_ID)
        self.fallback_id = fallback or getattr(C, "FALLBACK", PANNS_ID)
        self.device_pref = device
        self.problem = None
        try:
            self.backend = load_backend(self.wanted, device)
        except Exception as e:  # noqa: BLE001  (no network for the first download, CUDA trouble, a bad revision)
            if self.wanted == self.fallback_id:
                raise
            self._fall_back(f"{self.wanted} failed to load ({type(e).__name__}: {e})")
        print(f"[sounds] {self.backend.model_id} on {self.backend.device} "
              f"(load {self.backend.load_s:.1f} s, warm {getattr(self.backend, 'warm_s', 0.0):.2f} s)")

    def _fall_back(self, why):
        print(f"[sounds] {why}; falling back to {self.fallback_id}")
        self.problem = f"sound model fallback: {why}"[:160]
        self.backend = load_backend(self.fallback_id, self.device_pref)

    @property
    def model_id(self):
        return self.backend.model_id

    @property
    def device(self):
        return self.backend.device

    def _spec(self):
        return (getattr(C, "MODELS", {}) or {}).get(self.model_id) or {}

    @property
    def thresholds(self):
        return {**C.THRESHOLDS, **(self._spec().get("thresholds") or {})} if self.model_id != PANNS_ID \
            else C.THRESHOLDS

    @property
    def default_threshold(self):
        return float(self._spec().get("default_threshold", C.DEFAULT_THRESHOLD))

    @property
    def alert_min(self):
        """Per alert kind: this model's minimum score to alert (overrides alerts.min_confidence_by_kind)."""
        return dict(self._spec().get("alert_min") or {})

    def __call__(self, pcm16k):
        try:
            return self.backend(pcm16k)
        except Exception as e:  # noqa: BLE001
            if self.model_id == self.fallback_id:
                raise
            self._fall_back(f"{self.model_id} failed mid-session ({type(e).__name__}: {e})")
            return self.backend(pcm16k)


class SoundPipeline:
    """classify: (int16 window) -> (scores, genre). locate: optional (t0, t1, label, key) -> (bearing, conf)|None."""

    def __init__(self, classify, history, emit, alerts, locate=None, publish=None, verbose=False, clock=time.time):
        self.classify = classify
        self.history = history
        self.emit = emit
        self.alerts = alerts
        self.locate = locate
        self.publish = set(C.PUBLISH if publish is None else publish)
        self.verbose = verbose
        self.clock = clock
        self.on_scores = None            # (w0, w1, scores) -> None: every window's scores (speech tone uses laughter)
        self.stream = 0                  # audio stream (beam) this pipeline classifies
        self.arbiter = None              # shared {label: {stream: (w1, score)}} when there are several streams
        self.events = Events(thresholds=getattr(classify, "thresholds", None),
                             default=getattr(classify, "default_threshold", None))
        from laptop.settings import ROOT
        self.enrolled = EnrolledMatcher(getattr(C, "ENROLLED", None), ROOT)
        self.chunks = deque()            # (t of first sample, int16 pcm), the last BUFFER_S
        self.since_last = 0              # samples received since the last classified window
        self.ready = asyncio.Event()
        u = dict(getattr(C, "UNKNOWN", None) or {})
        self.unknown_cfg = {"enabled": True, "min_energy": 0.25, "min_rise_db": 10.0, "min_active_s": 0.15,
                            "upgrade_s": 3.0, "cooldown_s": 6.0, "ambient_n": 3, "ambient_window_s": 30.0, **u}
        self.onsets = deque(maxlen=int(getattr(C, "ONSET_QUEUE", 4)))   # drop the oldest
        self.onset_ready = asyncio.Event()
        self.early_q = deque(maxlen=8)         # this stream's onsets (speech-flagged too) for the early check
        self.early_done = deque(maxlen=16)     # onset times whose early enrolled-head check ran
        self.open_unknown = None         # the unknown event a known label may still upgrade
        self.unknown_times = deque(maxlen=16)
        self.stats = {"onsets": 0, "onsets_dropped": 0, "unknown": 0, "unknown_merged": 0, "upgraded": 0}

    # ---- audio in -----------------------------------------------------------------------------

    async def run(self, queue):
        await asyncio.gather(self._collect(queue), self._worker(), self._onset_worker())

    async def _collect(self, queue):
        while True:
            t, pcm = await queue.get()
            self.add(t, pcm)

    def add(self, t, pcm):
        n_win = int(C.WINDOW_S * C.SAMPLE_RATE)
        keep = int(max(C.WINDOW_S, float(getattr(C, "BUFFER_S", 3.0))) * C.SAMPLE_RATE)
        if self.chunks:                  # a gap (fixture loop, dropped blocks): start over
            lt, lp = self.chunks[-1]
            if abs(t - (lt + len(lp) / C.SAMPLE_RATE)) > 0.25:
                self.chunks.clear()
        self.chunks.append((t, pcm))
        if self.enrolled:
            self.enrolled.add(pcm)
        total = sum(len(p) for _, p in self.chunks)
        while self.chunks and total - len(self.chunks[0][1]) >= keep:
            total -= len(self.chunks.popleft()[1])
        self.since_last += len(pcm)
        if self.since_last >= C.HOP_S * C.SAMPLE_RATE and total >= n_win:
            self.since_last = 0
            self.ready.set()
        if self.onsets and self.buffered_until() >= self.onsets[0].t + self.onset_span()[1]:
            self.onset_ready.set()
        after = float(getattr(C, "EARLY_AFTER_S", 0.0) or 0.0)
        if after and self.enrolled and any(c.t not in self.early_done and self.buffered_until() >= c.t + after
                                           for c in self.early_q):
            self.onset_ready.set()

    def buffered_until(self):
        if not self.chunks:
            return -1e9
        t_last, last = self.chunks[-1]
        return t_last + len(last) / C.SAMPLE_RATE

    def window(self):
        """The last WINDOW_S of audio and the capture time of its first sample."""
        n = int(C.WINDOW_S * C.SAMPLE_RATE)
        pcm = np.concatenate([p for _, p in self.chunks])[-n:]
        return pcm, self.buffered_until() - n / C.SAMPLE_RATE

    def window_at(self, t0):
        """WINDOW_S of buffered audio starting at t0 (clamped into the buffer), and its actual start time."""
        n = int(C.WINDOW_S * C.SAMPLE_RATE)
        pcm = np.concatenate([p for _, p in self.chunks])
        start_t = self.chunks[0][0]
        i = int(round((t0 - start_t) * C.SAMPLE_RATE))
        i = max(0, min(i, len(pcm) - n))
        return pcm[i:i + n], start_t + i / C.SAMPLE_RATE

    # ---- onset candidates (docs/INTEGRATION.md §5.1) ---------------------------------------

    def onset_span(self):
        pre = float(getattr(C, "ONSET_PRE_S", 0.1))
        return pre, C.WINDOW_S - pre

    def on_onset(self, cand):
        """Called on the event loop for every candidate on every stream; keeps ours, newest first out if full."""
        if getattr(cand, "source_id", self.stream) == self.stream and self.enrolled:
            self.early_q.append(cand)    # the early enrolled check sees speech-flagged onsets too: the firmware
                                         # flags a chime as speech (fixtures/array_tts), the head template doesn't
        if getattr(cand, "source_id", self.stream) != self.stream or getattr(cand, "speech", False):
            return                       # another beam's, or a talker (the speech pipeline owns it)
        if len(self.onsets) == self.onsets.maxlen:
            self.stats["onsets_dropped"] += 1
            print(f"[sounds] onset queue full: dropped the one at {self.onsets[0].t:.2f}", flush=True)
        self.onsets.append(cand)
        self.stats["onsets"] += 1
        if self.buffered_until() >= cand.t + self.onset_span()[1]:
            self.onset_ready.set()

    async def _onset_worker(self):
        loop = asyncio.get_running_loop()
        while True:
            await self.onset_ready.wait()
            self.onset_ready.clear()
            await self.early_check(loop)
            while self.onsets and self.buffered_until() >= self.onsets[0].t + self.onset_span()[1]:
                cand = self.onsets.popleft()
                pcm, w0 = self.window_at(cand.t - self.onset_span()[0])
                if len(pcm) < int(C.WINDOW_S * C.SAMPLE_RATE):
                    continue             # the buffer was reset (a gap) under it
                scores, genre = await loop.run_in_executor(None, self.classify, pcm)
                await self.handle_window(w0, w0 + len(pcm) / C.SAMPLE_RATE, scores, genre, onset=(cand, pcm))

    async def early_check(self, loop):
        """An enrolled sound's head matching `early_after_s` after an onset: publish it now (~0.5 s), before the
        full window (~1 s). The full-window pass then merges into the same event (same event / alert id)."""
        after = float(getattr(C, "EARLY_AFTER_S", 0.0) or 0.0)
        if not after or not self.enrolled:
            return
        for cand in [c for c in self.early_q if c.t not in self.early_done]:
            if self.buffered_until() < cand.t + after:
                continue
            self.early_done.append(cand.t)
            # >= 1 s back: the matcher whitens against the window's median, so it needs room before the onset
            hits = await loop.run_in_executor(None, self.enrolled.head_scores, max(1.0, after + 0.6))
            hits = {k: v for k, v in hits.items() if k in self.publish}
            if hits:
                w1 = self.buffered_until()
                await self.handle_window(cand.t - 0.1, w1, dict(hits), None, onset=(cand, None), early=True)

    # ---- classification ---------------------------------------------------------------------------

    async def _worker(self):
        loop = asyncio.get_running_loop()
        while True:
            await self.ready.wait()            # only the newest window: drop, don't queue
            self.ready.clear()
            pcm, w0 = self.window()
            w1 = w0 + len(pcm) / C.SAMPLE_RATE
            scores, genre = await loop.run_in_executor(None, self.classify, pcm)
            await self.handle_window(w0, w1, scores, genre)

    async def handle_window(self, w0, w1, scores, genre, onset=None, early=False):
        loop = asyncio.get_running_loop()
        if self.on_scores is not None and onset is None:
            # What the classifier heard in THIS window: the enrolled matcher below scans the last ~5 s, so its
            # doorbell hit lingers on windows after the bell (it once marked the next sentence as doorbell).
            self.on_scores(w0, w1, dict(scores))
        if self.enrolled and not early:   # "teach HERE your doorbell": template match, robust in babble
            # ...unless the classifier clearly hears another alert sound now: a real baby cry matched the doorbell
            # template just above threshold through the room (real two-source test).
            other = max((s for k, (s, _) in scores.items() if k in OVERRULING_LABELS), default=0.0)
            for label, hit in (await loop.run_in_executor(None, self.enrolled.scores)).items():
                if other >= 0.3 and other > scores.get(label, (0.0, ""))[0] + 0.2:
                    continue
                if hit[0] > scores.get(label, (0.0, ""))[0]:
                    scores[label] = hit
        if self.verbose:
            print(f"[sounds] {w1:.1f}{' onset' if onset else ''} " + top_scores(scores), flush=True)
        if self.arbiter is not None:
            # Onset and early windows count too (array tuning proposal b2): before, only regular windows were recorded, so a
            # beam's onset-path doorbell was compared with the other beam's regular windows and never published
            # (real array, two beams: "doorbell (recognised, not published)", alert 2.3 s later instead of ~0.5 s).
            for label in self.publish:
                hist = self.arbiter.setdefault(label, {}).setdefault(self.stream, deque(maxlen=8))
                hist.append((w1, scores.get(label, (0.0, ""))[0]))
        thresholds = getattr(self.classify, "thresholds", None)
        if thresholds is not None:       # the model may have fallen back to PANNs mid-session: its scale
            self.events.thresholds = thresholds
            self.events.default = getattr(self.classify, "default_threshold", self.events.default)
        found = self.events.update(w0, w1, scores, genre)
        sid = getattr(onset[0], "source_id", None) if onset is not None else \
            self.stream if self.arbiter is not None else None
        for ev in found:                 # the event keeps the first stream that told us (sliding windows don't)
            opened = self.events.open.get(ev["label"])
            if opened is not None and opened.get("source_id") is None and sid is not None:
                opened["source_id"] = sid
            ev["source_id"] = opened.get("source_id") if opened is not None else sid
        published = False
        for ev in found:
            leak = scores.get(ev["label"], (0.0, ""))[0] < scores.get("speech", (0.0, ""))[0]
            if self.arbiter is not None and ev["label"] in ALERTING_LABELS and leak:
                continue     # beams: this beam is on a talker; the sound here is a leak (real test: a cry
                             # leaking into the talker's beam raised one baby alert from the front)
            if ev["label"] in self.publish and await self.loudest_here(ev["label"], w1):
                await self.publish_event(ev)
                published = True
        if early:
            return
        if onset is not None and not found:
            await self.maybe_unknown(onset[0], onset[1], w0, w1, scores)
        elif onset is not None and not published:
            print(f"[sounds] onset at {onset[0].t:.2f}: {found[0]['label']} (recognised, not published)", flush=True)

    # ---- unknown --------------------------------------------------------------------------------

    def active_s(self, pcm):
        """How long the window stays within 10 dB of its loudest 32 ms block: a click is short, a sound lasts."""
        n = 512
        blocks = pcm[: len(pcm) // n * n].astype(np.float32).reshape(-1, n)
        if not len(blocks):
            return 0.0
        db = 10 * np.log10(np.mean(blocks ** 2, axis=1) + 1e-9)
        return float(np.sum(db >= db.max() - 10.0) * n / C.SAMPLE_RATE)

    async def maybe_unknown(self, cand, pcm, w0, w1, scores):
        u = self.unknown_cfg
        if not u["enabled"]:
            return
        now = cand.t
        energy, rise = float(getattr(cand, "energy", 0.0)), float(getattr(cand, "rise_db", u["min_rise_db"]))
        active = self.active_s(pcm)
        if energy < u["min_energy"] or rise < u["min_rise_db"] or active < u["min_active_s"]:
            print(f"[sounds] onset at {now:.2f}: too faint/short for unknown (energy {energy:.2f}, rise {rise:.0f} dB,"
                  f" {active:.2f} s)", flush=True)
            return
        # Steady ambience: the same stream firing again and again. Merge into the open unknown, don't spam.
        recent = [t for t in self.unknown_times if now - t <= u["ambient_window_s"]]
        self.unknown_times.append(now)
        o = self.open_unknown
        if (o is not None and now - o["t_last"] <= u["cooldown_s"]) or len(recent) >= u["ambient_n"]:
            if o is not None:
                o["count"] += 1
                o["t_last"] = now
            self.stats["unknown_merged"] += 1
            print(f"[sounds] onset at {now:.2f}: unknown merged (cooldown / ambience, {len(recent)} in "
                  f"{u['ambient_window_s']:.0f} s)", flush=True)
            return
        best = max((s for s, _ in scores.values()), default=0.0)
        bearing = getattr(cand, "bearing_deg", None)
        if bearing is None:
            found = self.history.any_bearing(w0, w1)
            if found is not None:
                bearing = found[0]
        event_id = f"{self.events.prefix}-u{next(self.events.ids)}"
        sid = getattr(cand, "source_id", None)
        self.open_unknown = {"event_id": event_id, "t": now, "t_last": now, "count": 1, "bearing": bearing,
                             "t_start": w0, "source_id": sid}
        self.stats["unknown"] += 1
        # confidence: how sure we are that something real happened (rise over the floor), not a label probability
        conf = round(min(1.0, max(energy, rise / 30.0)), 3)
        category, emoji, text = SOUND_DISPLAY["unknown"]
        self.emit(SoundEvent(src="laptop", t=w1, event_id=event_id, label="unknown", category=category, emoji=emoji,
                             text=text, confidence=conf, bearing_deg=bearing, t_start=round(w0, 3),
                             t_end=round(w1, 3), count=1, source_id=sid,
                             model=self.model_id()))
        print(f"[sounds] unknown at {'?' if bearing is None else f'{bearing:+.0f}'} deg (energy {energy:.2f}, "
              f"rise {rise:.0f} dB, best label score {best:.2f})", flush=True)

    def adopt_unknown(self, ev):
        """A known label's first detection right after an unknown: upgrade that event (same event_id)."""
        o = self.open_unknown
        if o is None or ev["count"] != 1 or ev["t_start"] - o["t_last"] > self.unknown_cfg["upgrade_s"]:
            return ev
        self.open_unknown = None
        self.stats["upgraded"] += 1
        opened = self.events.open.get(ev["label"])
        sid = ev.get("source_id") if ev.get("source_id") is not None else o.get("source_id")
        if opened is not None:
            opened["event_id"] = o["event_id"]       # later updates of this sound keep the id (and the stream)
            opened["source_id"] = sid
        print(f"[sounds] unknown {o['event_id']} upgraded to {ev['label']}", flush=True)
        return {**ev, "event_id": o["event_id"], "t_start": min(ev["t_start"], o["t_start"]), "bearing": o["bearing"],
                "source_id": sid}

    def model_id(self):
        return getattr(self.classify, "model_id", PANNS_ID)

    # ---- publishing -----------------------------------------------------------------------------

    async def loudest_here(self, label, w1):
        """Several beams: publish a sound only from the beam that scores it highest for this window, so it's
        reported once and with that beam's direction (the other beam hears it leak in, weaker)."""
        if self.arbiter is None:
            return True
        await asyncio.sleep(0.15)        # the other beam's worker classifies the same window about now
        recent = lambda hist: max((s for t, s in hist if abs(t - w1) <= 1.5), default=0.0)  # noqa: E731
        streams = self.arbiter.get(label, {})
        mine = recent(streams.get(self.stream, ()))
        return all(recent(hist) <= mine for stream, hist in streams.items() if stream != self.stream)

    async def publish_event(self, ev):
        ev = self.adopt_unknown(ev)
        label = ev["label"]
        bearing = conf_dir = None
        if self.locate is not None:
            found = await asyncio.to_thread(self.locate, ev["t_start"], ev["t_end"], label, ev["event_id"])
            if found is not None:
                bearing, conf_dir = found
        if bearing is None:
            found = self.history.any_bearing(ev["t_start"], ev["t_end"])
            if found is not None:
                bearing, conf_dir = found
        if bearing is None:
            bearing = ev.get("bearing")            # an upgraded unknown keeps the onset's direction
        category, emoji, text = SOUND_DISPLAY.get(label, SOUND_DISPLAY["other"])
        self.emit(SoundEvent(src="laptop", t=ev["t_end"], event_id=ev["event_id"], label=label, category=category,
                             emoji=emoji, text=text, confidence=round(min(1.0, ev["confidence"]), 3),
                             bearing_deg=bearing, t_start=round(ev["t_start"], 3), t_end=round(ev["t_end"], 3),
                             count=ev["count"], source_id=ev.get("source_id"), model=self.model_id() if not str(ev["raw_label"]).startswith("enrolled") else
                             f"{self.model_id()}+enrolled"))
        print(f"[sounds] {label} x{ev['count']} ({ev['raw_label']}, {ev['confidence']:.2f}) "
              f"at {'?' if bearing is None else f'{bearing:+.0f}'} deg", flush=True)
        if label in ALERTING_LABELS and self.alerts is not None:
            self.alerts.raise_alert(label, bearing, ev["confidence"], event_id=ev["event_id"], t=ev["t_end"],
                                    min_confidence=getattr(self.classify, "alert_min", {}).get(label))


def top_scores(scores, n=3):
    return "  ".join(f"{k} {s:.2f}" for k, (s, _) in sorted(scores.items(), key=lambda kv: -kv[1][0])[:n])


def classify_wav(path, model=None):
    import soundfile as sf
    from scipy.signal import resample_poly

    audio, sr = sf.read(path, dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    if sr != C.SAMPLE_RATE:
        audio = resample_poly(audio, C.SAMPLE_RATE, sr)
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    classify = Classifier(model)
    events = Events(thresholds=classify.thresholds, default=classify.default_threshold)
    n, hop = int(C.WINDOW_S * C.SAMPLE_RATE), int(C.HOP_S * C.SAMPLE_RATE)
    for start in range(0, max(1, len(pcm) - n + 1), hop):
        w0 = start / C.SAMPLE_RATE
        scores, genre = classify(pcm[start:start + n])
        print(f"{w0:6.1f}s  " + top_scores(scores))
        for ev in events.update(w0, w0 + C.WINDOW_S, scores, genre):
            print(f"        -> sound_event {ev['event_id']} {ev['label']} x{ev['count']} "
                  f"[{ev['t_start']:.1f}-{ev['t_end']:.1f}s] {ev['raw_label']} {ev['confidence']:.2f}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(prog="python -m laptop.sounds.classifier")
    p.add_argument("--wav", required=True, help="classify a recording offline")
    p.add_argument("--model", help=f"model id (config: {getattr(C, 'MODEL', PANNS_ID)})")
    p.add_argument("--threshold", type=float, help=f"default score threshold (config: {C.DEFAULT_THRESHOLD})")
    args = p.parse_args()
    if args.threshold is not None:
        C.DEFAULT_THRESHOLD = args.threshold
    classify_wav(args.wav, args.model)

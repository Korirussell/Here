"""Wires the hearing module together: one input -> speech + sounds + alerts -> one relay connection.

    input (ReSpeaker | fixture | mic + slider)
        on_sources -> DirectionHistory (raw) + SourceTracker -> throttled `sources` to the relay
        on_audio   -> OnsetDetector (per stream) -> onset candidates (app.onsets.subscribe, for the classifier)
                   -> AudioFanout -> SpeechPipeline  (speech_start, utterance, name_called)
                                  -> SoundPipeline   (sound_event, doorbell alert)
                                  -> Recorder        (--record)
    relay client <- config from the phone (user name, aliases, alert switches)

Several audio streams (the ReSpeaker in beams mode, a 2-channel fixture): stream k gets its own
DirectionHistory (only source id k), SpeechPipeline and SoundPipeline, so a second talker or a crying baby
on the other beam is captioned / detected separately. A shared Crosstalk keeps one talker heard on both
beams to one caption, and the sound pipelines' arbiter publishes a sound once, from the beam hearing it best.
"""

import asyncio
import json
import time
import traceback
import wave
from pathlib import Path

from contracts.python.here_contract import Sources

from .alerts import AlertManager
from .audio.base import AudioFanout, DirectionHistory, SourcesThrottle
from .events.level_speech import LevelSpeech
from .events.onsets import OnsetDetector
from .events.stats import STATS
from .events.tracker import SourceTracker
from .settings import ROOT


class Recorder:
    """--record NAME: writes fixtures/NAME/{audio.wav, sources.jsonl, meta.json} (SPEC §20 test sessions),
    plus raw.wav (the 4 raw mics, USB order, int16, x raw_gain) when the input has them (6-channel firmware)."""

    def __init__(self, folder, meta, channels=1, raw_channels=0, raw_gain=1.0):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.channels = channels                       # 2 in beams mode: one WAV channel per beam
        self.wav = wave.open(str(self.folder / "audio.wav"), "wb")
        self.wav.setnchannels(channels)
        self.wav.setsampwidth(2)
        self.wav.setframerate(16000)
        self.sources = open(self.folder / "sources.jsonl", "w", encoding="utf-8")
        self.t0 = None
        self.samples = 0
        self.raw_wav, self.raw_samples, self.raw_gain = None, 0, float(raw_gain)
        if raw_channels:
            self.raw_wav = wave.open(str(self.folder / "raw.wav"), "wb")
            self.raw_wav.setnchannels(raw_channels)
            self.raw_wav.setsampwidth(2)
            self.raw_wav.setframerate(16000)
            meta = {**meta, "raw": {"file": "raw.wav", "channels": raw_channels, "gain": self.raw_gain}}
        self.meta = {**meta, "started": time.time()}
        # Written now and again on close, so a killed process still leaves a usable fixture
        # (the WAV header is patched on every write).
        (self.folder / "meta.json").write_text(json.dumps(self.meta, indent=2), encoding="utf-8")

    def _rel(self, t):
        if self.t0 is None:
            self.t0 = t
        return t - self.t0

    def audio(self, t, pcm):
        rel = self._rel(t)
        gap = int(round(rel * 16000)) - self.samples      # keep audio aligned to the clock across drops
        if gap > 160:
            import numpy as np
            self.wav.writeframes(np.zeros(gap * self.channels, np.int16).tobytes())
            self.samples += gap
        self.wav.writeframes(pcm.astype("<i2").tobytes())
        self.samples += len(pcm)

    def raw(self, t, block):
        """Raw mics, same clock as audio(): sample n of raw.wav and audio.wav is the same instant."""
        if self.raw_wav is None:
            return
        import numpy as np
        rel = self._rel(t)
        gap = int(round(rel * 16000)) - self.raw_samples
        if gap > 160:
            self.raw_wav.writeframes(np.zeros((gap, block.shape[1]), np.int16).tobytes())
            self.raw_samples += gap
        x = block if self.raw_gain == 1.0 else np.clip(block.astype(np.float32) * self.raw_gain, -32768, 32767)
        self.raw_wav.writeframes(x.astype("<i2").tobytes())
        self.raw_samples += len(block)

    def add_sources(self, t, sources):
        if self.t0 is None:
            return                                        # align on the first audio block
        self.sources.write(json.dumps({"t": round(self._rel(t), 3),
                                       "sources": [s.__dict__ for s in sources]}) + "\n")

    def close(self):
        self.wav.close()
        if self.raw_wav is not None:
            self.raw_wav.close()
        self.sources.close()
        self.meta.update(ended=time.time(), seconds=round(self.samples / 16000, 1))
        (self.folder / "meta.json").write_text(json.dumps(self.meta, indent=2), encoding="utf-8")
        print(f"[record] saved {self.folder} ({self.meta['seconds']} s)")


class Laptop:
    def __init__(self, cfg, inputs, client, speech=None, sounds=None, recorder=None, names=()):
        """inputs: (direction_source, audio_source); they may be the same object (ReSpeaker, fixture)."""
        self.cfg = cfg
        self.directions, self.audio = inputs
        self.client = client
        self.recorder = recorder
        self.history = DirectionHistory()
        # The ReSpeaker / ODAS / a fixture with speech flags can gate speech segmentation from the start.
        self.history.reports_speech = bool(getattr(self.directions, "reports_speech", False))
        a = cfg["audio"]
        self.throttle = SourcesThrottle(a["sources_idle_change_deg"], a["sources_keepalive_s"])
        self.tracker = SourceTracker.from_config(a)    # raw readings -> the wire `sources` (stable ids, expiry)
        self.onsets = OnsetDetector.from_config(a)      # per-stream onset candidates (docs/INTEGRATION.md §5.1)
        self.onsets.where = self.onset_where
        self.onsets.subscribe(lambda c: self.tracker.note_onset(c.source_id, c.t))   # non-speech tracks
        self.fanout = AudioFanout()                    # the input as delivered (recorder)
        self.n_streams = int(getattr(self.audio, "streams", 1))
        self.stream_fanouts = [AudioFanout() for _ in range(self.n_streams)] if self.n_streams > 1 else [self.fanout]
        self.stream_histories = [self.history]
        if self.n_streams > 1:
            fixed = bool(getattr(self.directions, "beams_fixed", False))     # each stream = one fixed beam
            self.stream_histories = [DirectionHistory(fixed_beam=fixed,
                                                      fixed_beam_spread_deg=a.get("fixed_beam_spread_deg", 30.0))
                                     for _ in range(self.n_streams)]
            for h in self.stream_histories:
                h.reports_speech = self.history.reports_speech
        # Beams mode: a level-based speech flag next to the firmware's (laptop/events/level_speech.py, off by default)
        self.level_speech = LevelSpeech.from_config(a) if self.n_streams > 1 else None
        self.speeches, self.sounds_list = [], []
        self.alerts = AlertManager(cfg["alerts"], self.client.send)
        self.speech_factory, self.sounds_factory = speech, sounds
        self.speech = self.sounds = None
        self.names = list(names)
        self.started = time.time()
        self.counts = {}
        self.extra_parts = {}          # name -> async start(): more supervised parts (e.g. the --dashboard view)
        self.detail_sources = []       # () -> str | None: notes for heartbeat `detail` that don't degrade (enrichment)

        self.raw_listeners = []        # (t, raw int16 (n, 4)) on the event loop: recorder, ODAS bridge
        if self.recorder is not None and getattr(self.recorder, "raw_wav", None) is not None:
            self.raw_listeners.append(self.recorder.raw)
        if hasattr(self.directions, "feed_raw"):
            self.raw_listeners.append(self.directions.feed_raw)
        # Directions that know which audio stream a source belongs to (ODAS: track -> beam / separated slot).
        self.stream_for = getattr(self.directions, "stream_for", None)
        self.directions.on_sources = self.on_sources
        self.audio.on_audio = self.on_audio
        # The raw-mic tap: the audio input itself, or the ears behind an ODAS direction source.
        for tap in (self.audio, getattr(self.directions, "ears", None)):
            if tap is not None and hasattr(tap, "on_raw"):
                tap.on_raw = self.on_raw
                break
        self.client.on_config = self.on_config
        self.client.heartbeat_fields = self.heartbeat

    # ---- callbacks ------------------------------------------------------------

    def stream_history(self, stream=0):
        return self.stream_histories[stream]

    def on_sources(self, t, sources):
        raw = sources
        if self.level_speech is not None:
            sources = self.level_speech.update(t, sources)
        self.history.add(t, sources)
        if self.n_streams > 1:
            route = self.stream_for or (lambda s: s.id)
            for k, h in enumerate(self.stream_histories):
                h.add(t, [s for s in sources if route(s) == k])
        if self.recorder:
            self.recorder.add_sources(t, raw)            # the firmware's readings: a replay re-runs level_speech
        wire = self.tracker.update(t, sources)
        if self.throttle.should_send(t, wire, active=self.tracker.active):
            msg = Sources(src="laptop", t=t, sources=wire)
            msg._stage = self.direction_stage()           # log only (laptop/events/stats.py), never on the wire
            self.client.send(msg)

    def on_audio(self, t, pcm):
        """May be called from an audio driver thread."""
        if self.fanout.loop is not None:
            self.fanout.loop.call_soon_threadsafe(self._publish, t, pcm)

    def on_raw(self, t, raw):
        """Raw mics (6-channel firmware or a raw fixture). May be called from an audio driver thread."""
        if self.raw_listeners and self.fanout.loop is not None:
            self.fanout.loop.call_soon_threadsafe(self._publish_raw, t, raw)

    def _publish_raw(self, t, raw):
        for f in self.raw_listeners:
            f(t, raw)

    def direction_stage(self):
        stage = {}
        for k in ("capture", "doa"):
            v = STATS.latest(k)
            if v is not None:
                stage[k] = v
        return stage

    def queue_depth(self):
        """Deepest in-process audio queue (speech / sounds / recorder lagging) and the relay outbox."""
        qs = [q.qsize() for f in {id(f): f for f in [self.fanout, *self.stream_fanouts]}.values() for q in f.queues]
        return max(qs, default=0), getattr(self.client, "queue_depth", 0)

    def audio_dropped(self):
        return sum(f.dropped for f in {id(f): f for f in [self.fanout, *self.stream_fanouts]}.values())

    def onset_where(self, stream, t):
        """(bearing, speech) for an onset on `stream` at t: that stream's track (beams: id = stream), else the
        loudest newest reading."""
        hist = self.stream_histories[stream] if stream < len(self.stream_histories) else self.history
        speech = hist.last_speech_t >= t - 0.3
        tr = self.tracker.track(stream) if self.n_streams > 1 else None
        if tr is not None:
            return tr.bearing, speech
        latest = hist.latest()
        if not latest or not latest[1]:
            return None, speech
        return max(latest[1], key=lambda s: s.energy).bearing_deg, speech

    def feed_onsets(self, t, pcm):
        if pcm.ndim == 1:
            self.onsets.feed(0, t, pcm)
        else:
            for k in range(min(self.n_streams, pcm.shape[1])):
                self.onsets.feed(k, t, pcm[:, k])

    def _publish(self, t, pcm):
        self.feed_onsets(t, pcm)
        self.fanout.publish(t, pcm)
        if self.n_streams > 1:
            for k, f in enumerate(self.stream_fanouts):
                f.publish(t, pcm[:, k])

    def on_config(self, cfg_msg):
        names = [n for n in [cfg_msg.user_name, *cfg_msg.name_aliases] if n] or self.names   # no name set on the phone: keep names.user_name
        for sp in self.speeches:
            sp.set_names(names)
        self.alerts.set_config(cfg_msg.features.alerts, cfg_msg.alerts_enabled)
        print(f"[config] user {cfg_msg.user_name!r} aliases {cfg_msg.name_aliases} "
              f"focus={cfg_msg.focus_mode} belt_only={cfg_msg.belt_only_mode}")

    def heartbeat(self):
        problems = list(getattr(self.directions, "problems", []))
        if self.audio is not self.directions:
            problems += getattr(self.audio, "problems", [])
        engine = "faster_whisper"
        if self.speech is not None:
            engine = self.speech.transcriber.engine
            reason = getattr(self.speech.transcriber, "reason", None) or getattr(self, "speech_detail", None)
            if reason:
                problems.append(reason)
        status = "degraded" if problems else "ok"
        fields = {"status": status, "engine": engine,
                  "doa_source": getattr(self.directions, "name", "builtin")}
        notes = [n for n in (src() for src in getattr(self, "detail_sources", [])) if n]   # extras: never degrade
        if problems or notes:
            fields["detail"] = "; ".join(problems + notes)[:200]
        return fields

    # ---- run ------------------------------------------------------------------

    async def _expire_alerts(self):
        while True:
            await asyncio.sleep(1.0)
            self.alerts.expire()

    async def _record_audio(self, queue):
        while True:
            t, pcm = await queue.get()
            self.recorder.audio(t, pcm)

    @staticmethod
    async def supervise(name, start, retry_s=1.0):
        """Run start() forever: a crash in one part (sounds, say) is logged and restarted, never fatal."""
        while True:
            try:
                await start()
                return
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                print(f"[laptop] {name} crashed; restarting in {retry_s:.0f} s")
                traceback.print_exc()
                await asyncio.sleep(retry_s)

    async def run(self):
        self.fanout.loop = asyncio.get_running_loop()
        for f in self.stream_fanouts:
            f.loop = self.fanout.loop
        parts = {"relay": self.client.run, "directions": self.directions.run, "alerts": self._expire_alerts}
        if self.audio is not self.directions:
            parts["audio"] = self.audio.run
        multi = self.n_streams > 1
        if multi:
            from .speech.pipeline import Crosstalk
            crosstalk, arbiter = Crosstalk(), {}
        for k in range(self.n_streams):
            tag = f"{k}" if multi else ""
            if self.speech_factory:
                sp = self.speech_factory(self, stream=k) if multi else self.speech_factory(self)
                sp.set_names(self.names)
                if multi:
                    sp.stream, sp.crosstalk = k, crosstalk
                    sp.prefix = f"{sp.prefix}b{k}"
                self.speeches.append(sp)
                # beams: speech gets every stream (its own + the other beam, for leak gating and masking)
                q = (self.fanout if multi else self.stream_fanouts[k]).subscribe()
                parts[f"speech{tag}"] = (lambda sp=sp, q=q: sp.run(q))
            if self.sounds_factory:
                so = self.sounds_factory(self, stream=k) if multi else self.sounds_factory(self)
                if multi:
                    so.stream, so.arbiter = k, arbiter
                self.sounds_list.append(so)
                q = self.stream_fanouts[k].subscribe()
                parts[f"sounds{tag}"] = (lambda so=so, q=q: so.run(q))
            if self.speeches and self.sounds_list and len(self.speeches) == len(self.sounds_list):
                if hasattr(self.speeches[-1], "note_sounds"):     # laughter, non-speech sounds -> that stream's speech
                    self.sounds_list[-1].on_scores = self.speeches[-1].note_sounds
        self.speech = self.speeches[0] if self.speeches else None
        self.sounds = self.sounds_list[0] if self.sounds_list else None
        if self.recorder:
            q_rec = self.fanout.subscribe(maxsize=2000)
            parts["recorder"] = lambda: self._record_audio(q_rec)
        parts.update(self.extra_parts)
        tasks = [self.supervise(name, start) for name, start in parts.items()]
        print(f"[laptop] running: directions={type(self.directions).__name__} "
              f"audio={type(self.audio).__name__} streams={self.n_streams} "
              f"speech={'on' if self.speech else 'off'} sounds={'on' if self.sounds else 'off'}")
        try:
            await asyncio.gather(*tasks)
        finally:
            if self.recorder:
                self.recorder.close()


def fixture_path(name):
    p = Path(name)
    return p if p.exists() else ROOT / "fixtures" / name

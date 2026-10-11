"""The ReSpeaker XVF3800 as HERE's ears: processed audio + built-in DoA -> `sources` (SPEC §11, §12.5).

Adapted from audio_here/__main__.py (v1). Same device logic: the array's processed 16 kHz output is
streamed from the WASAPI input, its direction of arrival is polled over USB control at DOA_RATE_HZ
(with the two focused beams), the LED ring shows directions, and with the 6-channel firmware the raw
mics are buffered so `locate()` can find the direction of non-speech sounds with SRP-PHAT.

Capture modes (`audio.capture`): "processed" = the device's default mix (USB ch 0 = auto-select beam,
one stream, one source). "beams" = the two slow-moving focused beams routed to USB ch 0/1
(AUDIO_MGR_OP_L/R = (6,0)/(6,1), volatile, restored on exit): two audio streams, and `sources` id 0/1 =
each beam's own direction and speech energy, so two talkers (or a talker and a crying baby) are heard
and captioned separately. When both beams point at the same talker, only beam 0 counts as speech.

What changed for v3: nothing is sent to a hub. Audio goes to `on_audio(t, pcm)` (in-process, to
speech and sounds), directions go to `on_sources(t, [Source])` in world bearings, and sounds ask
`locate(t0, t1, label)` directly instead of the v1 `sound_event` -> `sound_doa` round trip.
"""

import asyncio
import math
import os
import time
from collections import deque

import sounddevice as sd
import usb.core

from contracts.python.here_contract import Source
from laptop.geometry import angdiff

from . import config as C
from laptop.events.stats import STATS

from .base import dbfs, energy_from_dbfs
from .localize import EventBearings, RawLocator, SpeechBearings, SrpSources, aec_to_device
from .ring import LeadDoa, Ring, RingPlanner, set_mode
from .xvf3800 import ENERGY_HOLD_S, XVF3800, sample, to_world

RELOCATE_MIN_S = 0.5             # a growing sound event is re-localized at most this often
DEFAULT_OP_L, DEFAULT_OP_R = (8, 0), (7, 3)   # firmware defaults: auto-select beam, ASR beam (read on 2.0.6)
RAW_OPS = ("AUDIO_MGR_OP_CH3", "AUDIO_MGR_OP_CH4", "AUDIO_MGR_OP_CH5", "AUDIO_MGR_OP_CH6")
DEFAULT_RAW_CATEGORY = 1                      # 2.1.1 6-ch default: unamplified raw mics (-78 dBFS ambient)


def find_input():
    """WASAPI input of the ReSpeaker; it runs natively at 16 kHz there."""
    for i, d in enumerate(sd.query_devices()):
        api = sd.query_hostapis(d["hostapi"])["name"]
        if d["max_input_channels"] > 0 and "respeaker" in d["name"].lower() and "WASAPI" in api:
            return i
    raise SystemExit("ReSpeaker audio input not found; pass --device N (see python -m sounddevice)")


class ReSpeaker:
    """DirectionSource + AudioSource for the XVF3800. `name` is the heartbeat's doa_source."""

    name = "builtin"
    reports_speech = True            # DOA_VALUE carries the device's speech flag

    def __init__(self, offset=None, ccw=None, device=None, ring_mode=None, beams_as_sources=None, capture=None):
        self.capture = capture or getattr(C, "CAPTURE", "processed")
        self.streams = 2 if self.capture == "beams" else 1     # audio streams handed to on_audio
        self.offset = C.ARRAY_OFFSET_DEG if offset is None else offset
        self.ccw = (not C.ARRAY_CLOCKWISE) if ccw is None else ccw
        self.beams_as_sources = C.BUILTIN_BEAMS_AS_SOURCES if beams_as_sources is None else beams_as_sources
        self.device_arg, self.ring_mode = device, ring_mode
        self.on_sources = None        # (t, [Source]) -> None, event loop thread
        self.on_audio = None          # (t, int16 pcm) -> None, called from the audio thread
        self.on_raw = None            # (t, int16 (n, 4) raw mics, USB order) -> None, audio thread (recorder, ODAS)
        self.last_audio = time.monotonic()   # newest audio callback (the watchdog: unplug, sleep)
        self.lost = None              # why the array was lost, while reconnecting (heartbeat detail)
        self.reconnects = 0
        self.connect()

    def connect(self):
        """Open the array and set it up for this mode: everything `xvf3800.py --reset` undoes. Called at start and
        again after the array was lost (unplugged, laptop slept): a replug comes back with firmware defaults."""
        self.problems = []
        self.device = self.device_arg if self.device_arg is not None else find_input()
        self.channels = sd.query_devices(self.device)["max_input_channels"]
        self.array = XVF3800()
        # Always set the routing this mode needs: a killed beams run leaves the beams routed until power-cycle.
        self.routing = (DEFAULT_OP_L, DEFAULT_OP_R)  # restored on close (beams mode)
        if self.capture == "beams":
            if self.channels < 2:
                raise SystemExit("audio.capture: beams needs the 2 USB capture channels")
            self.array.write("AUDIO_MGR_OP_L", 6, 0)          # focused beam 0
            self.array.write("AUDIO_MGR_OP_R", 6, 1)          # focused beam 1
        else:
            self.array.write("AUDIO_MGR_OP_L", *DEFAULT_OP_L)
            self.array.write("AUDIO_MGR_OP_R", *DEFAULT_OP_R)
            self.routing = None
        self.beam_held = [(0.0, 0.0), (0.0, 0.0)]            # per-beam (peak energy, time) hold
        self.level_db = -120.0
        self.beam_peak_db = [-120.0, -120.0]   # beams mode: each beam's loudest block since the last DoA poll
        self.last_doa = None
        self.doa_ms = None            # the newest DoA poll's USB time (stage "doa")

        # Raw mics for locate(): only the 6-channel firmware exposes them.
        self.raw = self.srp = None
        self.raw_category = None
        self.geometry = C.MIC_GEOMETRY or self.array.mic_geometry()
        if self.channels > max(C.RAW_MIC_CHANNELS):
            self.raw = RawLocator(self.geometry, C.SAMPLE_RATE, seconds=C.RAW_BUFFER_S)
            self.srp = self.raw.srp
            self.set_raw_category(getattr(C, "RAW_MIC_CATEGORY", DEFAULT_RAW_CATEGORY))
        else:
            self.problems.append(f"no raw mics ({self.channels}-ch firmware): sound bearings from DoA only")
        self.located = EventBearings(RELOCATE_MIN_S, C.RAW_BUFFER_S)   # one bearing per sound event
        # fixed beams: the talker's bearing per utterance (the DoA only names the beam)
        self.speech_bearings = SpeechBearings(self.raw, self.offset, self.ccw) if self.raw is not None else None

        self.ring = None
        ring_mode = self.ring_mode or C.RING_MODE
        if ring_mode == "host":
            try:
                self.ring = Ring(self.array)
            except (usb.core.USBError, RuntimeError) as e:
                self.problems.append(f"LED ring unavailable ({e})")
        else:
            set_mode(self.array, ring_mode)
        # The ring's two markers (ring.RingPlanner): main = our SRP on the raw mics (or the DoA), second = ODAS /
        # another SRP track / the other auto-tracking beam (audio.ring_main, audio.ring_second).
        self.planner = RingPlanner(
            main=getattr(C, "RING_MAIN", "auto"), second=getattr(C, "RING_SECOND", "auto"), raw=self.raw is not None,
            min_sep_deg=getattr(C, "RING_SECOND_MIN_SEP_DEG", 45.0), veto_deg=getattr(C, "RING_VETO_DEG", 30.0),
            second_rel=getattr(C, "RING_SECOND_REL", 0.6), second_alive_s=getattr(C, "RING_SECOND_ALIVE_S", 0.3),
            second_leader_hops=getattr(C, "RING_SECOND_LEADER_HOPS", 2),
            lead=LeadDoa(onset_s=getattr(C, "RING_ONSET_S", 0.8), slew_deg=getattr(C, "RING_SLEW_DEG", 5.0)))
        self.srp_ring = None                  # our SRP tracker on the raw mics, fed from the audio thread's blocks
        self.srp_pending = deque()
        if self.ring is not None and self.planner.main == "srp" and self.raw is not None:
            self.srp_ring = SrpSources(self.geometry, C.SAMPLE_RATE, order=self.raw.order)
        self.fixed = getattr(C, "FIXED_BEAMS", None) if self.capture == "beams" else None
        self.beams_fixed = False
        self.set_fixed_beams(self.fixed)

    def set_raw_category(self, category):
        """What USB ch 3..6 carry: 1 = raw mics (firmware default, ~4 LSB of ambient noise), 3 = the same mics
        after AUDIO_MGR_MIC_GAIN (90x = +39 dB: real resolution for SRP / ODAS). Volatile; restored on exit."""
        try:
            for k, reg in enumerate(RAW_OPS):
                self.array.write(reg, int(category), k)
            self.raw_category = int(category)
        except (usb.core.USBError, RuntimeError) as e:
            self.problems.append(f"raw mic routing unavailable ({e})")

    def meta(self):
        """What a recording needs to be replayed and re-fitted later (fixture meta.json)."""
        out = {"firmware": None, "channels": self.channels, "capture": self.capture,
               "routing": {"op_l": list(DEFAULT_OP_L), "op_r": list(DEFAULT_OP_R)},
               "fixed_beams": self.fixed, "array_offset_deg": self.offset, "array_clockwise": not self.ccw,
               "aec_to_device_offset_deg": C.AEC_TO_DEVICE_OFFSET_DEG, "aec_to_device_sign": C.AEC_TO_DEVICE_SIGN,
               "mic_geometry": [list(m) for m in self.geometry], "raw_mic_channels": list(C.RAW_MIC_CHANNELS),
               "raw_mic_category": self.raw_category, "raw_mic_order": list(self.raw.order) if self.raw else None}
        try:
            out["firmware"] = ".".join(map(str, self.array.read("VERSION")))
            out["routing"] = {"op_l": list(self.array.read("AUDIO_MGR_OP_L")),
                              "op_r": list(self.array.read("AUDIO_MGR_OP_R"))}
            out["mic_gain"] = self.array.read("AUDIO_MGR_MIC_GAIN")[0]
        except (usb.core.USBError, RuntimeError, KeyError):
            pass
        return out

    def set_fixed_beams(self, world=None):
        """Lock the two focused beams on world bearings [b0, b1] (None: let them track talkers, the default)."""
        try:
            if not world:
                self.array.write("AEC_FIXEDBEAMSONOFF", 0)
                self.beams_fixed = False
                return
            # world -> device (array calibration) -> beam frame: device = aec_offset + sign * aec
            dev = [((-w if self.ccw else w) + self.offset) % 360.0 for w in world]
            aec = [((d - C.AEC_TO_DEVICE_OFFSET_DEG) * C.AEC_TO_DEVICE_SIGN) % 360.0 for d in dev]
            self.array.write("AEC_FIXEDBEAMSAZIMUTH_VALUES", *[math.radians(a) for a in aec])
            self.array.write("AEC_FIXEDBEAMSGATING", 0)          # both beams always live: two streams
            self.array.write("AEC_FIXEDBEAMSONOFF", 1)
            self.beams_fixed = True
            print(f"[audio] fixed beams at world {world[0]:+.0f} / {world[1]:+.0f} deg")
        except (usb.core.USBError, RuntimeError) as e:
            self.problems.append(f"fixed beams unavailable ({e})")

    # ---- audio ----------------------------------------------------------------

    def stream(self):
        raw_cols = list(C.RAW_MIC_CHANNELS)

        def callback(indata, frames, time_info, status):
            self.last_audio = time.monotonic()
            now = time.time()
            # Wall time of the first sample: now minus how long ago the ADC captured it.
            lag = (time_info.currentTime - time_info.inputBufferAdcTime
                   if time_info.inputBufferAdcTime else frames / C.SAMPLE_RATE)
            t = now - lag
            STATS.add("capture", lag * 1000)
            if self.raw is not None:
                raw = indata[:, raw_cols].copy()
                self.raw.add(t, raw)
                if self.srp_ring is not None:
                    self.srp_pending.append((t, raw))
                if self.on_raw is not None:
                    self.on_raw(t, raw)
            pcm = indata[:, :2].copy() if self.streams == 2 else indata[:, C.AUDIO_CHANNEL].copy()
            self.level_db = dbfs(pcm if pcm.ndim == 1 else pcm.max(axis=1))
            if pcm.ndim == 2:
                for k in range(min(2, pcm.shape[1])):
                    self.beam_peak_db[k] = max(self.beam_peak_db[k], dbfs(pcm[:, k]))
            if self.on_audio is not None:
                self.on_audio(t, pcm)

        return sd.InputStream(samplerate=C.SAMPLE_RATE, channels=self.channels, dtype="int16",
                              blocksize=C.CHUNK_SAMPLES, device=self.device, callback=callback)

    # ---- direction ------------------------------------------------------------

    def tick(self):
        """One DoA sample plus a ring update, on a worker thread (both use the USB control pipe)."""
        t0 = time.perf_counter()
        s = sample(self.array)
        self.doa_ms = (time.perf_counter() - t0) * 1000
        if self.srp_ring is not None:
            while self.srp_pending:
                for t, found in self.srp_ring.feed(*self.srp_pending.popleft()):
                    self.planner.set_srp(t, [(i, aec_to_device(az), v) for i, az, v in found])
        if self.ring is not None:
            try:
                self.ring.flush()
            except (usb.core.USBError, RuntimeError) as e:
                print(f"\n[audio] LED ring write failed ({e}); ring off")
                self.problems.append("LED ring write failed")
                self.ring = None
        return s

    def beams(self, s):
        """The two focused beams (two-talker tracking), world frame."""
        out = []
        for az, e in zip(s["beam_az_deg"][:2], s["beam_energy"][:2]):
            out.append({"bearing_deg": to_world(aec_to_device(az), self.offset, self.ccw),
                        "speech": e > 0, "energy_db": round(10 * math.log10(e), 1) if e > 0 else 0.0})
        return out

    def sources_for(self, s, confidence, energy):
        """Contract `sources` for one DoA sample: the main DoA as id 0, optionally the second beam as id 1."""
        bearing = to_world(s["doa_deg"], self.offset, self.ccw)
        out = [Source(id=0, bearing_deg=bearing, energy=round(energy, 2), speech=s["speech"],
                      confidence=round(confidence, 2))]
        if self.beams_as_sources:
            for b in self.beams(s):
                if b["speech"] and abs(angdiff(b["bearing_deg"], bearing)) >= C.SECOND_BEAM_MIN_SEP_DEG:
                    conf = min(1.0, max(0.0, (b["energy_db"] - C.CONF_DB_LOW) / (C.CONF_DB_HIGH - C.CONF_DB_LOW)))
                    out.append(Source(id=1, bearing_deg=b["bearing_deg"], energy=round(energy, 2), speech=True,
                                      confidence=round(conf, 2)))
                    break
        return out

    def take_beam_energy(self):
        """Each beam's own level (its audio stream's loudest block since the last poll) mapped to 0..1."""
        peaks, self.beam_peak_db = self.beam_peak_db, [-120.0, -120.0]
        return [energy_from_dbfs(db, C.ENERGY_DBFS_LOW, C.ENERGY_DBFS_HIGH) for db in peaks]

    def beam_sources(self, s, energy):
        """Beams mode: one Source per focused beam (id = beam = audio stream), each with its own direction,
        speech energy and level (`energy`: one value, or one per beam). Both beams on one talker
        (< second_beam_min_sep_deg apart): only beam 0 is speech."""
        energies = energy if isinstance(energy, (list, tuple)) else [energy, energy]
        out = []
        for k in (0, 1):
            e = s["beam_energy"][k]
            held, held_t = self.beam_held[k]
            if e > 0 or s["t"] - held_t > ENERGY_HOLD_S:
                self.beam_held[k] = held, held_t = (e, s["t"])
            lvl = 10 * math.log10(held) if held > 0 else 0.0
            conf = min(1.0, max(0.0, (lvl - C.CONF_DB_LOW) / (C.CONF_DB_HIGH - C.CONF_DB_LOW)))
            bearing = to_world(aec_to_device(s["beam_az_deg"][k]), self.offset, self.ccw)
            out.append(Source(id=k, bearing_deg=bearing, energy=round(energies[k], 2), speech=conf > 0,
                              confidence=round(conf, 2)))
        if out[1].speech and abs(angdiff(out[0].bearing_deg, out[1].bearing_deg)) < C.SECOND_BEAM_MIN_SEP_DEG:
            out[1] = Source(id=1, bearing_deg=out[1].bearing_deg, energy=out[1].energy, speech=False, confidence=0.0)
        return out

    def set_ring_tracks(self, t, sources):
        """Other sources for the ring's second marker, as world-frame `sources` (OdasSource pushes its tracks)."""
        dev = [(src.id, ((-src.bearing_deg if self.ccw else src.bearing_deg) + self.offset) % 360.0,
                src.confidence) for src in sources if src.speech]
        self.planner.set_odas(t, dev)

    def draw(self, s, confidence):
        if self.ring is None:
            return
        az, e = s["beam_az_deg"], s["beam_energy"]
        fast = (aec_to_device(az[2]), e[2]) if len(az) > 2 else (None, 0.0)
        beams = [(aec_to_device(a), x) for a, x in zip(az[:2], e[:2])]
        main, hide, second = self.planner.plan(s["t"], s["doa_deg"], s["speech"], fast, beams, self.beams_fixed,
                                               C.RING_HOLD_S)
        if main is not None:
            level = main[1] if self.planner.main == "srp" else confidence
            self.ring.show("doa", main[0], C.RING_SPEECH_COLOR, C.RING_MIN_LEVEL + (1 - C.RING_MIN_LEVEL) * level)
        elif hide:
            self.ring.hide("doa")
        if second is not None:
            self.ring.show("beam2", second[1], C.RING_BEAM2_COLOR,
                           C.RING_MIN_LEVEL + (1 - C.RING_MIN_LEVEL) * min(1.0, second[2]))

    def watchdog(self, fails, gap_s):
        """Why the array counts as lost now, or None: USB control failing, no audio, the laptop slept, a drill."""
        if fails >= getattr(C, "LOSS_DOA_FAILS", 10):
            return f"USB control failed {fails} times in a row"
        stale = time.monotonic() - self.last_audio
        if stale > getattr(C, "AUDIO_STALE_S", 1.5):
            return f"no audio for {stale:.1f} s"
        if gap_s > getattr(C, "SLEEP_GAP_S", 5.0):
            return f"the laptop paused {gap_s:.0f} s (sleep?)"
        drill = getattr(C, "DRILL_FILE", None)
        if drill and os.path.exists(drill):
            try:
                os.remove(drill)
            except OSError:
                pass
            return "drill: simulated unplug"
        return None

    async def poll_doa(self):
        """Poll the DoA until the array is lost; returns why."""
        period = 1.0 / C.DOA_RATE_HZ
        held, held_t = 0.0, 0.0
        next_t = time.perf_counter()
        fails, prev = 0, time.perf_counter()
        self.last_audio = time.monotonic()
        while True:
            now = time.perf_counter()
            gap, prev = now - prev, now
            reason = self.watchdog(fails, gap)
            if reason:
                return reason
            next_t = max(next_t + period, now - period)    # after a stall, don't burst to catch up
            try:
                s = await asyncio.to_thread(self.tick)
                fails = 0
            except usb.core.USBError as e:
                fails += 1
                if fails == 1:
                    print(f"\n[audio] DoA read failed ({e}); skipping sample")
                await asyncio.sleep(max(0.0, next_t - time.perf_counter()))
                continue
            # Per-beam speech energy arrives in bursts; hold the peak briefly.
            peak = max(s["beam_energy"])
            if peak > 0 or s["t"] - held_t > ENERGY_HOLD_S:
                held, held_t = peak, s["t"]
            energy_lvl = 10 * math.log10(held) if held > 0 else 0.0
            span = C.CONF_DB_HIGH - C.CONF_DB_LOW
            confidence = min(1.0, max(0.0, (energy_lvl - C.CONF_DB_LOW) / span)) if s["speech"] else 0.0
            energy = energy_from_dbfs(self.level_db, C.ENERGY_DBFS_LOW, C.ENERGY_DBFS_HIGH)
            if self.capture == "beams":
                sources = self.beam_sources(s, self.take_beam_energy())
            else:
                sources = self.sources_for(s, confidence, energy)
            if self.doa_ms is not None:
                STATS.add("doa", self.doa_ms)
            if self.on_sources is not None:
                self.on_sources(s["t"], sources)
            self.draw(s, confidence)
            self.last_doa = (sources[0].bearing_deg, s["speech"], confidence)
            await asyncio.sleep(max(0.0, next_t - time.perf_counter()))

    # ---- non-speech direction (SRP-PHAT on the raw mics) ------------------------

    def locate(self, t0, t1, label="other", key=None):
        """World bearing of a sound over [t0, t1] from the raw mics: (bearing_deg, confidence) or None.

        Blocking (FFTs); call via asyncio.to_thread. None when the firmware has no raw mics, the label is speech
        (DoA handles speech), or this event has no trustworthy direction yet (outside the buffer, low coherence).

        One bearing per sound event (`key`, the event id; localize.EventBearings): every update returns the
        event's median bearing, also when this update can't localize (rate-limited, low coherence).
        """
        if self.srp is None or label == "speech":
            return None
        key = key or (label, round(t0, 1))
        out, new = self.located.locate(key, time.time(), lambda: self.raw.locate(t0, t1, self.offset, self.ccw),
                                       C.SRP_MIN_CONFIDENCE)
        if new is not None and new[2] < C.SRP_MIN_CONFIDENCE:
            print(f"\n[audio] {label}: no clear direction (coherence {new[2]:.2f})")
        elif new is not None:
            print(f"\n[audio] {label} at {new[0]:+.0f} deg (coherence {new[2]:.2f}); event bearing {out[0]:+.0f}")
            if self.ring is not None:
                self.ring.show("alert", new[1], C.RING_ALERT_COLOR, 1.0, C.RING_ALERT_S)
        return out

    def locate_speech(self, key, t0, t1, beam_deg):
        """Fixed beams: the talker's world bearing for utterance `key` over [t0, t1], restricted to the beam's half
        of the room (localize.SpeechBearings): (bearing, coherence, spread_deg, looks) or None. ~4 ms per look."""
        if self.speech_bearings is None:
            return None
        return self.speech_bearings.locate(key, t0, t1, beam_deg)

    # ---- run ------------------------------------------------------------------

    def describe(self):
        version = ".".join(map(str, self.array.read("VERSION")))
        lines = [f"[audio] XVF3800 firmware {version}; audio from [{self.device}] "
                 f"{sd.query_devices(self.device)['name']} ({self.channels} ch)",
                 f"[audio] array offset {self.offset:.1f} deg, {'counter-clockwise' if self.ccw else 'clockwise'}; "
                 f"capture {self.capture} ({self.streams} stream{'s' if self.streams > 1 else ''}); "
                 f"raw-mic sound direction {'on' if self.srp else 'off'}; "
                 f"LED ring {'host' if self.ring else 'not driven by HERE'}"]
        return lines + [f"[audio] note: {p}" for p in self.problems]

    async def run(self):
        for line in self.describe():
            print(line)
        while True:
            try:
                reason = None
                try:
                    with self.stream():
                        reason = await self.poll_doa()
                except (sd.PortAudioError, usb.core.USBError, RuntimeError, OSError) as e:
                    reason = f"{type(e).__name__}: {e}"
            except asyncio.CancelledError:
                self.release(restore=True)
                raise
            await self.recover(reason)

    async def recover(self, reason):
        """The array was lost: say so in the heartbeat, let go of it, then reconnect and set it up again (no restart)."""
        self.lost = reason
        self.problems = [f"ReSpeaker lost ({reason}); reconnecting"]
        print(f"\n[audio] ReSpeaker lost: {reason}. Reconnecting every {getattr(C, 'RECONNECT_S', 2.0):.0f} s ...")
        self.release(restore=True)          # still attached after a glitch (sleep)? put it back to defaults
        t0, tries = time.monotonic(), 0
        while True:
            await asyncio.sleep(getattr(C, "RECONNECT_S", 2.0))
            tries += 1
            try:
                await asyncio.to_thread(self.reopen)
            except (SystemExit, Exception) as e:  # noqa: BLE001  (not found yet, busy, half-enumerated)
                first = (str(e).splitlines() or [type(e).__name__])[0][:60]
                self.problems = [f"ReSpeaker lost ({reason}); reconnecting (try {tries}: {first})"]
                continue
            self.reconnects += 1
            self.lost = None
            print(f"[audio] ReSpeaker back after {time.monotonic() - t0:.1f} s (reconnect #{self.reconnects})")
            for line in self.describe():
                print(line)
            return

    def reopen(self):
        """Re-enumerate (PortAudio caches the device list; a replug can change the index) and set up again."""
        sd._terminate()
        sd._initialize()
        self.connect()

    def release(self, restore):
        """Close the ring, routing and USB handle without raising (the device may already be gone)."""
        try:
            if self.ring is not None:
                self.ring.close()
        except (usb.core.USBError, RuntimeError, OSError):
            pass
        if restore:
            try:
                self.restore_routing()
            except (usb.core.USBError, RuntimeError, OSError):
                pass
        try:
            self.array.close()
        except (usb.core.USBError, RuntimeError, OSError):
            pass

    def restore_routing(self):
        if self.raw_category not in (None, DEFAULT_RAW_CATEGORY):
            self.set_raw_category(DEFAULT_RAW_CATEGORY)
        if self.fixed:
            try:
                self.array.write("AEC_FIXEDBEAMSONOFF", 0)
            except (usb.core.USBError, RuntimeError):
                pass
        if self.routing is not None:
            try:
                self.array.write("AUDIO_MGR_OP_L", *self.routing[0])
                self.array.write("AUDIO_MGR_OP_R", *self.routing[1])
            except (usb.core.USBError, RuntimeError) as e:
                print(f"[audio] couldn't restore the output routing ({e}); power-cycle the array to reset it")


# v1 name, kept so older notes and scripts still read naturally.
Audio = ReSpeaker

"""ODAS on the raw mics -> contract `sources` (SPEC §11). Opt-in:
`audio.doa_source: odas` or `python -m laptop --real --odas`.

    raw mics (ReSpeaker 6-ch firmware, or a raw fixture on replay)
      -> OdasBridge: AEC geometry order x input_gain -> TCP :raw_port  <- odaslive in WSL2 connects here
      <- tracked sources JSON :port, potentials :pots_port, separated / post-filtered audio :separated_port /
         :postfiltered_port (ODAS's sinks connect as clients too; WSL2 mirrored networking reaches Windows' 127.0.0.1)
      -> OdasSource: contract `sources` (id = ODAS track id, stable while tracked; world bearings through the
         beams' calibration chain), heartbeat doa_source "odas", problems when the bridge is down.

Audio for speech and sounds (`audio.odas.audio`):
  beams         hybrid (default): the XVF3800's two fixed beams are steered to the two strongest ODAS tracks
                (AEC_FIXEDBEAMSAZIMUTH_VALUES, with hysteresis); captions come from the beams as today.
  separated     ODAS's separated audio, one stream (speech + sound pipeline) per track slot.
  postfiltered  the same after ODAS's post-filter.

Frame times: ODAS stamps frames by hop count; the bridge keeps (sample index -> capture time) for every raw block
it sent, so a frame's time is the capture time of its last sample (laptop clock, like every other `sources`).
"""

import asyncio
import json
import math
import subprocess
import time
from collections import deque
from pathlib import Path

import numpy as np

from contracts.python.here_contract import Source
from laptop.geometry import angdiff

from . import config as C
from .localize import aec_to_device, in_geometry_order
from .odas_cfg import live_cfg, settings
from .xvf3800 import to_world

ROOT = Path(__file__).resolve().parents[2]
SR = 16000
RESTART_S = 2.0                   # wait before restarting odaslive after it exits
STALE_S = 1.5                     # no tracked frame for this long while raw flows -> problem in the heartbeat


class ActivityHold:
    """Per-track peak hold of ODAS activity: it flickers 0 <-> 1 within a word (hop 8 ms); hold the peak hold_s."""

    def __init__(self, hold_s=0.4):
        self.hold_s = hold_s
        self.peak = {}                    # id -> (activity, t)

    def __call__(self, t, tid, act):
        a, ta = self.peak.get(tid, (0.0, -1e9))
        if act >= a or t - ta > self.hold_s:
            self.peak[tid] = a, ta = (act, t)
        if len(self.peak) > 64:
            self.peak = {k: v for k, v in self.peak.items() if t - v[1] <= self.hold_s}
        return a


def frame_to_sources(frame, offset, ccw, min_activity=0.1, speech_activity=0.5, hold=None, t=None, aec=None):
    """One ODAS tracked frame -> [Source], strongest first; empty slots (id 0) and weak tracks dropped.
    hold: an ActivityHold (with t) to smooth activity per track before the thresholds. aec: a recording's
    aec_to_device (localize.aec_calibration(meta)) for offline runs; None = live config."""
    out = []
    for s in frame.get("src", []):
        if not s.get("id"):
            continue
        x, y = s.get("x", 0.0), s.get("y", 0.0)
        if x == 0.0 and y == 0.0:
            continue
        act = float(s.get("activity", 0.0))
        if hold is not None:
            act = hold(t, int(s["id"]), act)
        if act < min_activity:
            continue
        az = math.degrees(math.atan2(y, x)) % 360
        bearing = to_world(aec_to_device(az, aec), offset, ccw)
        a = round(min(1.0, act), 2)
        out.append(Source(id=int(s["id"]), bearing_deg=bearing, energy=a, speech=act >= speech_activity, confidence=a))
    return sorted(out, key=lambda s: -s.confidence)


def slots_of(frame):
    """ODAS track id -> slot index (the channel of separated / post-filtered audio carrying it)."""
    return {int(s["id"]): k for k, s in enumerate(frame.get("src", [])) if s.get("id")}


class SampleClock:
    """Capture time of raw sample n on the stream sent to ODAS (blocks may arrive with gaps)."""

    def __init__(self, keep=4000):
        self.blocks = deque(maxlen=keep)   # (first sample index, capture time of that sample)
        self.sent = 0

    def reset(self):
        self.blocks.clear()
        self.sent = 0

    def add(self, t, n):
        self.blocks.append((self.sent, t))
        self.sent += n

    def time_of(self, n):
        if not self.blocks:
            return None
        best = self.blocks[0]
        for b in reversed(self.blocks):
            if b[0] <= n:
                best = b
                break
        return best[1] + (n - best[0]) / SR


class OdasBridge:
    """Windows end of the ODAS link: serves raw audio, receives tracks / potentials / audio, runs odaslive."""

    def __init__(self, on_tracks=None, on_audio=None, audio_kind=None, launch=None, overrides=None):
        self.cfg = settings(overrides)
        self.overrides = overrides
        self.launch = self.cfg.get("launch", True) if launch is None else launch
        self.hop = int(self.cfg["hop"])
        self.k = int(self.cfg["max_sources"])
        self.gain = float(self.cfg["input_gain"])
        self.on_tracks, self.on_audio, self.audio_kind = on_tracks, on_audio, audio_kind
        self.raw_q = None                        # asyncio.Queue of bytes while ODAS is connected
        self.clock = SampleClock()
        self.connected = {"raw": False, "tracks": False}
        self.last_frame_wall = 0.0
        self.last_raw_wall = 0.0
        self.frames = 0
        self.problems = []
        self.loop = None
        self.proc = None
        self.latency = deque(maxlen=500)         # frame arrival - frame capture time (s)
        self.pots = None                         # (capture time, newest potentials frame): the 3D view's energy points
        self.tracks = None                       # (capture time, newest tracked frame)
        self.ready = asyncio.Event()             # set once ODAS has connected for raw audio

    # ---- raw in (any thread) ------------------------------------------------------

    def feed(self, t, raw_usb):
        """Raw mics in USB order (n, 4) int16 with the capture time of the first sample."""
        self.last_raw_wall = time.time()
        q = self.raw_q
        if q is None:
            return
        geo = in_geometry_order(raw_usb)
        if self.gain != 1.0:
            geo = np.clip(np.round(geo.astype(np.float32) * self.gain), -32768, 32767)
        data = np.ascontiguousarray(geo, "<i2").tobytes()
        self.clock.add(t, len(raw_usb))
        if q.qsize() > 400:                      # ODAS stalled: drop rather than grow without bound
            return
        q.put_nowait(data)

    def feed_threadsafe(self, t, raw_usb):
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.feed, t, raw_usb)

    # ---- servers ------------------------------------------------------------------

    async def _raw_client(self, reader, writer):
        self.clock.reset()
        self.raw_q = asyncio.Queue()
        self.connected["raw"] = True
        self.ready.set()
        print("[odas] ODAS connected for raw audio")
        try:
            while True:
                writer.write(await self.raw_q.get())
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            self.raw_q = None
            self.connected["raw"] = False
            writer.close()

    @staticmethod
    async def _json_frames(reader, handle):
        """Back-to-back JSON objects from an ODAS socket sink -> handle(frame) each."""
        dec = json.JSONDecoder()
        buf = ""
        while chunk := await reader.read(65536):
            buf += chunk.decode("utf-8", errors="ignore")
            while True:
                buf = buf.lstrip(" \r\n\t,")
                try:
                    frame, end = dec.raw_decode(buf)
                except json.JSONDecodeError:
                    break
                buf = buf[end:]
                handle(frame)

    def _frame_time(self, frame):
        t = self.clock.time_of(int(frame.get("timeStamp", 0)) * self.hop - 1)   # capture time of its newest sample
        return time.time() if t is None else t

    async def _tracks_client(self, reader, writer):
        self.connected["tracks"] = True
        try:
            await self._json_frames(reader, self._frame)
        except (ConnectionError, OSError):
            pass
        finally:
            self.connected["tracks"] = False
            writer.close()

    def _frame(self, frame):
        now = time.time()
        t = self._frame_time(frame)
        self.tracks = (t, frame)
        self.frames += 1
        self.last_frame_wall = now
        self.latency.append(now - t)
        if self.on_tracks is not None:
            self.on_tracks(t, frame)

    async def _pots_client(self, reader, writer):
        def keep(frame):
            self.pots = (self._frame_time(frame), frame)
        try:
            await self._json_frames(reader, keep)
        except (ConnectionError, OSError):
            pass
        writer.close()

    def _audio_client(self, kind):
        async def client(reader, writer):
            n = self.hop * self.k * 2
            j = 0
            try:
                while True:
                    data = await reader.readexactly(n)
                    j += 1
                    if self.on_audio is not None and kind == self.audio_kind:
                        t = self.clock.time_of((j - 1) * self.hop)
                        pcm = np.frombuffer(data, "<i2").reshape(self.hop, self.k)
                        self.on_audio(time.time() if t is None else t, pcm)
            except (asyncio.IncompleteReadError, ConnectionError, OSError):
                pass
            writer.close()
        return client

    # ---- odaslive ------------------------------------------------------------------

    def _cfg_path(self):
        work = ROOT / "logs" / "odas" / "live"
        work.mkdir(parents=True, exist_ok=True)
        path = work / "here.cfg"
        path.write_bytes(live_cfg(self.overrides).encode("utf-8"))
        return path

    def _wsl(self, script):
        return ["wsl", "-d", self.cfg.get("wsl_distro", "Ubuntu"), "--", "bash", "-lc", script]

    def _kill_stale(self):
        """An odaslive left from a killed run still holds nothing on Windows, but would race us for the ports."""
        try:
            subprocess.run(self._wsl("pkill -f 'odaslive -c .*logs/odas/live' || true"), capture_output=True,
                           timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass

    async def _odaslive(self):
        from .odas_wsl import win_to_wsl
        cfg = win_to_wsl(self._cfg_path())
        binary = self.cfg.get("binary", "~/here/odas/build/bin/odaslive")
        while True:
            await asyncio.to_thread(self._kill_stale)
            print(f"[odas] starting odaslive in WSL ({self.cfg.get('wsl_distro', 'Ubuntu')})")
            try:
                self.proc = await asyncio.create_subprocess_exec(
                    *self._wsl(f"exec {binary} -c {cfg}"), stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT)
                out, _ = await self.proc.communicate()
                tail = (out or b"").decode(errors="ignore").strip().splitlines()[-3:]
                msg = f"odaslive exited ({self.proc.returncode}): {' | '.join(tail)}"
            except OSError as e:
                msg = f"can't start odaslive ({e})"
            print(f"[odas] {msg}; restarting in {RESTART_S:.0f} s")
            self._problem(msg)
            await asyncio.sleep(RESTART_S)

    def _problem(self, msg):
        self.problems = [msg[:160]]

    def health(self):
        """Problems for the heartbeat ([] when ODAS is connected and tracking)."""
        now = time.time()
        out = []
        if not self.connected["raw"]:
            out.append("ODAS not connected (raw audio)")
        elif now - self.last_raw_wall > STALE_S:
            out.append("no raw mic audio for ODAS")
        elif now - self.last_frame_wall > STALE_S:
            out.append("ODAS sends no tracked frames")
        if out and self.problems:
            out += self.problems
        return out

    async def run(self):
        self.loop = asyncio.get_running_loop()
        h = self.cfg["host"]
        servers = [await asyncio.start_server(self._raw_client, h, int(self.cfg["raw_port"])),
                   await asyncio.start_server(self._tracks_client, h, int(self.cfg["port"])),
                   await asyncio.start_server(self._pots_client, h, int(self.cfg["pots_port"])),
                   await asyncio.start_server(self._audio_client("separated"), h, int(self.cfg["separated_port"])),
                   await asyncio.start_server(self._audio_client("postfiltered"), h,
                                              int(self.cfg["postfiltered_port"]))]
        print(f"[odas] listening on {h}:{self.cfg['raw_port']} (raw) / {self.cfg['port']} (tracks)"
              f"{'' if self.launch else '; start odaslive yourself with logs/odas/live/here.cfg'}")
        if not self.launch:
            self._cfg_path()
        try:
            if self.launch:
                await self._odaslive()
            else:
                await asyncio.Event().wait()
        finally:
            for s in servers:
                s.close()
            if self.proc is not None and self.proc.returncode is None:
                self.proc.terminate()
                try:                          # reap it here: a pipe left to the GC errors after the loop closes
                    await asyncio.wait_for(asyncio.shield(self.proc.wait()), 5)
                except (asyncio.TimeoutError, asyncio.CancelledError, ProcessLookupError):
                    pass
            if self.launch:
                await asyncio.to_thread(self._kill_stale)


class BeamSteer:
    """Hybrid: the two XVF3800 fixed beams follow the two strongest ODAS tracks.

    A track keeps its beam while it lives (no swapping mid-sentence); a free beam goes to the strongest unassigned
    track; a beam is re-pointed only when its track moved > move_deg, at most every min_interval_s. A beam whose
    track died stays where it was (the person may speak again) until another track needs it after hold_s.
    The beams never point within min_sep_deg of each other: live, ODAS split one loudspeaker into two drifting
    tracks and both beams went behind the array, leaving the front talker without a beam.
    """

    def __init__(self, initial, move_deg=12.0, min_interval_s=0.5, hold_s=1.5, min_activity=0.3, min_sep_deg=45.0):
        self.min_sep_deg = min_sep_deg
        self.az = list(initial)            # world bearing each beam points at
        self.track = [None, None]          # ODAS id per beam
        self.last_seen = [0.0, 0.0]
        self.last_move = -1e9
        self.move_deg, self.min_interval_s, self.hold_s, self.min_activity = move_deg, min_interval_s, hold_s, min_activity

    def update(self, t, sources):
        """sources: [Source] strongest first. Returns new [b0, b1] world bearings to write, or None."""
        live = {s.id: s for s in sources if s.confidence >= self.min_activity}
        for k in (0, 1):
            if self.track[k] in live:
                self.last_seen[k] = t
        for s in sorted(live.values(), key=lambda s: -s.confidence):
            if s.id in self.track:
                continue
            free = [k for k in (0, 1) if self.track[k] is None or
                    (self.track[k] not in live and t - self.last_seen[k] > self.hold_s)]
            if not free:
                break
            k = min(free, key=lambda k: abs(angdiff(self.az[k], s.bearing_deg)))
            if abs(angdiff(s.bearing_deg, self.az[1 - k])) < self.min_sep_deg:
                continue                      # the other beam already covers this direction
            self.track[k], self.last_seen[k] = s.id, t
        target = list(self.az)
        for k in (0, 1):
            s = live.get(self.track[k])
            if s is not None and abs(angdiff(s.bearing_deg, self.az[k])) > self.move_deg:
                if abs(angdiff(s.bearing_deg, target[1 - k])) < self.min_sep_deg:
                    continue                  # drifted onto the other beam: stay put
                target[k] = s.bearing_deg
        if target != self.az and t - self.last_move >= self.min_interval_s:
            self.az, self.last_move = target, t
            return list(target)
        return None

    def stream_for(self, source):
        return self.track.index(source.id) if source.id in self.track else None


class OdasSource:
    """DirectionSource from ODAS (and, with audio: separated/postfiltered, the AudioSource too)."""

    name = "odas"
    reports_speech = True             # tracked sources are active sources; ODAS can't tell speech from sounds

    def __init__(self, offset=None, ccw=None, ears=None, audio=None, launch=None, overrides=None):
        """ears: the raw-mic provider (ReSpeaker or a raw FixturePlayer). audio: beams | separated | postfiltered."""
        self.cfg = settings(overrides)
        self.offset = C.ARRAY_OFFSET_DEG if offset is None else offset
        self.ccw = (not C.ARRAY_CLOCKWISE) if ccw is None else ccw
        self.ears = ears
        self.audio_mode = audio or self.cfg.get("audio", "beams")
        self.own_audio = self.audio_mode in ("separated", "postfiltered")
        self.streams = int(self.cfg["max_sources"]) if self.own_audio else 1
        self.bridge = OdasBridge(on_tracks=self._on_tracks, on_audio=self._on_sep_audio if self.own_audio else None,
                                 audio_kind=self.audio_mode, launch=launch, overrides=overrides)
        self.on_sources = None
        self.on_audio = None
        self.min_activity = float(self.cfg.get("min_activity", 0.1))
        self.speech_activity = float(self.cfg.get("speech_activity", 0.5))
        self.period = 1.0 / float(getattr(C, "DOA_RATE_HZ", 20))
        self.hold = ActivityHold(float(self.cfg.get("activity_hold_s", 0.4)))
        self.last_emit = -1e9
        self.slots = {}                   # ODAS id -> separated-audio slot
        self.steer = None
        if self.audio_mode == "beams" and hasattr(ears, "set_fixed_beams") and getattr(ears, "streams", 1) == 2:
            self.steer = BeamSteer(getattr(ears, "fixed", None) or [0.0, 180.0])
        self.steer_pending = None
        self.sent_sources = 0
        if hasattr(self.ears, "gate"):             # a fixture: start the scene once ODAS listens
            self.ears.gate = self.bridge.ready.wait
        if self.ears is not None and getattr(self.ears, "srp", None) is not None:
            self.srp, self.locate = self.ears.srp, getattr(self.ears, "locate", None)

    @property
    def problems(self):
        return self.bridge.health()

    def feed_raw(self, t, raw):
        """Raw mics from the app's raw tap (event loop thread)."""
        self.bridge.feed(t, raw)

    def stream_for(self, source):
        """Which audio stream a source belongs to (Laptop routes per-stream bearings with it)."""
        if self.own_audio:
            return self.slots.get(source.id)
        if self.steer is not None:
            return self.steer.stream_for(source)
        return 0

    def _on_tracks(self, t, frame):
        self.slots = slots_of(frame)
        for s in frame.get("src", []):                  # every frame feeds the hold, not only emitted ones
            if s.get("id"):
                self.hold(t, int(s["id"]), float(s.get("activity", 0.0)))
        if t - self.last_emit < self.period:
            return
        self.last_emit = t
        sources = frame_to_sources(frame, self.offset, self.ccw, self.min_activity, self.speech_activity,
                                   self.hold, t)
        if self.steer is not None:
            target = self.steer.update(t, sources)
            if target is not None:
                self.steer_pending = target
        if self.on_sources is not None:
            self.sent_sources += 1
            self.on_sources(t, sources)
        if hasattr(self.ears, "set_ring_tracks"):
            self.ears.set_ring_tracks(t, sources)          # the LED ring's second marker follows ODAS

    def _on_sep_audio(self, t, pcm):
        if self.on_audio is not None:
            self.on_audio(t, pcm.copy())

    async def _steer_loop(self):
        while True:
            await asyncio.sleep(0.05)
            target, self.steer_pending = self.steer_pending, None
            if target is not None:
                await asyncio.to_thread(self.ears.set_fixed_beams, target)

    async def run(self):
        parts = [self.bridge.run()]
        if self.steer is not None:
            parts.append(self._steer_loop())
        if self.own_audio and self.ears is not None:
            parts.append(self.ears.run())          # nobody else runs the raw-mic provider in this mode
        await asyncio.gather(*parts)

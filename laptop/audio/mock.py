"""Inputs that need no ReSpeaker: fixture replay, a WAV, the laptop mic, and a direction slider.

A fixture is a folder (SPEC §22 fixtures/):
    audio.wav        16 kHz mono s16le, the array's processed output
    sources.jsonl    {"t": seconds_from_start, "sources": [Source, ...]} per line
    meta.json        name, notes, calibration, anything else (optional)
    raw.wav          the 4 raw mics, USB order (6-channel firmware recordings; optional): replayed through
                     `on_raw`, and `locate()` finds sound directions on them like the live array

`FixturePlayer` replays both streams in real time against the laptop clock, so speech, sounds,
bearings and the relay behave exactly as live. `--mock` replays fixtures/mock_scene in a loop.
"""

import asyncio
import json
import threading
import time
import wave
from pathlib import Path

import numpy as np

from contracts.python.here_contract import Source

SR = 16000


def read_wav(path):
    with wave.open(str(path)) as w:
        if w.getframerate() != SR or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise SystemExit(f"{path}: need 16 kHz mono 16-bit WAV")
        return np.frombuffer(w.readframes(w.getnframes()), np.int16)


def read_streams(path):
    """16 kHz 16-bit WAV with one channel per audio stream (beams mode records 2): (n, streams) int16."""
    with wave.open(str(path)) as w:
        if w.getframerate() != SR or w.getsampwidth() != 2:
            raise SystemExit(f"{path}: need 16 kHz 16-bit WAV")
        k = w.getnchannels()
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).reshape(-1, k)


def read_sources(path):
    rows = []
    if Path(path).exists():
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                rows.append((float(d["t"]), [Source(**s) for s in d["sources"]]))
    return rows


class FixturePlayer:
    """DirectionSource + AudioSource from a recorded (or synthetic) fixture folder."""

    name = "builtin"

    def __init__(self, folder, loop=False, speed=1.0, chunk=512):
        self.folder = Path(folder)
        if not (self.folder / "audio.wav").exists():
            raise SystemExit(f"{self.folder}: no audio.wav (see fixtures/README.md)")
        self.audio = read_streams(self.folder / "audio.wav")
        self.streams = self.audio.shape[1]                # 2 = a beams-mode recording (one channel per beam)
        if self.streams == 1:
            self.audio = self.audio[:, 0]
        self.sources = read_sources(self.folder / "sources.jsonl")
        meta = self.folder / "meta.json"
        self.meta = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
        self.beams_fixed = self.streams == 2 and bool(self.meta.get("fixed_beams"))   # as the ReSpeaker recorded it
        self.loop, self.speed, self.chunk = loop, speed, chunk
        self.on_audio = self.on_sources = None
        self.problems = [f"replaying fixture {self.folder.name}"]
        # Recorded from the array (or scripted like it): the speech flag can gate segmentation from the start.
        self.reports_speech = any(s.speech for _, ss in self.sources for s in ss)
        self.duration = self.audio.shape[0] / SR
        self.t0 = None
        self.on_raw = None
        self.gate = None                         # optional async callable awaited before playback starts
        self.raw_audio = self.raw = self.srp = self.speech_bearings = None
        if (self.folder / "raw.wav").exists():
            from . import config as C
            from .localize import XVF3800_GEOMETRY, EventBearings, RawLocator, SpeechBearings, aec_calibration
            self.raw_audio = read_streams(self.folder / "raw.wav")
            gain = float((self.meta.get("raw") or {}).get("gain", 1.0))
            if gain != 1.0:          # undo the recording gain: replay = what the array delivered
                self.raw_audio = np.round(self.raw_audio / gain).astype(np.int16)
            geometry = self.meta.get("mic_geometry") or C.MIC_GEOMETRY or XVF3800_GEOMETRY
            # The whole raw-mic chain in the recording's own frame (meta.json): array_offset_deg/clockwise below,
            # and the aec_to_device mapping here. Only the raw-mic ORDER stays live config (fitted, not recorded).
            self.raw = RawLocator(geometry, SR, seconds=C.RAW_BUFFER_S, aec=aec_calibration(self.meta))
            self.srp = self.raw.srp
            self.offset = float(self.meta.get("array_offset_deg", C.ARRAY_OFFSET_DEG))
            self.ccw = not bool(self.meta.get("array_clockwise", C.ARRAY_CLOCKWISE))
            self.located = EventBearings()
            self.speech_bearings = SpeechBearings(self.raw, self.offset, self.ccw)

    async def run(self):
        if self.gate is not None:
            await self.gate()                    # e.g. ODAS connected: don't start the scene before the listener
        while True:
            t0 = self.t0 = time.time()           # laptop time of the fixture's second 0 (for evaluation)
            await asyncio.gather(self._play_audio(t0), self._play_sources(t0))
            if not self.loop:
                return
            await asyncio.sleep(0.5)

    async def _play_audio(self, t0):
        for i in range(0, len(self.audio), self.chunk):
            rel = i / SR
            await self._wait(t0, rel)
            if self.raw_audio is not None:
                block = self.raw_audio[i:i + self.chunk]
                self.raw.add(t0 + rel / self.speed, block)
                if self.on_raw is not None:
                    self.on_raw(t0 + rel / self.speed, block)
            if self.on_audio is not None:
                self.on_audio(t0 + rel / self.speed, self.audio[i:i + self.chunk])

    def locate(self, t0, t1, label="other", key=None):
        """World bearing of a sound from the replayed raw mics, like ReSpeaker.locate: (bearing, conf) or None."""
        from . import config as C
        if self.raw is None or label == "speech":
            return None
        key = key or (label, round(t0, 1))
        out, _ = self.located.locate(key, time.time(), lambda: self.raw.locate(t0, t1, self.offset, self.ccw),
                                     C.SRP_MIN_CONFIDENCE)
        return out

    def locate_speech(self, key, t0, t1, beam_deg):
        """A fixed beam's talker from the replayed raw mics, like ReSpeaker.locate_speech, or None."""
        if self.speech_bearings is None:
            return None
        return self.speech_bearings.locate(key, t0, t1, beam_deg)

    async def _play_sources(self, t0):
        for rel, sources in self.sources:
            await self._wait(t0, rel)
            if self.on_sources is not None:
                self.on_sources(t0 + rel / self.speed, sources)

    async def _wait(self, t0, rel):
        delay = t0 + rel / self.speed - time.time()
        if delay > 0:
            await asyncio.sleep(delay)


class MicAudio:
    """AudioSource from any microphone (laptop mic, or the ReSpeaker as a plain USB mic)."""

    def __init__(self, device=None, chunk=512):
        self.device, self.chunk = device, chunk
        self.on_audio = None
        self.problems = ["laptop mic (no ReSpeaker audio)"]

    async def run(self):
        import sounddevice as sd

        def callback(indata, frames, time_info, status):
            lag = (time_info.currentTime - time_info.inputBufferAdcTime
                   if time_info.inputBufferAdcTime else frames / SR)
            if self.on_audio is not None:
                self.on_audio(time.time() - lag, indata[:, 0].copy())

        with sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=self.chunk,
                            device=self.device, callback=callback):
            print(f"[audio] mic: {sd.query_devices(self.device, 'input')['name']}")
            while True:
                await asyncio.sleep(3600)


class SliderDirections:
    """DirectionSource from a small window: drag the bearing, hold SPACE (or tick) for speech.

    Keys: Left/Right +-5 deg, 1-8 jump to the belt directions (1 front, 3 right, 5 back, 7 left).
    Ported from v1 tools/fake_doa.py.
    """

    name = "builtin"

    def __init__(self, rate_hz=20):
        self.rate_hz = rate_hz
        self.on_sources = None
        self.problems = ["slider directions (no ReSpeaker DoA)"]
        self.bearing = 0.0
        self.speech = False
        self.held = False

    def _window(self):
        import tkinter as tk

        root = tk.Tk()
        root.title("HERE direction slider")
        bearing = tk.DoubleVar(value=0.0)
        speech = tk.BooleanVar(value=False)
        label = tk.Label(root, font=("Segoe UI", 14))
        label.pack(padx=12, pady=(12, 0))
        tk.Scale(root, from_=-179, to=180, orient="horizontal", length=420, resolution=1, variable=bearing,
                 showvalue=False).pack(padx=12)
        tk.Checkbutton(root, text="speech (or hold SPACE)", variable=speech).pack(pady=(0, 12))

        def nudge(d):
            v = bearing.get() + d
            bearing.set(v - 360 if v > 180 else v + 360 if v <= -180 else v)

        root.bind("<Left>", lambda e: nudge(-5))
        root.bind("<Right>", lambda e: nudge(5))
        root.bind("<KeyPress-space>", lambda e: setattr(self, "held", True))
        root.bind("<KeyRelease-space>", lambda e: setattr(self, "held", False))
        for k in range(1, 9):
            b = (k - 1) * 45
            root.bind(str(k), lambda e, b=b: bearing.set(b - 360 if b > 180 else b))

        def tick():
            self.bearing, self.speech = bearing.get(), speech.get()
            on = self.speech or self.held
            label.config(text=f"{self.bearing:+.0f} deg   {'SPEECH' if on else 'silent'}", fg="red" if on else "black")
            root.after(50, tick)

        tick()
        root.mainloop()

    async def run(self):
        threading.Thread(target=self._window, daemon=True).start()
        while True:
            on = self.speech or self.held
            if self.on_sources is not None:
                self.on_sources(time.time(), [Source(id=0, bearing_deg=round(self.bearing, 1),
                                                     energy=0.6 if on else 0.05, speech=on,
                                                     confidence=0.9 if on else 0.0)])
            await asyncio.sleep(1 / self.rate_hz)

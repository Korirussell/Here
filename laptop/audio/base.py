"""Interfaces shared by every direction/audio input, and the history speech and sounds read bearings from.

A DirectionSource calls `on_sources(t, [Source])`; an AudioSource calls `on_audio(t, pcm)` with
16 kHz mono int16 blocks (t = capture time of the first sample, laptop clock). The ReSpeaker is
both (builtin_doa.py); mocks and fixture replay provide the same two callbacks, so speech, sounds
and the relay client never know which one is running.
"""

from __future__ import annotations

import asyncio
import math
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

import numpy as np

from contracts.python.here_contract import Source
from laptop.geometry import angdiff, circular_mean

SourcesCallback = Callable[[float, list[Source]], None]
AudioCallback = Callable[[float, np.ndarray], None]


class DirectionSource(Protocol):
    name: str                         # "builtin" | "odas" (heartbeat doa_source); mocks report "builtin"
    on_sources: Optional[SourcesCallback]
    problems: list[str]

    async def run(self) -> None: ...


class AudioSource(Protocol):
    on_audio: Optional[AudioCallback]
    problems: list[str]

    async def run(self) -> None: ...


@dataclass
class BearingEstimate:
    bearing_deg: float
    spread_deg: float
    source_id: int
    confidence: float
    samples: int
    overlap: bool


class DirectionHistory:
    """The last few seconds of `sources`, for utterance and sound bearings (README §13 "On the laptop")."""

    def __init__(self, seconds: float = 30.0, speech_min_confidence: float = 0.3, fixed_beam: bool = False,
                 fixed_beam_spread_deg: float = 30.0):
        self.seconds = seconds
        # One fixed beam's stream (beams mode, audio.fixed_beams): it points where it was told, so its speech has
        # that bearing even when the firmware never flags speech (a quiet talker in a loud room).
        self.fixed_beam = fixed_beam
        self.fixed_beam_spread_deg = fixed_beam_spread_deg
        self.items: deque[tuple[float, list[Source]]] = deque()
        self.speech_min_confidence = speech_min_confidence
        self.last_speech_t = -1e9           # newest reading that heard speech with some confidence
        self.reports_speech = False         # the input flags speech (set by the app from the direction source)

    def add(self, t: float, sources: list[Source]) -> None:
        self.items.append((t, sources))
        if any(s.speech and s.confidence >= self.speech_min_confidence for s in sources):
            self.last_speech_t = max(self.last_speech_t, t)
        while self.items and self.items[0][0] < t - self.seconds:
            self.items.popleft()

    def latest(self) -> Optional[tuple[float, list[Source]]]:
        return self.items[-1] if self.items else None

    def window(self, t0: float, t1: float):
        return [(t, s) for t, s in self.items if t0 <= t <= t1]

    def onset(self, t: float, lookback_s: float, trim_deg: float = 45.0) -> Optional[Source]:
        """Direction for speech_start: robust mean of the speech readings in [t - lookback, t].

        A single newest reading is right only ~half the time in babble (tools/stress_test.py), so this
        takes the confidence-weighted circular mean, drops readings more than `trim_deg` from it
        (babble pulling the array), and averages again. `confidence` falls when the readings disagree,
        so the phone can wait for the utterance's bearing before buzzing. Falls back to the newest
        source of any kind when nobody has been talking.
        """
        speaking: list[Source] = []
        newest = None
        for ts, sources in reversed(self.items):
            if ts > t + 0.05:
                continue
            if ts < t - lookback_s:
                break
            speaking += [s for s in sources if s.speech]
            if newest is None and sources:
                newest = sources[0]
        if not speaking:
            if newest is None and self.items:
                last = self.items[-1][1]
                newest = last[0] if last else None
            return newest
        weights = [max(s.confidence, 0.05) for s in speaking]
        mean, _ = circular_mean([s.bearing_deg for s in speaking], weights)
        kept = [(s, w) for s, w in zip(speaking, weights) if abs(angdiff(s.bearing_deg, mean)) <= trim_deg] or \
            list(zip(speaking, weights))
        mean, spread = circular_mean([s.bearing_deg for s, _ in kept], [w for _, w in kept])
        agreement = len(kept) / len(speaking)
        conf = sum(s.confidence for s, _ in kept) / len(kept) * agreement * max(0.0, 1.0 - spread / 60.0)
        ids = [s.id for s, _ in kept]
        return Source(id=max(set(ids), key=ids.count), bearing_deg=round(mean, 1),
                      energy=max(s.energy for s, _ in kept), speech=True, confidence=round(conf, 2))

    def speech_bearing(self, t0: float, t1: float, min_confidence: float, overlap_spread_deg: float,
                       exclude: Optional[tuple[float, float]] = None) -> Optional[BearingEstimate]:
        """Circular mean of speech directions during [t0, t1], for the dominant source id.
        exclude=(bearing, half_width) leaves out readings in that window (the wearer's own voice)."""
        by_id: dict[int, list[Source]] = {}
        for _, sources in self.window(t0, t1):
            for s in sources:
                if exclude is not None and abs(angdiff(s.bearing_deg, exclude[0])) <= exclude[1]:
                    continue
                if s.speech and s.confidence >= min_confidence:
                    by_id.setdefault(s.id, []).append(s)
        if not by_id:
            return self.beam_bearing(t0, t1) if self.fixed_beam else None
        weight = {i: sum(s.confidence * max(s.energy, 0.05) for s in ss) for i, ss in by_id.items()}
        best = max(weight, key=weight.get)
        ss = by_id[best]
        mean, spread = circular_mean([s.bearing_deg for s in ss], [s.confidence for s in ss])
        # Several tracked sources talking at once (ODAS / second beam) also counts as overlap.
        rivals = [i for i, w in weight.items() if i != best and w >= 0.3 * weight[best]
                  and abs(angdiff(circular_mean([s.bearing_deg for s in by_id[i]])[0], mean)) > overlap_spread_deg]
        return BearingEstimate(round(mean, 1), round(spread, 1), best,
                               round(sum(s.confidence for s in ss) / len(ss), 2), len(ss),
                               spread > overlap_spread_deg or bool(rivals))

    def beam_bearing(self, t0: float, t1: float) -> Optional[BearingEstimate]:
        """A fixed beam's own direction (its readings in [t0, t1], else the newest before t1): no speech samples,
        zero confidence, the beam's width as spread."""
        readings = [s for _, ss in self.window(t0, t1) for s in ss]
        if not readings:
            readings = next((ss for t, ss in reversed(self.items) if t <= t1 and ss), [])
        if not readings:
            return None
        mean, _ = circular_mean([s.bearing_deg for s in readings])
        return BearingEstimate(round(mean, 1), self.fixed_beam_spread_deg, readings[-1].id, 0.0, 0, False)

    def any_bearing(self, t0: float, t1: float) -> Optional[tuple[float, float]]:
        """Energy-weighted direction of whatever was loudest in [t0, t1], speech or not: (bearing, confidence)."""
        angles, weights = [], []
        for _, sources in self.window(t0, t1):
            for s in sources:
                w = max(s.energy, 0.0) * max(s.confidence, 0.1)
                if w > 0:
                    angles.append(s.bearing_deg)
                    weights.append(w)
        if not angles:
            return None
        mean, spread = circular_mean(angles, weights)
        return round(mean, 1), round(max(0.0, 1.0 - spread / 90.0), 2)


class SourcesThrottle:
    """README §12.5: send at the source rate while anything is active; otherwise on change or keepalive.
    `active` comes from the SourceTracker (laptop/events/tracker.py) when there is one; without it, a source
    with speech or energy >= active_energy counts. A change of the id set (a track appearing or expiring,
    down to the empty list) always sends."""

    def __init__(self, idle_change_deg: float = 3.0, keepalive_s: float = 1.0, active_energy: float = 0.15):
        self.idle_change_deg = idle_change_deg
        self.keepalive_s = keepalive_s
        self.active_energy = active_energy
        self.last_t = -math.inf
        self.last: list[Source] = []
        self.sent = self.suppressed = 0

    def should_send(self, t: float, sources: list[Source], active: Optional[bool] = None) -> bool:
        if active is None:
            active = any(s.speech or s.energy >= self.active_energy for s in sources)
        before = {s.id: s for s in self.last}
        changed = [s.id for s in sources] != [s.id for s in self.last] or any(
            abs(angdiff(s.bearing_deg, before[s.id].bearing_deg)) >= self.idle_change_deg
            or s.speech != before[s.id].speech for s in sources)
        if active or changed or t - self.last_t >= self.keepalive_s:
            self.last_t, self.last = t, sources
            self.sent += 1
            return True
        self.suppressed += 1
        return False


class AudioFanout:
    """One audio input, several in-process consumers (speech, sounds, recorder). Drops oldest when a consumer lags."""

    def __init__(self):
        self.queues: list[asyncio.Queue] = []
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.dropped = 0                  # blocks dropped because a consumer lagged

    def subscribe(self, maxsize: int = 200) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self.queues.append(q)
        return q

    def publish(self, t: float, pcm: np.ndarray) -> None:
        """Call from the event loop thread."""
        for q in self.queues:
            if q.full():
                try:
                    q.get_nowait()
                    self.dropped += 1
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait((t, pcm))

    def publish_threadsafe(self, t: float, pcm: np.ndarray) -> None:
        """Call from an audio driver thread."""
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.publish, t, pcm)


def energy_from_dbfs(dbfs: float, low: float, high: float) -> float:
    return float(min(1.0, max(0.0, (dbfs - low) / (high - low))))


def dbfs(pcm: np.ndarray) -> float:
    if len(pcm) == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(pcm.astype(np.float32))))) / 32768.0
    return 20 * math.log10(max(rms, 1e-6))

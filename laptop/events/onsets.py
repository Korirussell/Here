"""Onset candidates for the sound classifier (docs/INTEGRATION.md §5.1).

Whenever an audio stream gets suddenly louder than its own noise floor, the detector publishes an
`OnsetCandidate(t, bearing_deg, energy, source_id)`: "something started here, classify this window". The classifier
subscribes (`app.onsets.subscribe(fn)`), classifies around `t`, and emits a `sound_event` (a label, or
`unknown`); it never drops a candidate silently.

Per stream (beams mode: stream k = beam k = `sources` id k):
- level = dBFS of each audio block (32 ms);
- floor = follows the level down quickly and up slowly (`floor_rise_db_per_s`), so steady noise (a fan, HVAC,
  a crowd's hum) becomes the floor and never fires;
- onset = the level stays `margin_db` over the floor for `min_s` (debounce); `t` is when it first crossed;
- then nothing until the level falls back under floor + `rearm_db` and `cooldown_s` has passed (a ding-dong or
  a sustained cry is one candidate; the classifier merges repeats anyway).

It runs on the audio the pipeline already gets, so a fixture replay and the live array produce the same
candidates. Speech onsets are candidates too, flagged `speech=True` (the array heard a talker at that moment):
the speech pipeline owns them, the classifier may skip them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from laptop.audio.base import energy_from_dbfs


@dataclass
class OnsetCandidate:
    t: float                         # laptop clock, when the level first crossed floor + margin
    bearing_deg: Optional[float]     # world frame, the stream's track (None when nothing is known yet)
    energy: float                    # 0..1, the onset level (same mapping as sources.energy)
    source_id: int                   # the stream / `sources` id
    level_db: float = -120.0         # dBFS of the loudest block in the debounce window
    rise_db: float = 0.0             # level over the floor
    speech: bool = False             # the array flagged a talker on this stream at t


def block_dbfs(pcm: np.ndarray) -> float:
    if len(pcm) == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(pcm.astype(np.float32))))) / 32768.0
    return 20 * math.log10(max(rms, 1e-6))


class _Stream:
    def __init__(self):
        self.floor: Optional[float] = None
        self.first_t: Optional[float] = None
        self.last_t: Optional[float] = None
        self.above_since: Optional[float] = None
        self.peak = -120.0
        self.armed = True
        self.last_onset = -math.inf


class OnsetDetector:
    def __init__(self, margin_db=10.0, min_s=0.06, cooldown_s=1.5, rearm_db=4.0, floor_rise_db_per_s=1.5,
                 floor_fall=0.3, energy_dbfs_low=-60.0, energy_dbfs_high=-15.0, min_level_dbfs=-65.0, warmup_s=0.5):
        self.margin_db = margin_db
        self.min_s = min_s
        self.cooldown_s = cooldown_s
        self.rearm_db = rearm_db
        self.floor_rise = floor_rise_db_per_s
        self.floor_fall = floor_fall                 # fraction of the way down per block when quieter
        self.energy_range = (energy_dbfs_low, energy_dbfs_high)
        self.min_level = min_level_dbfs              # quieter than this is never an onset (digital silence, hiss)
        self.warmup_s = warmup_s                     # no onsets while a new stream's floor settles
        self.streams: dict[int, _Stream] = {}
        self.listeners: list[Callable[[OnsetCandidate], None]] = []
        self.where: Callable[[int, float], tuple[Optional[float], bool]] = lambda stream, t: (None, False)
        self.count = 0
        self.recent: list[OnsetCandidate] = []       # the last few, for the dashboard

    @classmethod
    def from_config(cls, a: dict):
        c = dict(a.get("onsets", {}) or {})
        c.setdefault("energy_dbfs_low", a.get("energy_dbfs_low", -60.0))
        c.setdefault("energy_dbfs_high", a.get("energy_dbfs_high", -15.0))
        keys = ("margin_db", "min_s", "cooldown_s", "rearm_db", "floor_rise_db_per_s", "floor_fall",
                "energy_dbfs_low", "energy_dbfs_high", "min_level_dbfs", "warmup_s")
        return cls(**{k: c[k] for k in keys if k in c})

    def subscribe(self, fn: Callable[[OnsetCandidate], None]) -> None:
        """fn(candidate) is called on the event loop thread for every onset, on every stream."""
        self.listeners.append(fn)

    def feed(self, stream: int, t: float, pcm: np.ndarray) -> Optional[OnsetCandidate]:
        """One audio block of one stream (t = capture time of its first sample). Returns the onset, if any."""
        st = self.streams.setdefault(stream, _Stream())
        level = block_dbfs(pcm)
        dt = 0.0 if st.last_t is None else max(0.0, t - st.last_t)
        st.last_t = t
        if st.floor is None:
            st.floor, st.first_t = level, t
            return None
        over = level - st.floor
        found = None
        if over >= self.margin_db and level >= self.min_level:
            if st.above_since is None:
                st.above_since, st.peak = t, level
            st.peak = max(st.peak, level)
            if st.armed and st.above_since - st.first_t >= self.warmup_s and t + len(pcm) / 16000.0 - st.above_since >= self.min_s - 1e-9 \
                    and st.above_since - st.last_onset >= self.cooldown_s:
                found = self._fire(stream, st)
        else:
            st.above_since = None
            if over < self.rearm_db:
                st.armed = True
        # The floor: down fast, up slowly (a sustained sound becomes the floor only after a while).
        if level < st.floor:
            st.floor += (level - st.floor) * self.floor_fall
        else:
            st.floor = min(level, st.floor + self.floor_rise * dt)
        return found

    def _fire(self, stream: int, st: _Stream) -> OnsetCandidate:
        st.armed = False
        st.last_onset = st.above_since
        bearing, speech = self.where(stream, st.above_since)
        cand = OnsetCandidate(t=round(st.above_since, 3), bearing_deg=bearing,
                              energy=round(energy_from_dbfs(st.peak, *self.energy_range), 2), source_id=stream,
                              level_db=round(st.peak, 1), rise_db=round(st.peak - st.floor, 1), speech=speech)
        self.count += 1
        self.recent = (self.recent + [cand])[-5:]
        for fn in self.listeners:
            fn(cand)
        return cand

"""Source state between the direction input and the wire `sources` (README §12.5, K1 B).

The direction input (ReSpeaker beams / DoA, ODAS, a fixture) reports raw readings every tick. `SourceTracker`
turns them into what the phone sees:

- **stable ids**: the input's id is the track (beams mode: 0 = front beam, 1 = rear beam; ODAS: its track id),
  and a track survives brief silences (`hold_s`) under the same id;
- **no lag**: a track's bearing snaps to a reading more than `snap_deg` away (a new talker shows on the first
  frame), and is smoothed (EMA `alpha`) only for small wobble;
- **expiry**: a track that hasn't been active for `hold_s` leaves the list (an empty list = nothing heard);
- **overlap**: two or more tracks active within `overlap_window_s`;
- **spread**: circular spread of a track's recent active bearings (internal; the contract `Source` has no field).

Active = the input flags speech, or (non-speech) `nonspeech`:
- "onsets" (default): an onset candidate on that stream (laptop/events/onsets.py, `note_onset`) within
  `nonspeech_hold_s`: a distinct sound starting. A loud, fluctuating room (measured: both beams 0.3-0.8 for
  minutes) crosses any level threshold all the time; onsets don't;
- "energy": the track's energy `active_margin` over its own noise floor (a slow minimum tracker), so steady
  noise never keeps a track alive;
- "off": speech only.
A non-speech reading `leak_margin` quieter than another active one on the same tick is that
source leaking into a second beam (fixed beams leak ~10 dB), not a source of its own. Only the wire list goes through here: `DirectionHistory` keeps the raw readings for speech and
sounds, so this never changes captions or sound bearings. Fixture replays re-run the tracker on the recorded
raw readings, so `--replay` sends exactly what the live array sent.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from contracts.python.here_contract import Source
from laptop.geometry import angdiff, circular_mean


@dataclass
class Track:
    id: int
    bearing: float
    energy: float = 0.0
    confidence: float = 0.0
    speech: bool = False
    active: bool = False
    first_t: float = 0.0
    last_active_t: float = -math.inf
    floor: float = math.inf
    floor_t: float = 0.0
    recent: deque = field(default_factory=lambda: deque(maxlen=10))   # (bearing, confidence) of active readings

    @property
    def spread(self) -> float:
        if len(self.recent) < 2:
            return 0.0
        return circular_mean([b for b, _ in self.recent], [max(c, 0.05) for _, c in self.recent])[1]


def _wrap(deg: float) -> float:
    b = round(((deg + 180.0) % 360.0) - 180.0, 1)
    return 180.0 if b <= -180.0 else b


class SourceTracker:
    def __init__(self, hold_s=1.5, snap_deg=25.0, alpha=0.5, active_margin=0.12, floor_rise_per_s=0.02,
                 overlap_window_s=0.3, leak_margin=0.15, nonspeech="onsets", nonspeech_hold_s=1.0,
                 ambience_window_s=30.0, ambience_max=4):
        self.hold_s = hold_s
        self.snap_deg = snap_deg
        self.alpha = alpha
        self.active_margin = active_margin
        self.floor_rise = floor_rise_per_s
        self.overlap_window_s = overlap_window_s
        self.leak_margin = leak_margin
        self.nonspeech = nonspeech
        self.nonspeech_hold_s = nonspeech_hold_s
        self.onset_t: dict[int, float] = {}          # source id -> newest onset candidate time that counted
        # A busy room (chatter, music) throws an onset every few seconds on every beam (real array 2026-10-10:
        # 125 in 5 min, both beams listed 93 % of the time). More than ambience_max in ambience_window_s on a
        # stream = ambience: those onsets don't make a track (A2 still gets every candidate).
        self.ambience_window_s, self.ambience_max = ambience_window_s, ambience_max
        self.onset_times: dict[int, list[float]] = {}
        self.tracks: dict[int, Track] = {}
        self.last_t = -math.inf

    @classmethod
    def from_config(cls, a: dict):
        c = a.get("tracker", {}) or {}
        return cls(**{k: c[k] for k in ("hold_s", "snap_deg", "alpha", "active_margin", "floor_rise_per_s",
                                         "overlap_window_s", "leak_margin", "nonspeech", "nonspeech_hold_s",
                                         "ambience_window_s", "ambience_max") if k in c})

    def note_onset(self, source_id: int, t: float) -> None:
        """An onset candidate on this stream / id (OnsetDetector subscriber): a non-speech track for a while."""
        recent = [x for x in self.onset_times.get(source_id, []) if t - self.ambience_window_s < x <= t]
        self.onset_times[source_id] = recent + [t]
        if len(recent) >= self.ambience_max:
            return                                   # ambience, not a sound starting
        self.onset_t[source_id] = max(t, self.onset_t.get(source_id, -math.inf))

    # ---- update -------------------------------------------------------------------

    def update(self, t: float, readings: list[Source]) -> list[Source]:
        """Feed one tick of raw readings; returns the wire list (live tracks, by id)."""
        dt = max(0.0, t - self.last_t) if self.last_t > -math.inf else 0.0
        self.last_t = t
        for s in readings:
            tr = self.tracks.get(s.id)
            if tr is None:
                tr = self.tracks[s.id] = Track(s.id, s.bearing_deg, first_t=t, floor_t=t)
            # Noise floor: follows the energy down at once, creeps up slowly (steady noise becomes the floor).
            # 0 = no level measured yet (the first DoA poll can come before the first audio block): no floor.
            if s.energy > 0:
                tr.floor = min(s.energy, tr.floor + self.floor_rise * dt) if tr.floor < math.inf else s.energy
            tr.energy, tr.speech = s.energy, s.speech
            if self.nonspeech == "energy":
                loud = tr.floor < math.inf and s.energy >= tr.floor + self.active_margin
            elif self.nonspeech == "onsets":
                loud = 0.0 <= t - self.onset_t.get(s.id, -math.inf) <= self.nonspeech_hold_s
            else:
                loud = False
            tr.active = s.speech or loud
        loudest = {s.id: max((o.energy for o in readings if o.id != s.id and self.tracks[o.id].active), default=0.0)
                   for s in readings}
        for s in readings:
            tr = self.tracks[s.id]
            if tr.active and not s.speech and s.energy < loudest[s.id] - self.leak_margin:
                tr.active = False                                  # another beam's source leaking in
            if tr.active:
                if t - tr.last_active_t > self.hold_s:             # (re)appearing: start where it is
                    tr.bearing, tr.first_t = s.bearing_deg, t
                    tr.recent.clear()
                d = angdiff(s.bearing_deg, tr.bearing)
                tr.bearing = s.bearing_deg if abs(d) > self.snap_deg else _wrap(tr.bearing + self.alpha * d)
                tr.confidence = s.confidence
                tr.last_active_t = t
                tr.recent.append((s.bearing_deg, s.confidence))
            else:
                tr.confidence = s.confidence if s.speech else 0.0
        seen = {s.id for s in readings}
        for tr in self.tracks.values():                            # ids the input stopped reporting
            if tr.id not in seen:
                tr.active, tr.speech = False, False
        return self.live(t)

    def live(self, t: float) -> list[Source]:
        out = []
        for tr in sorted(self.tracks.values(), key=lambda x: x.id):
            if t - tr.last_active_t <= self.hold_s:
                out.append(Source(id=tr.id, bearing_deg=_wrap(tr.bearing), energy=round(tr.energy, 2),
                                  speech=tr.speech, confidence=round(tr.confidence, 2)))
        return out

    # ---- state --------------------------------------------------------------------

    @property
    def active(self) -> bool:
        """Anything active on this tick (the throttle sends every frame while true)."""
        return any(tr.active for tr in self.tracks.values())

    @property
    def overlap(self) -> bool:
        """Two or more tracks active within overlap_window_s of the newest tick."""
        return sum(1 for tr in self.tracks.values() if self.last_t - tr.last_active_t <= self.overlap_window_s) >= 2

    def spread(self, track_id: int) -> float:
        tr = self.tracks.get(track_id)
        return round(tr.spread, 1) if tr else 0.0

    def track(self, track_id: int):
        return self.tracks.get(track_id)

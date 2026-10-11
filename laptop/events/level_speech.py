"""A level-based speech flag for the fixed beams, next to the firmware's (config `audio.level_speech`, off by default).

In a loud room the XVF3800's per-beam speech flag can miss a talker (fixtures/k1_room_live1: a laptop speaker at
0-5 dB SNR got flagged on 6-11 % of frames), and with `speech.endpoint_use_doa` nothing is captioned without it.
This flags a beam as speech while its level stands well over its own noise floor AND over the other beam:

- the floor follows the beam's quietest level (down at once, up `floor_rise_per_s`), so a steady crowd, fan or
  music bed becomes the floor and never opens it;
- the other-beam rule means diffuse babble (about equal on both beams) and a talker's leak into the opposite beam
  (quieter there) don't open it either: only the beam the sound is louder on.

It only adds flags (speech=True, `confidence`) to readings the firmware left unflagged, for `hold_s` after the
level last qualified. Applied to the raw readings in `app.on_sources`; the recorder keeps the firmware's readings,
so a replay re-runs it and sends the same messages as the live run.
"""

from __future__ import annotations

from dataclasses import replace


class LevelSpeech:
    def __init__(self, margin=0.2, separation=0.05, hold_s=0.4, confidence=0.5, floor_rise_per_s=0.02):
        self.margin, self.separation = margin, separation
        self.hold_s, self.confidence = hold_s, confidence
        self.floor_rise_per_s = floor_rise_per_s
        self.floor: dict[int, float] = {}
        self.last_t: dict[int, float] = {}       # last time each beam qualified
        self.prev_t = None
        self.added = 0                           # readings flagged here (dashboard / tests)

    @classmethod
    def from_config(cls, audio_cfg):
        c = dict(audio_cfg.get("level_speech") or {})
        if not c.pop("enabled", False):
            return None
        return cls(**c)

    def update(self, t, sources):
        """sources: one beams-mode reading (ids 0 and 1). Returns it with level-based speech flags added."""
        dt = 0.0 if self.prev_t is None else max(0.0, t - self.prev_t)
        self.prev_t = t
        if len(sources) != 2:
            return sources
        for s in sources:
            f = self.floor.get(s.id)
            self.floor[s.id] = s.energy if f is None or s.energy < f else min(s.energy, f + self.floor_rise_per_s * dt)
        out = []
        for s, other in ((sources[0], sources[1]), (sources[1], sources[0])):
            if s.energy >= self.floor[s.id] + self.margin and s.energy >= other.energy + self.separation:
                self.last_t[s.id] = t
            if not s.speech and t - self.last_t.get(s.id, -1e9) <= self.hold_s:
                s = replace(s, speech=True, confidence=max(s.confidence, self.confidence))
                self.added += 1
            out.append(s)
        return out

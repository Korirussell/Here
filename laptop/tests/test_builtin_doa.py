"""ReSpeaker glue without hardware: raw buffer -> SRP -> world bearing, and DoA sample -> contract sources.

Ported from v1 tests/test_audio_here_events.py (sound_event -> sound_doa); same scenarios, v3 API.
"""

import numpy as np
import pytest

from contracts.python import here_contract as hc
from laptop.audio import config as C
from laptop.audio.builtin_doa import ReSpeaker
from laptop.audio.localize import EventBearings, RawLocator
from laptop.geometry import angdiff
from laptop.tests.test_localize import FS, MICS, simulate


def make_respeaker(offset=0.0, ccw=False, beams_as_sources=False):
    a = ReSpeaker.__new__(ReSpeaker)          # skip __init__: it opens the USB device and audio input
    a.offset, a.ccw, a.beams_as_sources = offset, ccw, beams_as_sources
    a.raw = RawLocator(MICS, FS, seconds=12, order=[0, 1, 2, 3])
    a.srp = a.raw.srp
    a.located = EventBearings()
    a.ring = None
    return a


@pytest.fixture(autouse=True)
def mapping(monkeypatch):
    monkeypatch.setattr(C, "AEC_TO_DEVICE_OFFSET_DEG", 0.0)
    monkeypatch.setattr(C, "AEC_TO_DEVICE_SIGN", 1)


def fill(a, t0, x):
    for i in range(0, len(x), 1600):
        a.raw.add(t0 + i / FS, x[i:i + 1600].astype(np.int16))


def test_sound_gets_world_bearing():
    a = make_respeaker(offset=10.0)
    src = np.random.default_rng(3).standard_normal(FS * 2) * 3000
    x = np.concatenate([np.zeros((FS * 2, 4)), simulate(src, 100.0), np.zeros((FS, 4))])
    fill(a, 1000.0, x)
    bearing, conf = a.locate(1002.0, 1004.0, "doorbell")
    assert abs(angdiff(bearing, 90.0)) <= 3        # device 100 deg, array offset 10 deg
    assert conf > 0.5


def test_speech_and_rapid_repeats_are_skipped():
    a = make_respeaker()
    fill(a, 1000.0, simulate(np.random.default_rng(4).standard_normal(FS * 3) * 3000, 45.0))
    assert a.locate(1000.5, 1002.0, "speech") is None
    first = a.locate(1000.5, 1002.0, "dog_barking")
    assert first is not None
    assert a.locate(1000.5, 1002.5, "dog_barking") == first   # same event within RELOCATE_MIN_S: not re-run, same bearing


def test_sound_outside_buffer_is_ignored():
    a = make_respeaker()
    fill(a, 1000.0, np.zeros((FS, 4)))
    assert a.locate(900.0, 901.0, "knock") is None


def test_no_raw_mics_means_no_location():
    a = make_respeaker()
    a.srp = None
    assert a.locate(0, 1, "doorbell") is None


def test_beams_in_world_frame():
    a = make_respeaker(offset=20.0)
    s = {"beam_az_deg": [50.0, 200.0, 0.0, 0.0], "beam_energy": [1000.0, 0.0, 0.0, 0.0]}
    b = a.beams(s)
    assert b[0] == {"bearing_deg": 30.0, "speech": True, "energy_db": 30.0}
    assert b[1]["bearing_deg"] == 180.0 and b[1]["speech"] is False and b[1]["energy_db"] == 0.0


def doa_sample(doa_deg=40, speech=True, beams=((40.0, 1e4), (200.0, 1e4))):
    return {"t": 1.0, "doa_deg": doa_deg, "speech": speech,
            "beam_az_deg": [b[0] for b in beams] + [0.0, 0.0], "beam_energy": [b[1] for b in beams] + [0.0, 0.0]}


def test_sources_is_one_world_source_by_default():
    a = make_respeaker(offset=10.0)
    (src,) = a.sources_for(doa_sample(), confidence=0.8, energy=0.6)
    assert src == hc.Source(id=0, bearing_deg=30.0, energy=0.6, speech=True, confidence=0.8)
    msg = hc.Sources(src="laptop", t=1.0, sources=[src]).to_dict()
    assert hc.errors(msg) == []


def test_second_beam_as_source_when_enabled_and_far_apart():
    a = make_respeaker(beams_as_sources=True)
    sources = a.sources_for(doa_sample(), confidence=0.8, energy=0.6)
    assert [s.id for s in sources] == [0, 1]
    assert sources[1].bearing_deg == -160.0 and sources[1].speech
    near = a.sources_for(doa_sample(beams=((40.0, 1e4), (55.0, 1e4))), 0.8, 0.6)
    assert len(near) == 1                      # a beam within 30 deg of the main DoA is the same talker


def test_ccw_device_maps_to_clockwise_world():
    a = make_respeaker(offset=0.0, ccw=True)
    (src,) = a.sources_for(doa_sample(doa_deg=90), 0.5, 0.5)
    assert src.bearing_deg == -90.0


def ring_respeaker(fixed=True, main="doa", second="auto"):
    from laptop.audio.ring import RingPlanner

    class Marks:
        def __init__(self):
            self.marks = {}

        def show(self, key, deg, color, level=1.0, hold_s=None):
            self.marks[key] = round(deg)

        def hide(self, key):
            self.marks.pop(key, None)

    a = make_respeaker()
    a.ring, a.beams_fixed = Marks(), fixed
    a.planner = RingPlanner(main=main, second=second, raw=main == "srp")
    return a


def sample_at(t, doa, beams=(0.0, 180.0), energies=(0.0, 5e5), fast=None):
    fast_az, fast_e = (doa, 5e5) if fast is None else fast
    return {"t": t, "doa_deg": doa, "speech": True, "beam_az_deg": [*beams, fast_az, doa],
            "beam_energy": [*energies, fast_e, 5e5]}


def test_ring_never_draws_a_fixed_beam_as_a_source():
    # Live bug: with fixed beams [0, 180] the second marker sat on the front beam's LED whenever it leaked.
    a = ring_respeaker(fixed=True)
    a.draw(sample_at(1.0, 177, energies=(4e4, 5e5)), 0.8)
    assert a.ring.marks == {"doa": 177}


def test_ring_second_marker_follows_odas_tracks():
    a = ring_respeaker(fixed=True)
    for t in (0.9, 0.95):                                   # the other talker alone first: it led
        a.set_ring_tracks(t, [hc.Source(id=5, bearing_deg=-90.0, energy=0.6, speech=True, confidence=0.6)])
        a.draw(sample_at(t, 0), 0.8)
    a.set_ring_tracks(1.0, [hc.Source(id=3, bearing_deg=0.0, energy=0.9, speech=True, confidence=0.9),
                            hc.Source(id=5, bearing_deg=-90.0, energy=0.6, speech=True, confidence=0.6),
                            hc.Source(id=6, bearing_deg=135.0, energy=0.2, speech=False, confidence=0.2)])
    a.draw(sample_at(1.05, 0), 0.8)
    assert a.ring.marks == {"doa": 0, "beam2": 270}         # ODAS track 3 is the main talker; 5 is the other one


def test_ring_second_marker_from_auto_tracking_beams():
    a = ring_respeaker(fixed=False)
    a.draw(sample_at(1.0, 30, beams=(30.0, 200.0), energies=(5e5, 3e5)), 0.8)
    assert a.ring.marks == {"doa": 30, "beam2": 200}


def test_ring_main_marker_from_srp_ignores_doa_value_pinned_to_a_fixed_beam():
    # Live: with fixed beams DOA_VALUE names a beam (here 0) while the sound is at 100; SRP and the fast beam agree.
    a = ring_respeaker(fixed=True, main="srp")
    a.planner.set_srp(1.0, [(1, 100.0, 0.5)])
    a.draw(sample_at(1.0, 0, fast=(102.0, 5e5)), 0.8)
    assert a.ring.marks == {"doa": 100}


def test_one_sound_event_keeps_one_bearing():
    """Real array 2026-10-10: one doorbell press read 28.8 / 27.8 / 0.8 / 24.8 on the wire. The 0.8s were updates
    locate() couldn't localize (asked again within RELOCATE_MIN_S, low coherence): it returned None and the sound
    pipeline fell back to the beam's bearing. Now every update of an event gets that event's median bearing."""
    a = make_respeaker()
    found = iter([(28.8, 28.8, 0.6), (24.8, 24.8, 0.5), (27.8, 27.8, 0.05), (26.0, 26.0, 0.7), None])
    a.raw.locate = lambda t0, t1, offset, ccw: next(found)
    clock = [1000.0]
    import laptop.audio.builtin_doa as B
    real_time = B.time.time
    B.time.time = lambda: clock[0]
    try:
        out = []
        for dt in (0.0, 0.6, 0.6, 0.1, 0.6, 0.6):     # the 4th update comes 0.1 s after the 3rd: rate-limited
            clock[0] += dt
            out.append(a.locate(10.0, 11.0, "doorbell", key="s-19"))
    finally:
        B.time.time = real_time
    bearings = [b for b, _ in out]
    assert bearings[0] == 28.8
    assert all(b is not None for b in bearings)                 # never None once the event has a direction
    assert all(abs(b - 26.0) <= 3.0 for b in bearings[1:])      # one stable bearing, nowhere near the beam's 0.8
    assert bearings[2] == bearings[1] and bearings[3] == bearings[2]   # low coherence / rate limit: unchanged

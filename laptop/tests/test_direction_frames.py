"""Every path from array data to a WORLD bearing (README §8: clockwise, 0 = array front, (-180, 180]) gives the
same answer for the same physical source, for array_clockwise false AND true, so a mirrored path can't come
back silently (2026-10-11: recalibration flipped array_clockwise true -> false; left/right had been swapped).

A physical array is modelled once (`Array`): where a source at world bearing w shows up as the device's DOA_VALUE
and in the mic-geometry (AEC) frame the beams and SRP-PHAT use. The config under test is set to describe that
array; then each path must read w back:

  device DoA       DOA_VALUE -> xvf3800.to_world                         (ReSpeaker.sources_for)
  beams            AEC_AZIMUTH_VALUES -> aec_to_device -> to_world        (ReSpeaker.beam_sources)
  fixed beams      world -> AEC azimuth written to the array              (ReSpeaker.set_fixed_beams)
  sound SRP        raw mics -> SRP-PHAT -> aec_to_device -> to_world      (RawLocator.locate)
  speech SRP       same, restricted to a fixed beam's half of the room   (SpeechBearings / locate_within)
  replay           a fixture recorded under one calibration, replayed while the live config holds another
  phone            wrap(world - heading) -> belt motor (README §8; mirrors ios/HERE/Geometry/Geometry.swift)
"""

import math

import numpy as np
import pytest

from laptop.audio import config as C
from laptop.audio.builtin_doa import ReSpeaker
from laptop.audio.localize import XVF3800_GEOMETRY, RawLocator, SpeechBearings, world_to_aec
from laptop.audio.xvf3800 import to_world
from laptop.geometry import angdiff, motor_for, wrap

FS = 16000
WORLD = [0.0, 90.0, -90.0, 180.0, 45.0, -135.0]     # front, right, left, behind + two diagonals
TOL_DEVICE = 0.2                                     # pure arithmetic paths (to_world rounds to 0.1)
TOL_SRP = 6.0                                        # SRP-PHAT on a 66.6 mm square, 1 deg grid, noisy plane wave


class Array:
    """A physical XVF3800 as calibrated: DOA_VALUE = offset + (+w clockwise | -w counter-clockwise), and the
    AEC (mic-geometry) frame relates to DOA_VALUE by device = aec_offset + aec_sign * aec."""

    def __init__(self, clockwise, offset=356.9, aec_offset=356.3, aec_sign=1):
        self.clockwise, self.offset, self.aec_offset, self.aec_sign = clockwise, offset, aec_offset, aec_sign

    def device(self, w):
        return ((w if self.clockwise else -w) + self.offset) % 360.0

    def aec(self, w):
        return ((self.device(w) - self.aec_offset) * self.aec_sign) % 360.0

    def configure(self, monkeypatch):
        """The live config describes this array (what `python -m laptop.audio.calibrate` writes)."""
        monkeypatch.setattr(C, "ARRAY_OFFSET_DEG", self.offset)
        monkeypatch.setattr(C, "ARRAY_CLOCKWISE", self.clockwise)
        monkeypatch.setattr(C, "AEC_TO_DEVICE_OFFSET_DEG", self.aec_offset)
        monkeypatch.setattr(C, "AEC_TO_DEVICE_SIGN", self.aec_sign)

    def meta(self):
        return {"array_offset_deg": self.offset, "array_clockwise": self.clockwise,
                "aec_to_device_offset_deg": self.aec_offset, "aec_to_device_sign": self.aec_sign}


def plane_wave(az_deg, seconds=1.0, seed=0, snr_db=25.0):
    """A far-field noise burst from az_deg in the AEC frame on XVF3800_GEOMETRY, columns in geometry order."""
    rng = np.random.default_rng(seed)
    n = int(seconds * FS)
    S = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / FS)
    u = np.array([math.cos(math.radians(az_deg)), math.sin(math.radians(az_deg))])
    x = np.stack([np.fft.irfft(S * np.exp(2j * np.pi * f * np.dot(u, m[:2]) / C.SPEED_OF_SOUND), n)
                  for m in XVF3800_GEOMETRY], axis=1)
    x = x + rng.standard_normal(x.shape) * np.std(x) * 10 ** (-snr_db / 20)
    return x / np.max(np.abs(x)) * 8000


def usb_order(geo, order):
    """What USB delivers when geometry mic i is raw column order[i] (the inverse of localize.in_geometry_order)."""
    raw = np.zeros_like(geo)
    raw[:, order] = geo
    return raw.astype(np.int16)


def respeaker(arr):
    r = ReSpeaker.__new__(ReSpeaker)          # skip __init__: no USB
    r.offset, r.ccw = arr.offset, not arr.clockwise
    r.beams_as_sources, r.beam_held = False, [(0.0, 0.0), (0.0, 0.0)]
    return r


@pytest.fixture(params=[False, True], ids=["ccw_array", "cw_array"])
def clockwise(request):
    return request.param


# ---- device DoA and beams (arithmetic) --------------------------------------------------------------------

@pytest.mark.parametrize("aec_sign", [1, -1])
@pytest.mark.parametrize("w", WORLD)
def test_device_doa_and_beams_read_the_true_world_bearing(monkeypatch, clockwise, aec_sign, w):
    arr = Array(clockwise, aec_sign=aec_sign)
    arr.configure(monkeypatch)
    r = respeaker(arr)
    s = {"t": 1.0, "doa_deg": round(arr.device(w)), "speech": True,
         "beam_az_deg": [arr.aec(w), arr.aec(w + 120.0), 0.0, 0.0], "beam_energy": [2e6, 2e6, 0.0, 0.0]}
    (doa,) = r.sources_for(s, 0.8, 0.5)
    assert abs(angdiff(doa.bearing_deg, w)) <= 0.6, "device DoA path (DOA_VALUE is whole degrees)"
    b0, b1 = r.beam_sources(s, 0.5)
    assert abs(angdiff(b0.bearing_deg, w)) <= TOL_DEVICE, "beam path"
    assert abs(angdiff(b1.bearing_deg, wrap(w + 120.0))) <= TOL_DEVICE, "beam path (second beam)"


class FakeUsb:
    def __init__(self):
        self.writes = {}

    def write(self, name, *values):
        self.writes[name] = values


@pytest.mark.parametrize("aec_sign", [1, -1])
def test_fixed_beams_point_where_they_are_told(monkeypatch, clockwise, aec_sign):
    arr = Array(clockwise, aec_sign=aec_sign)
    arr.configure(monkeypatch)
    r = respeaker(arr)
    r.array, r.problems = FakeUsb(), []
    r.set_fixed_beams([90.0, -90.0])                  # right, left: the case a mirror gets wrong
    written = [math.degrees(a) % 360.0 for a in r.array.writes["AEC_FIXEDBEAMSAZIMUTH_VALUES"]]
    assert abs(angdiff(written[0], arr.aec(90.0))) <= 0.01 and abs(angdiff(written[1], arr.aec(-90.0))) <= 0.01
    # ...and the beam the array then reports reads back as the bearing it was told
    s = {"t": 1.0, "beam_az_deg": written + [0.0, 0.0], "beam_energy": [2e6, 2e6, 0.0, 0.0]}
    b0, b1 = r.beam_sources(s, 0.5)
    assert abs(angdiff(b0.bearing_deg, 90.0)) <= TOL_DEVICE and abs(angdiff(b1.bearing_deg, -90.0)) <= TOL_DEVICE


# ---- SRP-PHAT on the raw mics (sounds, fixed-beam speech) ------------------------------------------------

@pytest.mark.parametrize("order", [[0, 1, 2, 3], [2, 3, 0, 1]], ids=["identity", "rotated_usb"])
@pytest.mark.parametrize("w", [0.0, 90.0, -90.0, 180.0])
def test_sound_srp_reads_the_true_world_bearing(monkeypatch, clockwise, order, w):
    arr = Array(clockwise)
    arr.configure(monkeypatch)
    loc = RawLocator(XVF3800_GEOMETRY, FS, seconds=5, order=order)
    loc.add(100.0, usb_order(plane_wave(arr.aec(w)), order))
    bearing, device, conf = loc.locate(100.0, 101.0, arr.offset, not arr.clockwise)
    assert abs(angdiff(bearing, w)) <= TOL_SRP, f"SRP sound bearing {bearing} for a source at {w}"
    assert abs(angdiff(device, arr.device(w))) <= TOL_SRP, "SRP and the device DoA disagree: one is mirrored"
    assert conf > 0.5


@pytest.mark.parametrize("w,beam", [(30.0, 0.0), (-30.0, 0.0), (90.0, 0.0), (-90.0, 180.0), (150.0, 180.0),
                                    (-150.0, 180.0)])
def test_fixed_beam_speech_srp_reads_the_true_world_bearing(monkeypatch, clockwise, w, beam):
    arr = Array(clockwise)
    arr.configure(monkeypatch)
    loc = RawLocator(XVF3800_GEOMETRY, FS, seconds=5, order=[0, 1, 2, 3])
    loc.add(100.0, usb_order(plane_wave(arr.aec(w), seed=1), [0, 1, 2, 3]))
    sb = SpeechBearings(loc, arr.offset, not arr.clockwise, cfg={"enabled": True, "min_confidence": 0.3})
    got = sb.locate("u1", 100.0, 101.0, beam, now=1.0)
    assert got is not None, f"no speech bearing for a talker at {w} in beam {beam}'s half"
    assert abs(angdiff(got[0], w)) <= TOL_SRP, f"speech SRP {got[0]} for a talker at {w}"


def test_speech_srp_keeps_a_side_talker_on_their_side(monkeypatch, clockwise):
    """+-30 deg talkers both sit in the front beam; a mirrored speech path would swap them (the phone would put
    each caption on the other face)."""
    arr = Array(clockwise)
    arr.configure(monkeypatch)
    for w in (30.0, -30.0):
        loc = RawLocator(XVF3800_GEOMETRY, FS, seconds=5, order=[0, 1, 2, 3])
        loc.add(100.0, usb_order(plane_wave(arr.aec(w), seed=2), [0, 1, 2, 3]))
        got = SpeechBearings(loc, arr.offset, not arr.clockwise, cfg={"min_confidence": 0.3}).locate(
            "u", 100.0, 101.0, 0.0, now=1.0)
        assert got is not None and math.copysign(1, got[0]) == math.copysign(1, w)


# ---- replays: the recording's calibration, not tonight's ---------------------------------------------------

def write_raw_fixture(folder, arr, w):
    import json
    import wave

    folder.mkdir()
    raw = usb_order(plane_wave(arr.aec(w), seconds=2.0, seed=3), [0, 1, 2, 3])
    for name, x in (("raw.wav", raw), ("audio.wav", raw[:, :1])):
        with wave.open(str(folder / name), "wb") as f:
            f.setnchannels(x.shape[1])
            f.setsampwidth(2)
            f.setframerate(FS)
            f.writeframes(np.ascontiguousarray(x).tobytes())
    (folder / "sources.jsonl").write_text("")
    (folder / "meta.json").write_text(json.dumps(dict(arr.meta(), name=folder.name, raw_mic_order=[0, 1, 2, 3],
                                                      mic_geometry=[list(m) for m in XVF3800_GEOMETRY])))
    return raw


@pytest.mark.parametrize("w", [90.0, -90.0])
def test_replay_uses_the_recordings_calibration_not_the_live_one(tmp_path, monkeypatch, clockwise, w):
    """Recorded under one calibration, replayed after a recalibration that flipped array_clockwise AND the beam
    sign and moved the aec offset. Before 2026-10-11 FixturePlayer took offset/clockwise from meta.json but
    aec_to_device from the live config: a mixed frame (here: mirrored)."""
    from laptop.audio.mock import FixturePlayer

    recorded = Array(clockwise, offset=356.4, aec_offset=359.2, aec_sign=1)
    live = Array(not clockwise, offset=10.0, aec_offset=200.0, aec_sign=-1)
    live.configure(monkeypatch)
    monkeypatch.setattr(C, "RAW_MIC_ORDER", [0, 1, 2, 3], raising=False)
    raw = write_raw_fixture(tmp_path / "rec", recorded, w)
    player = FixturePlayer(tmp_path / "rec")
    player.raw.add(100.0, raw)
    bearing, _ = player.locate(100.0, 102.0, label="doorbell")
    assert abs(angdiff(bearing, w)) <= TOL_SRP, f"replayed sound bearing {bearing} for a source recorded at {w}"
    got = player.locate_speech("u1", 100.0, 101.0, 0.0)
    assert got is not None and abs(angdiff(got[0], w)) <= TOL_SRP, f"replayed speech bearing {got}"


def test_world_to_aec_is_the_inverse_for_any_calibration():
    for clockwise in (False, True):
        for sign in (1, -1):
            arr = Array(clockwise, aec_offset=123.0, aec_sign=sign)
            for w in WORLD:
                got = world_to_aec(w, arr.offset, not clockwise, (arr.aec_offset, arr.aec_sign))
                assert abs(angdiff(got, arr.aec(w))) < 1e-6


# ---- the live config and the calibration helper ------------------------------------------------------------

def test_live_config_is_not_mirrored_against_the_mic_geometry():
    """SRP / beam azimuths run counter-clockwise (atan2 in AEC_MIC_ARRAY_GEO, seen from the array's top); world
    bearings run clockwise. Face up, that needs (clockwise ? +1 : -1) * aec_to_device_sign == -1. The
    2026-10-11 recalibration (clockwise false, sign 1) passes; the swapped one before it (true, 1) did not.
    If the array is now deliberately mounted face down, update this test and calibrate.handedness_problem."""
    from laptop.audio.calibrate import handedness_problem

    assert handedness_problem(C.ARRAY_CLOCKWISE, C.AEC_TO_DEVICE_SIGN) is None, (
        f"laptop/config.yaml: array_clockwise {C.ARRAY_CLOCKWISE} with aec_to_device_sign {C.AEC_TO_DEVICE_SIGN} "
        "puts left-hand talkers on the right. Re-run python -m laptop.audio.calibrate --four-point.")


def test_handedness_check_flags_the_old_swapped_calibration():
    from laptop.audio.calibrate import handedness_problem

    assert handedness_problem(True, 1) is not None and handedness_problem(False, -1) is not None
    assert handedness_problem(False, 1) is None and handedness_problem(True, -1) is None


def test_four_point_check_catches_a_side_mixup():
    """The 'right' step done from the array's LEFT: the two-point fit is self-consistent (front 0, 'right' +90)
    but mirrored. Only the 4-point table shows it: the real right reads -90 and the real left +90."""
    from laptop.audio.calibrate import left_right_swapped, position_errors

    arr = Array(clockwise=False)
    front, wrong_right = arr.device(0.0), arr.device(-90.0)        # the helper stood on the left
    clockwise_fit = angdiff(wrong_right, front) > 0                  # calibrate.main's rule
    assert clockwise_fit is True                                     # the wrong handedness
    readings = [(name, w, arr.device(w)) for name, w in
                (("in FRONT", 0.0), ("to the RIGHT", 90.0), ("BEHIND", 180.0), ("to the LEFT", -90.0))]
    rows = position_errors(readings, front, clockwise_fit)
    errs = {name: err for name, _, _, err in rows}
    assert abs(errs["in FRONT"]) < 1 and abs(abs(errs["BEHIND"])) < 1     # front/back can't see a mirror
    assert abs(abs(errs["to the RIGHT"]) - 180) < 1 and left_right_swapped(rows)
    good = position_errors(readings, front, arr.clockwise)
    assert all(abs(err) < 1 for *_, err in good) and not left_right_swapped(good)


# ---- the phone's half (README §8; ios/HERE/Geometry/Geometry.swift relative + motor) -----------------------

@pytest.mark.parametrize("world,heading,motor", [(0.0, 0.0, 1), (90.0, 0.0, 3), (-90.0, 0.0, 7), (180.0, 0.0, 5),
                                                 (180.0, 90.0, 3), (0.0, 90.0, 7), (90.0, 90.0, 1),
                                                 (-90.0, -90.0, 1)])
def test_world_bearing_to_belt_motor(world, heading, motor):
    """Motors clockwise from the front: 1 front, 3 right, 5 back, 7 left. Wearer turned right (heading +90): what
    was behind is now on their right."""
    assert motor_for(wrap(world - heading)) == motor

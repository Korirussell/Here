"""SRP-PHAT localization on simulated 4-mic recordings (audio_here/localize.py)."""

import math

import numpy as np
import pytest

from laptop.audio.localize import RawBuffer, SrpPhat, fit_mapping, loudest_window
from laptop.geometry import angdiff

FS = 16000
# 4 mics on a 32 mm radius, like a small square array.
MICS = [(0.032 * math.cos(math.radians(a)), 0.032 * math.sin(math.radians(a)), 0.0) for a in (45, 135, 225, 315)]


def simulate(source, az_deg, snr_db=20.0, seed=0):
    """Far-field plane wave from az_deg (mic-geometry frame) plus independent mic noise."""
    rng = np.random.default_rng(seed)
    n = len(source)
    f = np.fft.rfftfreq(n, 1 / FS)
    S = np.fft.rfft(source)
    u = np.array([math.cos(math.radians(az_deg)), math.sin(math.radians(az_deg))])
    out = []
    for m in MICS:
        toa = -(np.dot(u, m[:2])) / 343.0
        out.append(np.fft.irfft(S * np.exp(-2j * np.pi * f * toa), n))
    x = np.stack(out, axis=1)
    noise = rng.standard_normal(x.shape) * np.std(x) * 10 ** (-snr_db / 20)
    return x + noise


@pytest.mark.parametrize("az", [0, 37, 90, 151, 180, 222, 270, 333])
def test_broadband_source(az):
    src = np.random.default_rng(1).standard_normal(FS)
    est, conf = SrpPhat(MICS).locate(simulate(src, az))
    assert abs(angdiff(est, az)) <= 3
    assert conf > 0.5


@pytest.mark.parametrize("az", [20, 110, 200, 290])
def test_doorbell_like_tones(az):
    t = np.arange(FS) / FS
    ding = (np.sin(2 * np.pi * 660 * t) + np.sin(2 * np.pi * 880 * t)) * np.exp(-3 * t)
    est, _ = SrpPhat(MICS).locate(simulate(ding, az, snr_db=15))
    assert abs(angdiff(est, az)) <= 6


def test_noise_only_has_low_confidence():
    x = np.random.default_rng(2).standard_normal((FS, 4))
    _, conf = SrpPhat(MICS).locate(x)
    assert conf < 0.15


def test_too_short_returns_none():
    assert SrpPhat(MICS).locate(np.zeros((100, 4))) == (None, 0.0)


def test_raw_buffer_segment_by_time():
    buf = RawBuffer(fs=FS, seconds=5)
    for k in range(10):                            # ten 100 ms blocks; sample value = block index
        buf.add(1000.0 + k * 0.1, np.full((1600, 4), k, np.int16))
    seg = buf.segment(1000.25, 1000.45)
    assert len(seg) == 3200
    assert seg[0, 0] == 2 and seg[-1, 0] == 4
    assert buf.segment(990.0, 999.0) is None


def test_loudest_window_finds_burst():
    x = np.zeros((5 * FS, 4))
    x[3 * FS: 3 * FS + FS // 2] = 1.0
    w = loudest_window(x, FS, 1.0)
    assert len(w) == FS and w.sum() == pytest.approx(FS // 2 * 4)


@pytest.mark.parametrize("offset,sign", [(30.0, 1), (200.0, -1)])
def test_fit_mapping(offset, sign):
    pairs = [((offset + sign * a) % 360, a) for a in range(0, 360, 20)]
    off, s, std = fit_mapping(pairs)
    assert s == sign and abs(angdiff(off, offset)) < 0.5 and std < 1


def test_peaks_top_two_separated():
    from laptop.audio.localize import peaks
    az = np.arange(0.0, 360.0, 1.0)
    p = 0.8 * np.exp(-0.5 * ((az - 90) / 8) ** 2) + 0.6 * np.exp(-0.5 * ((az - 250) / 8) ** 2)
    got = peaks(p, az, k=2, min_sep_deg=30, rel=0.6, floor=0.15)
    assert [round(a) for a, _ in got] == [90, 250]
    assert peaks(p * 0.1, az, floor=0.15) == []


def test_srp_tracker_birth_gating_and_death():
    from laptop.audio.localize import SrpTracker
    tr = SrpTracker(gate_deg=20, birth_hits=3, death_s=1.0)
    assert tr.update(0.0, [(100.0, 0.5)]) == [] and tr.update(0.1, [(104.0, 0.5)]) == []
    born = tr.update(0.2, [(102.0, 0.5), (300.0, 0.4)])
    assert len(born) == 1 and born[0][0] == 1 and abs(born[0][1] - 102) < 5
    assert tr.update(0.3, [(110.0, 0.5)])[0][0] == 1          # same id within the gate
    assert tr.update(2.0, []) == [] and tr.tracks == []       # died

"""Fixed beams: speech bearings from the raw mics (localize.SpeechBearings) and what the pipeline sends with them."""

import asyncio
import math

import numpy as np
import pytest

from contracts.python.here_contract import Source
from laptop.audio.base import DirectionHistory
from laptop.audio.localize import RawLocator, SpeechBearings, XVF3800_GEOMETRY, world_to_aec
from laptop.geometry import angdiff
from laptop.settings import load
from laptop.speech.pipeline import SpeechPipeline
from laptop.speech.transcriber import Hypothesis

CFG = load()
FS = 16000
OFFSET, CCW = 0.0, False
FRONT, REAR = 0.8, -179.2


def plane_wave(src, world_deg):
    """Far-field source at a world bearing on the XVF3800's square (raw columns in geometry = USB order)."""
    az = math.radians(world_to_aec(world_deg, OFFSET, CCW))
    n = len(src)
    f = np.fft.rfftfreq(n, 1 / FS)
    S = np.fft.rfft(src)
    u = np.array([math.cos(az), math.sin(az)])
    return np.stack([np.fft.irfft(S * np.exp(2j * np.pi * f * np.dot(u, m[:2]) / 343.0), n)
                     for m in XVF3800_GEOMETRY], axis=1)


def speechy(seconds, seed):
    """Broadband bursts (syllables) of noise: speech-like enough for SRP."""
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(int(seconds * FS))
    env = (np.sin(2 * np.pi * 4 * np.arange(len(x)) / FS + seed * math.pi) > -0.3).astype(float)   # odd/even
    # seeds take turns (two people talking over each other still leave each other gaps)
    return x * env


def locator(*sources, seconds=3.0, noise=0.05):
    """RawLocator holding `seconds` of the given (world bearing, level, seed) talkers plus mic noise, from t=0."""
    x = sum((level * plane_wave(speechy(seconds, seed), b) for b, level, seed in sources),
            np.zeros((int(seconds * FS), 4)))
    x = x + noise * np.random.default_rng(9).standard_normal(x.shape)
    raw = np.clip(np.round(x * 3000), -32768, 32767).astype(np.int16)
    loc = RawLocator(XVF3800_GEOMETRY, FS, seconds=60, order=[0, 1, 2, 3])
    for s in range(0, len(raw), 512):
        loc.add(s / FS, raw[s:s + 512])
    return loc


def test_front_talker_off_the_beam_axis_gets_their_own_bearing():
    sb = SpeechBearings(locator((30.0, 1.0, 1)), OFFSET, CCW)
    b, coh, spread, looks = sb.locate("u1", 0.0, 2.0, FRONT, now=2.0)
    assert abs(angdiff(b, 30.0)) <= 5 and coh >= sb.min_conf and looks == 1
    assert spread == sb.single_spread                   # one look: not claiming more than that


def test_each_beam_only_looks_in_its_half_of_the_room():
    """Two people talking at once, one in front, one behind: each beam gets its own talker."""
    loc = locator((-30.0, 1.0, 1), (175.0, 1.0, 2))
    front = SpeechBearings(loc, OFFSET, CCW).locate("f", 0.0, 2.0, FRONT, now=2.0)
    rear = SpeechBearings(loc, OFFSET, CCW).locate("r", 0.0, 2.0, REAR, now=2.0)
    assert abs(angdiff(front[0], -30.0)) <= 8
    assert abs(angdiff(rear[0], 175.0)) <= 8


def test_a_louder_talker_behind_never_lends_the_front_beam_a_bearing():
    """Rear talker 3 dB louder over the whole front line: the front half's strongest peak is the rear talker's
    front-back alias (~5 deg, coherence ~0.4: the same ghost the real array shows), not the front talker. The
    confidence floor turns that into no bearing (the beam's, with its width) rather than a wrong one."""
    loc = locator((-30.0, 0.7, 1), (175.0, 1.0, 1))
    front = SpeechBearings(loc, OFFSET, CCW).locate("f", 0.0, 2.0, FRONT, now=2.0)
    assert front is None or abs(angdiff(front[0], -30.0)) <= 8


def test_nobody_in_this_half_means_no_bearing():
    loc = locator((175.0, 1.0, 2))
    assert SpeechBearings(loc, OFFSET, CCW).locate("f", 0.0, 2.0, FRONT, now=2.0) is None
    noise = locator(noise=1.0)
    assert SpeechBearings(noise, OFFSET, CCW).locate("n", 0.0, 2.0, FRONT, now=2.0) is None


def test_one_bearing_per_utterance_with_a_spread_from_its_looks():
    loc = locator((40.0, 1.0, 3), seconds=4.0)
    sb = SpeechBearings(loc, OFFSET, CCW)
    out = [sb.locate("u", 0.0, t, FRONT, now=t) for t in (0.5, 0.6, 1.0, 1.4, 1.8, 2.2, 2.6, 3.0, 3.4, 3.8)]
    assert out[1][3] == out[0][3]                        # rate-limited (relocate_s): no new look, same answer
    bearings = [o[0] for o in out]
    assert max(abs(angdiff(b, bearings[-1])) for b in bearings) <= 3    # no jitter from partial to partial
    assert abs(angdiff(bearings[-1], 40.0)) <= 5
    assert out[-1][2] == pytest.approx(sb.min_spread)   # agreeing looks: SRP's own error, not 0
    assert out[-1][3] >= 5


def test_disabled_or_without_raw_mics():
    assert SpeechBearings(None, OFFSET, CCW).locate("u", 0.0, 1.0, FRONT) is None
    sb = SpeechBearings(locator((30.0, 1.0, 1)), OFFSET, CCW, {"enabled": False})
    assert sb.locate("u", 0.0, 2.0, FRONT, now=2.0) is None


# ---- the pipeline -------------------------------------------------------------------------------------------

class Engine:
    engine = "faster_whisper"

    def decode(self, audio, final, use_hint=True, utt_id=None):
        return Hypothesis("Did you see the email about the internship fair?", -0.2, 0.01, 20.0, "faster_whisper")


def run(found, fixed=True):
    """Beam 0 hears a talker for ~2 s; the firmware's readings sit on the beam (0.8). found = what locate_speech
    answers. Returns (messages sent, locate_speech calls)."""
    sent, calls = [], []
    h = DirectionHistory(fixed_beam=fixed, fixed_beam_spread_deg=30.0)
    for i in range(120):
        h.add(1000 + i * 0.05, [Source(id=0, bearing_deg=FRONT, energy=0.6, speech=True, confidence=0.8)])

    def locate(key, t0, t1, beam):
        calls.append((key, t0, t1, beam))
        return found

    p = SpeechPipeline(dict(CFG["speech"]), Engine(), h, sent.append, locate_speech=locate,
                       vad=lambda w: 0.9 if state["n"] < 60 else 0.0)
    state = {"n": 0}
    rng = np.random.default_rng(0)

    async def main():
        for i in range(0, FS * 4, 512):
            state["n"] += 1
            await p.feed(1000 + i / FS, (rng.standard_normal(512) * 3000).astype(np.int16))
            await asyncio.sleep(0)
        await asyncio.sleep(0.2)
        await asyncio.gather(*p.tasks)
    asyncio.run(main())
    return sent, calls


def test_fixed_beam_speech_carries_the_talkers_raw_mic_bearing():
    sent, calls = run((31.5, 0.8, 8.0, 6))
    start = next(m for m in sent if m.TYPE == "speech_start")
    utts = [m for m in sent if m.TYPE == "utterance"]
    assert start.bearing_deg == 31.5 and start.source_id == 0
    assert utts and all(u.bearing_deg == 31.5 and u.bearing_spread_deg == 8.0 for u in utts)
    assert utts[-1].final and not utts[-1].overlap
    assert all(c[3] == FRONT for c in calls)            # asked within beam 0's half of the room
    assert calls[0][2] < calls[-1][2]                    # the first look is speech_start's, at the onset


def test_fixed_beam_without_a_raw_mic_bearing_says_the_beam_is_wide():
    sent, _ = run(None)
    utts = [m for m in sent if m.TYPE == "utterance"]
    assert utts and all(u.bearing_deg == FRONT and u.bearing_spread_deg == 30.0 for u in utts)   # was 0.0
    assert not utts[-1].overlap                          # the beam's width alone isn't two talkers


def test_two_talkers_in_one_half_widen_the_spread_into_overlap():
    sent, _ = run((60.0, 0.8, 40.0, 7))
    assert [m for m in sent if m.TYPE == "utterance"][-1].overlap


def test_free_running_beams_never_ask_the_raw_mics():
    sent, calls = run((31.5, 0.8, 8.0, 6), fixed=False)
    assert not calls
    assert all(m.bearing_deg == FRONT for m in sent if m.TYPE in ("utterance", "speech_start"))

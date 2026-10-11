"""OnsetDetector (laptop/events/onsets.py): one candidate per doorbell press, none for steady noise."""

import wave
from pathlib import Path

import numpy as np

from contracts.python.here_contract import Source
from laptop.events.onsets import OnsetDetector

ROOT = Path(__file__).resolve().parents[2]
SR = 16000
rng = np.random.default_rng(0)


def wav(name):
    with wave.open(str(ROOT / "fixtures" / "sounds" / name)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32)


def noise(seconds, dbfs, brown=False):
    x = rng.standard_normal(int(seconds * SR))
    if brown:                                      # fan / HVAC-like rumble
        x = np.cumsum(x)
        x -= np.convolve(x, np.ones(800) / 800, "same")
    return x / np.sqrt(np.mean(x ** 2)) * 32768 * 10 ** (dbfs / 20)


def run(x, det=None, stream=0):
    det = det or OnsetDetector()
    out = []
    for i in range(0, len(x) - 512, 512):
        c = det.feed(stream, i / SR, np.clip(x[i:i + 512], -32768, 32767).astype(np.int16))
        if c:
            out.append(c)
    return out


def press(name="doorbell.wav"):
    return wav(name)[:int(2.1 * SR)]              # the fixture holds two presses 2.2 s apart: take one


def test_a_doorbell_press_gives_one_candidate():
    x = noise(6, -55)
    x[2 * SR:2 * SR + len(press())] += press()
    out = run(x)
    assert len(out) == 1
    assert abs(out[0].t - 2.0) < 0.1 and out[0].energy > 0.5 and out[0].rise_db > 20


def test_the_doorbell_through_the_array_over_a_fan_gives_one_candidate():
    x = noise(8, -35, brown=True)
    p = press("doorbell_array.wav")
    x[3 * SR:3 * SR + len(p)] += p
    assert len(run(x)) == 1


def test_steady_fan_noise_gives_none():
    assert run(noise(10, -35, brown=True)) == []
    assert run(noise(10, -30)) == []


def test_two_presses_are_two_candidates_and_the_cooldown_holds_within_one():
    x = noise(8, -55)
    door = wav("doorbell.wav")                     # two presses (ding-dong, 2.2 s, ding-dong)
    x[SR:SR + len(door)] += door
    out = run(x)
    assert len(out) == 2 and 2.0 < out[1].t - out[0].t < 2.5


def test_candidates_carry_the_streams_bearing_and_reach_subscribers():
    det = OnsetDetector()
    det.where = lambda stream, t: ({0: 0.8, 1: -179.2}[stream], False)
    got = []
    det.subscribe(got.append)
    x = noise(5, -55)
    x[2 * SR:2 * SR + len(press())] += press()
    run(noise(5, -55), det, stream=0)
    run(x, det, stream=1)
    assert len(got) == 1 and got[0].source_id == 1 and got[0].bearing_deg == -179.2 and not got[0].speech


def test_the_app_wires_onsets_to_the_stream_track():
    """Laptop: each stream's audio feeds the detector; the bearing is that stream's track."""
    from laptop import settings
    from laptop.app import Laptop

    class Dummy:
        name = "builtin"
        on_sources = on_audio = None
        problems = []
        streams = 2

    class Sink:
        on_config = on_message = None
        heartbeat_fields = None

        def send(self, msg):
            return True

    app = Laptop(settings.load(), (Dummy(), Dummy()), Sink())
    got = []
    app.onsets.subscribe(got.append)
    app.on_sources(1.0, [Source(id=0, bearing_deg=0.8, energy=0.2, speech=False, confidence=0.0),
                         Source(id=1, bearing_deg=-179.2, energy=0.6, speech=False, confidence=0.0)])
    x = noise(5, -55)
    x[2 * SR:2 * SR + len(press())] += press()
    q = np.stack([noise(5, -55), x], axis=1)
    for i in range(0, len(x) - 512, 512):
        app.feed_onsets(1.0 + i / SR, np.clip(q[i:i + 512], -32768, 32767).astype(np.int16))
    assert len(got) == 1 and got[0].source_id == 1 and got[0].bearing_deg == -179.2

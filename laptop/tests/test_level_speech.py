"""laptop/events/level_speech.py: a beam's level over its floor and over the other beam counts as speech."""

from types import SimpleNamespace

from contracts.python.here_contract import Source
from laptop.events.level_speech import LevelSpeech


def frame(e0, e1, sp0=False, sp1=False):
    return [Source(id=0, bearing_deg=0.8, energy=e0, speech=sp0, confidence=0.8 if sp0 else 0.0),
            Source(id=1, bearing_deg=-179.2, energy=e1, speech=sp1, confidence=0.8 if sp1 else 0.0)]


def run(ls, frames, t0=0.0):
    return [ls.update(t0 + i * 0.05, f) for i, f in enumerate(frames)]


def test_a_near_talker_on_one_beam_is_flagged_and_its_leak_is_not():
    ls = LevelSpeech()
    run(ls, [frame(0.25, 0.22)] * 40)                              # 2 s of room: the floors settle
    out = run(ls, [frame(0.6, 0.45)] * 10, t0=2.0)                 # talker in front, ~7 dB of leak behind
    assert all(o[0].speech and o[0].confidence >= 0.5 for o in out)
    assert not any(o[1].speech for o in out)


def test_diffuse_babble_steady_noise_and_quiet_rooms_stay_unflagged():
    ls = LevelSpeech()
    babble = [frame(0.25 + 0.3 * (i % 7 == 0), 0.24 + 0.3 * (i % 7 == 0)) for i in range(200)]   # loud, both beams
    assert not any(s.speech for o in run(ls, babble) for s in o)
    ls = LevelSpeech()
    out = run(ls, [frame(0.2, 0.1)] * 20 + [frame(0.55, 0.3)] * 600)  # a fan comes on and stays: becomes the floor
    assert not any(o[0].speech for o in out[-200:])


def test_it_only_adds_flags_and_holds_briefly():
    ls = LevelSpeech(hold_s=0.4)
    run(ls, [frame(0.2, 0.2)] * 40)
    assert run(ls, [frame(0.2, 0.2, sp1=True)], t0=2.0)[0][1].speech      # the firmware's flag passes through
    run(ls, [frame(0.6, 0.3)], t0=2.05)
    held = run(ls, [frame(0.2, 0.2)] * 12, t0=2.1)
    assert held[0][0].speech and not held[-1][0].speech                    # 0.4 s hold, then off
    assert ls.update(9.0, [frame(0.9, 0.1)[0]]) == [frame(0.9, 0.1)[0]]   # not a beams reading: untouched


def test_config_switch_and_app_wiring():
    assert LevelSpeech.from_config({"level_speech": {"enabled": False}}) is None
    assert LevelSpeech.from_config({}) is None
    ls = LevelSpeech.from_config({"level_speech": {"enabled": True, "margin": 0.3}})
    assert ls.margin == 0.3

    from laptop.app import Laptop
    from laptop.settings import load
    cfg = load()
    cfg["audio"]["level_speech"] = dict(cfg["audio"]["level_speech"], enabled=True)

    class Streams:
        name, streams, reports_speech = "builtin", 2, True
        on_audio = on_sources = None
        problems = []

    recorded = []
    app = Laptop(cfg, (Streams(), Streams()), SimpleNamespace(connected=True, on_config=None, heartbeat_fields=None,
                                                              send=lambda m: True))
    app.recorder = SimpleNamespace(add_sources=lambda t, s: recorded.append(s))
    for i in range(40):
        app.on_sources(i * 0.05, frame(0.25, 0.22))
    app.on_sources(2.0, frame(0.6, 0.4))
    assert app.stream_history(0).items[-1][1][0].speech                    # speech sees the flag ...
    assert not recorded[-1][0].speech                                      # ... the recording keeps the firmware's

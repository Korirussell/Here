"""Two sources at once (beams mode) without hardware: masking, leak gating, cross-talk, wiring, routing."""

import asyncio
from types import SimpleNamespace

import numpy as np

from contracts.python.here_contract import Source
from laptop.app import Laptop
from laptop.audio.base import DirectionHistory
from laptop.settings import load
from laptop.speech.pipeline import Crosstalk, SpeechPipeline, beam_mask
from laptop.speech.transcriber import Hypothesis

CFG = load()
SR = 16000


def tone(f, seconds, amp=0.3):
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * f * t)).astype(np.float32)


def test_beam_mask_keeps_this_beams_source_and_drops_the_leak():
    a, b = tone(440, 1.0), tone(1800, 1.0)       # two "talkers" at different frequencies
    leak = 10 ** (-9 / 20)
    beam0, beam1 = a + leak * b, b + leak * a
    out = beam_mask(beam0, beam1)
    spec = np.abs(np.fft.rfft(out))
    f = np.fft.rfftfreq(len(out), 1 / SR)
    near = lambda hz: spec[(f > hz - 30) & (f < hz + 30)].max()  # noqa: E731
    assert near(440) > 30 * near(1800)            # the 1800 Hz leak is gone (was 9 dB down, now > 29 dB)
    assert np.allclose(beam_mask(beam0, None), beam0)


def test_crosstalk_keeps_the_louder_beam():
    x = Crosstalk()
    x.register(1, "u-b1-1", 10.0, 12.0, "I am bringing twenty copies", level=-30.0)   # leaked into beam 1
    assert not x.duplicate(0, "u-b0-1", 10.1, 12.1, "I am bringing twenty copies", level=-21.0)
    x.register(0, "u-b0-1", 10.1, 12.1, "I am bringing twenty copies", level=-21.0)
    assert x.duplicate(1, "u-b1-1", 10.0, 12.0, "I am bringing twenty copies", level=-30.0)
    assert not x.duplicate(1, "u-b1-2", 10.0, 12.0, "Yes it is on Friday downstairs", level=-30.0)  # other talker


class Engine:
    engine = "faster_whisper"

    def __init__(self, text):
        self.text, self.calls = text, 0

    def decode(self, audio, final, use_hint=True):
        self.calls += 1
        return Hypothesis(text=self.text if final else self.text.split()[0], ms=1, engine=self.engine,
                          segments=[(self.text, -0.2)])


def run_stream(own_level, other_level, stream=1):
    """Stream `stream` VAD-hears speech the whole time; return what it sends. The array flags speech."""
    sent = []
    h = DirectionHistory()
    for i in range(80):
        h.add(1000 + i * 0.05, [Source(id=stream, bearing_deg=180.0, energy=0.6, speech=True, confidence=0.8)])
    p = SpeechPipeline(dict(CFG["speech"]), Engine("I am bringing twenty copies of my resume."), h, sent.append,
                       vad=lambda w: 0.9 if p_state["n"] < 60 else 0.0)
    p.stream = stream
    p_state = {"n": 0}
    rng = np.random.default_rng(0)

    async def main():
        for i in range(0, 16000 * 3, 512):
            p_state["n"] += 1
            own = rng.standard_normal(512) * own_level
            other = rng.standard_normal(512) * other_level
            block = np.stack([other, own] if stream == 1 else [own, other], axis=1).astype(np.int16)
            await p.feed(1000 + i / 16000, block)
            await asyncio.sleep(0)
        await asyncio.sleep(0.2)
        await asyncio.gather(*p.tasks)
    asyncio.run(main())
    return sent


def test_a_beam_ignores_speech_that_is_louder_on_the_other_beam():
    assert run_stream(own_level=900, other_level=3000) == []          # 10 dB louder next door: a leak
    sent = run_stream(own_level=3000, other_level=900)
    assert sent and sent[-1].final and sent[-1].text


class FakeStreams:
    """Two-stream input like the ReSpeaker in beams mode."""
    name = "builtin"
    streams = 2
    reports_speech = True

    def __init__(self):
        self.on_audio = self.on_sources = None
        self.problems = []

    async def run(self):
        await asyncio.sleep(0)


def test_app_builds_one_speech_and_sound_pipeline_per_beam():
    built = []

    class Client:
        connected = True

        def __init__(self):
            self.on_config = None
            self.heartbeat_fields = None

        def send(self, m):
            return True

        async def run(self):
            await asyncio.sleep(0)

    def speech(app, stream=0):
        built.append(("speech", stream, app.stream_history(stream)))
        return SimpleNamespace(set_names=lambda n: None, run=lambda q: asyncio.sleep(0), prefix="u1",
                               note_sounds=lambda *a: None, transcriber=SimpleNamespace(engine="faster_whisper"))

    def sounds(app, stream=0):
        built.append(("sounds", stream, app.stream_history(stream)))
        return SimpleNamespace(run=lambda q: asyncio.sleep(0), on_scores=None)

    src = FakeStreams()
    app = Laptop(CFG, (src, src), Client(), speech=speech, sounds=sounds)
    app.on_sources(1.0, [Source(id=0, bearing_deg=35.0, energy=0.5, speech=True, confidence=0.8),
                         Source(id=1, bearing_deg=180.0, energy=0.5, speech=True, confidence=0.7)])

    async def main():
        task = asyncio.create_task(app.run())
        await asyncio.sleep(0.05)
        task.cancel()
    asyncio.run(main())
    assert [(k, s) for k, s, _ in built] == [("speech", 0), ("sounds", 0), ("speech", 1), ("sounds", 1)]
    assert app.stream_history(0).items[-1][1][0].bearing_deg == 35.0
    assert app.stream_history(1).items[-1][1][0].bearing_deg == 180.0
    assert [sp.prefix for sp in app.speeches] == ["u1b0", "u1b1"]
    assert app.speeches[0].crosstalk is app.speeches[1].crosstalk and app.sounds_list[1].stream == 1


def test_beam_sources_one_talker_on_both_beams_counts_once():
    from laptop.audio.builtin_doa import ReSpeaker
    r = ReSpeaker.__new__(ReSpeaker)
    r.offset, r.ccw, r.beam_held = 0.0, False, [(0.0, 0.0), (0.0, 0.0)]
    s = {"t": 1.0, "beam_az_deg": [130.0, 131.0, 0.0, 0.0], "beam_energy": [2.5e6, 2.0e5, 0.0, 0.0]}
    a, b = r.beam_sources(s, 0.6)
    assert a.speech and not b.speech and (a.id, b.id) == (0, 1)
    s = {"t": 1.05, "beam_az_deg": [35.0, 180.0, 0.0, 0.0], "beam_energy": [2.5e6, 2.0e6, 0.0, 0.0]}
    a, b = r.beam_sources(s, 0.6)
    from laptop.audio.localize import aec_to_device
    from laptop.audio.xvf3800 import to_world
    assert a.speech and b.speech and b.bearing_deg == to_world(aec_to_device(180.0), 0.0, False)


def test_calibration_fits_the_beam_frame_from_front_and_right():
    from laptop.audio.calibrate import fit_beams
    from laptop.audio.xvf3800 import to_world
    assert fit_beams(355.0, 85.0, 10.0, 100.0) == (345.0, 1)        # beams turn the same way as the DoA
    off, sign = fit_beams(355.0, 85.0, 10.0, 280.0)                  # beams turn the other way
    assert sign == -1 and off == 5.0
    for aec in (10.0, 280.0):                                        # the fit maps both positions back
        assert abs(((off + sign * aec) % 360) - (355.0 if aec == 10.0 else 85.0)) < 1e-6
    assert to_world((off + sign * 280.0) % 360, 355.0, False) == 90.0   # "right" lands on +90 world
    assert fit_beams(355.0, 85.0, 10.0, 20.0) is None                # beams disagree: don't save


def test_calibration_saves_the_beam_frame(tmp_path):
    from laptop.audio.calibrate import save_beams
    from laptop.settings import CONFIG_PATH
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    save_beams(5.0, -1, cfg)
    a = load(cfg)["audio"]
    assert a["aec_to_device_offset_deg"] == 5.0 and a["aec_to_device_sign"] == -1


def test_fixed_beam_stream_keeps_its_bearing_when_the_firmware_flags_no_speech():
    """Noisy room (fixtures k1_room_live1): the firmware's speech flag stays off for a quiet talker, but a fixed
    beam's stream still knows where it points, so the caption and name_called keep the beam's bearing."""
    h = DirectionHistory(fixed_beam=True)
    for i in range(20):
        h.add(1 + i * 0.05, [Source(id=1, bearing_deg=-179.2, energy=0.3, speech=False, confidence=0.0)])
    est = h.speech_bearing(1.0, 2.0, 0.4, 25)
    assert est.bearing_deg == -179.2 and est.source_id == 1 and not est.overlap
    assert est.samples == 0 and est.spread_deg < 180
    assert h.speech_bearing(5.0, 6.0, 0.4, 25).bearing_deg == -179.2      # nothing in the window: the newest reading
    assert DirectionHistory().speech_bearing(5.0, 6.0, 0.4, 25) is None   # free-running beams: unchanged
    h.add(2.1, [Source(id=1, bearing_deg=-170.0, energy=0.5, speech=True, confidence=0.8)])
    assert h.speech_bearing(2.0, 2.2, 0.4, 25).samples == 1               # real speech readings still win


def test_app_marks_fixed_beam_streams():
    src = FakeStreams()
    src.beams_fixed = True
    app = Laptop(CFG, (src, src), SimpleNamespace(connected=True, on_config=None, heartbeat_fields=None,
                                                  send=lambda m: True))
    assert all(h.fixed_beam for h in app.stream_histories)
    src2 = FakeStreams()
    assert not any(h.fixed_beam for h in Laptop(CFG, (src2, src2), SimpleNamespace(
        connected=True, on_config=None, heartbeat_fields=None, send=lambda m: True)).stream_histories)

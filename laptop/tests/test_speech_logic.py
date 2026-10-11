"""Speech pipeline logic without models: names, tone, local agreement, bearings, speech_start, alerts."""

import asyncio

import numpy as np
import pytest

from contracts.python import here_contract as hc
from contracts.python.here_contract import Source
from laptop.alerts import AlertManager
from laptop.audio.base import DirectionHistory, SourcesThrottle
from laptop.speech import tone
from laptop.speech.names import NameMatcher, sound_key
from laptop.speech.pipeline import SpeechPipeline, common_prefix
from laptop.speech.transcriber import FallbackTranscriber, Hypothesis
from laptop.settings import load

CFG = load()
SLOW = 30.0          # max_latency_s for tests not about slowness: a stalled thread must not count as a slow engine


# ---- names --------------------------------------------------------------------------------

@pytest.mark.parametrize("heard", ["Kori", "Cory", "Corey", "Kory", "korri", "Kori's"])
def test_name_variants_match(heard):
    assert NameMatcher(["Kori"]).find(f"Hey {heard}, the pizza is here")


@pytest.mark.parametrize("text", ["the core idea", "I'm sorry", "a cork", "Hey Maya"])
def test_non_names_do_not_match(text):
    assert NameMatcher(["Kori"]).find(text) is None


def test_multi_word_names_and_aliases():
    m = NameMatcher(["Kori Russell", "KR"])         # "KR" too short to match safely
    assert m.find("Russell, look over here")[0] == "russell"
    assert m.find("kr") is None


def test_sound_key():
    assert sound_key("Cory") == sound_key("Kori") == sound_key("Korey") == "kori"
    assert sound_key("Philip") == sound_key("Filip")


# ---- tone ---------------------------------------------------------------------------------

def test_tone_rules():
    assert tone.classify("are you going?", 0.5, True)[0] == "questioning"
    assert tone.classify("watch out", 0.95, True)[0] == "shouting"
    assert tone.classify("psst over here", 0.05, True)[0] == "whispering"
    assert tone.classify("haha that's great", 0.5, True)[0] == "laughing"
    assert tone.classify("we won!", 0.5, True)[0] == "excited"
    assert tone.classify("okay", 0.5, True) == ("neutral", 0.5)
    assert tone.classify("", 0.5, True) == (None, 0.0)


@pytest.mark.parametrize("text", ["Ha, ha, ha, ha.", "Ha ha.", "hahaha that's great", "Hehe", "(laughs) okay",
                                  "[laughter]", "Ha! Ha!"])
def test_laughs_in_the_transcript(text):
    assert tone.classify(text, 0.5, True)[0] == "laughing"


@pytest.mark.parametrize("text", ["Ha.", "That's the theory", "Have a look", "He said hello", "the hat"])
def test_not_laughs(text):
    assert tone.classify(text, 0.5, True)[0] != "laughing"


def test_tone_laughter_from_the_sound_classifier():
    assert tone.classify("that was so funny", 0.5, True, laugh=0.6)[0] == "laughing"
    assert tone.classify("that was so funny", 0.5, True, laugh=0.1)[0] == "neutral"
    assert tone.classify("that was so funny", 0.95, True, laugh=0.6)[0] == "laughing"   # laughing beats loud
    assert tone.classify("", 0.5, True, laugh=0.9) == (None, 0.0)                    # no words: nothing to tag


def test_recent_laughter_makes_the_utterance_laughing():
    sent, _ = run_pipeline(partials=("that was",), final="That was the best pizza.", laughter=[(1.2, 0.7)])
    assert sent[-1].final and sent[-1].tone == "laughing" and sent[-1].tone_confidence >= 0.8
    sent, _ = run_pipeline(partials=("that was",), final="That was the best pizza.", laughter=[(9.0, 0.7)])
    assert sent[-1].tone != "laughing"                        # laughter long after the utterance doesn't count


def test_common_prefix_ignores_case_and_punctuation():
    assert common_prefix(["Hey,", "did", "you"], ["hey", "did", "we"]) == 2


# ---- direction history --------------------------------------------------------------------

def src(b, speech=True, conf=0.8, energy=0.6, id_=0):
    return Source(id=id_, bearing_deg=b, energy=energy, speech=speech, confidence=conf)


def test_speech_bearing_circular_mean_and_overlap():
    h = DirectionHistory()
    for i in range(20):
        h.add(100 + i * 0.05, [src(178 if i % 2 else -178)])
    est = h.speech_bearing(99, 102, 0.4, 25)
    assert abs(abs(est.bearing_deg) - 180) < 0.5 and not est.overlap
    h2 = DirectionHistory()
    for i in range(20):
        h2.add(100 + i * 0.05, [src(-60 if i % 2 else 60)])
    assert h2.speech_bearing(99, 102, 0.4, 25).overlap


def test_speech_bearing_ignores_low_confidence_and_non_speech():
    h = DirectionHistory()
    h.add(1.0, [src(90, conf=0.2)])
    h.add(1.1, [src(-90, speech=False)])
    assert h.speech_bearing(0, 2, 0.4, 25) is None
    assert h.any_bearing(0, 2) is not None


def test_two_tracked_sources_talking_is_overlap():
    h = DirectionHistory()
    for i in range(10):
        h.add(1 + i * 0.05, [src(30, id_=0), src(-120, id_=1)])
    est = h.speech_bearing(0, 2, 0.4, 25)
    assert est.overlap and est.source_id in (0, 1)


def test_onset_prefers_recent_speech():
    h = DirectionHistory()
    h.add(1.0, [src(10, speech=False)])
    h.add(1.2, [src(40)])
    h.add(1.3, [src(12, speech=False)])
    assert h.onset(1.35, 0.4).bearing_deg == 40
    assert h.onset(5.0, 0.4).bearing_deg == 12        # nothing recent: newest of any kind


def test_sources_throttle():
    th = SourcesThrottle(idle_change_deg=3, keepalive_s=1.0)
    idle = [src(10, speech=False, energy=0.01)]
    assert th.should_send(0.0, idle)
    assert not th.should_send(0.05, idle)                          # unchanged and quiet
    assert th.should_send(0.1, [src(20, speech=False, energy=0.01)])  # moved
    assert th.should_send(1.2, [src(20, speech=False, energy=0.01)])  # keepalive
    assert th.should_send(1.25, [src(20)]) and th.should_send(1.3, [src(20)])  # active: every sample


# ---- pipeline with a fake VAD and transcriber -------------------------------------------

class ScriptedTranscriber:
    """partials in order, then `final`. hinting: the name is a decoding hint; unhinted: the final without it.
    logprob: the final segment's avg_logprob."""
    engine = "faster_whisper"

    def __init__(self, partials, final, hinting=False, unhinted=None, logprob=-0.2):
        self.partials, self.final = list(partials), final
        self.hinting, self.unhinted, self.logprob = hinting, unhinted, logprob

    def decode(self, audio, final, use_hint=True):
        if final:
            text = self.final if use_hint or self.unhinted is None else self.unhinted
            return Hypothesis(text=text, ms=5, engine=self.engine, segments=[(text, self.logprob)] if text else [])
        return Hypothesis(text=self.partials.pop(0) if len(self.partials) > 1 else self.partials[0], ms=5)


def run_pipeline(speech_s=2.0, partials=("hey", "hey Cory the"), final="Hey Cory, the pizza is here!",
                 bearing=180.0, array_speech=True, **kw):
    """bearing: degrees, or a function of seconds since the start (a talker who moves).
    array_speech=False: the array never flags speech (noise only VAD heard)."""
    sent, names = [], []
    h = DirectionHistory()
    t0 = 1000.0
    for i in range(int((speech_s + 2) * 20)):
        h.add(t0 + i * 0.05, [src(bearing(i * 0.05) if callable(bearing) else bearing, speech=array_speech)])
    cfg = dict(CFG["speech"], partial_every_s=0.4)
    vad_state = {"n": 0}

    def vad(win):
        vad_state["n"] += 1
        return 0.9 if vad_state["n"] * 512 / 16000 <= speech_s else 0.0

    h.reports_speech = kw.pop("reports_speech", False)
    laughter = kw.pop("laughter", ())
    engine = ScriptedTranscriber(partials, final, **kw.pop("engine", {}))
    for i, b in enumerate(kw.pop("scatter", ())):    # extra confident speech readings from elsewhere (babble)
        h.add(t0 + 0.025 + i * 0.05, [src(b)])
    p = SpeechPipeline(cfg, engine, h, sent.append,
                       on_name=lambda **a: names.append(a), vad=vad, names=["Kori"], **kw)
    for w0, score in laughter:                       # what the sounds pipeline heard, seconds from the start
        p.note_sounds(t0 + w0, t0 + w0 + 1.0, {"laughter": (score, "Laughter"), "speech": (0.9, "Speech")})

    async def main():
        audio = (np.random.default_rng(0).standard_normal(16000 * int(speech_s + 1)) * 3000).astype(np.int16)
        for i in range(0, len(audio), 512):
            await p.feed(t0 + i / 16000, audio[i:i + 512])
            await asyncio.sleep(0)
        for _ in range(50):
            await asyncio.sleep(0.01)
        await asyncio.gather(*p.tasks)
    asyncio.run(main())
    return sent, names


def test_pipeline_emits_speech_start_partials_and_final_with_bearing():
    sent, names = run_pipeline()
    types = [m.TYPE for m in sent]
    assert types[0] == "speech_start" and types[-1] == "utterance"
    start = sent[0]
    assert start.bearing_deg == 180.0 and start.confidence > 0
    final = sent[-1]
    assert final.final and final.text == "Hey Kori, the pizza is here!"   # speech.spellings: Cory -> Kori and final.pending == ""
    assert final.bearing_deg == 180.0 and final.bearing_spread_deg < 1 and not final.overlap
    assert final.tone == "excited" and final.utt_id == start.utt_id
    partial = [m for m in sent if m.TYPE == "utterance" and not m.final]
    assert partial, "expected partials"
    for m in sent:
        assert hc.errors(m.to_dict()) == [], m
    assert len(names) == 1 and names[0]["bearing"] == 180.0     # name called once, from behind


def test_short_blip_retracts_its_speech_start():
    sent, names = run_pipeline(speech_s=0.1, partials=("",), final="")
    assert [m.TYPE for m in sent] == ["speech_start", "utterance"]
    retract = sent[1]
    assert retract.final and retract.text == "" and retract.pending == "" and retract.utt_id == sent[0].utt_id
    assert names == []


# ---- the wearer's own voice (README §5) ---------------------------------------------------

def test_wearer_is_never_captioned_and_never_starts():
    sent, names = run_pipeline(bearing=-172.0, wearer_bearing=175.0, wearer_ignore_deg=20)
    assert sent == [] and names == []          # no speech_start (no buzz), no caption, no name alert


def test_others_are_unaffected_by_the_wearer_filter():
    sent, _ = run_pipeline(bearing=180.0, wearer_bearing=90.0, wearer_ignore_deg=20)
    assert sent[0].TYPE == "speech_start" and sent[-1].final and sent[-1].text


def test_onset_near_wearer_but_someone_else_is_started_late_from_their_bearing():
    # the first readings (the onset) sit on the wearer, then the real talker at 180 dominates
    sent, names = run_pipeline(bearing=lambda s: 90.0 if s < 0.15 else 180.0, wearer_bearing=90.0,
                               wearer_ignore_deg=20)
    starts = [m for m in sent if m.TYPE == "speech_start"]
    assert sent[0].TYPE == "speech_start" and len(starts) == 1
    assert abs(starts[0].bearing_deg - 180.0) < 1
    assert sent[-1].final and sent[-1].text and len(names) == 1


def test_utterance_that_turns_out_to_be_the_wearer_is_retracted():
    # onset from the front, but most of the utterance comes from the wearer's mark
    sent, names = run_pipeline(bearing=lambda s: 0.0 if s < 0.15 else 90.0, wearer_bearing=90.0,
                               wearer_ignore_deg=20)
    assert sent[0].TYPE == "speech_start"
    final = sent[-1]
    assert final.TYPE == "utterance" and final.final and final.text == "" and final.pending == ""
    assert final.utt_id == sent[0].utt_id and names == []
    assert all(not m.text for m in sent if m.TYPE == "utterance")


def test_calibrate_wearer_rewrites_only_that_line(tmp_path):
    from laptop.audio.calibrate import save_wearer
    from laptop.settings import CONFIG_PATH
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    save_wearer(-163.25, cfg)
    after = load(cfg)
    assert after["audio"]["wearer_bearing_deg"] == -163.2 or after["audio"]["wearer_bearing_deg"] == -163.3
    assert after["audio"]["wearer_ignore_deg"] == CFG["audio"]["wearer_ignore_deg"]
    save_wearer(None, cfg)
    assert load(cfg)["audio"]["wearer_bearing_deg"] is None


# ---- alerts -------------------------------------------------------------------------------

def test_alerts_dedupe_expire_and_respect_config():
    now = [100.0]
    sent = []
    am = AlertManager(CFG["alerts"], sent.append, clock=lambda: now[0])
    a1 = am.raise_alert("doorbell", 170.0, 0.4, event_id="s-1")
    now[0] += 2
    a2 = am.raise_alert("doorbell", 160.0, 0.5, event_id="s-1")
    assert a1.alert_id == a2.alert_id and a2.active                     # same kind, similar direction
    a3 = am.raise_alert("doorbell", -20.0, 0.5)
    assert a3.alert_id != a1.alert_id                                   # other side of the room
    assert am.raise_alert("loud_bang", 0.0, 0.7) is None                # loud bang needs 0.8
    now[0] += 11
    am.expire()
    ended = [m for m in sent if not m.active]
    assert {m.alert_id for m in ended} == {a1.alert_id, a3.alert_id}
    am.set_config(True, {"doorbell": False})
    assert am.raise_alert("doorbell", 0.0, 0.9) is None
    am.set_config(False, {})
    assert am.raise_alert("name_called", 0.0, 0.9) is None
    for m in sent:
        assert hc.errors(m.to_dict()) == []


def test_fallback_transcriber_switches_on_error():
    class Broken:
        engine = "scribe_realtime"

        def decode(self, audio, final):
            raise RuntimeError("socket closed")

    class Whisper:
        engine = "faster_whisper"

        def decode(self, audio, final):
            return Hypothesis(text="ok")

    f = FallbackTranscriber(Broken(), Whisper(), retry_s=60, max_latency_s=SLOW)
    assert f.engine == "scribe_realtime"
    assert f.decode(None, False).text == "ok"
    assert f.engine == "faster_whisper" and "socket closed" in f.reason


def test_names_reach_the_transcriber_as_a_hint():
    class Engine:
        engine = "faster_whisper"
        names = None

        def set_names(self, names):
            self.names = list(names)

        def decode(self, audio, final):
            return Hypothesis(text="")

    primary, fallback = Engine(), Engine()
    p = SpeechPipeline(CFG["speech"], FallbackTranscriber(primary, fallback), DirectionHistory(), lambda m: None,
                       vad=lambda w: 0.0)
    p.set_names(["Kori", "Cory"])                  # from the phone's config (or names:/mock: defaults)
    assert primary.names == fallback.names == ["Kori", "Cory"]
    assert p.names.find("hey Kori")


def test_whisper_hint_formats():
    from laptop.speech.transcriber import WhisperTranscriber
    w = WhisperTranscriber.__new__(WhisperTranscriber)        # no model: only the hint logic
    w.set_names(["Kori", " Kori ", "", "Cory"])
    assert w.hint == "Kori, Cory"
    w.set_names([])
    assert w.hint is None


# ---- whisper inventions on noise (README §24 loud hall) ----------------------------------

@pytest.mark.parametrize("final", ["Thank you.", "Thanks for watching!", "Thank you. Thank you.", "Um.", "you"])
def test_classic_inventions_on_noise_are_retracted(final):
    sent, names = run_pipeline(partials=(final,), final=final, array_speech=False)
    assert all(not m.text and not m.pending for m in sent if m.TYPE == "utterance")
    assert sent[-1].final and sent[-1].text == ""


def test_the_same_words_from_a_real_talker_are_kept():
    sent, _ = run_pipeline(speech_s=2.0, partials=("Thank you.",), final="Thank you.")   # array heard ~2 s
    assert sent[-1].final and sent[-1].text == "Thank you."


@pytest.mark.parametrize("final", ["Thank you for the pizza.", "So where is the doorbell?", "Okay."])
def test_real_sentences_pass_even_without_the_array(final):
    sent, _ = run_pipeline(partials=(final,), final=final, array_speech=False)
    assert sent[-1].text == final


# ---- name hint echoes, doubtful segments, diffuse babble ---------------------------------

def test_name_hint_echo_loops_are_collapsed_and_never_alert():
    # a doorbell in babble, decoded with the name as a hint: "Kori. Kori. Kori." (real-array stress test)
    sent, names = run_pipeline(partials=("Kori. Kori.",), final="Kori. Kori. Kori.",
                               engine={"hinting": True, "unhinted": "We should go out to her."})
    assert sent[-1].final and sent[-1].text == "" and names == []
    sent, names = run_pipeline(partials=("Kori. Kori. He knows",), final="Kori. Kori. He knows how to do it.",
                               engine={"hinting": True, "unhinted": "and he knows how to do it"})
    assert sent[-1].text == "Kori. He knows how to do it." and names == []


def test_a_lone_name_is_checked_without_the_hint():
    sent, names = run_pipeline(partials=("Kori!",), final="Kori!", engine={"hinting": True, "unhinted": "Corey!"})
    assert sent[-1].text == "Kori!" and len(names) == 1                    # really said: alert
    assert all(not m.text for m in sent[:-1] if m.TYPE == "utterance")       # not shown before the check
    sent, names = run_pipeline(partials=("Kori!",), final="Kori!", engine={"hinting": True, "unhinted": "Thank you."})
    assert sent[-1].text == "" and names == []                              # the hint written back on noise


def test_doubtful_segments_need_the_array_to_back_them():
    kept, _ = run_pipeline(final="Stop it, you are making me spill my coffee.", engine={"logprob": -1.3})
    assert kept[-1].text == "Stop it, you are making me spill my coffee."    # a talker was there (laughing)
    gone, _ = run_pipeline(final="We should get rid of the train engines.", engine={"logprob": -1.3},
                           array_speech=False)
    assert gone[-1].text == ""


def test_diffuse_babble_is_not_captioned():
    import random
    rng = random.Random(4)
    scatter = [rng.uniform(-180, 180) for _ in range(80)]
    sent, _ = run_pipeline(final="So I told him the meeting was moved.", scatter=scatter)
    assert sent[-1].final and sent[-1].text == ""
    sent, _ = run_pipeline(final="So I told him the meeting was moved.", scatter=[175.0, -178.0] * 40)
    assert sent[-1].text == "So I told him the meeting was moved."           # one talker, a little jitter


def test_a_hint_loop_falls_back_to_the_plain_decode():
    sent, names = run_pipeline(partials=("Kori.",), final="Kori. Kori. Kori. Kori.",
                               engine={"hinting": True, "unhinted": "Hey Corey, the pizza is here!"})
    assert sent[-1].text == "Hey Kori, the pizza is here!" and len(names) == 1


def test_an_array_gates_from_the_first_second():
    # VAD hears "speech" (babble) but the array, which can flag speech, never does: nothing starts
    sent, _ = run_pipeline(final="So I told him the meeting was moved.", array_speech=False, reports_speech=True)
    assert sent == []
    sent, _ = run_pipeline(final="So I told him the meeting was moved.", reports_speech=True)
    assert sent[0].TYPE == "speech_start" and sent[-1].text


def test_partials_come_faster_until_the_first_words():
    """K1 proposal (a): decodes of the first ~0.2 s of a voice come back empty; retrying every 0.4 s put the first
    words at 605 ms on the real array (fixtures/array_tts). Every 0.2 s until words appear: 423 ms."""
    from types import SimpleNamespace
    cfg = dict(CFG["speech"], partial_every_s=0.4, first_words_every_s=0.2)
    p = SpeechPipeline(cfg, SimpleNamespace(engine="fake"), DirectionHistory(), lambda m: None, vad=lambda w: 0.0)
    seg = SimpleNamespace(prev_words=None, committed=[])
    assert p.partial_step(seg) == 0.2                       # nothing decoded yet
    seg.prev_words = []
    assert p.partial_step(seg) == 0.2                       # decoded, but no words yet
    seg.prev_words = ["hey"]
    assert p.partial_step(seg) == 0.4                       # words on screen: the normal cadence
    cfg2 = dict(CFG["speech"], partial_every_s=0.4)
    cfg2.pop("first_words_every_s", None)
    p2 = SpeechPipeline(cfg2, SimpleNamespace(engine="fake"), DirectionHistory(), lambda m: None, vad=lambda w: 0.0)
    assert p2.partial_step(SimpleNamespace(prev_words=None, committed=[])) == 0.4   # not configured: unchanged


def test_team_names_are_respelled_without_touching_other_words():
    """speech.spellings: "Corey" -> "Kori", "Trey" -> "Tre"; whole words only, and nothing is ever added."""
    from types import SimpleNamespace
    cfg = dict(CFG["speech"], spellings={"Kori": ["Corey", "Cory"], "Tre": ["Trey"], "Jennah": ["Jenna"]})
    p = SpeechPipeline(cfg, SimpleNamespace(engine="fake"), DirectionHistory(), lambda m: None, vad=lambda w: 0.0)
    assert p.respell("Hey Corey, did trey see Jenna?") == "Hey Kori, did Tre see Jennah?"
    assert p.respell("Coreys and Treyvon stay") == "Coreys and Treyvon stay"
    assert p.respell("") == ""


def test_name_plus_an_invention_is_checked_without_the_hint():
    """K1 proposal (c): "Kori. Thank you." on noise raised name_called twice in one real-array run (and once on 220 s
    of real chatter with level_speech on). Name + only inventions = name-only: the plain decode decides."""
    sent, names = run_pipeline(partials=("Kori. Thank you.",), final="Kori. Thank you.",
                               engine={"hinting": True, "unhinted": "Thank you."})
    assert sent[-1].text == "" and names == []                              # the hint written back on noise
    sent, names = run_pipeline(partials=("Kori. Thank you.",), final="Kori. Thank you.",
                               engine={"hinting": True, "unhinted": "Cory, thank you."})
    assert len(names) == 1                                                  # really said: still an alert
    sent, names = run_pipeline(partials=("Kori, thank you for the pizza",), final="Kori, thank you for the pizza.",
                               engine={"hinting": True, "unhinted": "unused"})
    assert len(names) == 1 and sent[-1].text == "Kori, thank you for the pizza."   # a real sentence: unchanged


def test_music_no_longer_vetoes_a_caption():
    """K1 proposal (c): speech.nonspeech_labels without music / tv (background music dropped "Kori.")."""
    from types import SimpleNamespace
    cfg = dict(CFG["speech"])
    p = SpeechPipeline(cfg, SimpleNamespace(engine="fake"), DirectionHistory(), lambda m: None, vad=lambda w: 0.0)
    p.note_sounds(1.0, 2.0, {"music": (0.9, ""), "speech": (0.2, "")})
    assert p.nonspeech_share(1.0, 2.0)[0] == 0.0
    p.note_sounds(3.0, 4.0, {"baby_crying": (0.9, ""), "speech": (0.2, "")})
    assert p.nonspeech_share(3.0, 4.0) == (1.0, "baby_crying")

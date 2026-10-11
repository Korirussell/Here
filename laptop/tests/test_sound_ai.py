"""A2 sound AI: onset windows, honest unknowns, upgrades, ambience, model fallback. Fake models only."""

import asyncio
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pytest

from laptop.sounds import classifier as K
from laptop.sounds import config as C


@dataclass
class Cand:
    t: float
    bearing_deg: Optional[float] = 150.0
    energy: float = 0.6
    source_id: int = 0
    level_db: float = -30.0
    rise_db: float = 20.0
    speech: bool = False


class History:
    def any_bearing(self, t0, t1):
        return None


class Fake:
    """A classifier answering whatever `scores` says (label -> score)."""
    model_id = "fake/model"
    thresholds = {"doorbell": 0.3, "siren": 0.3}
    default_threshold = 0.3
    alert_min = {"siren": 0.3}

    def __init__(self):
        self.scores = {}
        self.calls = 0

    def __call__(self, pcm):
        self.calls += 1
        return {k: (v, k) for k, v in self.scores.items()}, None


class Alerts:
    def __init__(self):
        self.raised = []

    def raise_alert(self, kind, bearing, confidence, **kw):
        self.raised.append((kind, bearing, confidence, kw))


def make(publish=("doorbell", "siren")):
    sent = []
    fake, alerts = Fake(), Alerts()
    p = K.SoundPipeline(fake, History(), sent.append, alerts, publish=list(publish))
    p.enrolled.sounds = []           # no template matching in these tests
    return p, fake, sent, alerts


def tone(seconds=1.0, amp=8000, sr=16000):
    t = np.arange(int(seconds * sr)) / sr
    return (np.sin(2 * np.pi * 700 * t) * amp).astype(np.int16)


def onset(p, cand, pcm=None):
    pcm = tone() if pcm is None else pcm
    asyncio.run(p.handle_window(cand.t, cand.t + 1.0, p.classify(pcm)[0], None, onset=(cand, pcm)))


def test_unknown_keeps_the_onset_bearing_and_names_the_model():
    p, fake, sent, alerts = make()
    fake.scores = {"doorbell": 0.05, "siren": 0.02}
    onset(p, Cand(t=10.0, bearing_deg=-120.0))
    assert [m.label for m in sent] == ["unknown"]
    u = sent[0]
    assert u.bearing_deg == -120.0 and u.model == "fake/model" and u.source_id == 0 and 0 < u.confidence <= 1
    assert alerts.raised == []                        # unknown never alerts


def test_never_forces_a_known_label_below_threshold():
    p, fake, sent, _ = make()
    fake.scores = {"doorbell": 0.29}                  # just under 0.3
    onset(p, Cand(t=1.0))
    assert [m.label for m in sent] == ["unknown"]


def test_faint_or_short_onsets_are_not_unknowns():
    p, fake, sent, _ = make()
    onset(p, Cand(t=1.0, energy=0.1))                 # too faint
    click = np.zeros(16000, np.int16)
    click[100:300] = 20000                            # 12 ms click: not an event
    onset(p, Cand(t=5.0), click)
    assert sent == []


def test_a_recognised_label_is_not_unknown():
    p, fake, sent, alerts = make()
    fake.scores = {"siren": 0.8}
    onset(p, Cand(t=1.0))
    assert [m.label for m in sent] == ["siren"] and alerts.raised[0][0] == "siren"
    assert alerts.raised[0][3]["min_confidence"] == 0.3       # the model's own alert minimum
    fake.scores = {"music": 0.9}                      # recognised, not published: no unknown either
    p2, f2, sent2, _ = make()
    f2.scores = {"music": 0.9}
    onset(p2, Cand(t=1.0))
    assert sent2 == []


def test_a_later_known_label_upgrades_the_same_event_id():
    p, fake, sent, _ = make()
    onset(p, Cand(t=1.0, bearing_deg=170.0))
    fake.scores = {"doorbell": 0.7}
    asyncio.run(p.handle_window(1.5, 2.5, p.classify(tone())[0], None))      # the sliding window, 0.5 s later
    assert [m.label for m in sent] == ["unknown", "doorbell"]
    assert sent[1].event_id == sent[0].event_id and sent[1].bearing_deg == 170.0
    asyncio.run(p.handle_window(2.0, 3.0, p.classify(tone())[0], None))      # the same ring continues
    assert sent[2].event_id == sent[0].event_id


def beam(stream):
    """A pipeline on one of several streams (fixed beams: 0 front, 1 rear), with the real AlertManager."""
    from laptop.alerts import AlertManager
    p, fake, sent, _ = make()
    p.stream, p.arbiter = stream, {}
    p.alerts = AlertManager({"min_confidence": 0.3, "min_confidence_by_kind": {}, "dedupe_s": 4, "dedupe_deg": 45,
                             "expire_s": 10}, sent.append, clock=lambda: 100.0)
    return p, fake, sent


def test_sound_event_names_the_rear_beam_and_the_alert_links_to_it():
    p, fake, sent = beam(1)
    fake.scores = {"doorbell": 0.8}
    onset(p, Cand(t=1.0, bearing_deg=-179.2, source_id=1))
    ev, alert = sent
    assert ev.TYPE == "sound_event" and ev.label == "doorbell" and ev.to_dict()["source_id"] == 1
    assert alert.TYPE == "alert" and alert.ref.event_id == ev.event_id      # the phone finds track 1 via the event
    asyncio.run(p.handle_window(1.5, 2.5, p.classify(tone())[0], None))      # the ring goes on (sliding window)
    assert [m.source_id for m in sent if m.TYPE == "sound_event"] == [1, 1]


def test_sliding_window_on_a_beam_names_the_beam():
    p, fake, sent = beam(1)
    fake.scores = {"siren": 0.8}
    asyncio.run(p.handle_window(1.0, 2.0, p.classify(tone())[0], None))
    assert sent[0].TYPE == "sound_event" and sent[0].source_id == 1


def test_unknown_upgrade_keeps_the_rear_beam():
    p, fake, sent = beam(1)
    onset(p, Cand(t=1.0, bearing_deg=170.0, source_id=1))
    fake.scores = {"doorbell": 0.7}
    asyncio.run(p.handle_window(1.5, 2.5, p.classify(tone())[0], None))
    events = [m for m in sent if m.TYPE == "sound_event"]
    assert [(m.label, m.source_id) for m in events] == [("unknown", 1), ("doorbell", 1)]
    assert events[1].event_id == events[0].event_id


def test_no_known_stream_omits_source_id_until_an_onset_says():
    p, fake, sent, _ = make()                         # one stream, sliding window only: which track is unknown
    fake.scores = {"siren": 0.8}
    asyncio.run(p.handle_window(1.0, 2.0, p.classify(tone())[0], None))
    assert "source_id" not in sent[0].to_dict()
    onset(p, Cand(t=1.5, source_id=0))                # an onset on the same sound: later updates name it
    assert sent[-1].event_id == sent[0].event_id and sent[-1].to_dict()["source_id"] == 0


def test_steady_ambience_merges_instead_of_spamming():
    p, fake, sent, _ = make()
    for k in range(10):                               # a fan clattering every 4 s for 40 s
        onset(p, Cand(t=4.0 * k))
    n = p.unknown_cfg["ambient_n"]                    # at most this many before it counts as ambience
    assert sum(m.label == "unknown" for m in sent) <= n
    assert p.stats["unknown_merged"] >= 10 - n


def test_speech_and_other_beam_candidates_are_left_alone():
    p, fake, sent, _ = make()
    p.on_onset(Cand(t=1.0, speech=True))
    p.on_onset(Cand(t=1.0, source_id=1))
    assert len(p.onsets) == 0


def test_onset_queue_drops_the_oldest():
    p, *_ = make()
    for k in range(C.ONSET_QUEUE + 3):
        p.on_onset(Cand(t=100.0 + k))                 # no audio buffered yet: they wait
    assert len(p.onsets) == C.ONSET_QUEUE
    assert p.onsets[0].t == 100.0 + 3 and p.stats["onsets_dropped"] == 3


def test_onset_window_is_taken_around_the_onset():
    p, fake, sent, _ = make()
    t0 = 50.0
    for k in range(60):                               # 3 s of 50 ms blocks, a tone from t0+1.5 s on
        t = t0 + k * 0.05
        p.add(t, tone(0.05) if t >= t0 + 1.5 else np.zeros(800, np.int16))
    pcm, w0 = p.window_at(t0 + 1.5 - 0.1)
    assert abs(w0 - (t0 + 1.4)) < 1e-6 and len(pcm) == 16000
    assert np.abs(pcm[:1000]).max() == 0 and np.abs(pcm[-1000:]).max() > 0


def test_classifier_falls_back_to_panns_at_load_and_mid_session(monkeypatch):
    class Broken:
        model_id, device, load_s = "hf/broken", "cpu", 0.0

        def __call__(self, pcm):
            raise RuntimeError("CUDA error")

    class Panns:
        model_id, device, load_s = "panns_cnn14", "cpu", 0.0

        def __call__(self, pcm):
            return {"doorbell": (0.5, "Doorbell")}, None

    def load(model_id, device=None, spec=None):
        if model_id == "hf/missing":
            raise OSError("no network")
        return Broken() if model_id == "hf/broken" else Panns()

    monkeypatch.setattr(K, "load_backend", load)
    c = K.Classifier("hf/missing", fallback="panns_cnn14")
    assert c.model_id == "panns_cnn14" and "no network" in c.problem
    c = K.Classifier("hf/broken", fallback="panns_cnn14")
    assert c.model_id == "hf/broken" and c.problem is None
    scores, _ = c(np.zeros(16000, np.int16))
    assert c.model_id == "panns_cnn14" and scores["doorbell"][0] == 0.5 and "mid-session" in c.problem
    assert c.thresholds == C.THRESHOLDS               # PANNs scale again


def test_alert_manager_takes_the_models_minimum():
    from laptop.alerts import AlertManager
    sent = []
    m = AlertManager({"min_confidence": 0.6, "min_confidence_by_kind": {}, "dedupe_s": 4, "dedupe_deg": 45,
                      "expire_s": 10}, sent.append, clock=lambda: 100.0)
    assert m.raise_alert("siren", 90.0, 0.4) is None
    assert m.raise_alert("siren", 90.0, 0.4, min_confidence=0.3) is not None


@pytest.mark.parametrize("label", ["car_horn", "vehicle", "siren", "door", "unknown"])
def test_new_labels_have_display_rows(label):
    from laptop.sounds.labels import SOUND_DISPLAY
    assert label in SOUND_DISPLAY


def test_an_onset_window_counts_for_its_beam_in_the_arbiter():
    """K1 proposal (b2): two beams share an arbiter. Before, only regular windows were recorded, so beam 1's onset
    window (doorbell 0.95) was compared with beam 0's last regular window (0.9) against its own stale 0.8 and never
    published (real array: "doorbell (recognised, not published)", alert from the regular windows 2.3 s later)."""
    arbiter = {}
    beams = []
    for k in (0, 1):
        p, fake, sent, alerts = make(publish=("doorbell",))
        p.stream, p.arbiter = k, arbiter
        beams.append((p, fake, sent, alerts))
    (p0, f0, _, _), (p1, f1, sent1, alerts1) = beams
    f0.scores, f1.scores = {"doorbell": 0.9}, {"doorbell": 0.8}
    asyncio.run(p0.handle_window(9.5, 10.5, p0.classify(tone())[0], None))      # regular windows, both beams
    asyncio.run(p1.handle_window(9.5, 10.5, p1.classify(tone())[0], None))
    f1.scores = {"doorbell": 0.95}
    onset(p1, Cand(t=10.0, source_id=1))                                         # beam 1's onset window
    assert [a[0] for a in alerts1.raised] == ["doorbell"]

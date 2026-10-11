"""tools/rehearsal.py's checks on hand-made dashboard traffic: they pass the real moments and catch the misses."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import rehearsal  # noqa: E402


def traffic(rear_bearing=180.0, door_bearing=165.0, start_first=True):
    t = iter(range(100))
    m = []

    def add(**kw):
        m.append({"_recv": float(next(t)), **kw})
    for utt, b in (("u1", 35.0), ("u2", rear_bearing)):
        if start_first:
            add(type="speech_start", bearing_deg=b)
        add(type="utterance", utt_id=utt, text="", pending="Hey", final=False, bearing_deg=b)
        if not start_first:
            add(type="speech_start", bearing_deg=b)
        add(type="utterance", utt_id=utt, text="Hey there", final=True, bearing_deg=b)
    add(type="sound_event", label="doorbell", model="panns_cnn14+enrolled", bearing_deg=door_bearing)
    add(type="alert", kind="doorbell", active=True, bearing_deg=door_bearing)
    add(type="alert", kind="name_called", active=True, bearing_deg=rear_bearing)
    add(type="perception_enrichment", attention_model="rules", ref={"utt_id": "u1"})
    return m


def test_all_moments_pass_on_good_traffic():
    assert all(ok for ok, _ in rehearsal.moments(traffic()).values())


def test_misses_are_caught():
    r = rehearsal.moments(traffic(rear_bearing=90.0))
    assert not r["4 rear speaker"][0] and not r["5 doorbell / name"][0]
    assert not rehearsal.moments(traffic(door_bearing=20.0))["5 doorbell / name"][0]
    assert not rehearsal.moments(traffic(start_first=False))["3 front caption"][0]

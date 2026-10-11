"""tools/doa_latency.py: arrival stamps -> per-metric latencies."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from doa_latency import summarize  # noqa: E402


def test_summarize_latencies():
    rows = [
        (100.03, {"type": "sources", "t": 100.0, "sources": [{"id": 0}]}),
        (100.50, {"type": "sources", "t": 100.5, "sources": []}),               # empty keepalive: not direction
        (101.40, {"type": "speech_start", "utt_id": "u1", "t": 101.35}),
        (101.45, {"type": "utterance", "utt_id": "u1", "t_start": 101.0, "t_end": 101.4, "text": "", "pending": ""}),
        (101.90, {"type": "utterance", "utt_id": "u1", "t_start": 101.0, "t_end": 101.8, "text": "", "pending": "hey"}),
        (103.00, {"type": "utterance", "utt_id": "u1", "t_start": 101.0, "t_end": 102.2, "text": "hey Kori",
                  "pending": "", "final": True}),
        (105.60, {"type": "sound_event", "event_id": "s1", "t_start": 105.0}),
        (105.61, {"type": "alert", "kind": "doorbell", "active": True, "ref": {"event_id": "s1"}}),
        (106.10, {"type": "alert", "kind": "doorbell", "active": True, "ref": {"event_id": "s1"}}),  # an update
    ]
    s = summarize(rows, onset_pre_s=0.1)
    assert [round(x) for x in s["direction"]] == [30]
    assert [round(x) for x in s["speech_start"]] == [400]
    assert [round(x) for x in s["first_words"]] == [900]
    assert [round(x) for x in s["final_after_end"]] == [800]
    assert [round(x) for x in s["doorbell_alert"]] == [510]


def test_an_upgraded_unknown_is_not_counted():
    rows = [(140.0, {"type": "sound_event", "event_id": "u5", "t_start": 105.0}),          # unknown since 105 s
            (140.1, {"type": "alert", "kind": "doorbell", "active": True, "ref": {"event_id": "u5"}})]
    assert summarize(rows)["doorbell_alert"] == []

"""tools/run_report.py on a synthetic log written by the real logger (laptop/client/relay.py log_line)."""

import json
import sys
from pathlib import Path

from contracts.python.here_contract import Alert, Heartbeat, SpeechStart, Utterance
from laptop.client.relay import log_line

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import run_report  # noqa: E402


def utt(uid, t_start, t_end, text, final, pending="", tone=None):
    return Utterance(src="laptop", t=t_end, utt_id=uid, t_start=t_start, t_end=t_end, text=text, pending=pending,
                     final=final, bearing_deg=30.0, bearing_spread_deg=2.0, source_id=0, overlap=False, tone=tone,
                     tone_confidence=0.5, loudness=0.5, engine="faster_whisper", stage_ms={"lag": 700.0})


def write(path, items):
    """items: (send time, message); seq restarts per session like a fresh laptop process."""
    seq = 0
    with open(path, "w", encoding="utf-8") as f:
        for sent, m in items:
            if m is None:
                seq = 0
                continue
            seq += 1
            m.seq = seq
            line = json.loads(log_line(m))
            line["_sent"] = sent
            f.write(json.dumps(line) + "\n")


def test_report_counts_latencies_and_sessions(tmp_path):
    hb = lambda t, status="ok", detail=None, engine="faster_whisper": Heartbeat(  # noqa: E731
        src="laptop", t=t, status=status, engine=engine, doa_source="builtin", **({"detail": detail} if detail else {}))
    items = [
        (100.0, hb(100.0)),
        (100.6, SpeechStart(src="laptop", t=100.55, utt_id="u-1", bearing_deg=30.0, source_id=0, confidence=0.8)),
        (100.9, utt("u-1", 100.3, 100.85, "", False, pending="hey")),
        (102.0, hb(102.0, "degraded", "scribe slow -> faster_whisper")),
        (102.6, utt("u-1", 100.3, 101.8, "Hey Kori, look", True, tone="excited")),
        (102.7, Alert(src="laptop", t=102.6, alert_id="a-1", kind="name_called", label="Name called", emoji="x",
                      priority=2, bearing_deg=30.0, active=True, confidence=0.9)),
        (103.0, SpeechStart(src="laptop", t=102.95, utt_id="u-2", bearing_deg=30.0, source_id=0, confidence=0.5)),
        (103.8, utt("u-2", 102.7, 103.1, "", True)),                     # a blip: typing dots retracted
        (110.0, hb(110.0)),                                            # 8 s without a heartbeat
        (None, None),                                                  # a new laptop process: seq restarts
        (200.0, hb(200.0)),
        (200.5, SpeechStart(src="laptop", t=200.45, utt_id="u-9", bearing_deg=-30.0, source_id=0, confidence=0.9)),
        (202.0, utt("u-9", 200.2, 201.0, "Hello there", True)),
    ]
    log = tmp_path / "laptop.jsonl"
    write(log, items)
    with open(log, "a", encoding="utf-8") as f:
        f.write('{"type": "utter')                                    # killed mid-write: ignored
    reports = run_report.main([str(log)])
    assert len(reports) == 2
    r = reports[0]
    assert r["captions"] == 1 and r["retractions"] == 1 and r["retractions_dots_only"] == 1
    assert r["alerts"] == {"name_called": 1} and r["tones"] == {"excited": 1}
    lat = r["latency_s"]
    assert abs(lat["speech_start_detect"]["max"] - 0.25) < 1e-6        # 100.55 - 100.3 and 102.95 - 102.7
    assert abs(lat["final_after_speech_end"]["median"] - 0.8) < 1e-6   # 102.6 - 101.8
    assert abs(lat["first_words"]["median"] - 0.6) < 1e-6              # 100.9 - 100.3
    assert r["heartbeat_gaps_over_5s"] == 1 and r["degraded_details"] == {"scribe slow -> faster_whisper": 1}
    assert reports[1]["captions"] == 1 and reports[1]["minutes"] < 0.1

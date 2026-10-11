"""fixtures/events scenes: every line is valid v3, the scenes match the script, and playback rebases them."""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from contracts.python import here_contract as hc  # noqa: E402
import fake_events  # noqa: E402

REQUIRED = ["front_speech", "rear_speech", "doorbell_rear", "name_called_rear", "unknown_sound", "two_speakers",
            "crowd_noise"]


def test_every_required_scene_exists_and_validates():
    assert set(REQUIRED) <= set(fake_events.scene_names())
    for name in fake_events.scene_names():
        rows = fake_events.load_scene(name)
        assert rows, name
        ts = [d["t"] for d in rows]
        assert ts == sorted(ts) and ts[0] >= 0, name
        for d in rows:
            assert hc.errors(d) == [], (name, d["type"], hc.errors(d))
            assert d["src"] == "laptop" and "seq" not in d and "session" not in d
            assert d["type"] in fake_events.LAPTOP_TYPES


def test_scripted_scenes_are_up_to_date():
    """fixtures/events/<scripted>.jsonl == what the script builds (rebuild: fake_events.py --build-scenes)."""
    for name in fake_events.SCENES:
        if name in fake_events.RECORDED:
            continue
        assert fake_events.build_scene(name) == fake_events.load_scene(name), f"{name}: run --build-scenes"


def finals(rows):
    return [d for d in rows if d["type"] == "utterance" and d["final"]]


def test_scene_content():
    rear = fake_events.load_scene("rear_speech")
    assert [(f["source_id"], f["bearing_deg"]) for f in finals(rear)] == [(1, 180.0)]
    start = next(i for i, d in enumerate(rear) if d["type"] == "speech_start")
    assert start < next(i for i, d in enumerate(rear) if d["type"] == "utterance")

    door = fake_events.load_scene("doorbell_rear")
    ev = [d for d in door if d["type"] == "sound_event"]
    assert ev[0]["label"] == "doorbell" and ev[0]["bearing_deg"] == 165.0 and ev[0]["source_id"] == 1
    alerts = [(a["kind"], a["active"]) for a in door if a["type"] == "alert"]
    assert alerts == [("doorbell", True), ("doorbell", False)]

    name = fake_events.load_scene("name_called_rear")
    al = [a for a in name if a["type"] == "alert"]
    assert al[0]["kind"] == "name_called" and al[0]["ref"]["utt_id"] == finals(name)[0]["utt_id"]

    unk = [d for d in fake_events.load_scene("unknown_sound") if d["type"] == "sound_event"]
    assert unk[0]["label"] == "unknown" and unk[0]["bearing_deg"] is not None

    two = fake_events.load_scene("two_speakers")
    assert {f["source_id"] for f in finals(two)} == {0, 1} and all(f["overlap"] for f in finals(two))
    assert any(len(d["sources"]) == 2 and all(s["speech"] for s in d["sources"])
               for d in two if d["type"] == "sources")

    crowd = fake_events.load_scene("crowd_noise")
    assert len(finals(crowd)) == 1


def test_rebase_moves_times_and_tags_ids():
    rows = fake_events.load_scene("name_called_rear")
    base = time.time()
    msgs = [fake_events.rebase(d, base, speed=2.0, tag="r1") for d in rows]
    for d, m in zip(rows, msgs):
        assert abs(m.t - (base + d["t"] / 2.0)) < 1e-3
        out = m.to_dict()
        assert hc.errors(out) == []
    al = next(m for m in msgs if m.TYPE == "alert")
    assert al.alert_id.endswith("-r1") and al.ref.utt_id.endswith("-r1")


def test_from_log_cuts_a_scene(tmp_path):
    log = tmp_path / "laptop.jsonl"
    lines = [
        {"type": "heartbeat", "src": "laptop", "t": 100.0, "v": 3, "status": "ok"},
        {"type": "sources", "src": "laptop", "t": 100.5, "v": 3, "seq": 4, "_sent": 100.51,
         "sources": [{"id": 0, "bearing_deg": 1.0, "energy": 0.5, "speech": True, "confidence": 0.8}]},
        {"type": "speech_start", "src": "laptop", "t": 101.0, "v": 3, "seq": 5, "utt_id": "u1", "bearing_deg": 1.0,
         "source_id": 0, "confidence": 0.8},
        {"type": "sources", "src": "laptop", "t": 200.0, "v": 3, "sources": []},
    ]
    log.write_text("\n".join(json.dumps(d) for d in lines) + "\n", encoding="utf-8")
    rows = fake_events.from_log(log, 100.0, 150.0, tmp_path / "x.jsonl")
    assert [d["type"] for d in rows] == ["sources", "speech_start"]
    assert rows[0]["t"] == 0.5 and "seq" not in rows[0] and "_sent" not in rows[0]
    assert fake_events.load_scene(str(tmp_path / "x.jsonl")) == rows

"""Every scripted scene (fixtures/events/*.jsonl, K1) through the local relay to a phone, a dashboard and a memory
client at once (TASKS.md K3 B). Each role gets every event message of the scene that its subscription covers,
valid v3, in order; enrichments arrive after their event; and each scene shows what it's for (the turn-around is
behind, two talkers give two tracks, the unknown sound keeps its direction...).

Skips until K1's scenes and `fake_events.play_scene` are on this branch.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from contracts.python import here_contract as hc  # noqa: E402
from laptop.client.relay import RelayClient  # noqa: E402
import fake_events  # noqa: E402
from tests.test_e2e_roles import listen, relay_server  # noqa: E402

SPEED = 8.0
EVENT_TYPES = {"speech_start", "utterance", "sound_event", "alert", "perception_enrichment"}
SCENES = sorted(p.stem for p in (ROOT / "fixtures" / "events").glob("*.jsonl"))

pytestmark = pytest.mark.skipif(not SCENES or not hasattr(fake_events, "play_scene"),
                                reason="needs K1's fixtures/events scenes and fake_events.play_scene")


def play(name):
    rows = fake_events.load_scene(name)

    async def main():
        async with relay_server() as port:
            done = asyncio.Event()
            got = {r: [] for r in ("phone", "dashboard", "memory")}
            tasks = [asyncio.create_task(listen(port, "demo", r, got[r], done, config=r == "phone")) for r in got]
            await asyncio.sleep(0.3)
            client = RelayClient(f"ws://127.0.0.1:{port}/ws", "demo", "e2e", role="laptop")
            runner = asyncio.create_task(client.run())
            while not client.connected:
                await asyncio.sleep(0.02)
            await fake_events.play_scene(client, rows, speed=SPEED, tag="t")
            await asyncio.sleep(0.5)
            done.set()
            await asyncio.gather(*tasks)
            runner.cancel()
            return got

    return rows, asyncio.run(asyncio.wait_for(main(), 60))


def ids(m):
    ref = m.get("ref") or {}
    return m["type"], m.get("utt_id") or m.get("event_id") or m.get("alert_id") or ref.get("utt_id") or \
        ref.get("event_id"), m.get("final"), m.get("active")


@pytest.mark.parametrize("name", SCENES)
def test_scene_through_the_relay(name):
    rows, got = play(name)

    for role, msgs in got.items():
        subs = set(hc.DEFAULT_SUBSCRIBE[role])
        for m in msgs:
            m = {k: v for k, v in m.items() if k != "_rx"}
            assert hc.errors(m) == [], (name, role, m["type"], hc.errors(m))
            assert "*" in subs or m["type"] in subs, (name, role, m["type"])

        # Every event message the role subscribes to arrives, once, in scene order (none coalesced away).
        want = [ids(fake_events.rebase(r, 0, tag="t").to_dict()) for r in rows
                if r["type"] in EVENT_TYPES and ("*" in subs or r["type"] in subs)]
        assert [ids(m) for m in msgs if m["type"] in EVENT_TYPES and m["src"] == "laptop"] == want, (name, role)

        # Enrichment always after the event it refers to.
        for i, m in enumerate(msgs):
            if m["type"] == "perception_enrichment":
                ref = m["ref"].get("utt_id") or m["ref"].get("event_id")
                assert any(x.get("utt_id") == ref or x.get("event_id") == ref for x in msgs[:i]), (name, role, ref)

    phone = got["phone"]
    finals = [m for m in phone if m["type"] == "utterance" and m["final"]]
    sounds = [m for m in phone if m["type"] == "sound_event"]
    alerts = {(a["kind"], a["active"]) for a in phone if a["type"] == "alert"}
    frames = [m["sources"] for m in phone if m["type"] == "sources"]
    assert frames, name

    # Speech: speech_start reaches the phone before that utterance's first words (fast path).
    for f in finals:
        i_start = next(i for i, m in enumerate(phone) if m["type"] == "speech_start" and m["utt_id"] == f["utt_id"])
        i_words = next(i for i, m in enumerate(phone) if m["type"] == "utterance" and m["utt_id"] == f["utt_id"])
        assert i_start < i_words, name

    checks = {
        "front_speech": lambda: finals and all(abs(f["bearing_deg"]) < 30 for f in finals),
        "rear_speech": lambda: finals and all(abs(f["bearing_deg"]) > 150 for f in finals),
        "doorbell_rear": lambda: any(s["label"] == "doorbell" and abs(s["bearing_deg"]) > 150 for s in sounds)
        and {("doorbell", True), ("doorbell", False)} <= alerts,
        "name_called_rear": lambda: {("name_called", True), ("name_called", False)} <= alerts
        and any(abs(f["bearing_deg"]) > 150 for f in finals),
        "unknown_sound": lambda: any(s["label"] == "unknown" and s["bearing_deg"] is not None for s in sounds),
        "two_speakers": lambda: any(len(f) == 2 for f in frames) and len({f["source_id"] for f in finals}) == 2,
        "crowd_noise": lambda: not alerts and any(len(f) == 2 for f in frames),
    }
    if name in checks:
        assert checks[name](), name


def test_every_planned_scene_exists():
    """INTEGRATION_CONTRACT §9 / TASKS K1 E: the seven scenes everyone builds against."""
    assert set(SCENES) >= {"front_speech", "rear_speech", "doorbell_rear", "name_called_rear", "unknown_sound",
                           "two_speakers", "crowd_noise"}


def test_scene_files_are_canonical():
    for name in SCENES:
        for line in (ROOT / "fixtures" / "events" / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                assert "seq" not in d and "session" not in d, name
                assert hc.errors(fake_events.rebase(d, time.time()).to_dict()) == [], name

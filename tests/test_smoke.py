"""End-to-end smoke test (no hardware, no models): relay + tools/fake_events.py + a headless phone.

Asserts the phone-side subscription receives SPEC §4's core moments in order, every message
valid against the contract, routed with the session set, and nothing the phone didn't subscribe to.
"""

import asyncio
import contextlib
import json
import sys
import time
from pathlib import Path

from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from contracts.python import here_contract as hc  # noqa: E402
from laptop.client.relay import RelayClient  # noqa: E402
from relay.relay import serve_relay  # noqa: E402
import fake_events  # noqa: E402

TOKEN = "smoke"
PHONE_TYPES = set(hc.DEFAULT_SUBSCRIBE["phone"])


@contextlib.asynccontextmanager
async def relay_server():
    ready = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(serve_relay("127.0.0.1", 0, TOKEN, log_path=None, ready=ready))
    _, port = await ready
    try:
        yield port
    finally:
        task.cancel()
        await asyncio.wait([task], timeout=5)


async def phone(port, received, done):
    url = f"ws://127.0.0.1:{port}/ws?session=demo&role=phone&token={TOKEN}"
    async with connect(url) as ws:
        await ws.send(hc.Hello(src="phone", t=time.time(), role="phone", subscribe=[]).to_json())
        await ws.send(hc.Config(src="phone", t=time.time(), user_name="Kori", name_aliases=["Cory"]).to_json())
        while not done.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), 0.2)
            except asyncio.TimeoutError:
                continue
            received.append(json.loads(raw))


def test_fake_events_reach_the_phone_in_order():
    async def main():
        async with relay_server() as port:
            received, done = [], asyncio.Event()
            phone_task = asyncio.create_task(phone(port, received, done))
            await asyncio.sleep(0.2)

            laptop_seen = []
            client = RelayClient(f"ws://127.0.0.1:{port}/ws", "demo", TOKEN, role="laptop")
            client.on_config = laptop_seen.append
            runner = asyncio.create_task(client.run())
            while not client.connected:
                await asyncio.sleep(0.02)
            script = fake_events.Script(client, speed=25.0)
            await script.play([1, 2, 3])
            await asyncio.sleep(0.6)                      # alerts end 6 s (script time) later
            done.set()
            await phone_task
            runner.cancel()
            return received, laptop_seen

    received, laptop_seen = asyncio.run(asyncio.wait_for(main(), 60))

    # The laptop got the phone's config (relay replays the latest to new clients).
    assert laptop_seen and laptop_seen[0].user_name == "Kori"

    # Everything the phone received is valid v3, from its subscription, tagged with the session.
    for m in received:
        assert hc.errors(m) == [], (m["type"], hc.errors(m))
        assert m["type"] in PHONE_TYPES and m["session"] == "demo"
    types = [m["type"] for m in received]
    assert "sources" in types and "heartbeat" in types

    finals = [m for m in received if m["type"] == "utterance" and m["final"]]
    assert [round(m["bearing_deg"]) for m in finals] == [35, -35, 35, 180, 180]
    assert finals[3]["text"].startswith("Why is nobody looking")

    # speech_start comes before that utterance's first words (fast path).
    for f in finals:
        idx_start = next(i for i, m in enumerate(received) if m["type"] == "speech_start" and m["utt_id"] == f["utt_id"])
        idx_first = next(i for i, m in enumerate(received) if m["type"] == "utterance" and m["utt_id"] == f["utt_id"])
        assert idx_start < idx_first

    alerts = [m for m in received if m["type"] == "alert"]
    kinds = [(a["kind"], a["active"]) for a in alerts]
    assert ("doorbell", True) in kinds and ("name_called", True) in kinds and ("doorbell", False) in kinds
    door = next(a for a in alerts if a["kind"] == "doorbell")
    assert door["bearing_deg"] == 165.0 and door["ref"]["event_id"]
    name = next(a for a in alerts if a["kind"] == "name_called")
    assert name["bearing_deg"] == 180.0 and name["ref"]["utt_id"] == finals[-1]["utt_id"]
    assert any(m["type"] == "sound_event" and m["label"] == "doorbell" for m in received)

"""End-to-end across every role (no hardware, no models): relay + tools/fake_events.py's core moments, with a
stand-in enricher that sends `perception_enrichment` after the event it refers to (A2's job on the laptop).

A phone, a dashboard and a memory client listen in the same session; a phone in another session must hear
nothing. Asserts each role gets exactly its README §12.10 subscription, every message is valid v3, and an
enrichment reaches the phone, dashboard and memory only after its event. The laptop side knows nothing about
the dashboard or memory: mirroring is the relay's job (Shared/INTEGRATION_CONTRACT.md §1).
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

TOKEN = "e2e"
ENRICH_DELAY_S = 0.05


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


class Enricher:
    """Wraps the laptop's RelayClient: forwards everything, and after each sound_event or final utterance
    sends a perception_enrichment that refers to it (once per event), like A2's attention model would (rules, no
    Gemini). The script may send enrichments of its own; those pass through untouched."""

    def __init__(self, client):
        self.c = client
        self.sent = []
        self.seen = set()

    def send(self, msg):
        self.c.send(msg)
        ref = None
        if isinstance(msg, hc.SoundEvent) and msg.event_id not in self.seen:
            self.seen.add(msg.event_id)
            ref, attention = hc.Ref(event_id=msg.event_id), "important"
        elif isinstance(msg, hc.Utterance) and msg.final and msg.utt_id not in self.seen:
            self.seen.add(msg.utt_id)
            ref, attention = hc.Ref(utt_id=msg.utt_id), "notice"
        if ref is not None:
            asyncio.get_running_loop().call_later(ENRICH_DELAY_S, self._enrich, ref, attention)

    def _enrich(self, ref, attention):
        e = hc.PerceptionEnrichment(src="laptop", t=time.time(), ref=ref, attention=attention,
                                    attention_confidence=0.8, attention_model="rules")
        self.sent.append(e)
        self.c.send(e)


async def listen(port, session, role, received, done, config=False):
    url = f"ws://127.0.0.1:{port}/ws?session={session}&role={role}&token={TOKEN}"
    async with connect(url) as ws:
        await ws.send(hc.Hello(src=role, t=time.time(), role=role, subscribe=[]).to_json())
        if config:
            await ws.send(hc.Config(src=role, t=time.time(), user_name="Kori", name_aliases=["Cory"]).to_json())
        while not done.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), 0.2)
            except asyncio.TimeoutError:
                continue
            m = json.loads(raw)
            m["_rx"] = time.time()
            received.append(m)


def run_session():
    async def main():
        async with relay_server() as port:
            done = asyncio.Event()
            got = {name: [] for name in ("phone", "dashboard", "memory", "stranger")}
            listeners = [
                asyncio.create_task(listen(port, "demo", "phone", got["phone"], done, config=True)),
                asyncio.create_task(listen(port, "demo", "dashboard", got["dashboard"], done)),
                asyncio.create_task(listen(port, "demo", "memory", got["memory"], done)),
                asyncio.create_task(listen(port, "other", "phone", got["stranger"], done)),
            ]
            await asyncio.sleep(0.3)

            client = RelayClient(f"ws://127.0.0.1:{port}/ws", "demo", TOKEN, role="laptop")
            runner = asyncio.create_task(client.run())
            while not client.connected:
                await asyncio.sleep(0.02)
            enricher = Enricher(client)
            await fake_events.Script(enricher, speed=25.0).play([1, 2, 3])
            await asyncio.sleep(0.8)                      # alert ends + last enrichment
            done.set()
            await asyncio.gather(*listeners)
            runner.cancel()
            return got, enricher.sent

    return asyncio.run(asyncio.wait_for(main(), 60))


def first_index(msgs, pred):
    return next(i for i, m in enumerate(msgs) if pred(m))


def refers_to(m, ref):
    return (ref.get("utt_id") and m.get("utt_id") == ref["utt_id"]) or \
        (ref.get("event_id") and m.get("event_id") == ref["event_id"])


def test_every_role_gets_its_subscription_and_enrichment_follows_its_event():
    got, enrichments = run_session()
    phone = got["phone"]
    events = {m["utt_id"] for m in phone if m["type"] == "utterance" and m["final"]} | \
        {m["event_id"] for m in phone if m["type"] == "sound_event"}
    assert len(events) >= 6                               # the core moments: 5 finals + the doorbell
    assert {e.ref.utt_id or e.ref.event_id for e in enrichments} == events

    # Nothing crosses sessions.
    assert got["stranger"] == []

    for role in ("phone", "dashboard", "memory"):
        msgs = got[role]
        assert msgs, role
        subs = set(hc.DEFAULT_SUBSCRIBE[role])
        for m in msgs:
            m = {k: v for k, v in m.items() if k != "_rx"}
            assert hc.errors(m) == [], (role, m["type"], hc.errors(m))
            assert m["session"] == "demo"
            assert "*" in subs or m["type"] in subs, (role, m["type"])

    # Memory gets A3's ingest stream (README §12.10) and none of the 20 Hz positional traffic.
    mem_types = {m["type"] for m in got["memory"]}
    assert {"utterance", "alert", "sound_event", "perception_enrichment"} <= mem_types
    assert not mem_types & {"sources", "speech_start", "heartbeat", "config"}

    # The dashboard sees everything, including the phone's config and the positional stream.
    dash_types = {m["type"] for m in got["dashboard"]}
    assert {"sources", "speech_start", "utterance", "sound_event", "alert", "perception_enrichment",
            "config"} <= dash_types

    # Every enrichment (ours and any the script sends) reaches phone, dashboard and memory, after its event.
    per_role = {}
    for role in ("phone", "dashboard", "memory"):
        msgs = got[role]
        enr = [m for m in msgs if m["type"] == "perception_enrichment"]
        per_role[role] = [m["seq"] for m in enr]
        assert len(enr) >= len(enrichments), role
        for e in enr:
            i_enr = msgs.index(e)
            i_evt = first_index(msgs, lambda m: m["type"] in ("utterance", "sound_event") and refers_to(m, e["ref"]))
            assert i_evt < i_enr, (role, e["ref"])
        door = next(e for e in enr if e["ref"].get("event_id") and e["attention_confidence"] == 0.8)
        assert door["attention"] == "important" and door["attention_model"] == "rules"
    assert per_role["phone"] == per_role["dashboard"] == per_role["memory"]

    # Same messages, same order, for phone and memory on the shared types (the relay doesn't reorder).
    shared = {"utterance", "alert", "sound_event", "perception_enrichment"}
    key = lambda m: (m["type"], m.get("utt_id") or m.get("event_id") or m.get("alert_id"), m.get("seq"))  # noqa: E731
    assert [key(m) for m in got["phone"] if m["type"] in shared] == \
        [key(m) for m in got["memory"] if m["type"] in shared]


def test_local_relay_latency_is_well_under_budget():
    """Laptop observation `t` -> client receive, through the local relay. The ≤100 ms network budget
    (INTEGRATION_CONTRACT §7) is for Vultr; locally the relay hop should be a few ms."""
    got, _ = run_session()
    lags = sorted((m["_rx"] - m["t"]) * 1000 for m in got["phone"] if m["src"] == "laptop")
    assert lags
    p95 = lags[int(0.95 * (len(lags) - 1))]
    assert p95 < 100, f"p95 {p95:.1f} ms"

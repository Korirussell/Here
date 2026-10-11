"""Memory service checks : seed shapes, ingest rules, Ask HERE, replay, the HTTP API, the relay
link and the Neon -> SQLite fallback. Run: python -m pytest extras"""

import asyncio
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from contracts.python import here_contract as hc
from extras.memory import config
from extras.memory.api import make_server
from extras.memory.ask import DEMO_QUERIES, Asker, search_result_hits
from extras.memory.ingest import Ingestor
from extras.memory.memory import Memory
from extras.memory.replay import replay
from extras.memory.store import ResilientStore, open_sqlite

SEED_LINES = [json.loads(line) for line in config.SEED_DAY.read_text(encoding="utf-8").splitlines()]


def quiet(*_):
    pass


@pytest.fixture(scope="module")
def mem():
    m = Memory(store=open_sqlite(":memory:"), log=quiet)
    m.seed()
    return m


def fresh():
    return Memory(store=open_sqlite(":memory:"), log=quiet)


# ---- seed ------------------------------------------------------------------------------------------------

def test_every_seed_line_is_a_valid_canonical_message():
    assert len(SEED_LINES) > 500
    for m in SEED_LINES:
        assert hc.errors(m) == [], m
        assert m["session"].startswith("seeded-")


def test_seed_is_labelled_seeded_and_up_to_date():
    meta = json.loads(config.SEED_SEGMENTS.read_text(encoding="utf-8"))
    assert meta["seeded"] is True and "Not live capture" in meta["note"]
    assert [s["id"] for s in meta["segments"]] == ["morning", "commute", "lecture", "lunch", "meeting", "hackathon",
                                                   "evening"]
    from extras.memory.seed import build_seed
    jsonl, segments = build_seed.build()
    assert jsonl == config.SEED_DAY.read_text(encoding="utf-8"), "run python -m extras.memory.seed.build_seed"
    assert segments == config.SEED_SEGMENTS.read_text(encoding="utf-8")


def test_seed_covers_the_required_moments(mem):
    rows = mem.store.events()
    kinds = {r["label"] for r in rows if r["type"] == "alert"}
    assert {"name_called", "car_horn", "siren", "doorbell"} <= kinds
    persons = {r["person_label"] for r in rows if r["type"] == "utterance"}
    assert {"Person 2", "Person 3"} <= persons
    tones = {r["label"] for r in rows if r["type"] == "utterance"}
    assert len(tones) >= 6
    assert any(r["label"] == "unknown" for r in rows if r["type"] == "sound_event")
    rear_hazards = [r for r in rows if r["type"] == "alert" and r["label"] in ("car_horn", "siren")
                    and abs(r["rel_bearing"]) > 120]
    assert len(rear_hazards) == 2


def test_snapshots_are_fresh():
    from extras.memory.snapshots import write
    assert write(check=True) == [], "run python -m extras.memory snapshots"


def test_precomputed_insights_are_fresh():
    from extras.cloud.insights_local import compute, labels_of, seed_rows
    m, rows = seed_rows()
    ins = compute(rows, "precomputed", labels_of(m), m.meta["user_name"])
    on_disk = json.loads((config.SEED_DIR / "insights.json").read_text(encoding="utf-8"))["insights"]
    assert ins == on_disk, "run python -m extras.cloud.insights_local --write-seed"
    assert all(i["source"] == "precomputed" for i in on_disk)


# ---- ingest ----------------------------------------------------------------------------------------------

def utt(utt_id="u-1", final=True, text="hello there", t=1000.0, bearing=170.0):
    return {"type": "utterance", "v": 3, "src": "laptop", "t": t + 1, "session": "live1", "utt_id": utt_id,
            "t_start": t, "t_end": t + 1, "text": text, "pending": "", "final": final, "bearing_deg": bearing,
            "bearing_spread_deg": 3.0, "source_id": 0, "overlap": False, "tone": "neutral", "tone_confidence": 0.5,
            "loudness": 0.5, "engine": "faster_whisper"}


def test_ingest_keeps_finals_and_applies_attribution_both_orders():
    m = fresh()
    ing = Ingestor(m.store, log=quiet)
    attr = {"type": "attribution", "v": 3, "src": "phone", "t": 1001.5, "session": "live1", "ref": {"utt_id": "u-1"},
            "person_label": "Person 2", "method": "bearing", "score": 0.9}
    assert not ing.accept(utt(final=False))                     # partials are skipped
    ing.accept(attr)                                            # attribution before the final
    ing.accept(utt())
    ing.accept(utt("u-2", t=1010))
    ing.flush_all()
    ing.accept({**attr, "ref": {"utt_id": "u-2"}, "person_label": "Person 3"})   # ... and after
    ing.flush_all()
    rows = {r["ref_id"]: r for r in m.store.events(types=["utterance"])}
    assert rows["u-1"]["person_label"] == "Person 2" and rows["u-2"]["person_label"] == "Person 3"
    assert m.store.search_utterances(["hello"])[0]["person_label"] in ("Person 2", "Person 3")
    assert m.store.counts()["sessions"] == 1


def test_ingest_rel_bearing_from_scene_alert_start_time_and_label_upgrade():
    m = fresh()
    ing = Ingestor(m.store, log=quiet)
    ing.accept({"type": "scene", "v": 3, "src": "phone", "t": 999.0, "session": "s", "heading_deg": 90.0,
                "people": []})
    base = {"type": "sound_event", "v": 3, "src": "laptop", "t": 1001.0, "session": "s", "event_id": "e1",
            "label": "unknown", "category": "other", "emoji": "❔", "text": "Unknown sound", "confidence": 0.3,
            "bearing_deg": -90.0, "t_start": 1000.0, "t_end": 1001.0, "count": 1}
    ing.accept(base)
    ing.accept({**base, "t": 1003.0, "label": "siren", "category": "alert", "emoji": "🚑", "text": "Siren",
                "confidence": 0.8})
    alert = {"type": "alert", "v": 3, "src": "laptop", "t": 1002.0, "session": "s", "alert_id": "a1", "kind": "siren",
             "label": "Siren", "emoji": "🚑", "priority": 3, "bearing_deg": -90.0, "active": True, "confidence": 0.8}
    ing.accept(alert)
    ing.accept({**alert, "t": 1010.0, "active": False})
    ing.flush_all()
    ev = {r["ref_id"]: r for r in m.store.events()}
    assert ev["e1"]["label"] == "siren" and ev["e1"]["rel_bearing"] == 180.0
    assert ev["a1"]["event_time"] == 1002.0 and ev["a1"]["payload"]["active"] is False


def test_ingest_rejects_invalid_and_is_bounded(monkeypatch):
    m = fresh()
    ing = Ingestor(m.store, log=quiet)
    assert not ing.accept({**utt(), "bearing_deg": -180})       # schema rejects -180
    assert ing.stats["invalid"] == 1
    ing.queue = type(ing.queue)(maxlen=3)
    for i in range(5):
        ing.accept(utt(f"u-{i}", t=1000 + i))
    assert len(ing.queue) == 3 and ing.stats["dropped"] == 2


def test_write_failure_never_raises():
    class Broken:
        def write_batch(self, ops):
            raise RuntimeError("db down")
    ing = Ingestor(Broken(), log=quiet)
    ing.accept(utt())
    assert ing.flush_all() == 0 and ing.stats["errors"] == 1


def test_retention_purges_live_keeps_seeded():
    m = fresh()
    m.seed()
    ing = Ingestor(m.store, log=quiet)
    ing.accept(utt(t=time.time() - 30 * 86400))
    ing.flush_all()
    before = m.store.counts()["events"]
    assert m.purge() == 1
    assert m.store.counts()["events"] == before - 1
    assert m.store.has_session(m.meta["session"])


def test_databricks_and_precomputed_insights_coexist(mem):
    from extras.memory.api import insights
    pre = insights(mem)
    assert pre["source"] == "precomputed"
    mem.store.put_insights([{**i, "text": "from spark"} for i in pre["insights"]], "databricks")
    try:
        out = insights(mem)
        assert out["source"] == "databricks" and len(out["insights"]) == len(pre["insights"])
        assert all(i["text"] == "from spark" for i in out["insights"])
        assert out["insights"][0]["data"] == next(p for p in pre["insights"] if p["id"] == out["insights"][0]["id"])["data"]
    finally:
        mem.store.put_insights([], "databricks")


def test_old_insights_table_is_migrated():
    import sqlite3
    from extras.memory.store import Store
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("CREATE TABLE insights (id TEXT PRIMARY KEY, category TEXT, title TEXT, text TEXT, score REAL, "
                 "evidence_ids TEXT, source TEXT NOT NULL, computed_at REAL)")
    s = Store("local", conn)
    s.migrate()
    rows = [{"id": "x", "category": "c", "title": "t", "text": "t", "score": 1.0, "evidence_ids": []}]
    s.put_insights(rows, "precomputed")
    s.put_insights(rows, "databricks")
    assert len(s.insights()) == 2


def test_neon_https_numbers_parameters():
    from extras.memory.store import Store
    assert Store("neon", None, "https").sql("a = ? AND b IN (?, ?)") == "a = $1 AND b IN ($2, $3)"
    assert Store("neon", None, "tcp").sql("a = ?") == "a = %s"


def test_neon_unreachable_falls_back_to_local(tmp_path):
    s = ResilientStore("postgresql://nobody:x@127.0.0.1:1/none?connect_timeout=1", tmp_path / "m.sqlite", log=quiet)
    assert s.kind == "local" and s.fallback_reason.startswith("neon unavailable")


# ---- Ask HERE --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("q, must", [
    (DEMO_QUERIES[0], ["October 15th", "Person 1", "chapters four through seven"]),
    (DEMO_QUERIES[1], ["Person 2", "Person 3", "crowd"]),
    (DEMO_QUERIES[2], ["5 times", "behind you"]),
    (DEMO_QUERIES[3], ["siren", "car horn"]),
    (DEMO_QUERIES[4], ["seeded", "Signals lecture", "Noisy lunch"]),
    ("what did Person 2 say about the fair?", ["Wednesday"]),
])
def test_demo_questions_answer_with_evidence(mem, q, must):
    r = Asker(mem).ask(q, phrase=False)
    assert r["evidence"], q
    for word in must:
        assert word in r["answer"], (word, r["answer"])
    for e in r["evidence"]:
        assert ("utt_id" in e) ^ ("event_id" in e)
        assert {"t", "text", "person_label", "bearing_deg"} <= set(e)


def test_ask_cache(mem):
    a = Asker(mem)
    a.ask("What happened at lunch?", phrase=False)
    assert a.ask("what happened at lunch", phrase=False)["cached"] is True


def test_search_result_is_a_valid_contract_message(mem):
    hits = search_result_hits(mem, "what did Person 2 say about the fair?", 5)
    msg = hc.SearchResult(src="memory", t=1.0, query_id="q-1", results=[hc.SearchHit(**h) for h in hits])
    assert hc.errors(msg.to_dict()) == []
    assert hits and all(h["person_label"] == "Person 2" for h in hits)


# ---- replay ----------------------------------------------------------------------------------------------

def test_replay_policies(mem):
    rows = mem.segment_rows(mem.segment("lunch"))
    default = replay(rows, use_model=False)
    everything = replay(rows, {"sensitivity": 1.0, "crowd_filter": False}, use_model=False)
    assert default["by_category"]["crowd"]["surfaced"] == 0
    assert everything["surfaced"] > default["surfaced"]
    assert default["missed_important"] == 0 and default["kept_important"] >= 2
    commute = mem.segment_rows(mem.segment("commute"))
    low = replay(commute, {"sensitivity": 0.0, "hazard_priority": True}, use_model=False)
    assert low["by_category"]["hazard"]["suppressed"] == 0
    d = default["decisions"][0]
    assert {"event_id", "surfaced", "reason"} <= set(d)


class FakeScorer:
    """Stands in for relevance.RelevanceScorer: fixed scores, records what replay asked for."""
    def __init__(self, scores):
        self.scores, self.requested = scores, []

    def request(self, texts):
        self.requested.extend(texts)

    def cached(self, text):
        return self.scores.get(text)

    def status(self):
        return {"model": "fake", "scored": len(self.scores), "pending": 0, "error": None}


def test_replay_blends_cached_model_scores_for_speech_only(mem):
    rows = mem.segment_rows(mem.segment("lunch"))
    fries = "I'll grab more fries, do you want anything?"
    sc = FakeScorer({fries: 0.0, "Speech": 1.0})
    with_model = replay(rows, {"sensitivity": 0.3}, scorer=sc)
    rules = replay(rows, {"sensitivity": 0.3}, use_model=False)
    assert sc.requested and all(t in {r["text"] for r in rows if r["type"] == "utterance"} for t in sc.requested)
    d = {x["text"]: x for x in with_model["decisions"]}
    assert d[fries]["model_score"] == 0.0 and d[fries]["score"] < {x["text"]: x for x in rules["decisions"]}[fries]["score"]
    assert all("model_score" not in x for x in with_model["decisions"] if x["type"] != "utterance")
    assert with_model["model"]["utterances_scored"] == 1 and with_model["missed_important"] == 0
    assert "model" not in rules


def test_relevance_scorer_failure_keeps_replay_deterministic(tmp_path):
    from extras.memory.relevance import RelevanceScorer
    sc = RelevanceScorer(model_id="nobody/no-such-model-here", cache_path=tmp_path / "c.json", log=quiet)
    sc.request(["hello"])
    sc.wait_idle(60)
    assert sc.error and sc.cached("hello") is None and sc.status()["pending"] == 0


@pytest.mark.skipif(not __import__("os").environ.get("HERE_TEST_MODEL"), reason="set HERE_TEST_MODEL=1 (CPU, ~30 s)")
def test_relevance_model_separates_deadline_from_small_talk(tmp_path):
    from extras.memory.relevance import RelevanceScorer
    sc = RelevanceScorer(cache_path=tmp_path / "c.json", log=quiet)
    deadline = "The midterm exam is next Thursday, October 15th, here in this room."
    chat = "I'll grab more fries, do you want anything?"
    sc.request([deadline, chat])
    assert sc.wait_idle(300) and sc.cached(deadline) > 0.8 > sc.cached(chat)
    assert RelevanceScorer(cache_path=tmp_path / "c.json").cached(deadline) == sc.cached(deadline)   # persisted


# ---- HTTP API + relay link -------------------------------------------------------------------------------

def call(port, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def test_http_api(mem):
    server = make_server(mem, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        h = call(port, "/api/health")
        assert h["ok"] and h["db"] == "local" and h["events"] > 300 and h["sessions"] >= 1
        day = call(port, "/api/day?date=2026-10-09")
        assert day["seeded"] and len(day["segments"]) == 7
        assert {"person_label", "location_id"} <= set(day["events"][0])
        a = call(port, "/api/ask", {"q": "What did my professor say about the exam?"})
        assert a["evidence"] and isinstance(a["cached"], bool)
        ins = call(port, "/api/insights")["insights"]
        assert ins and {"id", "category", "title", "text", "score", "evidence_ids", "source"} <= set(ins[0])
        r = call(port, "/api/replay", {"segment_id": "lunch", "policy": {"sensitivity": 0.9}})
        assert {"surfaced", "suppressed", "kept_important", "by_category", "decisions"} <= set(r)
    finally:
        server.shutdown()


def test_integration_endpoints(mem, monkeypatch, tmp_path):
    for k in ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "VULTR_S3_ENDPOINT", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("extras.cloud.export_bundle.OUT_DIR", tmp_path)
    monkeypatch.setattr("extras.cloud.export_bundle.build_bundle.__defaults__", (tmp_path, None))
    server = make_server(mem, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        names = {i["name"]: i for i in call(port, "/api/integrations")["integrations"]}
        assert set(names) == {"elevenlabs", "vultr", "huggingface", "databricks", "neon", "gemini"}
        assert names["databricks"]["configured"] is False and names["neon"]["configured"] is False
        assert call(port, "/api/insights/refresh", {"go": 1}) == {"ok": True, "started": True, "backend": "precomputed"}
        session = mem.store.sessions()[0]["id"]
        out = call(port, "/api/export", {"session": session})
        assert out["ok"] and out["uploaded"] is False and out["events"] > 0
        assert str(tmp_path) in out["local"]
    finally:
        server.shutdown()


def test_proxied_requests_need_the_token(mem, monkeypatch):
    monkeypatch.setenv("HERE_RELAY_TOKEN", "sekret")
    server = make_server(mem, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def get(path, headers):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
    try:
        proxied = {"X-Forwarded-For": "203.0.113.9"}
        assert get("/api/insights", {}) == 200                               # local dashboard: no token
        assert get("/api/insights", proxied) == 401
        assert get("/api/insights?token=sekret", proxied) == 200
        assert get("/api/insights", {**proxied, "Authorization": "Bearer sekret"}) == 200
        assert get("/api/health", proxied) == 200
    finally:
        server.shutdown()


def test_relay_link_end_to_end(tmp_path):
    """A final utterance from a laptop-role client lands in memory; a search_request gets a search_result."""
    from websockets.asyncio.client import connect

    from extras.memory.service import RelayLink
    from relay.relay import serve_relay

    m = fresh()
    m.seed()
    ing = Ingestor(m.store, log=quiet)

    async def scenario():
        ready = asyncio.get_running_loop().create_future()
        relay_task = asyncio.create_task(serve_relay("127.0.0.1", 0, "tok", None, ready))
        _, port = await ready
        link = RelayLink(m, ing, f"ws://127.0.0.1:{port}/ws", "t1", "tok", log=quiet)
        link_task = asyncio.create_task(link.run())
        while link.ws is None:
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.1)
        async with connect(f"ws://127.0.0.1:{port}/ws?session=t1&role=tool&token=tok") as ws:
            await ws.send(json.dumps({"type": "hello", "v": 3, "src": "tool", "t": 1.0, "role": "tool",
                                      "subscribe": ["search_result"]}))
            await ws.send(json.dumps(utt(text="the robot demo is at noon", t=time.time())))
            await asyncio.sleep(0.3)
            ing.flush_all()
            await ws.send(json.dumps({"type": "search_request", "v": 3, "src": "tool", "t": 2.0, "query_id": "q9",
                                      "text": "robot demo", "limit": 3}))
            reply = json.loads(await asyncio.wait_for(ws.recv(), 5))
        for task in (link_task, relay_task):            # the link first, so the relay has no open socket
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return reply

    reply = asyncio.run(scenario())
    assert reply["type"] == "search_result" and reply["query_id"] == "q9"
    assert hc.errors({k: v for k, v in reply.items() if k != "session"} | {"session": "t1"}) == []
    assert reply["results"][0]["text"] == "the robot demo is at noon"
    assert any(r["id"] == "t1" for r in m.store.sessions())

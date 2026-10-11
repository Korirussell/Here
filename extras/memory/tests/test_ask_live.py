"""Ask HERE during a live demo: the cache survives live ingest, few store round trips (each one is an HTTPS call on
Neon), and a Gemini outage (quota, bad key) pauses Gemini instead of costing every question a doomed call."""

import io
import json
import time
import urllib.error

import pytest

from extras.memory import ask as ask_mod
from extras.memory import config
from extras.memory.ask import Asker
from extras.memory.ingest import Ingestor
from extras.memory.memory import Memory
from extras.memory.store import open_sqlite


def quiet(*_):
    pass


class Counting:
    """Wraps a store and counts calls per method (on Neon each is one HTTPS round trip)."""

    def __init__(self, store):
        self._s = store
        self.calls = []

    def __getattr__(self, name):
        attr = getattr(self._s, name)
        if not callable(attr):
            return attr

        def wrapped(*a, **k):
            self.calls.append(name)
            return attr(*a, **k)
        return wrapped


@pytest.fixture
def counted():
    store = Counting(open_sqlite(":memory:"))
    m = Memory(store=store, log=quiet)
    m.seed()
    ask_mod._cache.clear()
    ask_mod.GEMINI.reset()
    yield m, store
    ask_mod._cache.clear()
    ask_mod.GEMINI.reset()


def live_utt(n, t):
    return {"type": "utterance", "v": 3, "src": "laptop", "t": t + 1, "session": "live-demo", "utt_id": f"u-live-{n}",
            "t_start": t, "t_end": t + 1, "text": f"live line {n}", "pending": "", "final": True, "bearing_deg": 10.0,
            "bearing_spread_deg": 3.0, "source_id": 0, "overlap": False, "tone": "neutral", "tone_confidence": 0.5,
            "loudness": 0.5, "engine": "faster_whisper"}


def test_live_ingest_does_not_empty_the_ask_cache(counted):
    m, _ = counted
    a = Asker(m)
    a.ask("What did my professor say about the exam?", phrase=False)
    ing = Ingestor(m.store, log=quiet)
    ing.accept(live_utt(1, time.time()))
    ing.flush_all()
    assert a.ask("What did my professor say about the exam?", phrase=False)["cached"] is True


def test_ask_cache_expires(counted, monkeypatch):
    m, _ = counted
    a = Asker(m)
    now = [1000.0]
    monkeypatch.setattr(ask_mod, "monotonic", lambda: now[0])
    a.ask("What happened at lunch?", phrase=False)
    now[0] += config.ASK_CACHE_S + 1
    assert a.ask("What happened at lunch?", phrase=False)["cached"] is False


def test_ask_uses_few_store_round_trips(counted):
    m, store = counted
    a = Asker(m)
    a.ask("When did someone call my name?", phrase=False)            # first call may load sessions once
    store.calls.clear()
    a.ask("What did my professor say about the exam?", use_cache=False, phrase=False)
    assert "counts" not in store.calls
    assert len(store.calls) <= 3, store.calls


class HTTP429(urllib.error.HTTPError):
    def __init__(self, delay="16859s", code=429):
        body = {"error": {"code": code, "status": "RESOURCE_EXHAUSTED",
                          "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}]}}
        super().__init__("https://example.invalid", code, "Too Many Requests", {}, io.BytesIO(json.dumps(body).encode()))


def test_gemini_quota_pauses_phrasing_and_answers_at_once(counted, monkeypatch):
    m, _ = counted
    calls = []

    def urlopen(req, timeout=None):
        calls.append(req)
        time.sleep(0.05)
        raise HTTP429()
    monkeypatch.setattr(ask_mod.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(config, "env", lambda k, d=None: "test-key" if k == "GEMINI_API_KEY" else d)
    a = Asker(m)
    first = a.ask("What did my professor say about the exam?", use_cache=False)
    assert first["phrased_by"] == "template" and len(calls) == 1
    t0 = time.perf_counter()
    second = a.ask("What happened at lunch?", use_cache=False)
    assert len(calls) == 1 and second["phrased_by"] == "template"       # paused: no second doomed call
    assert time.perf_counter() - t0 < 1.0
    st = ask_mod.GEMINI.status()
    assert st["state"] == "paused" and "quota" in st["reason"] and 0 < st["retry_in_s"] <= config.GEMINI_PAUSE_MAX_S

"""A2 enrichment: attention tiers, JEV-9B prompt/probabilities, worker bounds, Gemini, perception_enrichment.
Fakes only: pytest never downloads a model or calls Gemini."""

import io
import json
import math
import threading
import time
import urllib.error

import pytest

from contracts.python.here_contract import Alert, PerceptionEnrichment, Ref, SoundEvent, Utterance
from laptop.enrich import attention as A
from laptop.enrich.enricher import Enricher
from laptop.enrich.gemini import GeminiContext, parse
from laptop.enrich.states import DEMO_STATES, sound_state, speech_state


class Probs(A.Decider):
    def __init__(self, name, probs=None, delay=0.0, fail=False, directed=0.8):
        self.name, self.probs, self.delay, self.fail, self.dir = name, probs, delay, fail, directed
        self.calls = 0

    def attention(self, job):
        self.calls += 1
        time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("CUDA out of memory")
        return self.probs

    def directed(self, job):
        return self.dir


SURE = {"ambient": 0.05, "notice": 0.1, "important": 0.8, "urgent": 0.05}
UNSURE = {"ambient": 0.3, "notice": 0.3, "important": 0.2, "urgent": 0.2}


def job(**kw):
    return A.Job(kw.pop("key", "s-1"), kw.pop("kind", "sound"), kw.pop("state", "Sound: doorbell."), **kw)


# ---- rules + tiers -------------------------------------------------------------------------------

@pytest.mark.parametrize("kw,want", [
    ({"priority": 3, "label": "siren"}, "urgent"),
    ({"priority": 2, "label": "doorbell"}, "important"),
    ({"label": "vehicle"}, "notice"),
    ({"label": "unknown", "energy": 0.3}, "ambient"),
    ({"label": "unknown", "energy": 0.9}, "notice"),
    ({"label": "music"}, "ambient"),
    ({"kind": "speech", "label": "speech", "name_called": True}, "important"),
])
def test_rules_table(kw, want):
    assert A.RulesDecider().pick(job(**kw)) == want


def test_tier_answers_when_sure():
    d = A.TieredDecider([Probs("tier1", SURE)], {"tier1": 1.0}).decide(job(ask_directed=True))
    assert (d.attention, d.model, d.directed) == ("important", "tier1", 0.8)
    assert d.rules_attention == "notice"


def test_timeout_error_and_unsure_fall_through_to_rules():
    tiers = [Probs("slow", SURE, delay=0.5), Probs("broken", fail=True), Probs("unsure", UNSURE)]
    t = A.TieredDecider(tiers, {"slow": 0.05, "broken": 1.0, "unsure": 1.0})
    t0 = time.perf_counter()
    d = t.decide(job(priority=3, label="siren"))
    assert time.perf_counter() - t0 < 0.4                      # the slow tier didn't hold the answer
    assert (d.attention, d.model) == ("urgent", "rules")
    assert [x[1].split()[0] for x in d.tried] == ["timeout", "error", "unsure", "ok"]


def test_a_model_never_talks_an_alert_down():
    calm = {"ambient": 0.9, "notice": 0.05, "important": 0.03, "urgent": 0.02}
    d = A.TieredDecider([Probs("t1", calm)], {}).decide(job(priority=3, label="siren"))
    assert (d.attention, d.model) == ("urgent", "rules") and d.tried[0][1].startswith("below floor")
    d = A.TieredDecider([Probs("t1", calm)], {}).decide(job(kind="speech", name_called=True))
    assert d.attention == "important"
    d = A.TieredDecider([Probs("t1", calm)], {}).decide(job(label="music"))      # no alert: the model may say ambient
    assert (d.attention, d.model) == ("ambient", "t1")


def test_name_called_keeps_directed_high():
    d = A.TieredDecider([Probs("t1", SURE, directed=0.07)], {}).decide(job(kind="speech", name_called=True,
                                                                            ask_directed=True))
    assert d.model == "t1" and d.directed >= 0.9
    d = A.TieredDecider([Probs("t1", SURE, directed=0.07)], {}).decide(job(kind="speech", ask_directed=True))
    assert d.directed == 0.07


def test_second_tier_takes_over():
    d = A.TieredDecider([Probs("t1", fail=True), Probs("t2", SURE)], {}).decide(job())
    assert d.model == "t2"


# ---- JEV-9B (fake forward: no model) ----------------------------------------------------------------

JUDGE = {"slots": {"ranges": {"noul": [0, 2], "score": [2, 8], "choice": [8, 24]}}}
TEMPS = {"noul": 1.0, "choice": 1.0, "score": 1.0}


def test_jev_prompt_matches_the_model_card():
    p = A.jev_prompt("choice", "Sound: doorbell.", "How much?", ["x", "y"])
    assert p == "[kind] choice\n[state] Sound: doorbell.\n[question] How much?\n[options]\nA) x\nB) y\n[decision]:"
    assert A.jev_prompt("noul", "s", "q", ["false", "true"]).endswith("[options]\nfalse\ntrue\n[decision]:")


def test_jev_probabilities_from_the_head_slots():
    seen = []

    def forward(text):
        seen.append(text)
        z = [0.0] * 24
        if "[kind] choice" in text:
            z[8:12] = [0.0, 0.0, math.log(8.0), 0.0]          # slot 10 = "important"
            z[12] = 50.0                                        # an inactive choice slot: must be masked out
        else:
            z[0:2] = [0.0, math.log(3.0)]                       # P(true) = 0.75
        return z

    jev = A.JevDecider(forward=forward, judge_cfg=JUDGE, temps={**TEMPS, "choice": 1.0})
    p = jev.attention(job())
    assert max(p, key=p.get) == "important" and abs(p["important"] - 8 / 11) < 1e-6
    assert abs(jev.directed(job()) - 0.75) < 1e-6
    assert "A) ambient:" in seen[0] and "D) urgent:" in seen[0]


def test_jev_temperature():
    p = A.jev_probs([0.0, math.log(3.0)] + [0.0] * 22, "noul", 2, JUDGE["slots"]["ranges"], {"noul": 2.0})
    assert abs(p[1] - math.sqrt(3) / (1 + math.sqrt(3))) < 1e-9


# ---- worker ------------------------------------------------------------------------------------------

def test_worker_queue_drops_oldest_and_caches():
    out = []
    w = A.AttentionWorker(A.TieredDecider([], {}), lambda j, d: out.append(j.key), queue_max=4)
    for k in range(7):
        w.submit(job(key=f"s-{k}", state=f"state {k}"))
    assert [j.key for j in w.queue] == ["s-3", "s-4", "s-5", "s-6"] and w.dropped == 3
    a = w.process(job(key="a", state="same"))
    b = w.process(job(key="b", state="same"))
    assert b.tried == [("cache", "hit")] and a.attention == b.attention


def test_zeroshot_builds_one_hypothesis_per_option_and_maps_back():
    seen = []

    def score(premise, hyps):                      # fake NLI: favours the third hypothesis (ATTENTION_LABELS order)
        seen.append((premise, list(hyps)))
        raw = [3.0 if i == 2 else 0.0 for i in range(len(hyps))]
        e = [math.exp(x) for x in raw]
        return [x / sum(e) for x in e]

    d = A.ZeroShotDecider(score=score)
    assert len(seen) == 1                          # warmed at construction
    probs = d.attention(job())
    assert set(probs) == set(A.ATTENTION_LABELS) and abs(sum(probs.values()) - 1) < 1e-6
    assert max(probs, key=probs.get) == "important"
    assert len(seen[-1][1]) == len(A.ATTENTION_LABELS) and seen[-1][0] == job().state
    assert 0.0 <= d.directed(job()) <= 1.0 and len(seen[-1][1]) == 2


def test_worker_thread_runs_jobs():
    done = threading.Event()
    w = A.AttentionWorker(A.TieredDecider([], {}), lambda j, d: done.set()).start()
    w.submit(job())
    assert done.wait(2.0)
    w.stop()


# ---- states: never a direction relative to the wearer ---------------------------------------------------

def test_states_never_mention_direction():
    words = ("behind", "left", "right", "front", "deg", "bearing")
    for state, *_ in DEMO_STATES:
        assert not any(w in state.lower() for w in words), state
    assert "conversation" in sound_state("doorbell", 0.7, 0.6, conversation=True)
    assert "wearer's name" in speech_state("Hey Kori", name_called=True)


# ---- Gemini ----------------------------------------------------------------------------------------------

GOOD = {"label": "Delivery at the door", "category": "household", "directed_at_user": 0.4,
        "note": "Someone is probably at the door with a delivery."}


def test_gemini_parse_is_strict():
    assert parse(json.dumps(GOOD))["category"] == "household"
    assert parse('{"label": "x"}') is None and parse("not json") is None
    assert parse(json.dumps({**GOOD, "category": "weird", "directed_at_user": 7}))["directed_at_user"] == 1.0


def test_gemini_without_a_key_never_calls():
    calls = []
    g = GeminiContext(api_key="", transport=lambda b: calls.append(b))
    assert not g.available and g.ask("k", "e", [], lambda *a: None) is False and calls == []


def test_gemini_debounce_cache_and_failure():
    clock = [0.0]
    results = []
    done = threading.Event()

    def on_done(key, result, meta):
        results.append((key, result, meta))
        done.set()

    def transport(body):
        assert "doorbell" in body["contents"][0]["parts"][0]["text"]
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(GOOD)}]}}],
                "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 30}}

    g = GeminiContext(api_key="k", transport=transport, min_gap_s=2.0, cache_s=60, clock=lambda: clock[0])
    assert g.ask("e1", "sound: doorbell", [], on_done)
    assert done.wait(2.0) and results[0][1]["note"].startswith("Someone")
    assert g.ask("e2", "sound: car horn", [], on_done) is False          # debounced
    clock[0] = 10.0
    done.clear()
    assert g.ask("e3", "sound: doorbell", [], on_done)                  # cached
    assert results[-1][2]["cached"] and g.stats["tokens_in"] == 120

    def boom(body):
        raise TimeoutError("slow")
    bad = GeminiContext(api_key="k", transport=boom, clock=lambda: 0.0)
    done.clear()
    bad.ask("x", "sound: siren", [], on_done)
    assert done.wait(2.0) and results[-1][1] is None and "TimeoutError" in results[-1][2]["error"]


def http_error(code, retry_delay=None):
    details = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}] if retry_delay else []
    body = json.dumps({"error": {"code": code, "details": details}}).encode()
    return urllib.error.HTTPError("https://example.invalid", code, "error", {}, io.BytesIO(body))


def test_gemini_quota_error_pauses_calls_until_retry_delay():
    clock = [0.0]
    calls = []
    done = threading.Event()

    def transport(body):
        calls.append(body)
        raise http_error(429, "16859s")
    g = GeminiContext(api_key="k", transport=transport, min_gap_s=0.0, clock=lambda: clock[0])
    assert g.ask("e1", "sound: doorbell", [], lambda *a: done.set()) and done.wait(2.0)
    for i in range(5):                                         # paused: no doomed calls, nothing blocks
        clock[0] += 1.0
        assert g.ask(f"e{i}", f"sound: knock {i}", [], lambda *a: None) is False
    assert len(calls) == 1 and g.stats["paused"] == 5
    assert "quota" in g.problem and g.status()["state"] == "paused"
    clock[0] += g.pause_max_s                                  # capped retryDelay passed: one retry allowed
    done.clear()
    assert g.ask("e9", "sound: siren", [], lambda *a: done.set()) and done.wait(2.0) and len(calls) == 2


def test_gemini_timeout_does_not_pause():
    def transport(body):
        raise TimeoutError("slow")
    done = threading.Event()
    g = GeminiContext(api_key="k", transport=transport, min_gap_s=0.0, clock=lambda: 0.0)
    g.ask("e1", "sound: doorbell", [], lambda *a: done.set())
    assert done.wait(2.0) and g.problem is None and g.status()["state"] == "on"


def test_enrichment_problem_reaches_the_heartbeat_detail_without_degrading():
    from laptop.app import Laptop
    app = Laptop.__new__(Laptop)                               # heartbeat() only needs these attributes
    app.directions = app.audio = type("D", (), {"problems": [], "name": "builtin"})()
    app.speech = None
    app.detail_sources = [lambda: "Gemini paused: quota exhausted (retry in 10 min)", lambda: None]
    hb = app.heartbeat()
    assert hb["status"] == "ok" and "quota exhausted" in hb["detail"]


# ---- the enricher: perception_enrichment after the canonical event --------------------------------------------

class Client:
    def __init__(self):
        self.sent = []
        self.log = None

    def send(self, msg):
        self.sent.append(msg)
        return True


class FakeGemini:
    model = "gemini-test"
    available = True

    def __init__(self, result=GOOD):
        self.asked, self.result = [], result

    def ask(self, key, event, recent, on_done):
        self.asked.append(event)
        on_done(key, self.result, {"ms": 5.0, "model": self.model})
        return True


def sound(event_id="s-1", label="doorbell", count=1, conf=0.7, t=100.0):
    return SoundEvent(src="laptop", t=t, event_id=event_id, label=label, category="alert", emoji="🔔",
                      text=label.replace("_", " ").title(), confidence=conf, bearing_deg=150.0, t_start=t - 1,
                      t_end=t, count=count, model="fake/model")


def utt(text, utt_id="u-1", final=True, t=100.0):
    return Utterance(src="laptop", t=t, utt_id=utt_id, text=text, pending="", final=final, bearing_deg=30.0,
                     bearing_spread_deg=5.0, source_id=0, t_start=t - 2, t_end=t, overlap=False, tone="neutral", tone_confidence=0.5,
                     loudness=0.5, engine="fake")


def make(decider=None, gemini=None):
    c = Client()
    e = Enricher(c.send, decider=decider, gemini=gemini or FakeGemini(result=None))
    e.wrap(c)
    return c, e


def drain(e):
    while e.worker.queue:
        e.worker.process(e.worker.queue.popleft())


def test_sound_event_gets_attention_after_it_was_sent():
    c, e = make(A.TieredDecider([Probs("hf/zs", SURE)], {}))
    c.send(sound())
    assert [m.TYPE for m in c.sent] == ["sound_event"]          # sent first, untouched
    drain(e)
    pe = c.sent[-1]
    assert isinstance(pe, PerceptionEnrichment) and pe.ref.event_id == "s-1"
    assert (pe.attention, pe.attention_model) == ("important", "hf/zs") and pe.context is None


def test_count_bumps_are_not_rejudged_but_upgrades_are():
    c, e = make()
    c.send(sound(label="unknown", conf=0.4))
    c.send(sound(label="unknown", count=2, conf=0.4))           # same event, more repeats
    assert len(e.worker.queue) == 1
    c.send(sound(label="doorbell"))                             # unknown -> doorbell, same event_id
    assert len(e.worker.queue) == 2
    drain(e)
    pes = [m for m in c.sent if m.TYPE == "perception_enrichment"]
    assert [p.attention for p in pes] == ["ambient", "important"] and all(p.attention_model == "rules" for p in pes)


def test_final_utterance_asks_directed_and_name_called_is_important():
    c, e = make(A.TieredDecider([Probs("hf/zs", UNSURE)], {}))   # unsure model -> rules
    c.send(Alert(src="laptop", t=100.0, alert_id="a-1", kind="name_called", label="Name called", emoji="🗣️",
                 priority=2, bearing_deg=30.0, active=True, confidence=0.9, ref=Ref(utt_id="u-1")))
    c.send(utt("Hey Kori, the pizza is here!", final=False))
    c.send(utt("Hey Kori, the pizza is here!"))
    drain(e)
    pe = c.sent[-1]
    assert pe.ref.utt_id == "u-1" and pe.attention == "important" and pe.directed_at_user == 0.9


def test_gemini_context_rides_along_or_updates():
    g = FakeGemini()
    c, e = make(gemini=g)
    c.send(sound(label="siren", conf=0.9))
    assert "siren" in g.asked[0]
    drain(e)
    pe = c.sent[-1]
    assert pe.attention == "urgent" and pe.context.startswith("Someone") and pe.context_model == "gemini-test"


def test_everything_down_changes_nothing_else():
    c, e = make(A.TieredDecider([Probs("t1", fail=True)], {}), FakeGemini(result=None))
    c.send(sound())
    c.send(utt("Hello there everyone"))
    drain(e)
    canonical = [m for m in c.sent if m.TYPE != "perception_enrichment"]
    assert [m.TYPE for m in canonical] == ["sound_event", "utterance"]
    assert all(m.attention_model == "rules" for m in c.sent if m.TYPE == "perception_enrichment")


def test_a_broken_enricher_never_breaks_sending():
    c, e = make()

    def boom(msg):
        raise RuntimeError("bug")
    e.observe = boom
    assert c.send(sound()) is True and c.sent[0].TYPE == "sound_event"


def test_decisions_are_logged_with_probabilities(tmp_path):
    c, e = make(A.TieredDecider([Probs("hf/zs", SURE)], {}))
    e.log = open(tmp_path / "log.jsonl", "w", encoding="utf-8")
    c.send(sound())
    drain(e)
    e.log.close()
    rows = [json.loads(x) for x in (tmp_path / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    row = next(r for r in rows if r["type"] == "attention_decision")
    assert row["probs"]["important"] == 0.8
    assert row["rules_attention"] == "important" and row["_log_only"] is True


def test_enrichment_message_validates_against_the_schema():
    import jsonschema
    from pathlib import Path
    schema = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "schemas" /
                         "perception_enrichment.schema.json").read_text(encoding="utf-8"))
    c, e = make(A.TieredDecider([Probs("hf/zs", SURE)], {}), FakeGemini())
    c.send(utt("Can you pass me the charger?"))
    drain(e)
    msg = c.sent[-1].to_dict()
    jsonschema.validate(msg, schema)

"""Turn canonical relay messages into memory rows (TASKS A3 B).

Stored unchanged as `payload`, plus normalized columns. What is kept:
- `utterance`: finals only (partials are a streaming UI detail); also indexed for full-text search.
- `sound_event`, `alert`: every update replaces the row with the same event_id / alert_id (labels upgrade, alerts end).
- `perception_enrichment`, `attribution`: one row per referenced event; attribution also sets `person_label` on the
  utterance or sound it refers to (whichever arrives first).
- `scene`: never stored; only the wearer's latest heading per session, so `rel_bearing` (and "behind me") can be
  computed server-side from the phone's own data (INTEGRATION_CONTRACT §3).

`accept()` is cheap and never blocks; `flush()` does one batched write. The queue is bounded and drops the oldest.
"""

import time
from collections import OrderedDict, deque

from contracts.python import here_contract as hc

from . import config

STORED = {"utterance", "sound_event", "alert", "perception_enrichment", "attribution"}


def ref_id(msg):
    t = msg["type"]
    if t == "utterance":
        return msg["utt_id"]
    if t == "sound_event":
        return msg["event_id"]
    if t == "alert":
        return msg["alert_id"]
    ref = msg.get("ref") or {}
    return ref.get("utt_id") or ref.get("event_id") or ""


def event_time(msg):
    return msg.get("t_start", msg["t"])


class Ingestor:
    def __init__(self, store, origin="live", locate=None, log=print):
        self.store = store
        self.origin = origin
        self.locate = locate or (lambda session, t: config.env("HERE_LOCATION", config.LIVE_LOCATION) or None)
        self.log = log
        self.queue = deque(maxlen=config.QUEUE_MAX)
        self.headings = {}                      # session -> (heading_deg, t)
        self.persons = OrderedDict()            # (session, ref_id) -> person_label, bounded
        self.first_t = OrderedDict()            # (session, alert_id) -> first t: an alert's end keeps its start time
        self.stats = {"accepted": 0, "skipped": 0, "invalid": 0, "dropped": 0, "written": 0, "errors": 0}

    # ---- fast path (event loop) ------------------------------------------------------------------------

    def accept(self, msg):
        t = msg.get("type")
        session = msg.get("session") or config.env("HERE_SESSION", config.SESSION)
        if t == "scene":
            if isinstance(msg.get("heading_deg"), (int, float)):
                self.headings[session] = (float(msg["heading_deg"]), float(msg.get("t", time.time())))
            return False
        if t not in STORED or (t == "utterance" and not msg.get("final")):
            self.stats["skipped"] += 1
            return False
        if hc.errors(msg):
            self.stats["invalid"] += 1
            return False
        if len(self.queue) == self.queue.maxlen:
            self.stats["dropped"] += 1
        self.queue.append(self.normalize(msg, session))
        self.stats["accepted"] += 1
        return True

    def rel_bearing(self, session, bearing, t):
        h = self.headings.get(session)
        if bearing is None or h is None or abs(t - h[1]) > config.HEADING_MAX_AGE_S:
            return None
        return round(hc.wrap(bearing - h[0]), 1)

    def normalize(self, msg, session):
        t = msg["type"]
        rid = ref_id(msg)
        et = event_time(msg)
        if t == "alert":
            et = self.first_t.setdefault((session, rid), et)
            while len(self.first_t) > 4000:
                self.first_t.popitem(last=False)
        bearing = msg.get("bearing_deg")
        ops = []
        person = None
        if t == "attribution":
            person = msg.get("person_label")
            self.persons[(session, rid)] = person
            while len(self.persons) > 4000:
                self.persons.popitem(last=False)
        else:
            person = self.persons.get((session, rid))
        label = {"sound_event": msg.get("label"), "alert": msg.get("kind"), "utterance": msg.get("tone"),
                 "perception_enrichment": msg.get("attention"), "attribution": msg.get("method")}[t]
        text = {"utterance": msg.get("text"), "sound_event": msg.get("text"), "alert": msg.get("label"),
                "perception_enrichment": msg.get("context"), "attribution": msg.get("person_label")}[t]
        conf = msg.get("confidence", msg.get("attention_confidence", msg.get("tone_confidence", msg.get("score"))))
        rel = self.rel_bearing(session, bearing, et)
        loc = self.locate(session, et)
        ops.append(("event", {
            "session": session, "origin": self.origin, "type": t, "ref_id": rid, "source_id": msg.get("source_id"),
            "world_bearing": bearing, "rel_bearing": rel, "label": label, "text": text, "confidence": conf,
            "person_label": person, "location_id": loc, "event_time": et, "ingest_time": time.time(),
            "payload": msg}))
        if t == "utterance":
            ops.append(("utterance", {
                "session": session, "origin": self.origin, "utt_id": rid, "t_start": msg["t_start"],
                "t_end": msg["t_end"], "text": msg["text"], "tone": msg.get("tone"), "person_label": person,
                "world_bearing": bearing, "rel_bearing": rel, "location_id": loc}))
        if t == "attribution":
            ops.append(("person", session, rid, person))
        return ops

    # ---- slow path (worker thread) ---------------------------------------------------------------------

    def flush(self, max_items=config.FLUSH_MAX):
        batch = []
        while self.queue and len(batch) < max_items:
            batch.extend(self.queue.popleft())
        if not batch:
            return 0
        spans = {}                              # every batch upserts its sessions (survives a Neon -> SQLite switch)
        for op in batch:
            if op[0] == "event":
                lo, hi = spans.get(op[1]["session"], (op[1]["event_time"], op[1]["event_time"]))
                spans[op[1]["session"]] = (min(lo, op[1]["event_time"]), max(hi, op[1]["event_time"]))
        label = "seeded synthetic day" if self.origin == "seeded" else "live capture"
        batch = [("session", {"id": s, "origin": self.origin, "label": label, "first": lo, "t": hi})
                 for s, (lo, hi) in spans.items()] + batch
        try:
            self.store.write_batch(batch)
        except Exception as e:
            self.stats["errors"] += 1
            self.log(f"[memory] write failed ({type(e).__name__}: {e}); {len(batch)} rows lost")
            return 0
        n = sum(1 for op in batch if op[0] == "event")
        self.stats["written"] += n
        return n

    def flush_all(self):
        n = 0
        while self.queue:
            n += self.flush()
        return n

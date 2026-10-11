"""Ask HERE: a question in, an answer plus evidence events out.

1. Understand the question with plain rules: who (Person N, or a role such as "professor" from the segment's
   roles), when (a segment: lunch, lecture...), what kind (name called, behind me, summary).
2. Retrieve: Postgres full-text (Neon) / SQLite FTS5 over final utterances, plus the stored alerts and sounds.
3. Phrase it with a template. If GEMINI_API_KEY is set, Gemini rewrites the template answer from the same
   evidence (4 s timeout, falls back to the template). The evidence is always what the database returned.
   A quota (429) or key (400/401/403) error pauses Gemini until the server's retryDelay (capped at
   GEMINI_PAUSE_MAX_S), so an exhausted key costs one failed call, not one per question (`GEMINI.status()`).

Answers are cached in-process per question for ASK_CACHE_S (live ingest used to empty the cache every few seconds
during a demo, so the demo questions were never instant). The five demo questions are warmed at startup
(`DEMO_QUERIES`). On Neon every store call is an HTTPS round trip, so an uncached answer uses as few as it can.
"""

import json
import re
import threading
import urllib.error
import urllib.request
from collections import Counter
from time import monotonic

from . import config
from .memory import DAY_TYPES, clock, public_event
from .replay import replay

HAZARD_KINDS = {"car_horn", "siren", "alarm", "loud_bang", "baby_crying"}
DEMO_QUERIES = [
    "What did my professor say about the exam?",
    "What happened at lunch?",
    "When did someone call my name?",
    "What important thing happened behind me?",
    "Summarize my day.",
]
STOP = set("""a about after all am an and any are as at be been before but by can could day did do does doing during
for from get got had has have he her him his how i if in into is it its me mine my of on or our said say says she
so some someone something that the their them then there they thing this to today up was we were what when where
which who why will with you your yours tell told talk talked happened happen important ask asked asking
anything everything""".split())
ROLE_WORDS = {"professor": "professor", "prof": "professor", "teacher": "professor", "lecturer": "professor",
              "roommate": "roommate", "classmate": "classmate", "teammate": "teammate", "judge": "judge",
              "judges": "judge"}
SEGMENT_WORDS = {"lunch": "lunch", "lecture": "lecture", "class": "lecture", "morning": "morning",
                 "breakfast": "morning", "commute": "commute", "crosswalk": "commute", "street": "commute",
                 "walk": "commute", "meeting": "meeting", "lab": "meeting", "hackathon": "hackathon",
                 "presentation": "hackathon", "pitch": "hackathon", "evening": "evening", "tonight": "evening",
                 "dinner": "evening", "night": "evening"}
_cache = {}                                       # (question, phrase) -> (monotonic time, response)


class GeminiCircuit:
    """Pauses Gemini after a quota or key error. `paused()` is checked before every call; `trip(err)` opens it for
    the server's retryDelay (from the error details) capped at GEMINI_PAUSE_MAX_S."""

    def __init__(self, clock=monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.until, self.reason = 0.0, None

    def paused(self):
        return self.clock() < self.until

    def trip(self, err):
        """Pause on HTTP 429 (quota) or 400/401/403 (key); other errors (timeouts) don't pause. True if it paused."""
        code = getattr(err, "code", None)
        if code not in (400, 401, 403, 429):
            return False
        delay = config.GEMINI_PAUSE_MAX_S if code != 429 else retry_delay_s(err, config.GEMINI_PAUSE_DEFAULT_S)
        delay = max(1.0, min(delay, config.GEMINI_PAUSE_MAX_S))
        with self.lock:
            fresh = not self.paused()
            self.until = self.clock() + delay
            self.reason = "quota exhausted" if code == 429 else f"key rejected (HTTP {code})"
        if fresh:
            print(f"[memory] gemini: {self.reason}, paused {delay:.0f} s (answers use the template)", flush=True)
        return True

    def status(self):
        if not config.env("GEMINI_API_KEY"):
            return {"state": "off", "reason": "no GEMINI_API_KEY", "model": config.GEMINI_MODEL}
        if self.paused():
            return {"state": "paused", "reason": self.reason, "retry_in_s": round(self.until - self.clock()),
                    "model": config.GEMINI_MODEL}
        return {"state": "on", "model": config.GEMINI_MODEL}


GEMINI = GeminiCircuit()


def retry_delay_s(err, default):
    """The `retryDelay` ("16859s") Google puts in a 429's error details, else `default`."""
    try:
        body = json.loads(err.read().decode("utf-8"))
        for d in body.get("error", {}).get("details", []):
            if str(d.get("retryDelay", "")).endswith("s"):
                return float(d["retryDelay"][:-1])
    except Exception:  # noqa: BLE001 - an unreadable body just means the default pause
        pass
    return default


def norm(q):
    return " ".join(re.findall(r"[a-z0-9]+", q.lower()))


def direction(rel, world):
    if rel is None:
        return "" if world is None else f"from {world:+.0f}° (room frame)"
    a = abs(rel)
    side = "right" if rel > 0 else "left"
    if a <= 30:
        return "in front of you"
    if a >= 150:
        return "behind you"
    if a >= 120:
        return f"behind you to the {side}"
    return f"to your {side}"


def evidence(row):
    key = "utt_id" if row["type"] == "utterance" or (row["type"] == "perception_enrichment" and
                                                     "utt_id" in (row["payload"].get("ref") or {})) else "event_id"
    return {key: row["ref_id"], "type": row["type"], "t": row["event_time"], "text": row["text"],
            "person_label": row["person_label"], "bearing_deg": row["world_bearing"],
            "rel_bearing": row["rel_bearing"], "location_id": row["location_id"]}


class Asker:
    def __init__(self, memory):
        self.m = memory

    # ---- entry point -----------------------------------------------------------------------------------

    def ask(self, q, use_cache=True, phrase=True):
        key = (norm(q), phrase)
        hit = _cache.get(key)
        if use_cache and hit is not None and monotonic() - hit[0] <= config.ASK_CACHE_S:
            return {**hit[1], "cached": True}
        answer, rows = self.answer(q)
        out = {"answer": answer, "evidence": [evidence(r) for r in rows[:config.ASK_EVIDENCE_MAX]],
               "cached": False, "phrased_by": "template"}
        phrased = gemini_phrase(q, answer, out["evidence"]) if phrase else None
        if phrased:
            out["answer"], out["phrased_by"] = phrased, config.GEMINI_MODEL
        _cache[key] = (monotonic(), out)
        return out

    def warm(self):
        for q in DEMO_QUERIES:
            self.ask(q)

    # ---- understanding ---------------------------------------------------------------------------------

    def parse(self, q):
        n = norm(q)
        words = n.split()
        person = re.search(r"person (\d+)", n)
        return {
            "n": n,
            "person": f"Person {person.group(1)}" if person else None,
            "role": next((ROLE_WORDS[w] for w in words if w in ROLE_WORDS), None),
            "segment": next((SEGMENT_WORDS[w] for w in words if w in SEGMENT_WORDS), None),
            "name": bool(re.search(r"\b(call|called|calling|say|said)\b.*\bmy name\b|\bname called\b", n)),
            "behind": bool(re.search(r"\b(behind|rear|back of me)\b", n)),
            "summary": bool(re.search(r"\b(summar\w*|recap|overview)\b|what happened today|my day\b", n)),
            "what_happened": bool(re.search(r"\bwhat (happened|went on)\b", n)),
            "terms": [w for w in words if w not in STOP and w not in ROLE_WORDS and w not in SEGMENT_WORDS
                      and not w.isdigit() and w != "person"],
        }

    def scope(self, intent):
        """Segments the question is about: all of memory (seeded day + live sessions) unless something narrows it."""
        segs = self.m.segments()
        if intent["segment"]:
            segs = [s for s in segs if s["id"] == intent["segment"]] or segs
        if intent["role"]:
            segs = [s for s in segs if intent["role"] in s.get("roles", {}).values()] or segs
        return segs

    def who(self, intent, seg):
        if intent["person"]:
            return intent["person"]
        if intent["role"]:
            for label, role in seg.get("roles", {}).items():
                if role == intent["role"]:
                    return label
        return None

    # ---- answering -------------------------------------------------------------------------------------

    def answer(self, q):
        it = self.parse(q)
        segs = self.scope(it)
        if not segs:
            return "I don't have any memories yet.", []
        if it["summary"]:
            return self.summarize_day(segs)
        if it["name"]:
            return self.name_called(segs)
        if it["behind"]:
            return self.behind(segs)
        if it["what_happened"] and (it["segment"] or len(segs) == 1) and not it["terms"]:
            return self.summarize_segment(segs[0])
        return self.search(it, segs)

    def rows(self, segs, types=DAY_TYPES):
        """The rows of these segments, in segment order: one store call per session (not per segment), because on
        Neon each call is an HTTPS round trip and the whole day is eight segments of one session."""
        fetched = {}
        for session in dict.fromkeys(s["session"] for s in segs):
            mine = [s for s in segs if s["session"] == session]
            fetched[session] = self.m.store.events(min(s["start"] for s in mine), max(s["end"] for s in mine) + 60,
                                                   types=types, session=session)
        out, seen = [], set()
        for s in segs:
            for r in fetched[s["session"]]:
                if s["start"] <= r["event_time"] <= s["end"] + 60 and id(r) not in seen:
                    seen.add(id(r))
                    out.append(r)
        return out

    def summarize_day(self, segs):
        lines, ev = [], []
        day = self.rows(segs)
        for s in segs:
            text, rows = self.summarize_segment(s, short=True, rows=[r for r in day if r["session"] == s["session"]
                                                                     and s["start"] <= r["event_time"] <= s["end"] + 60])
            lines.append(text)
            ev.extend(rows[:1])
        seeded = " (seeded demo day)" if any(s.get("seeded") for s in segs) else ""
        return f"Your day{seeded}: " + " ".join(lines), ev

    def summarize_segment(self, seg, short=False, rows=None):
        rows = self.m.segment_rows(seg) if rows is None else rows
        utts = [r for r in rows if r["type"] == "utterance"]
        people = [p for p, _ in Counter(r["person_label"] for r in utts if r["person_label"]).most_common()]
        alerts = [r for r in rows if r["type"] == "alert"]
        notes = [r for r in rows if r["type"] == "perception_enrichment" and r["text"]
                 and r["label"] in ("important", "urgent")]
        rp = replay(rows, use_model=False)
        when = f"{clock(seg['start'])}-{clock(seg['end'])}"
        who = (" with " + " and ".join(people)) if people else ""
        by_ref = {r["ref_id"]: r for r in rows if r["type"] in ("utterance", "sound_event")}
        note_rows = [by_ref.get(n["ref_id"], n) for n in notes]
        if short:
            bits = [f"{seg['label']} ({when}){who}"]
            if notes:
                bits.append(notes[0]["text"].rstrip("."))
            return "; ".join(bits) + ".", (note_rows or alerts or utts)
        parts = [f"{seg['label']}, {when}{who}."]
        for a in alerts:
            d = direction(a["rel_bearing"], a["world_bearing"])
            parts.append(f"{a['payload']['emoji']} {a['payload']['label']} {d} at {clock(a['event_time'])}.")
        for nt in notes[:3]:
            parts.append(nt["text"].rstrip(".") + ".")
        crowd = rp["by_category"].get("crowd", {}).get("suppressed", 0)
        parts.append(f"HERE surfaced {rp['surfaced']} events and kept {rp['suppressed']} quiet"
                     + (f", including {crowd} crowd sounds." if crowd else "."))
        key_utts = sorted(utts, key=lambda r: -(self.attention(rows, r) or 0))[:3]
        ev = alerts + sorted(key_utts, key=lambda r: r["event_time"])
        return " ".join(parts), ev

    @staticmethod
    def attention(rows, r):
        for e in rows:
            if e["type"] == "perception_enrichment" and e["ref_id"] == r["ref_id"]:
                return {"ambient": 0.1, "notice": 0.4, "important": 0.8, "urgent": 1.0}[e["label"]] + \
                    (e["payload"].get("directed_at_user") or 0) * 0.1
        return 0

    def name_called(self, segs):
        rows = self.rows(segs, ["alert", "utterance"])
        utts = {r["ref_id"]: r for r in rows if r["type"] == "utterance"}
        hits = [r for r in rows if r["type"] == "alert" and r["label"] == "name_called"]
        if not hits:
            return "Nobody called your name in what I remember.", []
        lines, ev = [], []
        for a in hits:
            u = utts.get((a["payload"].get("ref") or {}).get("utt_id"))
            seg = self.m.segment_of(a["event_time"], a["session"])
            who = (u and u["person_label"]) or "someone"
            where = f" ({seg['label'].lower()})" if seg else ""
            quote = f': "{u["text"]}"' if u else ""
            lines.append(f"{clock(a['event_time'])}{where}, {who} {direction(a['rel_bearing'], a['world_bearing'])}"
                         f"{quote}")
            ev.append(u or a)
        return f"Your name was called {len(hits)} time{'s' if len(hits) != 1 else ''}: " + "; ".join(lines) + ".", ev

    def behind(self, segs):
        rows = self.rows(segs, ["alert", "utterance", "sound_event", "perception_enrichment"])
        attn = {r["ref_id"]: r["label"] for r in rows if r["type"] == "perception_enrichment"}
        hits = []
        for r in rows:
            if r["rel_bearing"] is None or abs(r["rel_bearing"]) < 120:
                continue
            if r["type"] == "alert" and r["payload"]["priority"] >= 2 and r["label"] != "name_called":
                hits.append(r)
            elif r["type"] == "utterance" and attn.get(r["ref_id"]) in ("urgent",):
                hits.append(r)
        if not hits:
            return "Nothing important happened behind you.", []
        hazard = [r for r in hits if r["type"] == "alert" and r["label"] in HAZARD_KINDS]
        hazard.sort(key=lambda r: (-r["payload"]["priority"], r["event_time"]))
        rest = [r for r in hits if r not in hazard]

        def where(r):
            seg = self.m.segment_of(r["event_time"], r["session"])
            return f" ({seg['label'].lower()})" if seg else ""

        parts = []
        if hazard:
            top = hazard[0]
            parts.append(f"The most important: a {top['payload']['label'].lower()} "
                         f"{direction(top['rel_bearing'], top['world_bearing'])} at {clock(top['event_time'])}"
                         f"{where(top)}.")
            if len(hazard) > 1:
                parts.append("Also behind you: " + "; ".join(
                    f"{r['payload']['label'].lower()} at {clock(r['event_time'])}{where(r)}" for r in hazard[1:])
                    + ".")
        if rest:
            kinds = Counter("your name" if r["type"] == "utterance" else r["payload"]["label"].lower() for r in rest)
            parts.append(f"{len(rest)} other things came from behind: "
                         + ", ".join(f"{k} x{n}" if n > 1 else k for k, n in kinds.items()) + ".")
        return " ".join(parts), hazard + sorted(rest, key=lambda r: r["event_time"])

    def search(self, it, segs):
        start, end = min(s["start"] for s in segs), max(s["end"] for s in segs) + 60
        terms = it["terms"]
        hits = self.m.store.search_utterances(terms, start, end, limit=40) if terms else []
        speaker = next((self.who(it, s) for s in segs if self.who(it, s)), None)
        if speaker:
            hits = [h for h in hits if h["person_label"] == speaker]
            if not hits and not terms:                     # "what did the judge ask?": what that person said
                said = [r for r in self.rows(segs, ["utterance"]) if r["person_label"] == speaker]
                if re.search(r"ask", it["n"]):
                    said = [r for r in said if r["label"] == "questioning"] or said
                hits = [{"t_start": r["event_time"], "session": r["session"], "person_label": speaker,
                         "utt_id": r["ref_id"]} for r in said]
        if hits and "score" in hits[0]:
            top = hits[0]["score"]
            hits = [h for h in hits if h["score"] >= 0.5 * top]
        if not hits and it["segment"]:
            return self.summarize_segment(segs[0])
        if not hits:
            return f"I couldn't find anything about {' '.join(terms) or 'that'}.", []
        hits = hits[:3]
        # include what the same speaker said right after a hit (the sentence after "about the exam" is the scope).
        # One store call per session covers every hit's window, enrichments included (each call is a round trip).
        rows, nearby = {}, []
        for session in dict.fromkeys(h["session"] for h in hits):
            mine = [h for h in hits if h["session"] == session]
            nearby += self.m.store.events(min(h["t_start"] for h in mine) - 1, max(h["t_start"] for h in mine) + 90,
                                          types=["utterance", "perception_enrichment"], session=session)
        for h in hits:
            for r in nearby:
                if r["type"] != "utterance" or r["session"] != h["session"] \
                        or not h["t_start"] - 1 <= r["event_time"] <= h["t_start"] + 90:
                    continue
                same_person = h["person_label"] is not None and r["person_label"] == h["person_label"]
                if same_person or r["ref_id"] == h["utt_id"]:
                    rows[r["ref_id"]] = r
        rows = sorted(rows.values(), key=lambda r: r["event_time"])[:config.ASK_EVIDENCE_MAX]
        ids = {r["ref_id"] for r in rows}
        notes = [n for n in nearby if n["type"] == "perception_enrichment" and n["text"] and n["ref_id"] in ids]
        seg = self.m.segment_of(rows[0]["event_time"], rows[0]["session"])
        role = next((s.get("roles", {}).get(speaker) for s in segs if speaker in s.get("roles", {})), None)
        speakers = {r["person_label"] for r in rows[:3]}
        if not speaker and len(speakers) == 1 and None not in speakers:
            speaker = next(iter(speakers))
        if speaker:
            speaker_text = (f"{'The' if role == 'judge' else 'Your'} {role} ({speaker})" if role else speaker) + " said"
            quotes = " ".join(f'"{r["text"]}"' for r in rows[:3])
        else:
            speaker_text = "HERE heard"
            quotes = " ".join(f'{r["person_label"] or "someone"}: "{r["text"]}"' for r in rows[:3])
        where = f" in {seg['label'].lower()} at {clock(rows[0]['event_time'])}" if seg else ""
        tail = (" In short: " + "; ".join(n["text"] for n in notes[:3]) + ".") if notes else ""
        return f"{speaker_text}{where}: {quotes}{tail}", rows


def gemini_phrase(q, draft, ev):
    key = config.env("GEMINI_API_KEY")
    if not key or not ev or GEMINI.paused():
        return None
    prompt = ("You are HERE, a memory assistant for a deaf or hard-of-hearing wearer. Rewrite the draft answer to "
              "the question in at most two short sentences, second person, using only facts in the draft and the "
              "evidence. Keep person labels (Person 2) exactly; never invent names.\n"
              f"Question: {q}\nDraft: {draft}\nEvidence: {json.dumps(ev, ensure_ascii=False)}")
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],    # no thinking: it cost ~3 s per answer
                       "generationConfig": {"temperature": 0.2, "maxOutputTokens": 160,
                                            "thinkingConfig": {"thinkingBudget": 0}}}).encode()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent"
    req = urllib.request.Request(url, body, {"Content-Type": "application/json", "x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=config.GEMINI_TIMEOUT_S) as r:
            data = json.loads(r.read())
        return data["candidates"][0]["content"]["parts"][0]["text"].strip() or None
    except urllib.error.HTTPError as e:
        GEMINI.trip(e)
        return None
    except Exception:  # noqa: BLE001 - timeouts and odd replies fall back to the template
        return None


def search_result_hits(memory, text, limit):
    """Hits for the contract's search_request -> search_result (kind utterance | alert)."""
    asker = Asker(memory)
    it = asker.parse(text)
    segs = asker.scope(it)
    if not segs:
        return []
    start, end = min(s["start"] for s in segs), max(s["end"] for s in segs) + 60
    hits = memory.store.search_utterances(it["terms"], start, end, limit=limit * 3) if it["terms"] else []
    if it["person"]:
        hits = [h for h in hits if h["person_label"] == it["person"]]
    out = []
    top = max((h["score"] for h in hits), default=1.0) or 1.0
    for h in hits[:limit]:
        out.append({"t": h["t_start"], "kind": "utterance", "person_label": h["person_label"], "text": h["text"],
                    "tone": h["tone"], "bearing_deg": h["world_bearing"], "score": round(max(0.0, h["score"] / top), 3)})
    if it["name"] or not out:
        _, rows = asker.name_called(segs) if it["name"] else ("", [])
        for r in rows[:limit - len(out)]:
            out.append({"t": r["event_time"], "kind": "alert" if r["type"] == "alert" else "utterance",
                        "person_label": r["person_label"], "text": r["text"], "bearing_deg": r["world_bearing"],
                        "score": 0.9})
    return out[:limit]


__all__ = ["Asker", "DEMO_QUERIES", "search_result_hits", "public_event"]

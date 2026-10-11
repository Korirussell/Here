"""Memory API client with seeded fallbacks (Shared/INTEGRATION_CONTRACT.md §5.3).

`Memory.call(endpoint, query)` asks A3's service first; if it doesn't answer within MEMORY_TIMEOUT_S, the same
shape is built from ONE seed bundle: A3's frozen API snapshots (extras/memory/seed/api/) when they exist, else
ours (demo_data/). Bundles are never mixed: insights and Ask evidence must point at events of the same day.
Every reply carries `_source`: "api" or "seed", and the page labels seeded data as seeded. A seed reply given
because the API failed also carries `_fallback_reason` (e.g. "memory API slow (>4 s)"). A slow answer is not a
dead API: only a second timeout in a row (or a refusal / connection error) skips the API for MEMORY_RETRY_S.

Both sources go through the same normalizers, so the page sees one shape: segment `location` is a display name,
Ask answers carry full evidence, replay decisions carry label / person / bearing and the page's categories.
A seeded day is shown on today's date (SEED_AS_TODAY); live memory is never moved.

The replay policy here is the dashboard's offline stand-in for A3's /api/replay: deterministic, explainable,
and built on the same attention labels the live system sends (perception_enrichment), falling back to the
rules in Shared/ATTENTION.md tier 3 when an event has none.
"""

import datetime as dt
import json
import re
import socket
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from . import settings as S

ATTENTION_SCORE = {"ambient": 0.15, "notice": 0.45, "important": 0.75, "urgent": 0.95}
HAZARDS = {"car_horn", "siren", "alarm", "loud_bang", "vehicle"}
MEDIA = {"music", "tv", "laughter"}
CROWDED = {"crowd", "street", "cafe", "transit", "noisy_lunch"}
DEFAULT_POLICY = {"sensitivity": 0.6, "crowd_filter": 0.3, "focus_mode": False, "hazard_priority": 0.7,
                  "speech_priority": 0.6}


def seed_folder():
    """The one seed bundle to use: A3's frozen API responses when present, else the dashboard's own."""
    a3 = S.MEMORY_SEED / "api"
    return a3 if (a3 / "day.json").exists() else S.DEMO_DATA


def _load_json(folder, name):
    p = folder / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def normalize_day(day):
    """A3's day names locations by id ({locations:[{id, name}]}); ours by name. The page wants names."""
    names = {loc["id"]: loc.get("name") or loc["id"] for loc in day.get("locations") or [] if "id" in loc}
    segs = [{**g, "location_id": g.get("location"), "location": names.get(g.get("location"), g.get("location"))}
            for g in day.get("segments") or []]
    label = day.get("label") or ("Synthetic day (seeded)" if day.get("seeded") else "")
    return {**day, "segments": segs, "label": label}


WORD = re.compile(r"[a-z0-9']+")
STOP = {"what", "did", "the", "a", "an", "about", "say", "said", "when", "was", "is", "my", "i", "me", "to", "of",
        "who", "where", "which", "do", "does", "how", "today", "in", "on", "at", "it", "and", "for", "from"}


def normalize_ask(raw):
    """Ours: {answers:[{match, q, answer, evidence_ids}]}. A3's snapshot: {question: {answer, evidence, ...}}."""
    if not raw:
        return []
    if "answers" in raw:
        return raw["answers"]
    return [{"q": q, "match": sorted(set(WORD.findall(q.lower())) - STOP), "answer": a.get("answer", ""),
             "evidence": a.get("evidence", [])} for q, a in raw.items() if isinstance(a, dict)]


class DayIndex:
    """Lookups over one day's events: by id, enrichment by ref, alerts by ref."""

    def __init__(self, day):
        self.day = day
        events = day.get("events", [])
        self.enrich = {}
        self.alerts_by_ref = {}
        for e in events:
            ref = e.get("ref") or {}
            rid = ref.get("event_id") or ref.get("utt_id")
            if e.get("type") == "perception_enrichment" and rid:
                self.enrich[rid] = e
            elif e.get("type") == "alert" and rid:
                self.alerts_by_ref[rid] = e
        self.by_id = {event_id(e): e for e in events if event_id(e)}


class Seed(DayIndex):
    """The seeded day, loaded once from one bundle."""

    def __init__(self, folder=None):
        self.folder = folder or seed_folder()
        super().__init__(normalize_day(_load_json(self.folder, "day.json")
                                       or {"date": "", "segments": [], "events": []}))
        self.ask = normalize_ask(_load_json(self.folder, "ask.json"))
        self.insights = _load_json(self.folder, "insights.json") or {"insights": []}
        self.questions = [a["q"] for a in self.ask if a.get("q")]


def day_shift(date_str, today=None):
    """Seconds that move a seeded day (YYYY-MM-DD, local time) onto today, same clock times."""
    try:
        seeded = dt.date.fromisoformat(date_str)
    except (TypeError, ValueError):
        return 0.0
    today = today or dt.date.today()
    a = dt.datetime.combine(seeded, dt.time(12)).timestamp()
    b = dt.datetime.combine(today, dt.time(12)).timestamp()
    return round(b - a)


def shift_times(items, delta, keys=("t", "start", "end"), only=None):
    """Copies of `items` with their timestamps moved by delta; with `only` (a set of ids), just those items.
    Ask can cite live days too, and live memory is never moved."""
    if not delta:
        return items
    return [{**x, **{k: x[k] + delta for k in keys if isinstance(x.get(k), (int, float))}}
            if only is None or event_id(x) in only else x for x in items]


def event_id(e):
    return e.get("event_id") or e.get("utt_id") or e.get("alert_id")


def rules_attention(e, alert):
    """ATTENTION.md tier 3."""
    if alert is not None:
        return "urgent" if alert.get("priority") == 3 else "important"
    if e.get("type") == "sound_event":
        if e.get("label") in HAZARDS:
            return "important"
        if e.get("label") == "unknown":
            return "ambient" if e.get("confidence", 0) < 0.4 else "notice"
        return "ambient" if e.get("label") in MEDIA else "notice"
    return "notice"


def category(e, alert):
    label = e.get("label") or (alert or {}).get("kind")
    if label in HAZARDS or (alert or {}).get("kind") in HAZARDS:
        return "hazard"
    if alert is not None:
        return "alert"
    if e.get("type") == "utterance":
        return "speech"
    if label == "unknown":
        return "unknown"
    if label in MEDIA:
        return "media"
    return "sound"


def replay(seed, segment_id, policy):
    pol = {**DEFAULT_POLICY, **(policy or {})}
    seg = next((s for s in seed.day.get("segments", []) if s["id"] == segment_id), None)
    if seg is None:
        return {"error": f"unknown segment {segment_id}", "surfaced": 0, "suppressed": 0, "kept_important": 0,
                "by_category": {}, "decisions": []}
    crowded = seg.get("environment") in CROWDED
    threshold = 1.0 - float(pol["sensitivity"]) * 0.9 + (0.25 if pol["focus_mode"] else 0.0)
    decisions, by_cat = [], {}
    surfaced = suppressed = kept = 0
    for e in seed.day.get("events", []):
        if not (seg["start"] <= e.get("t", 0) < seg["end"]):
            continue
        is_utt = e.get("type") == "utterance" and e.get("final") and (e.get("text") or "").strip()   # empty = retracted
        is_sound = e.get("type") == "sound_event"
        orphan_alert = e.get("type") == "alert" and e.get("active") and not (e.get("ref") or {})
        if not (is_utt or is_sound or orphan_alert):
            continue
        eid = event_id(e)
        alert = e if orphan_alert else seed.alerts_by_ref.get(eid)
        enr = seed.enrich.get(eid)
        att = enr["attention"] if enr else rules_attention(e, alert)
        model = enr["attention_model"] if enr else "rules"
        directed = (enr or {}).get("directed_at_user", 0.0) or 0.0
        cat = category(e, alert)
        score = ATTENTION_SCORE[att]
        why = [f"attention {att} ({'rules' if model == 'rules' else model.split('/')[-1]})"]
        if cat == "speech":
            score += (float(pol["speech_priority"]) - 0.5) * 0.4 + 0.2 * directed
            if directed >= 0.5:
                why.append(f"addressed to you ({directed:.0%})")
        if cat == "hazard":
            score += (float(pol["hazard_priority"]) - 0.5) * 0.5
            why.append("hazard priority")
        noisy = crowded or e.get("overlap") or cat == "media"
        if noisy and directed < 0.5 and cat in ("speech", "media", "sound", "unknown"):
            score -= float(pol["crowd_filter"]) * 0.35
            if pol["crowd_filter"] > 0.05:
                why.append("crowd filter")
        important = (alert is not None and alert.get("priority", 1) >= 2) or att == "urgent"
        score = max(0.0, min(1.0, score))
        if important:
            ok = True
            kept += 1
            why.append("important: always kept" + (" under focus" if pol["focus_mode"] else ""))
        else:
            ok = score >= threshold
            if pol["focus_mode"] and not ok:
                why.append("focus mode")
        if not ok and cat == "unknown":
            why.append("still shown as ambient with its direction")
        surfaced += ok
        suppressed += not ok
        c = by_cat.setdefault(cat, {"surfaced": 0, "suppressed": 0})
        c["surfaced" if ok else "suppressed"] += 1
        decisions.append({"event_id": eid, "surfaced": ok, "reason": "; ".join(why), "model_score": round(score, 3),
                          "category": cat, "t": e.get("t"), "label": e.get("label") or (alert or {}).get("kind")
                          or "speech", "text": e.get("text", ""), "person_label": e.get("person_label"),
                          "bearing_deg": e.get("bearing_deg"), "important": important})
    return {"segment_id": segment_id, "policy": pol, "threshold": round(min(threshold, 1.0), 3),
            "surfaced": surfaced, "suppressed": suppressed, "kept_important": kept, "by_category": by_cat,
            "decisions": decisions}


def evidence(e):
    out = {"t": e.get("t"), "text": e.get("text") or e.get("label", ""), "person_label": e.get("person_label"),
           "bearing_deg": e.get("bearing_deg")}
    if e.get("utt_id"):
        out["utt_id"] = e["utt_id"]
    else:
        out["event_id"] = event_id(e)
    return out


def ask(seed, q):
    words = set(WORD.findall((q or "").lower()))
    best, best_key = None, (0.0, 0)
    for a in seed.ask:
        need = [m.lower() for m in a.get("match", [])]
        hits = sum(1 for m in need if m in words)
        key = (hits / len(need) if need else 0.0, hits)
        if need and (hits == len(need) or (hits >= 2 and key[0] >= 0.6)) and key > best_key:
            best, best_key = a, key
    if best is not None:
        ev = best.get("evidence") or [evidence(seed.by_id[i]) for i in best.get("evidence_ids", []) if i in seed.by_id]
        return {"answer": best["answer"], "evidence": ev, "cached": True, "_mode": "seeded answer"}
    terms = words - STOP
    scored = []
    for e in seed.day.get("events", []):
        if e.get("type") not in ("utterance", "sound_event", "alert"):
            continue
        hay = " ".join(str(e.get(k, "")) for k in ("text", "label", "person_label", "kind")).lower()
        hits = sum(1 for w in terms if w in hay)
        if hits:
            scored.append((hits, e.get("t", 0), e))
    scored.sort(key=lambda x: (-x[0], x[1]))
    ev = [evidence(e) for _, _, e in scored[:5]]
    if not ev:
        return {"answer": "I couldn't find that in today's memory.", "evidence": [], "cached": False,
                "_mode": "keyword search"}
    return {"answer": f"{len(ev)} moment{'s' if len(ev) > 1 else ''} from today match "
                      f"“{' '.join(sorted(terms))}”.", "evidence": ev, "cached": False,
            "_mode": "keyword search"}


def a3_policy(pol):
    """The page's sliders (0..1) as A3's policy (§5.3: sensitivity 0..1, the rest booleans)."""
    pol = {**DEFAULT_POLICY, **(pol or {})}
    return {"sensitivity": float(pol["sensitivity"]), "focus_mode": bool(pol["focus_mode"]),
            "crowd_filter": float(pol["crowd_filter"]) >= 0.25, "hazard_priority": float(pol["hazard_priority"]) >= 0.6,
            "speech_priority": float(pol["speech_priority"]) > 0.6}


def join_replay(idx, up, policy):
    """A3's /api/replay answer, with each decision filled from the same day's event and the page's categories."""
    decisions, by_cat = [], {}
    for d in up.get("decisions") or []:
        e = idx.by_id.get(d.get("event_id")) or {}
        alert = e if e.get("type") == "alert" else idx.alerts_by_ref.get(d.get("event_id"))
        cat = category(e, alert) if e else d.get("category", "sound")
        decisions.append({**d, "category": cat, "source_category": d.get("category"),
                          "model_score": d.get("model_score", d.get("score")),
                          "label": e.get("label") or (alert or {}).get("kind") or ("speech" if cat == "speech" else d.get("type")),
                          "text": d.get("text") or e.get("text", ""), "person_label": e.get("person_label"),
                          "bearing_deg": e.get("bearing_deg"), "important": bool(d.get("important"))})
        c = by_cat.setdefault(cat, {"surfaced": 0, "suppressed": 0})
        c["surfaced" if d.get("surfaced") else "suppressed"] += 1
    return {**up, "policy": {**DEFAULT_POLICY, **(policy or {})}, "api_policy": up.get("policy"),
            "by_category": by_cat if decisions else up.get("by_category", {}), "decisions": decisions}


LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def scrub(obj):
    """Drop anything that looks like a credential before a reply leaves for the page (defence in depth)."""
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items() if not any(w in k.lower() for w in ("token", "secret", "password", "api_key"))}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    return obj


class Memory:
    def __init__(self, base, today=None, token=""):
        self.base = base.rstrip("/")
        self.remote = (urlsplit(self.base).hostname or "") not in LOCAL_HOSTS
        self.token = token if self.remote else ""          # A3's API wants the relay token only through a proxy
        self.last_error = ""
        self.seed = Seed()
        self.down_until = 0.0
        self.slow = 0                                        # timeouts in a row (two mark the API down)
        self.today = today                                   # None: the real date (tests pin it)
        self.api_day = None                                  # DayIndex of the last day the API returned

    def shift_for(self, day):
        return day_shift(day.get("date"), self.today) if S.SEED_AS_TODAY and day.get("seeded") else 0.0

    def show_day(self, day, source):
        delta = self.shift_for(day)
        out = {**day, "events": shift_times(day.get("events", []), delta),
               "segments": shift_times(day.get("segments", []), delta), "_source": source}
        if delta:
            out["seeded_date"] = day.get("date")
            out["date"] = (self.today or dt.date.today()).isoformat()
        return out

    def day_index(self):
        """The day the page is showing: the API's when it answered, else the seed."""
        if self.api_day is None and self.reachable():
            up = self._upstream("GET", "/api/day")
            if up and up.get("events"):
                self.api_day = DayIndex(normalize_day(up))
        return self.api_day or self.seed

    def reachable(self):
        """Quick TCP probe: on Windows a refused localhost connection otherwise takes ~2 s per request."""
        if time.time() < self.down_until:
            return False
        u = urlsplit(self.base)
        try:
            with socket.create_connection((u.hostname, u.port or (443 if u.scheme == "https" else 80)),
                                          timeout=S.MEMORY_PROBE_S):
                return True
        except OSError:
            self.down_until = time.time() + S.MEMORY_RETRY_S
            return False

    def _upstream(self, method, path, body=None):
        if not self.reachable():
            return None
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"     # server side only; never sent to the page
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=S.MEMORY_TIMEOUT_S) as r:
                out = json.loads(r.read().decode("utf-8"))
                out["_source"] = "api"
                self.last_error = ""
                self.slow = 0
                return out
        except urllib.error.HTTPError as e:
            self.last_error = "memory API refused the token (401)" if e.code == 401 else f"memory API HTTP {e.code}"
            self.down_until = time.time() + S.MEMORY_RETRY_S
            return None
        except (urllib.error.URLError, OSError, ValueError) as e:
            if isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError):
                self.slow += 1                       # reachable but slow (e.g. a cold Neon query): don't mark it down
                self.last_error = f"memory API slow (>{S.MEMORY_TIMEOUT_S:g} s)"
                if self.slow >= 2:
                    self.down_until = time.time() + S.MEMORY_RETRY_S
                return None
            self.last_error = f"memory API unreachable ({type(e).__name__})"
            self.down_until = time.time() + S.MEMORY_RETRY_S
            return None

    def _seeded(self, out):
        """A seed reply, with why the API wasn't used when it failed (not when it simply isn't configured)."""
        out = {**out, "_source": "seed"}
        if self.last_error:
            out["_fallback_reason"] = self.last_error
        return out

    def call(self, endpoint, q):
        if endpoint == "status":                       # A3's server status: relay health + message rates
            where = "vultr" if self.remote else "local"
            up = self._upstream("GET", "/api/status")
            if up is None:
                return {"ok": False, "where": where, "reason": self.last_error or "memory API offline", "_source": "seed"}
            return {**scrub(up), "where": where}
        if endpoint == "health":
            up = self._upstream("GET", "/api/health")
            return up or {"ok": True, "db": "seed", "events": len(self.seed.day.get("events", [])),
                          "sessions": 1, "_source": "seed"}
        if endpoint == "day":
            date = q.get("date", "")
            up = self._upstream("GET", "/api/day" + (f"?date={date}" if date else ""))
            if up and up.get("events"):
                self.api_day = DayIndex(normalize_day(up))
                return self.show_day(self.api_day.day, "api")
            self.api_day = None
            out = self.show_day(self.seed.day, "seed")
            return {**out, "_fallback_reason": self.last_error} if self.last_error else out
        if endpoint == "suggest":
            return {"questions": self.seed.questions, "_source": "seed"}
        if endpoint == "ask":
            up = self._upstream("POST", "/api/ask", {"q": q.get("q", "")})
            out = up or self._seeded(ask(self.seed, q.get("q", "")))
            idx = self.day_index() if up else self.seed
            return {**out, "evidence": shift_times(out.get("evidence") or [], self.shift_for(idx.day), only=idx.by_id)}
        if endpoint == "insights":
            up = self._upstream("GET", "/api/insights")
            if up and up.get("insights"):
                return up
            return self._seeded(self.seed.insights)
        if endpoint == "replay":
            try:
                policy = json.loads(q.get("policy") or "{}")
            except ValueError:
                policy = {}
            sid = q.get("segment_id", "")
            up = self._upstream("POST", "/api/replay", {"segment_id": sid, "policy": a3_policy(policy)})
            if up and "surfaced" in up:
                idx = self.day_index()
                out = {**join_replay(idx, up, policy), "_source": "api"}
            else:
                idx = self.seed
                out = self._seeded(replay(self.seed, sid, policy))
            return {**out, "decisions": shift_times(out["decisions"], self.shift_for(idx.day), only=idx.by_id)}
        return None

"""The memory core shared by the API, the relay client and the tools: store + seed + day view.

A "segment" is a slice of a day: the seeded day's scenes (segments.json), or one per live session.
"""

import json
import time
from datetime import datetime, timedelta, timezone

from . import config
from .ingest import Ingestor
from .store import ResilientStore

DAY_TYPES = ["utterance", "sound_event", "alert", "perception_enrichment"]


def load_seed_meta():
    return json.loads(config.SEED_SEGMENTS.read_text(encoding="utf-8"))


def tz():
    return timezone(timedelta(hours=float(config.env("HERE_TZ_OFFSET_H", config.TZ_OFFSET_H))))


def local_date(t):
    return datetime.fromtimestamp(t, tz()).strftime("%Y-%m-%d")


def day_range(date):
    start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=tz())
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def clock(t):
    return datetime.fromtimestamp(t, tz()).strftime("%I:%M %p").lstrip("0").lower()


def public_event(row):
    """Canonical message unchanged + the memory fields (docs/INTEGRATION.md §5.3)."""
    return {**row["payload"], "person_label": row["person_label"], "location_id": row["location_id"],
            "rel_bearing": row["rel_bearing"], "origin": row["origin"]}


class Memory:
    def __init__(self, store=None, log=print):
        self.log = log
        if store is None:
            store = ResilientStore(config.env("NEON_DATABASE_URL"), config.env("HERE_MEMORY_SQLITE") or None, log=log)
            store.on_fallback = lambda: self.seed()
        self.store = store
        self.meta = load_seed_meta()
        self.segs = {s["id"]: s for s in self.meta["segments"]}
        self.started = time.time()
        self._sessions = (float("-inf"), [])      # (monotonic time, store.sessions()): one round trip per few s

    @property
    def db_kind(self):
        return self.store.kind

    # ---- seed ------------------------------------------------------------------------------------------

    def locate_seed(self, session, t):
        for s in self.meta["segments"]:
            if s["start"] <= t < s["end"] + 60:
                return s["location"]
        return None

    def seed(self, force=False):
        """Load the SEEDED day (idempotent: rows upsert on their ids)."""
        session = self.meta["session"]
        self.seed_insights()
        if not force and self.store.has_session(session):
            return 0
        self.store.upsert_locations(self.meta["locations"] + [
            {"id": config.LIVE_LOCATION, "name": "Hackathon hall", "environment": "presentation"}])
        ing = Ingestor(self.store, origin="seeded", locate=self.locate_seed, log=self.log)
        with open(config.SEED_DAY, encoding="utf-8") as f:
            for line in f:
                ing.accept(json.loads(line))
                if len(ing.queue) >= config.FLUSH_MAX:
                    ing.flush_all()
        ing.flush_all()
        self._sessions = (float("-inf"), [])
        self.log(f"[memory] seeded day loaded: {ing.stats['written']} events ({session})")
        return ing.stats["written"]

    def seed_insights(self):
        """The precomputed insights (Databricks fallback), loaded once."""
        if not [i for i in self.store.insights() if i["source"] == "precomputed"]:
            path = config.SEED_DIR / "insights.json"
            if path.exists():
                self.store.put_insights(json.loads(path.read_text(encoding="utf-8"))["insights"], "precomputed")

    # ---- segments and days -----------------------------------------------------------------------------

    def sessions(self):
        """store.sessions(), reused for SESSIONS_CACHE_S: Ask and the day view call segments() several times per
        request, and on Neon each read is an HTTPS round trip."""
        t, rows = self._sessions
        if time.monotonic() - t > config.SESSIONS_CACHE_S:
            rows = self.store.sessions()
            self._sessions = (time.monotonic(), rows)
        return rows

    def segments(self, date=None):
        out = []
        for s in self.meta["segments"]:
            if date is None or local_date(s["start"]) == date:
                out.append({**s, "seeded": True, "session": self.meta["session"]})
        for ses in self.sessions():
            if ses["origin"] != "live" or ses["first_seen"] is None:
                continue
            if date is None or local_date(ses["first_seen"]) == date or local_date(ses["last_seen"]) == date:
                out.append({"id": f"live-{ses['id']}", "label": f"Live capture ({ses['id']})",
                            "start": ses["first_seen"], "end": ses["last_seen"],
                            "location": config.env("HERE_LOCATION", config.LIVE_LOCATION),
                            "environment": "live", "roles": {}, "seeded": False, "session": ses["id"]})
        return sorted(out, key=lambda s: s["start"])

    def segment(self, segment_id):
        for s in self.segments():
            if s["id"] == segment_id:
                return s
        return None

    def segment_rows(self, seg, types=DAY_TYPES):
        return self.store.events(seg["start"], seg["end"] + 60, types=types, session=seg["session"])

    def default_date(self):
        """The seeded day: a stable MEMORY view for the demo. Live days are listed in /api/day `dates`."""
        return self.meta["date"]

    def dates(self):
        return sorted({local_date(s["start"]) for s in self.segments()})

    def day(self, date=None):
        date = date or self.default_date()
        start, end = day_range(date)
        rows = self.store.events(start, end, types=DAY_TYPES)
        segs = self.segments(date)
        return {"date": date, "dates": self.dates(), "seeded": any(s["seeded"] for s in segs),
                "note": self.meta["note"] if any(s["seeded"] for s in segs) else "",
                "locations": list(self.store.locations().values()),
                "segments": [{k: s[k] for k in ("id", "label", "start", "end", "location", "environment", "seeded",
                                                "roles")} for s in segs],
                "events": [public_event(r) for r in rows]}

    def segment_of(self, t, session):
        for s in self.segments():
            if s["session"] == session and s["start"] <= t < s["end"] + 60:
                return s
        return None

    def health(self):
        from .ask import GEMINI          # ask imports this module
        c = self.store.counts()
        return {"ok": True, "db": self.db_kind, "transport": getattr(self.store, "transport", None),
                "events": c["events"], "sessions": c["sessions"],
                "fallback_reason": getattr(self.store, "fallback_reason", None), "gemini": GEMINI.status(),
                "retention_days": float(config.env("HERE_RETENTION_DAYS", config.RETENTION_DAYS)),
                "uptime_s": round(time.time() - self.started)}

    def purge(self):
        days = float(config.env("HERE_RETENTION_DAYS", config.RETENTION_DAYS))
        return self.store.purge(time.time() - days * 86400)

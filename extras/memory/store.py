"""Where memory lives: Neon Postgres when NEON_DATABASE_URL works, local SQLite otherwise (INTEGRATION_CONTRACT §8).

Both speak the same schema (one SQL text, two dialects):

    sessions    id, origin (live | seeded), label, first_seen, last_seen
    locations   id, name, environment
    events      every stored canonical message: payload (the message unchanged) + normalized columns
                (type, ref_id, source_id, world_bearing, rel_bearing, label, text, confidence, person_label,
                location_id, event_time, ingest_time); unique per (session, type, ref_id) so updates replace
    utterances  final utterances with full-text search (Postgres tsvector / SQLite FTS5)
    insights    Databricks or precomputed insights

All writes go through `write_batch` (one transaction per batch). A Postgres failure flips the service to SQLite
for the rest of the run (`ResilientStore`); the relay and the phone never notice.
"""

import json
import re
import socket
import sqlite3
import urllib.parse
import threading
import time
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, origin TEXT NOT NULL, label TEXT, first_seen DOUBLE PRECISION, last_seen DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS locations (id TEXT PRIMARY KEY, name TEXT NOT NULL, environment TEXT);
CREATE TABLE IF NOT EXISTS events (
    id {PK}, session TEXT NOT NULL, origin TEXT NOT NULL, type TEXT NOT NULL, ref_id TEXT NOT NULL,
    source_id INTEGER, world_bearing DOUBLE PRECISION, rel_bearing DOUBLE PRECISION, label TEXT, text TEXT,
    confidence DOUBLE PRECISION, person_label TEXT, location_id TEXT, event_time DOUBLE PRECISION NOT NULL,
    ingest_time DOUBLE PRECISION NOT NULL, payload {JSON} NOT NULL, UNIQUE (session, type, ref_id));
CREATE INDEX IF NOT EXISTS events_time ON events (event_time);
CREATE INDEX IF NOT EXISTS events_type ON events (type);
CREATE INDEX IF NOT EXISTS events_session ON events (session);
CREATE TABLE IF NOT EXISTS utterances (
    id {PK}, session TEXT NOT NULL, origin TEXT NOT NULL, utt_id TEXT NOT NULL, t_start DOUBLE PRECISION,
    t_end DOUBLE PRECISION, text TEXT NOT NULL, tone TEXT, person_label TEXT, world_bearing DOUBLE PRECISION,
    rel_bearing DOUBLE PRECISION, location_id TEXT{TSV}, UNIQUE (session, utt_id));
CREATE INDEX IF NOT EXISTS utterances_time ON utterances (t_start);
CREATE TABLE IF NOT EXISTS insights (
    id TEXT NOT NULL, category TEXT, title TEXT, text TEXT, score DOUBLE PRECISION, evidence_ids {JSON},
    source TEXT NOT NULL, computed_at DOUBLE PRECISION, data {JSON}, PRIMARY KEY (id, source));
"""

SQLITE_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS utterances_fts USING fts5(text, content='utterances', content_rowid='id',
    tokenize='porter');
CREATE TRIGGER IF NOT EXISTS utt_ai AFTER INSERT ON utterances BEGIN
    INSERT INTO utterances_fts(rowid, text) VALUES (new.id, new.text); END;
CREATE TRIGGER IF NOT EXISTS utt_ad AFTER DELETE ON utterances BEGIN
    INSERT INTO utterances_fts(utterances_fts, rowid, text) VALUES ('delete', old.id, old.text); END;
CREATE TRIGGER IF NOT EXISTS utt_au AFTER UPDATE ON utterances BEGIN
    INSERT INTO utterances_fts(utterances_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO utterances_fts(rowid, text) VALUES (new.id, new.text); END;
"""

PG_EXTRA = "CREATE INDEX IF NOT EXISTS utterances_tsv ON utterances USING GIN (tsv);"

EVENT_COLS = ["session", "origin", "type", "ref_id", "source_id", "world_bearing", "rel_bearing", "label", "text",
              "confidence", "person_label", "location_id", "event_time", "ingest_time", "payload"]
UTT_COLS = ["session", "origin", "utt_id", "t_start", "t_end", "text", "tone", "person_label", "world_bearing",
            "rel_bearing", "location_id"]
WORD = re.compile(r"[a-z0-9]+")


class Store:
    """One database connection behind a lock. `kind` is "neon" or "local"."""

    def __init__(self, kind, conn, transport=""):
        self.kind = kind
        self.conn = conn
        self.lock = threading.Lock()
        self.pg = kind == "neon"
        self.transport = transport or ("tcp" if self.pg else "sqlite")

    # ---- plumbing --------------------------------------------------------------------------------------

    def sql(self, s):
        if self.transport == "https":                    # Neon's HTTP API numbers its parameters
            n = iter(range(1, 10_000))
            return re.sub(r"\?", lambda _: f"${next(n)}", s)
        return s.replace("?", "%s") if self.pg else s

    def _exec(self, cur, s, params=()):
        cur.execute(self.sql(s), params)

    def _rows(self, s, params=()):
        with self.lock:
            cur = self.conn.cursor()
            self._exec(cur, s, params)
            names = [d[0] for d in cur.description]
            out = [dict(zip(names, r)) for r in cur.fetchall()]
            if self.pg:
                self.conn.commit()
            return out

    def migrate(self):
        ddl = SCHEMA.replace("{PK}", "BIGSERIAL PRIMARY KEY" if self.pg else "INTEGER PRIMARY KEY AUTOINCREMENT")
        ddl = ddl.replace("{JSON}", "JSONB" if self.pg else "TEXT")
        ddl = ddl.replace("{TSV}", ",\n    tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', text)) STORED"
                          if self.pg else "")
        self._migrate_insights_key()
        with self.lock:
            cur = self.conn.cursor()
            if self.pg:
                for stmt in filter(str.strip, (ddl + PG_EXTRA).split(";")):
                    cur.execute(stmt)
                self.conn.commit()
            else:
                cur.executescript(ddl + SQLITE_FTS)
                self.conn.commit()

    def _migrate_insights_key(self):
        """2026-10-10: insights were keyed by id alone (Databricks and precomputed rows share ids by design) and had
        no `data` column. Insights are derived data: drop an old-shaped table and let migrate() recreate it."""
        try:
            if self.pg:
                cols = [r["column_name"] for r in self._rows(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'insights'")]
            else:
                cols = [r["name"] for r in self._rows("SELECT name FROM pragma_table_info('insights')")]
            stale = bool(cols) and "data" not in cols
        except Exception:
            return
        if stale:
            with self.lock:
                cur = self.conn.cursor()
                self._exec(cur, "DROP TABLE insights")
                self.conn.commit()

    # ---- writes ----------------------------------------------------------------------------------------

    def write_batch(self, ops):
        """ops: ("session", dict) | ("event", dict) | ("utterance", dict) | ("person", session, ref_id, label).
        One transaction; later ops in the batch see earlier ones."""
        if not ops:
            return
        json_t = "JSONB" if self.pg else "TEXT"
        ev_sql = (f"INSERT INTO events ({', '.join(EVENT_COLS)}) VALUES ({', '.join('?' * (len(EVENT_COLS) - 1))}, "
                  f"CAST(? AS {json_t})) ON CONFLICT (session, type, ref_id) DO UPDATE SET "
                  + ", ".join(f"{c} = COALESCE(excluded.{c}, events.{c})" if c == "person_label" else
                              f"{c} = excluded.{c}" for c in EVENT_COLS[4:]))
        utt_sql = (f"INSERT INTO utterances ({', '.join(UTT_COLS)}) VALUES ({', '.join('?' * len(UTT_COLS))}) "
                   "ON CONFLICT (session, utt_id) DO UPDATE SET "
                   + ", ".join(f"{c} = COALESCE(excluded.{c}, utterances.{c})" if c == "person_label" else
                               f"{c} = excluded.{c}" for c in UTT_COLS[3:]))
        ses_sql = ("INSERT INTO sessions (id, origin, label, first_seen, last_seen) VALUES (?, ?, ?, ?, ?) "
                   "ON CONFLICT (id) DO UPDATE SET last_seen = CASE WHEN excluded.last_seen > sessions.last_seen "
                   "THEN excluded.last_seen ELSE sessions.last_seen END, "
                   "first_seen = CASE WHEN excluded.first_seen < sessions.first_seen THEN excluded.first_seen "
                   "ELSE sessions.first_seen END")
        with self.lock:
            cur = self.conn.cursor()
            try:
                for op in ops:
                    if op[0] == "event":
                        r = op[1]
                        vals = [r.get(c) for c in EVENT_COLS]
                        vals[-1] = json.dumps(r["payload"], ensure_ascii=False)
                        self._exec(cur, ev_sql, vals)
                    elif op[0] == "utterance":
                        self._exec(cur, utt_sql, [op[1].get(c) for c in UTT_COLS])
                    elif op[0] == "person":
                        _, session, ref_id, label = op
                        self._exec(cur, "UPDATE events SET person_label = ? WHERE session = ? AND ref_id = ? "
                                        "AND type <> 'attribution'", (label, session, ref_id))
                        self._exec(cur, "UPDATE utterances SET person_label = ? WHERE session = ? AND utt_id = ?",
                                   (label, session, ref_id))
                    elif op[0] == "session":
                        s = op[1]
                        self._exec(cur, ses_sql, (s["id"], s["origin"], s.get("label"), s.get("first", s["t"]), s["t"]))
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def upsert_locations(self, locations):
        with self.lock:
            cur = self.conn.cursor()
            for loc in locations:
                self._exec(cur, "INSERT INTO locations (id, name, environment) VALUES (?, ?, ?) ON CONFLICT (id) "
                                "DO UPDATE SET name = excluded.name, environment = excluded.environment",
                           (loc["id"], loc["name"], loc.get("environment")))
            self.conn.commit()

    def put_insights(self, insights, source):
        json_t = "JSONB" if self.pg else "TEXT"
        with self.lock:
            cur = self.conn.cursor()
            self._exec(cur, "DELETE FROM insights WHERE source = ?", (source,))
            for i in insights:
                self._exec(cur, f"INSERT INTO insights (id, category, title, text, score, evidence_ids, source, "
                                f"computed_at, data) VALUES (?, ?, ?, ?, ?, CAST(? AS {json_t}), ?, ?, "
                                f"CAST(? AS {json_t}))",
                           (i["id"], i["category"], i["title"], i["text"], i["score"],
                            json.dumps(i.get("evidence_ids", [])), source, time.time(),
                            json.dumps(i.get("data", {}), ensure_ascii=False)))
            self.conn.commit()

    def purge(self, before):
        """README §25: delete live memory older than `before` (epoch s). Seeded data is synthetic and exempt."""
        n = int(self._rows("SELECT COUNT(*) AS n FROM events WHERE origin = 'live' AND event_time < ?",
                           (before,))[0]["n"])
        with self.lock:
            cur = self.conn.cursor()
            self._exec(cur, "DELETE FROM events WHERE origin = 'live' AND event_time < ?", (before,))
            self._exec(cur, "DELETE FROM utterances WHERE origin = 'live' AND t_start < ?", (before,))
            self._exec(cur, "DELETE FROM sessions WHERE origin = 'live' AND last_seen < ?", (before,))
            self.conn.commit()
            return n

    def delete_session(self, session):
        with self.lock:
            cur = self.conn.cursor()
            for table, col in (("events", "session"), ("utterances", "session"), ("sessions", "id")):
                self._exec(cur, f"DELETE FROM {table} WHERE {col} = ?", (session,))
            self.conn.commit()

    # ---- reads -----------------------------------------------------------------------------------------

    def _decode(self, rows):
        for r in rows:
            for k in ("payload", "evidence_ids", "data"):
                if isinstance(r.get(k), str):
                    r[k] = json.loads(r[k])
        return rows

    def events(self, start=None, end=None, types=None, session=None):
        where, params = [], []
        if start is not None:
            where.append("event_time >= ?"); params.append(start)
        if end is not None:
            where.append("event_time < ?"); params.append(end)
        if session is not None:
            where.append("session = ?"); params.append(session)
        if types:
            where.append(f"type IN ({', '.join('?' * len(types))})"); params.extend(types)
        s = "SELECT * FROM events" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY event_time, id"
        return self._decode(self._rows(s, params))

    def search_utterances(self, terms, start=None, end=None, limit=20):
        """Full-text search over final utterances. Returns rows with `score` (higher is better)."""
        terms = [t for t in dict.fromkeys(WORD.findall(" ".join(terms).lower())) if len(t) > 1]
        if not terms:
            return []
        extra, params = "", []
        if start is not None:
            extra += " AND u.t_start >= ?"; params.append(start)
        if end is not None:
            extra += " AND u.t_start < ?"; params.append(end)
        if self.pg:
            s = ("SELECT u.id, u.session, u.origin, u.utt_id, u.t_start, u.t_end, u.text, u.tone, u.person_label, "
                 "u.world_bearing, u.rel_bearing, u.location_id, ts_rank(u.tsv, q) AS score "
                 "FROM utterances u, to_tsquery('english', ?) q WHERE u.tsv @@ q" + extra +
                 " ORDER BY score DESC LIMIT ?")
            return self._rows(s, [" | ".join(terms)] + params + [limit])
        s = ("SELECT u.*, -bm25(utterances_fts) AS score FROM utterances_fts JOIN utterances u "
             "ON u.id = utterances_fts.rowid WHERE utterances_fts MATCH ?" + extra + " ORDER BY score DESC LIMIT ?")
        return self._rows(s, [" OR ".join(f'"{t}"' for t in terms)] + params + [limit])

    def sessions(self):
        return self._rows("SELECT * FROM sessions ORDER BY first_seen")

    def has_session(self, session):
        return bool(self._rows("SELECT id FROM sessions WHERE id = ?", (session,)))

    def locations(self):
        return {r["id"]: r for r in self._rows("SELECT * FROM locations")}

    def insights(self):
        return self._decode(self._rows("SELECT * FROM insights ORDER BY score DESC"))

    def counts(self):
        ev = self._rows("SELECT COUNT(*) AS n FROM events")[0]["n"]
        ses = self._rows("SELECT COUNT(*) AS n FROM sessions")[0]["n"]
        return {"events": int(ev), "sessions": int(ses)}

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


def open_sqlite(path=None):
    path = Path(path or config.SQLITE_PATH)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    store = Store("local", conn)
    store.migrate()
    return store


def port_open(url, timeout=2.0):
    p = urllib.parse.urlsplit(url)
    try:
        socket.create_connection((p.hostname, p.port or 5432), timeout=timeout).close()
        return True
    except OSError:
        return False


def open_neon(url):
    """Direct Postgres (psycopg, port 5432) when reachable, else Neon's SQL over HTTPS (port 443).
    HERE_NEON_TRANSPORT=tcp|https forces one."""
    transport = config.env("HERE_NEON_TRANSPORT", "auto")
    if transport == "auto":
        transport = "tcp" if port_open(url) else "https"
    if transport == "tcp":
        import psycopg                                # only needed for the direct connection
        conn = psycopg.connect(url, connect_timeout=config.NEON_CONNECT_TIMEOUT_S)
    else:
        from .neon_http import NeonHTTPConnection
        conn = NeonHTTPConnection(url)
    store = Store("neon", conn, transport)
    store.migrate()
    return store


class ResilientStore:
    """Neon first; any Neon error switches to local SQLite for the rest of the run (and says so in /api/health)."""

    def __init__(self, neon_url="", sqlite_path=None, log=print):
        self.log = log
        self.sqlite_path = sqlite_path
        self.fallback_reason = None
        self.on_fallback = None                      # called once after switching (e.g. to seed the local store)
        self.store = None
        if neon_url:
            try:
                self.store = open_neon(neon_url)
                log(f"[memory] database: Neon ({self.store.transport})")
            except Exception as e:                   # missing driver, bad URL, network: never fatal
                self.fallback_reason = f"neon unavailable: {type(e).__name__}"
                log(f"[memory] {self.fallback_reason}; using local SQLite")
        if self.store is None:
            self.store = open_sqlite(sqlite_path)
            if not neon_url:
                log(f"[memory] database: local SQLite ({sqlite_path or config.SQLITE_PATH})")

    @property
    def kind(self):
        return self.store.kind

    def __getattr__(self, name):
        attr = getattr(self.store, name)
        if not callable(attr):
            return attr

        def call(*a, **kw):
            try:
                return getattr(self.store, name)(*a, **kw)
            except Exception as e:
                if self.store.kind != "neon":
                    raise
                self.fallback_reason = f"neon failed during {name}: {type(e).__name__}"
                self.log(f"[memory] {self.fallback_reason}; switching to local SQLite")
                self.store.close()
                self.store = open_sqlite(self.sqlite_path)
                if self.on_fallback:
                    self.on_fallback()
                return getattr(self.store, name)(*a, **kw)
        return call

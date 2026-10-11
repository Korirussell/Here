"""Memory API over HTTP (docs/INTEGRATION.md §5.3): :8790 locally, behind Caddy at /api on Vultr.

    GET  /api/health                  {ok, db: "neon" | "local", events, sessions, ...}
    GET  /api/day?date=YYYY-MM-DD     {date, seeded, segments:[...], events:[canonical + person_label, location_id]}
    POST /api/ask {q}                 {answer, evidence:[...], cached}
    GET  /api/insights                {insights:[{id, category, title, text, score, evidence_ids, source}]}
    POST /api/replay {segment_id, policy, use_model?}
                                      {surfaced, suppressed, kept_important, by_category, decisions, model?}
    GET  /api/segments                every known segment (seeded + live sessions)
    GET  /api/status                  relay health + message rates/ages from its log, memory, insights (status.py)
    GET  /api/status.html             the same as a small self-refreshing page
    GET  /api/integrations            each sponsor technology: configured or on its fallback, and where it lives
    POST /api/insights/refresh        Databricks job when DATABRICKS_* is set, else recompute locally (202, background)
    POST /api/export {session}        session bundle (text + metadata, never audio) -> Vultr Object Storage if VULTR_S3_*

Stdlib only (ThreadingHTTPServer). CORS is open (the dashboard runs on another port).

Auth: requests that come through a proxy (Caddy on Vultr adds X-Forwarded-For) must carry the relay token
(`?token=` or `Authorization: Bearer`), because memory holds transcripts (SPEC §25). Direct local requests
(the dashboard on the demo laptop) need none. `/api/health` is always open.
"""

import hmac
import json
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import config, status
from .ask import Asker
from .replay import DEFAULT_POLICY, replay

MAX_BODY = 64 * 1024


def insights(memory):
    rows = memory.store.insights()
    live = [r for r in rows if r["source"] == "databricks"]
    chosen = live or [r for r in rows if r["source"] == "precomputed"]
    if not chosen:
        path = config.SEED_DIR / "insights.json"
        chosen = json.loads(path.read_text(encoding="utf-8"))["insights"] if path.exists() else []
    keys = ("id", "category", "title", "text", "score", "evidence_ids", "source", "data")
    return {"insights": [{k: r.get(k) for k in keys} for r in chosen],
            "source": chosen[0]["source"] if chosen else None}


def _set(*names):
    return all(config.env(n) for n in names)


def integrations(memory):
    """Which sponsor technologies are live on this server, and which are on their fallback (SPEC §17)."""
    return {"integrations": [
        {"name": "elevenlabs", "role": "Scribe v2 Realtime speech-to-text, failover to local Whisper",
         "where": "laptop/speech/transcriber.py", "configured": None, "note": "runs on the laptop, not this server"},
        {"name": "vultr", "role": "WebSocket relay behind Caddy + Object Storage session export",
         "where": "relay/, tools/deploy_vultr.py, extras/cloud/s3.py",
         "configured": _set("VULTR_S3_ENDPOINT", "VULTR_S3_BUCKET", "VULTR_S3_ACCESS_KEY", "VULTR_S3_SECRET_KEY"),
         "endpoints": ["wss://<host>/ws", "GET /health", "GET /api/status.html", "POST /api/export"]},
        {"name": "huggingface", "role": "JEV-9B attention, DeBERTa zero-shot, Whisper, CED / AST / CLAP / PANNs",
         "where": "laptop/enrich/attention.py, laptop/sounds/models.py, extras/memory/relevance.py",
         "configured": True, "endpoints": ["POST /api/replay (use_model)"]},
        {"name": "databricks", "role": "Spark job over the event log -> Delta tables -> ranked insights",
         "where": "extras/cloud/databricks/, extras/cloud/databricks_sync.py",
         "configured": _set("DATABRICKS_HOST", "DATABRICKS_TOKEN"),
         "endpoints": ["GET /api/insights", "POST /api/insights/refresh"]},
        {"name": "neon", "role": "Postgres memory: sessions, events, full-text utterances, insights",
         "where": "extras/memory/store.py, extras/memory/neon_http.py",
         "configured": memory.health().get("db") == "neon", "endpoints": ["GET /api/day", "POST /api/ask"]},
        {"name": "gemini", "role": "one-line caption context, phrases Ask HERE answers from evidence",
         "where": "laptop/enrich/gemini.py, extras/memory/ask.py",
         "configured": _set("GEMINI_API_KEY"), "endpoints": ["POST /api/ask"]},
    ]}


def refresh_insights(memory):
    """Databricks when configured, otherwise the same metrics computed here. Runs in a thread; returns at once."""
    backend = "databricks" if _set("DATABRICKS_HOST", "DATABRICKS_TOKEN") else "precomputed"

    def run():
        try:
            if backend == "databricks":
                subprocess.run([sys.executable, "-m", "extras.cloud.databricks_sync"], cwd=config.ROOT, timeout=900)
            else:
                from extras.cloud.insights_local import compute, labels_of
                from .export import rows_for_export
                ins = compute(list(rows_for_export(memory)), "precomputed", labels_of(memory),
                              config.env("HERE_USER_NAME", ""))
                memory.store.put_insights(ins, "precomputed")
        except Exception as e:                             # the old insights stay in place
            memory.log(f"[memory api] insights refresh ({backend}): {type(e).__name__}: {e}")

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True, "started": True, "backend": backend}


def export_session(memory, session):
    from extras.cloud.export_bundle import build_bundle, upload_bundle
    from extras.cloud.s3 import S3
    if not session:
        raise ValueError("session is required")
    folder, manifest = build_bundle(memory, session)
    out = {"ok": True, "session": session, "events": manifest["events"], "local": str(folder), "uploaded": False}
    s3 = S3.from_env()
    if s3 is not None:
        keys = upload_bundle(s3, folder, session)
        out.update(uploaded=True, bucket=s3.bucket, prefix=keys[-1].rsplit("/", 1)[0])
    return out


def make_handler(memory, asker):
    class Handler(BaseHTTPRequestHandler):
        server_version = "HEREmemory/1"

        def log_message(self, fmt, *args):
            pass                                           # one line per request below instead

        def send_json(self, status, body):
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def send_html(self, html):
            data = html.encode()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ValueError("body too large")
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def do_OPTIONS(self):
            self.send_json(HTTPStatus.NO_CONTENT, {})

        def do_GET(self):
            self.route("GET")

        def do_POST(self):
            self.route("POST")

        def authorized(self, path, q):
            token = config.env("HERE_RELAY_TOKEN")
            if path in ("/health", "/") or not token or not self.headers.get("X-Forwarded-For"):
                return True
            given = q.get("token") or (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
            return hmac.compare_digest(given.encode(), token.encode())

        def route(self, method):
            t0 = time.perf_counter()
            url = urlsplit(self.path)
            path = url.path.rstrip("/")
            if path.startswith("/api"):
                path = path[4:] or "/"
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            if not self.authorized(path, q):
                self.send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "token required"})
                return
            if method == "GET" and path == "/status.html":
                self.send_html(status.PAGE)
                return
            try:
                code, body = self.dispatch(method, path, q)
            except (ValueError, KeyError, TypeError) as e:
                code, body = HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"{type(e).__name__}: {e}"}
            except Exception as e:                         # never take the service down
                code, body = HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": type(e).__name__}
                memory.log(f"[memory api] {method} {path}: {type(e).__name__}: {e}")
            self.send_json(code, body)
            memory.log(f"[memory api] {method} {path} {int(code)} {1000 * (time.perf_counter() - t0):.0f} ms")

        def dispatch(self, method, path, q):
            if method == "GET" and path in ("/health", "/"):
                return HTTPStatus.OK, memory.health()
            if method == "GET" and path == "/day":
                return HTTPStatus.OK, memory.day(q.get("date") or None)
            if method == "GET" and path == "/segments":
                return HTTPStatus.OK, {"segments": memory.segments()}
            if method == "GET" and path == "/status":
                return HTTPStatus.OK, status.status(memory)
            if method == "GET" and path == "/insights":
                return HTTPStatus.OK, insights(memory)
            if method == "GET" and path == "/integrations":
                return HTTPStatus.OK, integrations(memory)
            if method == "POST" and path == "/insights/refresh":
                return HTTPStatus.ACCEPTED, refresh_insights(memory)
            if method == "POST" and path == "/export":
                return HTTPStatus.OK, export_session(memory, str(self.body().get("session") or "").strip())
            if method == "POST" and path == "/ask":
                text = str(self.body().get("q") or "").strip()
                if not text:
                    raise ValueError("q is required")
                return HTTPStatus.OK, asker.ask(text[:500])
            if method == "POST" and path == "/replay":
                b = self.body()
                seg = memory.segment(b.get("segment_id") or "")
                if seg is None:
                    return HTTPStatus.NOT_FOUND, {"ok": False, "error": "unknown segment_id",
                                                  "segments": [s["id"] for s in memory.segments()]}
                policy = {k: b.get("policy", {}).get(k, v) for k, v in DEFAULT_POLICY.items()}
                use_model = None if b.get("use_model", True) else False
                return HTTPStatus.OK, {"segment_id": seg["id"],
                                       **replay(memory.segment_rows(seg), policy, use_model=use_model)}
            return HTTPStatus.NOT_FOUND, {"ok": False, "error": "see extras/memory/api.py for the endpoints"}

    return Handler


def make_server(memory, host=config.API_HOST, port=config.API_PORT):
    asker = Asker(memory)
    server = ThreadingHTTPServer((host, port), make_handler(memory, asker))
    server.daemon_threads = True
    server.asker = asker
    return server

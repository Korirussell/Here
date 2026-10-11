"""Memory API over HTTP (INTEGRATION_CONTRACT §5.3): :8790 locally, behind Caddy at /api on Vultr.

    GET  /api/health                  {ok, db: "neon" | "local", events, sessions, ...}
    GET  /api/day?date=YYYY-MM-DD     {date, seeded, segments:[...], events:[canonical + person_label, location_id]}
    POST /api/ask {q}                 {answer, evidence:[...], cached}
    GET  /api/insights                {insights:[{id, category, title, text, score, evidence_ids, source}]}
    POST /api/replay {segment_id, policy, use_model?}
                                      {surfaced, suppressed, kept_important, by_category, decisions, model?}
    GET  /api/segments                every known segment (seeded + live sessions)
    GET  /api/status                  relay health + message rates/ages from its log, memory, insights (status.py)
    GET  /api/status.html             the same as a small self-refreshing page

Stdlib only (ThreadingHTTPServer). CORS is open (the dashboard runs on another port).

Auth: requests that come through a proxy (Caddy on Vultr adds X-Forwarded-For) must carry the relay token
(`?token=` or `Authorization: Bearer`), because memory holds transcripts (README §25). Direct local requests
(the dashboard on the demo laptop) need none. `/api/health` is always open.
"""

import hmac

import json
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

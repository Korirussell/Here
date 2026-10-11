"""HERE World dashboard server: one asyncio process on 127.0.0.1:8780.

    python -m extras.dashboard                      # relay from laptop/config.yaml + laptop/.env
    python -m extras.dashboard --relay ws://localhost:8080/ws --memory http://127.0.0.1:8790

- Joins the relay as role `dashboard` (subscribes to `*`) and fans every message out to the open pages over
  `/live`, wrapped as {"k": "msg", "rx": <receive time>, "m": <message unchanged>}. The relay token stays here.
- Serves the page (`static/`), and `/api/*`: the memory service's memory API (§5.3) when it answers, otherwise the seeded
  JSON in `demo_data/` (every reply says which: `_source: "api" | "seed"`).
- `/dash/demo?...` starts `tools/fake_events.py` against the same relay (the one-button scripted scene). With no
  relay reachable (or `local=1`) it plays the laptop's `fixtures/events` scenes itself, straight to the pages, labelled
  as a local replay: the booth demo survives the venue Wi-Fi. While a laptop is live on the relay (a fresh
  heartbeat from src "laptop"), Run demo is always a local replay too, so a stray click or `S` never puts
  scripted captions or buzzes on the wearer's phone; `force_relay=1` overrides that on purpose.

The browser only ever makes GET requests and opens one WebSocket, so the server needs nothing beyond
`websockets` (already a laptop dependency).
"""

import argparse
import asyncio
import json
import mimetypes
import re
import subprocess
import sys
import time
from collections import OrderedDict, deque
from http import HTTPStatus
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Response

from . import fallback, settings as S

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("font/woff2", ".woff2")


def relay_ws_url(base, session, token):
    parts = urlsplit(base)
    q = dict(parse_qsl(parts.query))
    q.update({"session": session, "role": "dashboard"})
    if token:
        q["token"] = token
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/ws", urlencode(q), ""))


def redact(url):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


SCENE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SCENE_TIME_FIELDS = ("t", "t_start", "t_end")


def load_scene(name):
    """One fixtures/events scene: canonical messages, times in seconds from the scene start."""
    if not SCENE_NAME.match(name or ""):
        return None
    path = S.SCENES / f"{name}.jsonl"
    if not path.is_file():
        return None
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def rebase_row(d, base, tag):
    """A scene line as a message for now: times onto this clock, ids unique per playback (like fake_events)."""
    d = json.loads(json.dumps(d))
    for k in SCENE_TIME_FIELDS:
        if isinstance(d.get(k), (int, float)):
            d[k] = round(base + d[k], 3)
    for k in ("utt_id", "event_id", "alert_id"):
        if k in d:
            d[k] = f"{d[k]}-{tag}"
    ref = d.get("ref") or {}
    for k in ("utt_id", "event_id"):
        if k in ref:
            ref[k] = f"{ref[k]}-{tag}"
    d.pop("seq", None)
    d.pop("session", None)
    return d


def transport_label(url):
    host = urlsplit(url).hostname or ""
    local = host in ("localhost", "127.0.0.1", "::1") or host.startswith("192.168.") or host.startswith("10.")
    return "local relay" if local else "Vultr relay"


class Page:
    """One open dashboard tab: bounded outbox, newest-only for positional streams (like the relay)."""

    def __init__(self, ws):
        self.ws = ws
        self.latest = OrderedDict()
        self.events = deque(maxlen=S.BROWSER_OUTBOX)
        self.wake = asyncio.Event()

    def push(self, key, text):
        if key is not None:
            self.latest[key] = text
            self.latest.move_to_end(key)
        else:
            self.events.append(text)
        self.wake.set()

    async def write_loop(self):
        while True:
            await self.wake.wait()
            self.wake.clear()
            while self.events or self.latest:
                text = self.events.popleft() if self.events else self.latest.popitem(last=False)[1]
                await self.ws.send(text)


class Dashboard:
    def __init__(self, relay_url, session, token, memory_url):
        self.relay_base = relay_url
        self.session = session
        self.token = token
        self.memory = fallback.Memory(memory_url, token=token)
        self.pages: set[Page] = set()
        self.history = deque(maxlen=S.HISTORY)        # wrapped event texts for a freshly opened page
        self.latest = {}                              # (type, src) -> wrapped text
        self.relay_connected = False
        self.relay_since = None
        self.relay_error = ""
        self.received = 0
        self.demo = None                              # subprocess.Popen of tools/fake_events.py
        self.demo_label = ""
        self.local = None                             # asyncio.Task of a local labelled replay
        self.demo_q = {}                              # the last Run demo request (to resume it locally)
        self.local_n = 0
        self.laptop_seen = float("-inf")              # time.time() of the last heartbeat from src "laptop"

    # ---- relay -> pages ------------------------------------------------------

    def local_running(self):
        return self.local is not None and not self.local.done()

    def laptop_live(self):
        """The laptop pipeline is on the relay (same rule as the page's laptopLive(); fake_events beats as "tool")."""
        return time.time() - self.laptop_seen < S.PAGE["heartbeat_stale_s"]

    def status(self):
        demo_running = (self.demo is not None and self.demo.poll() is None) or self.local_running()
        return {"k": "status", "t": time.time(),
                "relay": {"connected": self.relay_connected, "url": redact(self.relay_base),
                          "session": self.session, "transport": transport_label(self.relay_base),
                          "since": self.relay_since, "error": self.relay_error, "received": self.received},
                "demo": {"running": demo_running, "label": self.demo_label if demo_running else "",
                         "local": self.local_running()}}

    def broadcast_status(self):
        text = json.dumps(self.status())
        for p in self.pages:
            p.push(("_status", ""), text)

    def on_relay_text(self, text):
        try:
            msg = json.loads(text)
        except ValueError:
            return
        if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
            return
        self.received += 1
        if msg["type"] == "heartbeat" and msg.get("src") == "laptop":
            self.laptop_seen = time.time()
        self.fan_out(msg)

    def fan_out(self, msg, local=False):
        body = {"k": "msg", "rx": time.time(), "m": msg}
        if local:
            body["local"] = True                     # played by this server, not received from the relay
        wrapped = json.dumps(body, ensure_ascii=False)
        mtype = msg["type"]
        if mtype in S.LATEST_ONLY:
            key = (mtype, str(msg.get("src", "")))
            self.latest[key] = wrapped
        else:
            key = None
            self.history.append(wrapped)
        for p in self.pages:
            p.push(key, wrapped)

    async def relay_loop(self):
        delay = S.RELAY_RECONNECT_MIN_S
        url = relay_ws_url(self.relay_base, self.session, self.token)
        while True:
            try:
                async with connect(url, open_timeout=3, max_size=256 * 1024, ping_interval=10) as ws:
                    await ws.send(json.dumps({"type": "hello", "v": 3, "src": "dashboard", "t": time.time(),
                                              "role": "dashboard", "subscribe": ["*"],
                                              "app_version": "here-world-0.1"}))
                    self.relay_connected, self.relay_since, self.relay_error = True, time.time(), ""
                    delay = S.RELAY_RECONNECT_MIN_S
                    self.broadcast_status()
                    print(f"[dashboard] relay connected: {redact(self.relay_base)} session={self.session}")
                    async for text in ws:
                        if isinstance(text, str):
                            self.on_relay_text(text)
            except (OSError, ConnectionClosed, asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
                err = type(e).__name__
                if getattr(e, "response", None) is not None and getattr(e.response, "status_code", None) == 401:
                    err = "relay refused the token (401)"
                if self.relay_connected or err != self.relay_error:
                    print(f"[dashboard] relay unavailable ({err}); retrying")
                was_playing = self.relay_connected and self.demo is not None and self.demo.poll() is None
                self.relay_connected, self.relay_error = False, err
                if was_playing:                           # the relay dropped mid-demo: carry on locally
                    print("[dashboard] relay lost during a scripted demo: continuing as a local replay")
                    self.start_local(self.demo_q)
                self.broadcast_status()
            await asyncio.sleep(delay)
            delay = min(S.RELAY_RECONNECT_MAX_S, delay * 2)

    async def status_loop(self):
        while True:
            await asyncio.sleep(2.0)
            self.broadcast_status()

    # ---- pages ---------------------------------------------------------------

    async def page_handler(self, ws):
        page = Page(ws)
        page.push(("_status", ""), json.dumps(self.status()))
        for text in self.history:
            page.push(None, text)
        for key, text in self.latest.items():
            page.push(key, text)
        self.pages.add(page)
        writer = asyncio.create_task(page.write_loop())
        try:
            async for _ in ws:                     # the page never sends anything we act on
                pass
        except ConnectionClosed:
            pass
        finally:
            self.pages.discard(page)
            writer.cancel()

    # ---- demo ----------------------------------------------------------------

    def demo_action(self, q):
        action = q.get("action", "")
        if action not in ("", "stop", "reset"):
            return {"ok": False, "error": f"unknown action {action!r}"}     # never start a demo by accident
        if action not in ("stop", "reset"):
            self.demo_q = dict(q)
        if action == "stop":
            self.stop_demo()
            return {"ok": True, "running": False}
        if action == "reset":
            self.stop_demo()
            self.history.clear()
            self.latest.clear()
            return {"ok": True, "reset": True}
        args = [sys.executable, str(S.ROOT / "tools" / "fake_events.py"), "--relay", self.relay_base,
                "--session", self.session]
        if q.get("scene"):
            name = q["scene"]
            if not (S.SCENES / f"{name}.jsonl").exists():
                return {"ok": False, "error": f"no scene fixtures/events/{name}.jsonl"}
            args += ["--scene", name]
            label = f"scene {name}"
        elif q.get("moment") == "tour":
            return self.start_local(q)                  # the tour is always a labelled local replay
        elif q.get("moment") in ("1", "2", "3"):
            args += ["--moment", q["moment"]]
            label = f"moment {q['moment']}"
        else:
            label = "core moments"
        if q.get("loop") == "1":
            args.append("--loop")
            label += " (loop)"
        if not self.relay_connected or q.get("local") == "1":
            return self.start_local(q)
        if self.laptop_live() and q.get("force_relay") != "1":
            r = self.start_local(q)
            if r.get("ok"):
                r["why_local"] = "the laptop is live on the relay: the scripted scene plays on this screen only"
            return r
        self.stop_demo()
        self.demo = subprocess.Popen(args, cwd=str(S.ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.demo_label = label
        self.broadcast_status()
        return {"ok": True, "running": True, "label": label}

    # ---- local labelled replay (no relay needed) -------------------------------

    def start_local(self, q):
        if q.get("scene"):
            names = [q["scene"]]
        else:
            names = S.LOCAL_MOMENTS.get(q.get("moment") or "all", S.LOCAL_MOMENTS["all"])
        scenes = [(n, load_scene(n)) for n in names]
        scenes = [(n, rows) for n, rows in scenes if rows]
        if not scenes:
            return {"ok": False, "error": "relay not connected, and no fixtures/events scenes to play locally"}
        self.stop_demo()
        loop = q.get("loop") == "1"
        moment = q.get("moment") or "all"
        what = q["scene"] if q.get("scene") else {"all": "all moments", "tour": "judge tour"}.get(moment, f"moment {moment}")
        self.demo_label = f"{what}{' (loop)' if loop else ''} · local replay"
        self.local = asyncio.get_running_loop().create_task(self.play_local(scenes, loop))
        self.broadcast_status()
        return {"ok": True, "running": True, "local": True, "label": self.demo_label}

    async def play_local(self, scenes, loop):
        try:
            while True:
                for name, rows in scenes:
                    self.local_n += 1
                    base = time.time() + 0.05
                    for d in rows:
                        delay = base + d["t"] - time.time()
                        if delay > 0:
                            await asyncio.sleep(delay)
                        self.fan_out(rebase_row(d, base, f"local{self.local_n}"), local=True)
                    await asyncio.sleep(S.LOCAL_GAP_S)
                if not loop:
                    break
        finally:
            self.broadcast_status()

    def stop_demo(self):
        if self.local_running():
            self.local.cancel()
        self.local = None
        if self.demo is not None and self.demo.poll() is None:
            self.demo.terminate()
            try:
                self.demo.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.demo.kill()
        self.demo = None
        self.demo_label = ""
        self.broadcast_status()

    # ---- HTTP ----------------------------------------------------------------

    @staticmethod
    def response(status, body, ctype="application/json", cache="no-store"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        headers = Headers([("Content-Type", ctype), ("Content-Length", str(len(data))),
                           ("Cache-Control", cache)])
        return Response(status.value, status.phrase, headers, data)

    def page_config(self):
        scenes = sorted(p.stem for p in S.SCENES.glob("*.jsonl")) if S.SCENES.exists() else []
        return {"page": S.PAGE, "relay": self.status()["relay"], "scenes": scenes,
                "memory": {"url": self.memory.base}}

    async def process_request(self, connection, request):
        url = urlsplit(request.path)
        path = url.path
        q = dict(parse_qsl(url.query))
        if path == "/live":
            return None                                          # WebSocket upgrade
        if path == "/dash/config":
            return self.response(HTTPStatus.OK, self.page_config())
        if path == "/dash/demo":
            return self.response(HTTPStatus.OK, self.demo_action(q))
        if path.startswith("/api/"):
            body = await asyncio.to_thread(self.memory.call, path[len("/api/"):], q)
            if body is None:
                return self.response(HTTPStatus.NOT_FOUND, {"ok": False, "error": "unknown endpoint"})
            return self.response(HTTPStatus.OK, body)
        return self.static(path)

    def static(self, path):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (S.STATIC / rel).resolve()
        if S.STATIC.resolve() not in target.parents or not target.is_file():
            return self.response(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript") or ctype.endswith("json"):
            ctype += "; charset=utf-8"
        cache = "public, max-age=86400" if rel.startswith("vendor/") else "no-store"   # three.js, fonts
        return self.response(HTTPStatus.OK, target.read_bytes(), ctype, cache)


async def run_dashboard(dash, host=S.HOST, port=S.PORT, ready=None):
    """Serve until cancelled. `ready` (a future) gets the bound port, for tests (port=0)."""
    tasks = [asyncio.create_task(dash.relay_loop()), asyncio.create_task(dash.status_loop())]
    try:
        async with serve(dash.page_handler, host, port, process_request=dash.process_request,
                         max_size=64 * 1024) as server:
            bound = next(iter(server.sockets)).getsockname()[1]
            print(f"[dashboard] HERE World on http://{host}:{bound}  (relay {redact(dash.relay_base)}, "
                  f"memory {dash.memory.base})")
            if ready is not None:
                ready.set_result(bound)
            await asyncio.Future()
    finally:
        dash.stop_demo()
        for t in tasks:
            t.cancel()


async def amain(args):
    sys.path.insert(0, str(S.ROOT))
    from laptop import settings as laptop_settings     # read-only: relay url / session / token
    relay = laptop_settings.load()["relay"]
    dash = Dashboard(args.relay or relay["url"], args.session or relay["session"], relay.get("token", ""),
                     args.memory or S.MEMORY_URL)
    await run_dashboard(dash, S.HOST, args.port)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--relay", help="relay base URL (default: laptop/config.yaml or HERE_RELAY_URL)")
    p.add_argument("--session")
    p.add_argument("--memory", help=f"memory API base (default {S.MEMORY_URL})")
    p.add_argument("--port", type=int, default=S.PORT)
    try:
        asyncio.run(amain(p.parse_args(argv)))
    except KeyboardInterrupt:
        pass

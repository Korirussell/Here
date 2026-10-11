"""HERE relay (SPEC §7): routes contract messages between the laptop, the phone and the extras.

    wss://<host>/ws?session=<session>&role=<role>&token=<token>      (Caddy terminates TLS)
    GET /health -> {"ok": true, "sessions": {...}, "clients": [{"role", "session", "connected_s"}]}

Rules:
- Token from HERE_RELAY_TOKEN; a wrong or missing token gets HTTP 401 before the WebSocket opens.
- A message goes to every *other* client in the same session whose `hello.subscribe` contains its
  `type` (or "*"). Clients that don't list subscriptions get the SPEC §12.10 defaults for their role.
- The latest `config` per session is kept and sent to each new client right after its hello.
- No queues: per client, positional streams (sources, scene, heartbeat) keep only the newest message
  per (type, src); events (utterance, alert...) go in a bounded outbox that drops the oldest.
- TCP_NODELAY on every socket; every message logged as JSON lines.

Run locally (also the "local fallback relay", SPEC §7):  python -m relay --port 8080
"""

import argparse
import asyncio
import json
import os
import socket
import time
from collections import OrderedDict, deque
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Response

ROLES = {"laptop", "phone", "dashboard", "memory", "tool"}
DEFAULT_SUBSCRIBE = {
    "phone": ["sources", "speech_start", "utterance", "sound_event", "alert", "perception_enrichment", "config",
              "clock_pong", "heartbeat"],
    "laptop": ["config", "clock_ping"],
    "dashboard": ["*"],
    "memory": ["utterance", "alert", "sound_event", "perception_enrichment", "attribution", "search_request"],
    "tool": ["*"],
}
LATEST_ONLY = {"sources", "scene", "heartbeat"}   # newer replaces older per (type, src) when a client lags
OUTBOX_MAX = 256
MAX_MESSAGE = 64 * 1024


def load_dotenv(path):
    path = Path(path)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip()
                end = v.find(v[:1], 1) if v[:1] in ("'", '"') else -1
                if end > 0:                                          # quoted: up to the closing quote, '#' kept
                    v = v[1:end]
                else:
                    v = v.split(" #", 1)[0].split("\t#", 1)[0].strip()   # trailing comments, as in relay/.env.example
                os.environ.setdefault(k.strip(), v)


class Client:
    def __init__(self, ws, session, role):
        self.ws = ws
        self.session = session
        self.role = role
        self.subscribe = set(DEFAULT_SUBSCRIBE.get(role, []))
        self.connected_at = time.time()
        self.latest = OrderedDict()          # (type, src) -> text, for LATEST_ONLY types
        self.events = deque(maxlen=OUTBOX_MAX)
        self.wake = asyncio.Event()
        self.dropped = 0
        self.writer = None

    def wants(self, msg_type):
        return "*" in self.subscribe or msg_type in self.subscribe

    def enqueue(self, msg_type, src, text):
        if msg_type in LATEST_ONLY:
            key = (msg_type, src)
            if key in self.latest:
                self.dropped += 1
            self.latest[key] = text
            self.latest.move_to_end(key)
        else:
            if len(self.events) == self.events.maxlen:
                self.dropped += 1
            self.events.append(text)
        self.wake.set()

    async def write_loop(self):
        while True:
            await self.wake.wait()
            self.wake.clear()
            while self.events or self.latest:
                # Events first (captions, alerts), then the newest positional state.
                text = self.events.popleft() if self.events else self.latest.popitem(last=False)[1]
                await self.ws.send(text)


class Relay:
    def __init__(self, token="", log_path="logs/relay.jsonl"):
        self.token = token
        self.clients: set[Client] = set()
        self.configs: dict[str, str] = {}    # session -> latest config message (JSON text)
        self.started = time.time()
        self.counts: dict[str, int] = {}
        self.log = None
        if log_path:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            self.log = open(log_path, "a", encoding="utf-8", buffering=1)

    # ---- HTTP: auth + health --------------------------------------------------

    def process_request(self, connection, request):
        url = urlsplit(request.path)
        if url.path == "/health":
            return self.json_response(HTTPStatus.OK, self.health())
        if url.path != "/ws":
            return self.json_response(HTTPStatus.NOT_FOUND, {"ok": False, "error": "use /ws or /health"})
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if self.token and q.get("token") != self.token:
            return self.json_response(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "bad token"})
        if q.get("role") not in ROLES:
            return self.json_response(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"role must be one of {sorted(ROLES)}"})
        return None

    @staticmethod
    def json_response(status, body):
        data = json.dumps(body).encode()
        headers = Headers([("Content-Type", "application/json"), ("Content-Length", str(len(data))),
                           ("Access-Control-Allow-Origin", "*"), ("Cache-Control", "no-store")])
        return Response(status.value, status.phrase, headers, data)

    def health(self):
        now = time.time()
        sessions = {}
        for c in self.clients:
            sessions.setdefault(c.session, []).append(c.role)
        return {"ok": True, "uptime_s": round(now - self.started),
                "sessions": sessions,
                "clients": [{"role": c.role, "session": c.session, "connected_s": round(now - c.connected_at, 1),
                             "dropped": c.dropped} for c in self.clients]}

    # ---- WebSocket ------------------------------------------------------------

    async def handler(self, ws):
        q = {k: v[0] for k, v in parse_qs(urlsplit(ws.request.path).query).items()}
        sock = ws.transport.get_extra_info("socket")
        if sock is not None:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
        client = Client(ws, q.get("session", "default"), q["role"])
        self.clients.add(client)
        client.writer = asyncio.create_task(client.write_loop())
        self.write_log({"event": "connect", "session": client.session, "role": client.role})
        print(f"+ {client.role} joined session {client.session!r} ({len(self.clients)} connected)")
        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue                     # SPEC §12.1: JSON text only
                self.on_text(client, raw)
        except ConnectionClosed:
            pass
        finally:
            self.clients.discard(client)
            client.writer.cancel()
            self.write_log({"event": "disconnect", "session": client.session, "role": client.role,
                            "dropped": client.dropped})
            print(f"- {client.role} left session {client.session!r}")

    def on_text(self, client, raw):
        try:
            msg = json.loads(raw)
            msg_type = msg["type"]
        except (ValueError, KeyError, TypeError):
            self.write_log({"event": "bad_message", "role": client.role, "raw": raw[:200]})
            return
        msg["session"] = client.session
        text = json.dumps(msg, ensure_ascii=False, separators=(",", ":"))
        self.counts[msg_type] = self.counts.get(msg_type, 0) + 1
        self.write_log({"rx": time.time(), "from": client.role, **msg})

        if msg_type == "hello":
            subscribe = msg.get("subscribe") or DEFAULT_SUBSCRIBE.get(client.role, [])
            client.subscribe = set(subscribe)
            if client.wants("config") and client.session in self.configs:
                client.enqueue("config", "relay", self.configs[client.session])
            return
        if msg_type == "config":
            self.configs[client.session] = text
        for other in self.clients:
            if other is not client and other.session == client.session and other.wants(msg_type):
                other.enqueue(msg_type, msg.get("src", client.role), text)

    def write_log(self, entry):
        if self.log:
            self.log.write(json.dumps(entry, ensure_ascii=False) + "\n")

    async def report(self, every_s=30.0):
        while True:
            await asyncio.sleep(every_s)
            if self.counts:
                rates = ", ".join(f"{k} {n / every_s:.1f}/s" for k, n in sorted(self.counts.items()))
                print(f"[relay] {len(self.clients)} clients | {rates}")
                self.counts.clear()


async def serve_relay(host="127.0.0.1", port=8080, token="", log_path="logs/relay.jsonl", ready=None):
    relay = Relay(token, log_path)
    async with serve(relay.handler, host, port, process_request=relay.process_request,
                     max_size=MAX_MESSAGE, ping_interval=20, ping_timeout=20) as server:
        port = server.sockets[0].getsockname()[1]
        print(f"HERE relay on ws://{host}:{port}/ws  (health: http://{host}:{port}/health, "
              f"auth {'on' if token else 'OFF: set HERE_RELAY_TOKEN'})")
        if ready is not None:
            ready.set_result((relay, port))
        reporter = asyncio.create_task(relay.report())
        try:
            await server.serve_forever()
        finally:
            reporter.cancel()


def main():
    load_dotenv(Path(__file__).with_name(".env"))
    p = argparse.ArgumentParser(prog="python -m relay", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default=os.environ.get("HERE_RELAY_HOST", "127.0.0.1"),
                   help="127.0.0.1 behind Caddy; 0.0.0.0 for the local fallback relay")
    p.add_argument("--port", type=int, default=int(os.environ.get("HERE_RELAY_PORT", "8080")))
    p.add_argument("--log", default=os.environ.get("HERE_RELAY_LOG", "logs/relay.jsonl"))
    args = p.parse_args()
    try:
        asyncio.run(serve_relay(args.host, args.port, os.environ.get("HERE_RELAY_TOKEN", ""), args.log))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

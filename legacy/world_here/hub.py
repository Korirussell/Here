"""The hub: WebSocket server on :8765 that routes contract messages and runs fusion (HERE_PLAN.md §5, §8)."""

import asyncio
import json
import time
from collections import Counter
from pathlib import Path

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from here_common.client import BINARY_HEADERS

from . import config as C
from .fusion import World

# Messages the hub consumes itself instead of forwarding as-is.
HUB_ONLY = {"hello", "clock_ping", "calibrate", "config"}
# Real-time streams: if a consumer is still busy with the previous one, skip it.
DROPPABLE = {"video_frame", "frame", "heading", "doa"}


class Client:
    def __init__(self, ws):
        self.ws = ws
        self.role = "?"
        self.subscribe = set()
        self.lock = asyncio.Lock()
        self.last_heartbeat = None
        self.last_seen = time.time()

    def wants(self, msg_type):
        return "*" in self.subscribe or msg_type in self.subscribe

    async def send(self, text, payload=None, drop_if_busy=False):
        if drop_if_busy and self.lock.locked():
            return
        async with self.lock:
            try:
                await self.ws.send(text)
                if payload is not None:
                    await self.ws.send(payload)
            except ConnectionClosed:
                pass


class Hub:
    def __init__(self):
        self.clients = set()
        self.world = World()
        self.counts = Counter()
        self.seq = 0
        Path(C.LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
        self.log = open(C.LOG_PATH, "a", encoding="utf-8")

    # ---- plumbing ---------------------------------------------------------------

    def envelope(self, msg_type, **fields):
        self.seq += 1
        return {"type": msg_type, "v": 1, "src": "world_here", "t": time.time(), "seq": self.seq, **fields}

    def broadcast(self, msg, payload=None, exclude=None):
        text = json.dumps(msg)
        drop = msg["type"] in DROPPABLE
        for c in list(self.clients):
            if c is not exclude and c.wants(msg["type"]):
                asyncio.create_task(c.send(text, payload, drop_if_busy=drop))

    async def handle(self, ws):
        client = Client(ws)
        self.clients.add(client)
        header = None
        try:
            async for raw in ws:
                client.last_seen = time.time()
                if isinstance(raw, bytes):
                    if header is not None:
                        await self.on_message(client, header, raw)
                    header = None
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    print(f"! bad JSON from {client.role}: {raw[:120]}")
                    continue
                self.log.write(json.dumps({"rx": client.last_seen, **msg}) + "\n")
                header = msg if msg.get("type") in BINARY_HEADERS else None
                if header is None:
                    await self.on_message(client, msg, None)
        except ConnectionClosed:
            pass                                  # client dropped; it reconnects on its own
        finally:
            self.clients.discard(client)
            print(f"- {client.role} disconnected")

    # ---- routing ----------------------------------------------------------------

    async def on_message(self, client, msg, payload):
        kind = msg.get("type")
        self.counts[f"{client.role}:{kind}"] += 1
        w = self.world

        if kind == "hello":
            client.role = msg.get("role", "?")
            client.subscribe = set(msg.get("subscribe", []))
            print(f"+ {client.role} connected, subscribe={sorted(client.subscribe)}")
            if client.wants("config"):
                await client.send(json.dumps(self.envelope("config", **w.config)))
            if client.wants("calibrate"):
                await client.send(json.dumps(self.envelope("calibrate", kind="heading_zero", value=w.heading_offset)))
            return
        if kind == "clock_ping":
            await client.send(json.dumps(self.envelope("clock_pong", t_phone=msg["t_phone"], t_hub=time.time())))
            return
        if kind == "heartbeat":
            client.last_heartbeat = msg
        elif kind == "calibrate":
            if msg.get("kind") == "heading_zero":
                offset = w.calibrate_heading_zero()
                print(f"calibrated heading zero: offset {offset:.1f} deg")
                # vision_here needs the offset to turn raw heading into world bearings.
                self.broadcast(self.envelope("calibrate", kind="heading_zero", value=offset))
            else:
                self.broadcast(msg, exclude=client)   # array_forward → audio_here
            return
        elif kind == "config":
            w.config = {k: msg.get(k, v) for k, v in C.DEFAULT_CONFIG.items()}
            self.broadcast(self.envelope("config", **w.config))
            return
        elif kind == "heading":
            w.on_heading(msg)
        elif kind == "doa":
            w.on_doa(msg)
        elif kind == "detections":
            w.on_detections(msg)
        elif kind == "utterance":
            w.on_utterance(msg, time.time())

        self.broadcast(msg, payload, exclude=client)

        if kind == "video_frame":
            meta = w.frame_meta(msg, time.time())
            meta["health"] = self.health()
            self.broadcast(meta, payload)

    def health(self):
        now = time.time()
        out = {}
        for c in self.clients:
            hb = c.last_heartbeat
            if now - c.last_seen > 5:
                out[c.role] = "offline"
            else:
                out[c.role] = hb["status"] if hb else "ok"
        return out

    async def report(self):
        while True:
            await asyncio.sleep(C.STATUS_EVERY_S)
            rates = ", ".join(f"{k} {n / C.STATUS_EVERY_S:.1f}/s" for k, n in sorted(self.counts.items())
                              if not k.endswith(":heartbeat"))
            print(f"[hub] clients: {sorted(c.role for c in self.clients) or 'none'} | {rates or 'no traffic'}")
            self.counts.clear()
            self.log.flush()


async def main(host=C.HOST, port=C.PORT):
    hub = Hub()
    async with serve(hub.handle, host, port, max_size=None, ping_interval=None) as server:
        print(f"world_here hub listening on ws://{host}:{port}  (log: {C.LOG_PATH})")
        asyncio.create_task(hub.report())
        await server.serve_forever()

"""The laptop's one connection to the relay (SPEC §7, §12.1).

    wss://<host>/ws?session=<session>&role=laptop&token=<token>

Sends `hello` (role laptop, subscribes to config + clock_ping), a heartbeat every 2 s with the
transcription engine and DoA source, answers `clock_ping` with `clock_pong`, and reconnects with
backoff (500 ms doubling to 5 s). `send()` never blocks the pipeline: while disconnected messages are
dropped (stale captions are worse than none), and a bounded outbox drops the oldest if the link stalls.
Every message is also written to a JSON-lines log, with its stage timings (`_stage`: serialize, socket_send,
and capture/doa for `sources`; laptop/events/stats.py) once the websocket write finishes, or `_dropped` when it
never went out. `utterance.stage_ms` also gets the latest capture/doa (keys the speech pipeline set are kept).
Several links at once (local relay + Vultr mirror): laptop/client/fanout.py.
"""

import asyncio
import json
import socket
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from contracts.python.here_contract import (DEFAULT_SUBSCRIBE, ClockPing, ClockPong, Config, Heartbeat, Hello,
                                            Message, parse)
from laptop.events.stats import STATS

APP_VERSION = "0.3.0"


def relay_url(base, session, role, token):
    """Add session/role/token query params to a ws(s)://host/ws URL."""
    parts = urlsplit(base)
    q = dict(parse_qsl(parts.query))
    q.update({"session": session, "role": role})
    if token:
        q["token"] = token
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/ws", urlencode(q), ""))


def log_line(msg: Message, sent=None, stage=None, dropped=False) -> str:
    """One JSON-lines log entry: the message exactly as sent, plus `_sent` (laptop clock when it was handed
    to the link), `_stage` (ms per stage) and `_dropped`. Log only, never on the wire; tools/run_report.py uses
    it for latencies."""
    d = {**msg.to_dict(), "_sent": round(time.time() if sent is None else sent, 3)}
    if stage:
        d["_stage"] = {k: round(v, 2) for k, v in stage.items()}
    if dropped:
        d["_dropped"] = True
    return json.dumps(d, ensure_ascii=False) + "\n"


def prepare(msg: Message):
    """Stage timings for a message about to be sent: utterance.stage_ms gets the latest capture/doa (without
    overwriting the speech pipeline's keys); `sources` carry theirs on `msg._stage` (set by the app)."""
    stage = dict(getattr(msg, "_stage", None) or {})
    if getattr(msg, "TYPE", "") == "utterance":
        extra = {k: round(v, 1) for k in ("capture", "doa") if (v := STATS.latest(k)) is not None}
        if extra:
            msg.stage_ms = {**extra, **(msg.stage_ms or {})}
    t0 = time.perf_counter()
    text = msg.to_json()
    stage["serialize"] = (time.perf_counter() - t0) * 1000
    STATS.add("serialize", stage["serialize"])
    return text, stage


def redact(url):
    parts = urlsplit(url)
    q = [(k, "***" if k == "token" else v) for k, v in parse_qsl(parts.query)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), ""))


class RelayClient:
    def __init__(self, url, session="demo", token="", role="laptop", subscribe=None, log_path=None,
                 heartbeat_s=2.0, outbox=256):
        self.url = relay_url(url, session, role, token)
        self.role = role
        self.subscribe = list(subscribe if subscribe is not None else DEFAULT_SUBSCRIBE.get(role, []))
        self.heartbeat_s = heartbeat_s  # None: no heartbeats of its own (a FanoutClient sends them on every link)
        self.ws = None
        self.seq = 0
        self.next_seq = self._next_seq  # a FanoutClient points every link at one shared counter
        self.outbox = deque(maxlen=outbox)
        self.wake = asyncio.Event()
        self.on_config = None           # (Config) -> None
        self.on_message = None          # (Message) -> None, everything else received
        self.heartbeat_fields = lambda: {"status": "ok"}
        self.sent = 0
        self.dropped = 0
        self.max_queue = 0
        self.log = None
        if log_path:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            self.log = open(log_path, "a", encoding="utf-8", buffering=1)

    @property
    def connected(self):
        return self.ws is not None

    @property
    def queue_depth(self):
        return len(self.outbox)

    def _log(self, msg, sent, stage=None, dropped=False):
        if self.log:
            self.log.write(log_line(msg, sent, stage, dropped))

    def _next_seq(self):
        self.seq += 1
        return self.seq

    def send(self, msg: Message):
        """Queue a message for the relay (non-blocking). Dropped while disconnected."""
        msg.seq = self.next_seq()
        text, stage = prepare(msg)
        return self.push(msg, text, stage)

    def push(self, msg: Message, text, stage):
        """Queue an already numbered and serialized message (send() does both; FanoutClient does them once)."""
        now = time.time()
        if self.ws is None:
            self.dropped += 1
            self._log(msg, now, stage, dropped=True)
            return False
        if len(self.outbox) == self.outbox.maxlen:
            self.dropped += 1
            _, old_msg, old_t, old_stage, _ = self.outbox[0]
            self._log(old_msg, old_t, old_stage, dropped=True)
        self.outbox.append((text, msg, now, stage, time.perf_counter()))
        self.max_queue = max(self.max_queue, len(self.outbox))
        self.wake.set()
        return True

    async def run(self):
        delay = 0.5
        while True:
            try:
                async with connect(self.url, open_timeout=5, ping_interval=10, ping_timeout=10,
                                   max_size=2**20) as ws:
                    sock = ws.transport.get_extra_info("socket")
                    if sock is not None:
                        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    await ws.send(Hello(src=self.role, t=time.time(), role=self.role, subscribe=self.subscribe,
                                        app_version=APP_VERSION).to_json())
                    self.ws, delay = ws, 0.5
                    self.outbox.clear()
                    print(f"[relay] connected {redact(self.url)}")
                    tasks = [asyncio.create_task(self._writer(ws))]
                    if self.heartbeat_s:
                        tasks.append(asyncio.create_task(self._heartbeats()))
                    try:
                        await self._reader(ws)
                    finally:
                        for t in tasks:
                            t.cancel()
            except (OSError, ConnectionClosed, asyncio.TimeoutError) as e:
                if self.ws is not None:
                    print(f"[relay] disconnected ({type(e).__name__}); reconnecting")
                elif delay >= 5.0 or delay == 0.5:
                    print(f"[relay] can't reach {redact(self.url)} ({type(e).__name__}: {e}); retrying")
            except Exception as e:  # noqa: BLE001 - e.g. HTTP 401 from a bad token
                print(f"[relay] connection refused: {e}")
            finally:
                self.ws = None
            await asyncio.sleep(delay)
            delay = min(delay * 2, 5.0)

    async def _writer(self, ws):
        while True:
            await self.wake.wait()
            self.wake.clear()
            while self.outbox:
                text, msg, handed, stage, t0 = self.outbox.popleft()
                await ws.send(text)
                self.sent += 1
                stage["socket_send"] = (time.perf_counter() - t0) * 1000
                STATS.add("socket_send", stage["socket_send"])
                self._log(msg, handed, stage)

    async def _reader(self, ws):
        async for raw in ws:
            if isinstance(raw, bytes):
                continue
            try:
                msg = parse(raw)
            except (ValueError, TypeError, KeyError) as e:
                print(f"[relay] unreadable message ({e}): {raw[:120]}")
                continue
            if isinstance(msg, ClockPing):
                self.send(ClockPong(src=self.role, t=time.time(), t_phone=msg.t_phone, t_laptop=time.time()))
            elif isinstance(msg, Config):
                if self.on_config:
                    self.on_config(msg)
            elif self.on_message:
                self.on_message(msg)

    async def _heartbeats(self):
        while True:
            self.send(Heartbeat(src=self.role, t=time.time(), **self.heartbeat_fields()))
            await asyncio.sleep(self.heartbeat_s)


class ConsoleSink:
    """--offline: print messages instead of sending them (no relay needed)."""

    connected = True

    def __init__(self, quiet_types=("sources", "heartbeat"), log_path=None, heartbeat_s=2.0):
        self.quiet = set(quiet_types)
        self.on_config = self.on_message = None
        self.heartbeat_fields = lambda: {"status": "ok"}
        self.heartbeat_s = heartbeat_s
        self.sent = self.dropped = 0
        self.seq = 0
        self.log = None
        if log_path:            # same JSON-lines log as the relay client, so offline runs can be reported on
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            self.log = open(log_path, "a", encoding="utf-8", buffering=1)

    queue_depth = max_queue = 0

    def send(self, msg: Message):
        self.sent += 1
        self.seq += 1
        msg.seq = self.seq
        _, stage = prepare(msg)
        if self.log:
            self.log.write(log_line(msg, stage=stage))
        if msg.TYPE not in self.quiet:
            print(json.dumps(msg.to_dict(), ensure_ascii=False))
        return True

    async def run(self):
        while True:              # heartbeats too, so the log shows engine / degraded problems like a real run
            self.send(Heartbeat(src="laptop", t=time.time(), **self.heartbeat_fields()))
            await asyncio.sleep(self.heartbeat_s)

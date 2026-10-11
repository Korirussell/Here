"""The laptop on several relays at once: the local relay (the reflex path) plus mirrors such as Vultr (SPEC §7).

    FanoutClient([local_url, vultr_url], session, token, role="laptop", log_path=...)

Each URL gets its own RelayClient: its own connection, reconnect backoff and bounded outbox, so a dead or slow
mirror never blocks or delays the primary. Every message is numbered and serialized once and goes out
identically (same `seq`, same JSON) on every link. Heartbeats are sent once per interval on all links. A
`clock_ping` is answered only on the link it arrived on (the phone holds one connection), so the other links
see a gap in `seq` there. `config` is taken from whichever link delivers it (the latest wins). Only the first
link writes the JSON-lines log: run_report's latencies stay about the primary.

The app sees the same interface as a single RelayClient (send, run, connected, queue_depth, sent, dropped,
max_queue, on_config, on_message, heartbeat_fields).
"""

import asyncio
import time

from contracts.python.here_contract import Heartbeat, Message

from .relay import RelayClient, prepare, redact


class FanoutClient:
    def __init__(self, urls, session="demo", token="", role="laptop", subscribe=None, log_path=None,
                 heartbeat_s=2.0, outbox=256):
        urls = list(dict.fromkeys(u for u in urls if u))           # order kept, duplicates dropped
        if not urls:
            raise ValueError("FanoutClient needs at least one relay URL")
        self.role = role
        self.heartbeat_s = heartbeat_s
        self.seq = 0
        self.on_config = None
        self.on_message = None
        self.heartbeat_fields = lambda: {"status": "ok"}
        self.links = [RelayClient(u, session, token, role=role, subscribe=subscribe,
                                  log_path=log_path if i == 0 else None, heartbeat_s=None, outbox=outbox)
                      for i, u in enumerate(urls)]
        for link in self.links:
            link.next_seq = self._next_seq                         # pongs on one link share the counter
            link.on_config = lambda c: self.on_config and self.on_config(c)
            link.on_message = lambda m: self.on_message and self.on_message(m)

    def _next_seq(self):
        self.seq += 1
        return self.seq

    @property
    def urls(self):
        return [redact(link.url) for link in self.links]

    @property
    def connected(self):
        """True while any link is up (the dashboard's 'relay' light)."""
        return any(link.connected for link in self.links)

    @property
    def queue_depth(self):
        return max(link.queue_depth for link in self.links)

    @property
    def max_queue(self):
        return max(link.max_queue for link in self.links)

    @property
    def sent(self):
        return self.links[0].sent

    @property
    def dropped(self):
        return self.links[0].dropped

    def status(self):
        """[(redacted url, connected, sent, dropped)] per link, primary first."""
        return [(redact(link.url), link.connected, link.sent, link.dropped) for link in self.links]

    def send(self, msg: Message):
        """Queue on every link (non-blocking). True if at least one link took it."""
        msg.seq = self._next_seq()
        text, stage = prepare(msg)
        took = [link.push(msg, text, dict(stage)) for link in self.links]
        return any(took)

    async def run(self):
        await asyncio.gather(*(link.run() for link in self.links), self._heartbeats())

    async def _heartbeats(self):
        while True:
            if self.connected:
                self.send(Heartbeat(src=self.role, t=time.time(), **self.heartbeat_fields()))
            await asyncio.sleep(self.heartbeat_s)

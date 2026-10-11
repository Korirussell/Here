"""The memory service: a relay client (role `memory`) + the batching writer + the HTTP API, in one process.

Nothing here is on the live path: the relay doesn't wait for us, and if this process dies or the database is
slow, the phone never notices (the relay drops our oldest messages; we drop our oldest rows).
"""

import asyncio
import json
import threading
import time

from websockets.asyncio.client import connect

from contracts.python import here_contract as hc

from . import config
from .api import make_server
from .ask import search_result_hits
from .ingest import Ingestor
from .memory import Memory


def relay_url(base, session, token):
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    parts = urlsplit(base)
    q = dict(parse_qsl(parts.query))
    q.update({"session": session, "role": "memory"})
    if token:
        q["token"] = token
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/ws", urlencode(q), ""))


class RelayLink:
    """Minimal relay client: hello, receive, reconnect with backoff (README §12.1). Sends only search_result."""

    def __init__(self, memory, ingestor, url, session, token, log=print):
        self.memory = memory
        self.ing = ingestor
        self.url = relay_url(url, session, token)
        self.session = session
        self.log = log
        self.ws = None
        self.seq = 0
        self.received = 0

    async def run(self):
        delay = config.RECONNECT_MIN_S
        while True:
            try:
                async with connect(self.url, open_timeout=5, max_size=256 * 1024) as ws:
                    self.ws = ws
                    delay = config.RECONNECT_MIN_S
                    subscribe = hc.DEFAULT_SUBSCRIBE["memory"] + config.EXTRA_SUBSCRIBE
                    await ws.send(hc.Hello(src="memory", t=time.time(), role="memory", subscribe=subscribe,
                                           app_version="memory-0.1").to_json())
                    self.log(f"[memory] relay connected (session {self.session!r})")
                    async for raw in ws:
                        if isinstance(raw, bytes):
                            continue
                        try:
                            msg = json.loads(raw)
                        except ValueError:
                            continue
                        self.received += 1
                        if msg.get("type") == "search_request":
                            asyncio.create_task(self.answer(msg))
                        else:
                            self.ing.accept(msg)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.log(f"[memory] relay link down ({type(e).__name__}); retry in {delay:.1f} s")
            self.ws = None
            await asyncio.sleep(delay)
            delay = min(config.RECONNECT_MAX_S, delay * 2)

    async def answer(self, msg):
        try:
            hits = await asyncio.to_thread(search_result_hits, self.memory, msg.get("text", ""),
                                           int(msg.get("limit") or 5))
            self.seq += 1
            res = hc.SearchResult(src="memory", t=time.time(), seq=self.seq, query_id=msg["query_id"],
                                  results=[hc.SearchHit(**h) for h in hits])
            if self.ws is not None:
                await self.ws.send(res.to_json())
        except Exception as e:
            self.log(f"[memory] search_request failed: {type(e).__name__}: {e}")


async def flush_loop(ingestor):
    while True:
        await asyncio.sleep(config.FLUSH_EVERY_S)
        if ingestor.queue:
            await asyncio.to_thread(ingestor.flush_all)


async def purge_loop(memory):
    while True:
        try:
            n = await asyncio.to_thread(memory.purge)
            if n:
                memory.log(f"[memory] retention: deleted {n} live events")
        except Exception as e:
            memory.log(f"[memory] purge failed: {type(e).__name__}")
        await asyncio.sleep(config.PURGE_EVERY_S)


async def stats_loop(memory, ingestor, link, every_s=30.0):
    while True:
        await asyncio.sleep(every_s)
        s = ingestor.stats
        memory.log(f"[memory] db {memory.db_kind} | rx {link.received if link else 0} | written {s['written']} "
                   f"skipped {s['skipped']} invalid {s['invalid']} dropped {s['dropped']} errors {s['errors']}")


async def run(args):
    memory = Memory()
    if config.SEED_ON_START and not args.no_seed:
        await asyncio.to_thread(memory.seed)
    server = make_server(memory, args.host, args.port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    memory.log(f"[memory] API on http://{args.host}:{server.server_address[1]}/api/health (db {memory.db_kind})")
    threading.Thread(target=server.asker.warm, daemon=True).start()
    from .relevance import scorer
    if scorer() is not None:                         # HERE_REPLAY_MODEL=1: score the stored texts in the background
        texts = {r["text"] for s in memory.segments() for r in memory.segment_rows(s, ["utterance"]) if r["text"]}
        n = scorer().request(sorted(texts))
        memory.log(f"[memory] relevance model: {n} texts queued ({scorer().status()['scored']} cached)")
    ingestor = Ingestor(memory.store, origin="live")
    tasks = [asyncio.create_task(flush_loop(ingestor)), asyncio.create_task(purge_loop(memory))]
    link = None
    if not args.no_relay:
        link = RelayLink(memory, ingestor, args.relay, args.session, config.env("HERE_RELAY_TOKEN"))
        tasks.append(asyncio.create_task(link.run()))
    tasks.append(asyncio.create_task(stats_loop(memory, ingestor, link)))
    try:
        await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.to_thread(ingestor.flush_all)
        server.shutdown()

"""The laptop on the local relay plus a cloud mirror (laptop/client/fanout.py): the mirror being down, dying or
hanging never delays the local path, and both relays carry the same numbered stream."""

import asyncio
import json
import socket
import time

from websockets.asyncio.client import connect

from contracts.python import here_contract as hc
from laptop.client.fanout import FanoutClient
from tests.test_resilience import TOKEN, free_port, start, stop, wait_for


async def phone(port):
    ws = await connect(f"ws://127.0.0.1:{port}/ws?session=demo&role=phone&token={TOKEN}")
    await ws.send(hc.Hello(src="phone", t=time.time(), role="phone", subscribe=["speech_start"]).to_json())
    return ws


async def next_of(ws, msg_type, timeout=5.0):
    while True:
        got = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if got["type"] == msg_type:
            return got


def speech(i):
    return hc.SpeechStart(src="laptop", t=time.time(), utt_id=f"u{i}", bearing_deg=10.0 * i, source_id=0,
                          confidence=0.8)


def test_both_relays_get_the_same_stream_and_one_dying_leaves_the_other_untouched():
    async def main():
        pa, pb = free_port(), free_port()
        ra, rb = await start(pa), await start(pb)
        laptop = FanoutClient([f"ws://127.0.0.1:{pa}/ws", f"ws://127.0.0.1:{pb}/ws"], "demo", TOKEN)
        runner = asyncio.create_task(laptop.run())
        assert await wait_for(lambda: all(link.connected for link in laptop.links))
        a, b = await phone(pa), await phone(pb)
        await asyncio.sleep(0.2)                              # hellos registered

        laptop.send(speech(1))
        got_a, got_b = await next_of(a, "speech_start"), await next_of(b, "speech_start")
        assert got_a == got_b and got_a["utt_id"] == "u1"     # identical, same seq

        await b.close()
        await stop(rb)                                        # the mirror goes away mid-stream
        assert await wait_for(lambda: not laptop.links[1].connected)
        assert laptop.connected
        for i in range(2, 12):
            t0 = time.perf_counter()
            assert laptop.send(speech(i))
            got = await next_of(a, "speech_start")
            assert got["utt_id"] == f"u{i}"
            assert time.perf_counter() - t0 < 0.5             # local delivery isn't held up by the dead link
        await a.close()
        runner.cancel()
        await stop(ra)
    asyncio.run(main())


def test_an_unreachable_or_hanging_mirror_doesnt_delay_the_primary():
    async def main():
        pa = free_port()
        ra = await start(pa)
        hang = socket.socket()                                # accepts TCP, never answers the websocket handshake
        hang.bind(("127.0.0.1", 0))
        hang.listen()
        dead = free_port()                                    # nothing listening: connection refused
        laptop = FanoutClient([f"ws://127.0.0.1:{pa}/ws", f"ws://127.0.0.1:{hang.getsockname()[1]}/ws",
                               f"ws://127.0.0.1:{dead}/ws"], "demo", TOKEN)
        t0 = time.perf_counter()
        runner = asyncio.create_task(laptop.run())
        assert await wait_for(lambda: laptop.links[0].connected, timeout=2)
        assert time.perf_counter() - t0 < 1.5                 # not waiting on the 5 s handshake timeout
        a = await phone(pa)
        await asyncio.sleep(0.2)
        laptop.send(speech(1))
        assert (await next_of(a, "speech_start", timeout=1))["utt_id"] == "u1"
        assert not laptop.links[1].connected and not laptop.links[2].connected
        await a.close()
        runner.cancel()
        hang.close()
        await stop(ra)
    asyncio.run(main())


def test_seq_is_assigned_once_and_heartbeats_go_out_on_every_link():
    laptop = FanoutClient(["ws://127.0.0.1:1/ws", "ws://127.0.0.1:2/ws", "ws://127.0.0.1:1/ws"], "demo", TOKEN)
    assert len(laptop.links) == 2                             # duplicate URL dropped
    for link in laptop.links:                                 # pretend both are up
        link.ws = object()
    m = speech(1)
    assert laptop.send(m)
    assert m.seq == 1
    assert [json.loads(link.outbox[0][0])["seq"] for link in laptop.links] == [1, 1]
    assert laptop.links[0].next_seq() == 2                    # a pong on one link takes the next shared number
    assert laptop.send(speech(2)) and json.loads(laptop.links[1].outbox[-1][0])["seq"] == 3
    laptop.links[1].ws = None                                 # mirror down: primary still takes it
    assert laptop.send(speech(3)) and laptop.links[1].dropped == 1

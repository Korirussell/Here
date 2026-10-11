"""Relay behaviour with real sockets: auth, routing, sessions, config replay, health, coalescing."""

import asyncio
import contextlib
import json
import time
import urllib.error
import urllib.request

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from relay.relay import Client, serve_relay

TOKEN = "test-token"


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 20))


@contextlib.asynccontextmanager
async def relay_server(token=TOKEN):
    """A relay on a free port; shut down (and awaited) when the block exits."""
    ready = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(serve_relay("127.0.0.1", 0, token, log_path=None, ready=ready))
    relay, port = await ready
    try:
        yield relay, port
    finally:
        task.cancel()
        await asyncio.wait([task], timeout=5)


def url(port, role, session="demo", token=TOKEN):
    return f"ws://127.0.0.1:{port}/ws?session={session}&role={role}&token={token}"


def msg(type_, src="laptop", **fields):
    return json.dumps({"type": type_, "v": 3, "src": src, "t": time.time(), **fields})


async def hello(ws, role, subscribe=None):
    await ws.send(msg("hello", src=role, role=role, subscribe=subscribe or []))


async def recv_types(ws, n, timeout=2.0):
    out = []
    for _ in range(n):
        out.append(json.loads(await asyncio.wait_for(ws.recv(), timeout)))
    return out


async def nothing_arrives(ws, timeout=0.3):
    try:
        await asyncio.wait_for(ws.recv(), timeout)
        return False
    except asyncio.TimeoutError:
        return True


def test_bad_token_and_bad_role_rejected():
    async def main():
        async with relay_server() as (_, port):
            with pytest.raises(InvalidStatus) as e:
                async with connect(url(port, "phone", token="wrong")):
                    pass
            assert e.value.response.status_code == 401
            with pytest.raises(InvalidStatus) as e:
                async with connect(url(port, "hacker")):
                    pass
            assert e.value.response.status_code == 400
    run(main())


def test_routes_by_default_subscriptions_and_isolates_sessions():
    async def main():
        async with relay_server() as (_, port):
            async with connect(url(port, "laptop")) as laptop, connect(url(port, "phone")) as phone, \
                    connect(url(port, "phone", session="other")) as other, connect(url(port, "tool")) as tool:
                for ws, role in ((laptop, "laptop"), (phone, "phone"), (other, "phone"), (tool, "tool")):
                    await hello(ws, role)
                await asyncio.sleep(0.1)
                await laptop.send(msg("utterance", utt_id="u-1", text="hi"))
                got = (await recv_types(phone, 1))[0]
                assert got["type"] == "utterance" and got["session"] == "demo"
                assert (await recv_types(tool, 1))[0]["type"] == "utterance"
                assert await nothing_arrives(other)                 # different session
                assert await nothing_arrives(laptop)                # never echoed back
                await phone.send(msg("scene", src="phone", heading_deg=0, people=[]))
                assert await nothing_arrives(laptop)                # laptop doesn't subscribe to scene
                await phone.send(msg("clock_ping", src="phone", t_phone=1.0))
                assert (await recv_types(laptop, 1))[0]["type"] == "clock_ping"
    run(main())


def test_explicit_subscribe_overrides_defaults():
    async def main():
        async with relay_server() as (_, port):
            async with connect(url(port, "laptop")) as laptop, connect(url(port, "phone")) as phone:
                await hello(laptop, "laptop")
                await hello(phone, "phone", subscribe=["alert"])
                await asyncio.sleep(0.1)
                await laptop.send(msg("utterance", utt_id="u-1"))
                await laptop.send(msg("alert", alert_id="a-1"))
                assert (await recv_types(phone, 1))[0]["type"] == "alert"
    run(main())


def test_latest_config_is_replayed_to_new_clients():
    async def main():
        async with relay_server() as (_, port):
            async with connect(url(port, "phone")) as phone:
                await hello(phone, "phone")
                await phone.send(msg("config", src="phone", user_name="Old"))
                await phone.send(msg("config", src="phone", user_name="Kori"))
                await asyncio.sleep(0.1)
                async with connect(url(port, "laptop")) as laptop:
                    await hello(laptop, "laptop")
                    got = (await recv_types(laptop, 1))[0]
                    assert got["type"] == "config" and got["user_name"] == "Kori"
                async with connect(url(port, "laptop", session="other")) as laptop2:
                    await hello(laptop2, "laptop")
                    assert await nothing_arrives(laptop2)           # config is per session
    run(main())


def test_health_endpoint_lists_clients():
    async def main():
        async with relay_server() as (_, port):
            async with connect(url(port, "phone")) as phone:
                await hello(phone, "phone")
                await asyncio.sleep(0.1)
                body = await asyncio.to_thread(
                    lambda: json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3).read()))
                assert body["ok"] is True and body["sessions"] == {"demo": ["phone"]}
                assert body["clients"][0]["role"] == "phone"
                with pytest.raises(urllib.error.HTTPError):
                    await asyncio.to_thread(lambda: urllib.request.urlopen(f"http://127.0.0.1:{port}/nope", timeout=3))
    run(main())


def test_no_token_configured_means_open_relay():
    async def main():
        async with relay_server(token="") as (_, port):
            async with connect(f"ws://127.0.0.1:{port}/ws?session=demo&role=phone") as ws:
                await hello(ws, "phone")
    run(main())


def test_slow_client_keeps_only_newest_positional_messages():
    c = Client(ws=None, session="demo", role="phone")
    for i in range(50):
        c.enqueue("sources", "laptop", f"s{i}")
    c.enqueue("utterance", "laptop", "u1")
    c.enqueue("sources", "laptop", "s-final")
    assert list(c.latest.values()) == ["s-final"] and list(c.events) == ["u1"]
    assert c.dropped == 50
    for i in range(300):
        c.enqueue("alert", "laptop", f"a{i}")
    assert len(c.events) == 256 and c.events[-1] == "a299"

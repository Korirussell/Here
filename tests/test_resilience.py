"""The relay restarts mid-session (deploy, crash, Wi-Fi blip): the laptop reconnects by itself and
messages flow again; the phone's config is replayed to the laptop after it rejoins."""

import asyncio
import json
import socket
import time

from websockets.asyncio.client import connect

from contracts.python import here_contract as hc
from laptop.client.relay import RelayClient
from relay.relay import serve_relay

TOKEN = "r"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def start(port):
    ready = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(serve_relay("127.0.0.1", port, TOKEN, log_path=None, ready=ready))
    await ready
    return task


async def stop(task):
    task.cancel()
    await asyncio.wait([task], timeout=5)


async def wait_for(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


def test_laptop_reconnects_after_relay_restart_and_gets_config_again():
    async def main():
        port = free_port()
        url = f"ws://127.0.0.1:{port}/ws"
        relay = await start(port)
        configs = []
        laptop = RelayClient(url, "demo", TOKEN, role="laptop")
        laptop.on_config = configs.append
        runner = asyncio.create_task(laptop.run())
        assert await wait_for(lambda: laptop.connected)

        async with connect(f"{url}?session=demo&role=phone&token={TOKEN}") as phone:
            await phone.send(hc.Hello(src="phone", t=time.time(), role="phone", subscribe=[]).to_json())
            await phone.send(hc.Config(src="phone", t=time.time(), user_name="Kori").to_json())
            assert await wait_for(lambda: len(configs) == 1)

        await stop(relay)                                   # relay goes away
        assert await wait_for(lambda: not laptop.connected)
        assert not laptop.send(hc.Heartbeat(src="laptop", t=time.time(), status="ok"))   # dropped, not queued

        relay = await start(port)                           # comes back on the same port
        assert await wait_for(lambda: laptop.connected, timeout=12)

        async with connect(f"{url}?session=demo&role=phone&token={TOKEN}") as phone:
            await phone.send(hc.Hello(src="phone", t=time.time(), role="phone", subscribe=[]).to_json())
            await phone.send(hc.Config(src="phone", t=time.time(), user_name="Kori", focus_mode=True).to_json())
            assert await wait_for(lambda: len(configs) == 2)
            laptop.send(hc.SpeechStart(src="laptop", t=time.time(), utt_id="u1", bearing_deg=35.0, source_id=0,
                                       confidence=0.8))
            got = json.loads(await asyncio.wait_for(phone.recv(), 5))
            while got["type"] != "speech_start":
                got = json.loads(await asyncio.wait_for(phone.recv(), 5))
            assert got["utt_id"] == "u1"
        assert configs[-1].focus_mode is True
        runner.cancel()
        await stop(relay)

    asyncio.run(asyncio.wait_for(main(), 60))

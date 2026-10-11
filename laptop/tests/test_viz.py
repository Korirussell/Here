"""The 3D sound-field view (laptop/viz.py) without hardware: the heat map points at the sound, ODAS vectors land in
the world frame, and the page + websocket serve from a fake app."""

import asyncio
import json
import socket
import urllib.request
from types import SimpleNamespace

import numpy as np
import pytest

from contracts.python import here_contract as hc
from laptop.audio import config as C
from laptop.audio.base import DirectionHistory
from laptop.geometry import angdiff
from laptop.tests.test_localize import FS, MICS, simulate
from laptop.viz import SoundField, Viz, unit_to_world


@pytest.fixture(autouse=True)
def mapping(monkeypatch):
    monkeypatch.setattr(C, "AEC_TO_DEVICE_OFFSET_DEG", 0.0)
    monkeypatch.setattr(C, "AEC_TO_DEVICE_SIGN", 1)


def noise(seconds, seed=1):
    return np.random.default_rng(seed).standard_normal(int(seconds * FS))


def test_heat_map_peaks_at_the_sound_in_the_world_frame():
    # array offset 10 deg clockwise: a sound at device 100 is at world +90
    f = SoundField(MICS, offset=10.0, ccw=False, order=[0, 1, 2, 3])
    for k in range(20):                                   # quiet room first (the noise floor)
        f.feed((noise(0.05, k) * 30).astype(np.int16)[:, None].repeat(4, 1))
        f.compute()
    f.feed((simulate(noise(0.3) * 3000, 100.0) ).astype(np.int16))
    grid, activity, over = f.compute()
    row = np.asarray(grid)[0]                             # elevation 0
    assert abs(angdiff(float(f.world_az[int(np.argmax(row))]), 90.0)) <= 6
    assert activity > 0.5 and over > 10


def test_odas_unit_vectors_to_world_bearing_and_elevation():
    az, el = unit_to_world(0.0, 1.0, 0.0, offset=0.0, ccw=False)
    assert az == 90.0 and el == 0.0
    az, el = unit_to_world(0.5, 0.0, 0.866, offset=10.0, ccw=False)
    assert az == -10.0 and abs(el - 60.0) < 0.1


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fake_app():
    history = DirectionHistory()
    history.add(1e12, [hc.Source(id=0, bearing_deg=35.0, energy=0.5, speech=True, confidence=0.8)])
    bridge = SimpleNamespace(tracks=(1e12, {"src": [{"id": 4, "x": 0.0, "y": 1.0, "z": 0.0, "activity": 0.9},
                                                     {"id": 0, "x": 0.0, "y": 0.0, "z": 0.0, "activity": 0.0}]}),
                             pots=(1e12, {"src": [{"x": -1.0, "y": 0.0, "z": 0.0, "E": 0.4}]}))
    directions = SimpleNamespace(bridge=bridge, offset=0.0, ccw=False)
    return SimpleNamespace(directions=directions, audio=SimpleNamespace(), history=history, raw_listeners=[])


def test_snapshot_carries_sources_odas_tracks_and_pots():
    app = fake_app()
    v = Viz(app, {"az_step_deg": 10.0})
    assert app.raw_listeners == [v.feed_raw]
    snap = v.snapshot(None, 0.0, 0.0, now=1e12 + 0.1)
    assert snap["sources"] == [{"id": 0, "az": 35.0, "speech": True, "c": 0.8}]
    assert snap["tracks"] == [{"id": 4, "az": 90.0, "el": 0.0, "a": 0.9}]           # empty ODAS slot dropped
    assert snap["pots"] == [{"az": 180.0, "el": 0.0, "e": 0.4}]
    assert snap["map"] is None and v.hello()["odas"] is True


def test_page_and_websocket_serve():
    from websockets.asyncio.client import connect

    port = free_port()
    v = Viz(fake_app(), {"port": port, "open_browser": False, "rate_hz": 50})

    async def go():
        task = asyncio.create_task(v.run())
        await asyncio.sleep(0.3)
        html = await asyncio.to_thread(lambda: urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode())
        async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
            hello = json.loads(await ws.recv())
            frame = json.loads(await asyncio.wait_for(ws.recv(), 2.0))
        task.cancel()
        return html, hello, frame

    html, hello, frame = asyncio.run(go())
    assert "<title>HERE Sound Field</title>" in html and "three@0.160.0" in html
    assert hello["type"] == "hello" and len(hello["grid"]["el"]) == 6 and len(hello["leds_az"]) == 12
    assert frame["type"] == "frame" and frame["tracks"][0]["id"] == 4

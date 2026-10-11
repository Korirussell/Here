"""ODAS track B without ODAS: config generation, frame parsing, time mapping, beam steering, and the bridge
against a fake odaslive over real sockets."""

import asyncio
import json

import numpy as np
import pytest

from laptop.audio import config as C
from laptop.audio.odas import BeamSteer, OdasBridge, OdasSource, SampleClock, frame_to_sources, slots_of
from laptop.audio.odas_cfg import make_cfg
from laptop.audio.odas_wsl import parse_frames, win_to_wsl
from laptop.geometry import angdiff
from contracts.python.here_contract import Source


@pytest.fixture(autouse=True)
def mapping(monkeypatch):
    monkeypatch.setattr(C, "AEC_TO_DEVICE_OFFSET_DEG", 0.0)
    monkeypatch.setattr(C, "AEC_TO_DEVICE_SIGN", 1)


def frame(ts, *srcs):
    """srcs: (id, azimuth deg in the AEC frame, activity); padded to 4 slots like ODAS."""
    out = [{"id": i, "tag": "dynamic", "x": float(np.cos(np.radians(a))), "y": float(np.sin(np.radians(a))),
            "z": 0.0, "activity": act} for i, a, act in srcs]
    out += [{"id": 0, "tag": "", "x": 0.0, "y": 0.0, "z": 0.0, "activity": 0.0}] * (4 - len(out))
    return {"timeStamp": ts, "src": out}


def test_cfg_has_geometry_ports_and_tracks():
    cfg = make_cfg(("socket", "127.0.0.1", 9002), ("file", "/tmp/t.json"),
                   geometry=[(0.0333, -0.0333, 0), (0.0333, 0.0333, 0), (-0.0333, 0.0333, 0), (-0.0333, -0.0333, 0)],
                   overrides={"max_sources": 3, "hop": 256})
    assert 'type = "socket"; ip = "127.0.0.1"; port = 9002;' in cfg
    assert 'path = "/tmp/t.json"' in cfg
    assert "mu = ( +0.0333, -0.0333, +0.0000 )" in cfg and "nChannels = 4" in cfg and "map: (1, 2, 3, 4)" in cfg
    assert "N_inactive = ( 250, 250, 250 )" in cfg and "hopSize = 256" in cfg


def test_parse_frames_back_to_back_and_cut_short():
    text = json.dumps(frame(1, (5, 90, 0.9))) + "\n" + json.dumps(frame(2)) + "\n{\"timeStamp\": 3, \"sr"
    frames = parse_frames(text)
    assert [f["timeStamp"] for f in frames] == [1, 2]


def test_win_to_wsl():
    assert win_to_wsl("C:/Users/x/shh/logs").endswith("/mnt/c/Users/x/shh/logs")


def test_frame_to_sources_world_bearings_and_slots():
    f = frame(10, (7, 90.0, 0.9), (3, 180.0, 0.05), (9, 0.0, 0.4))
    srcs = frame_to_sources(f, offset=0.0, ccw=False, min_activity=0.1, speech_activity=0.5)
    assert [s.id for s in srcs] == [7, 9]                       # weak track dropped, strongest first
    assert abs(angdiff(srcs[0].bearing_deg, 90.0)) < 0.5 and srcs[0].speech and not srcs[1].speech
    assert slots_of(f) == {7: 0, 3: 1, 9: 2}


def test_sample_clock_maps_sample_index_to_capture_time():
    c = SampleClock()
    c.add(100.0, 512)
    c.add(100.5, 512)                                           # a gap in capture
    assert c.time_of(0) == 100.0
    assert abs(c.time_of(511) - (100.0 + 511 / 16000)) < 1e-9
    assert abs(c.time_of(600) - (100.5 + 88 / 16000)) < 1e-9


def S(i, b, c=0.9):
    return Source(id=i, bearing_deg=b, energy=c, speech=True, confidence=c)


def test_beam_steer_assigns_holds_and_moves():
    st = BeamSteer([0.0, 180.0], move_deg=12, min_interval_s=0.5, hold_s=1.5)
    assert st.update(0.0, [S(1, 5.0), S(2, 170.0)]) is None     # both within 12 deg of the initial beams
    assert st.track == [1, 2]
    assert st.update(1.0, [S(1, 40.0), S(2, 170.0)]) == [40.0, 180.0]
    assert st.update(1.2, [S(1, 70.0)]) is None                 # rate limit
    assert st.stream_for(S(2, 0)) == 1 and st.stream_for(S(5, 0)) is None
    # a new talker can't steal beam 1 while track 2 was seen within hold_s ...
    st.update(2.0, [S(1, 70.0), S(3, -90.0)])
    assert st.track == [1, 2]
    # ... but takes it once track 2 has been gone longer
    assert st.update(4.0, [S(1, 70.0), S(3, -90.0)]) == [70.0, -90.0] and st.track == [1, 3]


def test_bridge_with_a_fake_odaslive(monkeypatch):
    """Raw audio out (geometry order), tracked frames in, times from the sample clock, audio per slot."""
    async def scenario():
        over = {"host": "127.0.0.1", "raw_port": 0, "port": 0, "pots_port": 0, "separated_port": 0,
                "postfiltered_port": 0, "hop": 128, "max_sources": 4}
        got_frames, got_audio = [], []
        br = OdasBridge(on_tracks=lambda t, f: got_frames.append((t, f)), on_audio=lambda t, p: got_audio.append((t, p)),
                        audio_kind="separated", launch=False, overrides=over)
        ports = {}
        real_start = asyncio.start_server

        async def start_server(cb, host, port):
            srv = await real_start(cb, host, 0)
            ports[len(ports)] = srv.sockets[0].getsockname()[1]
            return srv
        monkeypatch.setattr(asyncio, "start_server", start_server)
        task = asyncio.create_task(br.run())
        while len(ports) < 5:
            await asyncio.sleep(0.01)
        raw_r, raw_w = await asyncio.open_connection("127.0.0.1", ports[0])
        trk_r, trk_w = await asyncio.open_connection("127.0.0.1", ports[1])
        sep_r, sep_w = await asyncio.open_connection("127.0.0.1", ports[3])
        await br.ready.wait()
        block = np.arange(256 * 4, dtype=np.int16).reshape(256, 4)   # USB order
        monkeypatch.setattr(C, "RAW_MIC_ORDER", [1, 0, 3, 2], raising=False)
        br.feed(50.0, block)
        data = await raw_r.readexactly(256 * 4 * 2)
        sent = np.frombuffer(data, "<i2").reshape(256, 4)
        assert np.array_equal(sent, block[:, [1, 0, 3, 2]])
        trk_w.write((json.dumps(frame(2, (4, 90.0, 0.8))) + "\n").encode())
        sep_w.write(np.ones((128, 4), "<i2").tobytes())
        await trk_w.drain()
        await sep_w.drain()
        for _ in range(100):
            if got_frames and got_audio:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for w in (raw_w, trk_w, sep_w):
            w.close()
        return got_frames, got_audio

    frames, audio = asyncio.run(scenario())
    t, f = frames[0]
    assert abs(t - (50.0 + 255 / 16000)) < 1e-6                   # frame 2 ends at sample 255
    assert f["src"][0]["id"] == 4
    assert audio[0][1].shape == (128, 4) and abs(audio[0][0] - 50.0) < 1e-6


def test_odas_source_emits_throttled_sources_and_routes_slots():
    src = OdasSource(offset=0.0, ccw=False, audio="separated", launch=False)
    got = []
    src.on_sources = lambda t, s: got.append((t, s))
    src._on_tracks(10.00, frame(1, (4, 90.0, 0.9), (6, 270.0, 0.7)))
    src._on_tracks(10.01, frame(2, (4, 91.0, 0.9)))             # within the doa_rate_hz period: not sent
    src._on_tracks(10.10, frame(3, (6, 270.0, 0.7), (4, 92.0, 0.9)))
    assert len(got) == 2 and [s.id for s in got[0][1]] == [4, 6]
    assert src.stream_for(S(6, 0)) == 0 and src.stream_for(S(4, 0)) == 1 and src.streams == 4


def test_beam_steer_never_doubles_up_on_one_direction():
    st = BeamSteer([0.0, 180.0], move_deg=12, min_interval_s=0.0, hold_s=1.5, min_sep_deg=45)
    st.update(0.0, [S(1, 170.0)])                              # takes beam 1
    st.update(3.0, [S(1, 165.0), S(2, 150.0)])                # second track 15 deg away: not given beam 0
    assert st.track == [None, 1] and st.az[0] == 0.0
    st.update(4.0, [S(1, 160.0), S(3, 20.0)])                 # a real second talker in front gets beam 0
    assert st.track == [3, 1] and abs(angdiff(st.az[0], 20.0)) < 0.5
    st.update(5.0, [S(1, 160.0), S(3, 140.0)])                # beam 0's track drifts onto beam 1: stays put
    assert abs(angdiff(st.az[0], 20.0)) < 0.5

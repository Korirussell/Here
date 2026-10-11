"""6-channel firmware plumbing without hardware: raw.wav recording, raw replay, mic order, locate on replay."""

import asyncio
import json
import math
import wave

import numpy as np
import pytest

from contracts.python.here_contract import Source
from laptop.app import Recorder
from laptop.audio.localize import XVF3800_GEOMETRY, RawLocator, in_geometry_order, mic_order
from laptop.audio.mock import FixturePlayer
from laptop.geometry import angdiff

FS = 16000


def plane_wave(source, az_deg, geometry=XVF3800_GEOMETRY, snr_db=25.0, seed=0):
    """Far-field source at az_deg (mic-geometry frame) on the given mics, columns in geometry order."""
    rng = np.random.default_rng(seed)
    n = len(source)
    f = np.fft.rfftfreq(n, 1 / FS)
    S = np.fft.rfft(source)
    u = np.array([math.cos(math.radians(az_deg)), math.sin(math.radians(az_deg))])
    x = np.stack([np.fft.irfft(S * np.exp(2j * np.pi * f * np.dot(u, m[:2]) / 343.0), n) for m in geometry], axis=1)
    return x + rng.standard_normal(x.shape) * np.std(x) * 10 ** (-snr_db / 20)


def write_wav(path, x):
    x = np.asarray(x, np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1 if x.ndim == 1 else x.shape[1])
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes(x.tobytes())


def test_recorder_writes_aligned_raw(tmp_path):
    rec = Recorder(tmp_path / "f", {"name": "f"}, channels=1, raw_channels=4, raw_gain=2.0)
    t0 = 1000.0
    for k in range(5):
        t = t0 + k * 512 / FS
        raw = np.full((512, 4), k + 1, np.int16)
        rec.raw(t, raw)
        rec.audio(t, np.zeros(512, np.int16))
    rec.raw(t0 + 10 * 512 / FS, np.full((512, 4), 9, np.int16))      # a dropped stretch -> zero fill
    rec.close()
    meta = json.loads((tmp_path / "f" / "meta.json").read_text())
    assert meta["raw"] == {"file": "raw.wav", "channels": 4, "gain": 2.0}
    with wave.open(str(tmp_path / "f" / "raw.wav")) as w:
        assert w.getnchannels() == 4
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).reshape(-1, 4)
    assert len(x) == 11 * 512
    assert x[0, 0] == 2 and x[512 * 4, 3] == 10           # gain applied
    assert np.all(x[5 * 512:10 * 512] == 0) and x[-1, 0] == 18


def test_recorder_without_raw_has_no_raw_file(tmp_path):
    rec = Recorder(tmp_path / "g", {"name": "g"})
    rec.raw(0.0, np.zeros((512, 4), np.int16))                # ignored
    rec.close()
    assert not (tmp_path / "g" / "raw.wav").exists()


def test_mic_order_is_a_permutation():
    assert mic_order([2, 3, 0, 1]) == [2, 3, 0, 1]
    with pytest.raises(ValueError):
        mic_order([0, 0, 1, 2])
    raw = np.arange(8).reshape(2, 4)
    assert in_geometry_order(raw, [3, 2, 1, 0]).tolist() == [[3, 2, 1, 0], [7, 6, 5, 4]]


@pytest.mark.parametrize("order", [[0, 1, 2, 3], [1, 2, 3, 0], [3, 2, 1, 0]])
def test_raw_locator_uses_the_mic_order(order):
    src = np.random.default_rng(3).standard_normal(FS)
    geo = plane_wave(src, 120.0) * 3000                       # columns in geometry order
    raw = np.zeros_like(geo)
    raw[:, order] = geo                                       # what USB delivers if geometry mic i is raw col order[i]
    loc = RawLocator(XVF3800_GEOMETRY, FS, seconds=5, order=order)
    loc.add(10.0, raw.astype(np.int16))
    bearing, device, conf = loc.locate(10.0, 11.0, offset=0.0, ccw=False)
    from laptop.audio.localize import aec_to_device
    assert abs(angdiff(device, aec_to_device(120.0))) <= 3
    assert conf > 0.5


def make_raw_fixture(folder, az_deg, gain=1.0):
    folder.mkdir()
    rng = np.random.default_rng(5)
    n = 3 * FS
    burst = np.zeros(n)
    burst[FS:2 * FS] = rng.standard_normal(FS)
    geo = plane_wave(burst, az_deg, snr_db=30) * 2000
    write_wav(folder / "audio.wav", (burst * 2000).astype(np.int16))
    write_wav(folder / "raw.wav", np.clip(geo * gain, -32768, 32767).astype(np.int16))
    (folder / "sources.jsonl").write_text(json.dumps({"t": 0.0, "sources": [
        Source(id=0, bearing_deg=0.0, energy=0.1, speech=False, confidence=0.0).__dict__]}) + "\n")
    (folder / "meta.json").write_text(json.dumps({"name": folder.name, "array_offset_deg": 0.0,
                                                  "array_clockwise": True, "raw": {"gain": gain},
                                                  "mic_geometry": [list(m) for m in XVF3800_GEOMETRY]}))


def test_fixture_player_replays_raw_and_locates(tmp_path, monkeypatch):
    from laptop.audio import config as C
    monkeypatch.setattr(C, "RAW_MIC_ORDER", [0, 1, 2, 3], raising=False)
    make_raw_fixture(tmp_path / "raw_fix", 90.0, gain=2.0)
    player = FixturePlayer(tmp_path / "raw_fix", speed=20.0)
    assert player.raw is not None and player.srp is not None
    got = []
    player.on_raw = lambda t, b: got.append(len(b))
    asyncio.run(player.run())
    assert sum(got) == 3 * FS
    t0 = player.t0
    found = player.locate(t0 + 1.0 / 20, t0 + 2.0 / 20 + 0.5, label="doorbell")
    # mic-geometry 90 deg -> device (aec offset/sign from config) -> world with offset 0, clockwise
    from laptop.audio.localize import aec_to_device
    from laptop.audio.xvf3800 import to_world
    assert found is not None
    assert abs(angdiff(found[0], to_world(aec_to_device(90.0), 0.0, False))) <= 4
    assert player.locate(t0 + 1.0 / 20, t0 + 2.0 / 20, label="speech") is None


def test_fixture_without_raw_has_no_locate(tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()
    write_wav(folder / "audio.wav", np.zeros(FS, np.int16))
    player = FixturePlayer(folder)
    assert player.raw is None and player.srp is None


def test_world_to_aec_inverts_to_world():
    from laptop.audio.localize import aec_to_device, world_to_aec
    from laptop.audio.xvf3800 import to_world
    for w in (0.0, 90.0, 180.0, -45.0):
        for ccw in (False, True):
            assert abs(angdiff(to_world(aec_to_device(world_to_aec(w, 356.4, ccw)), 356.4, ccw), w)) < 0.2


def test_fit_order_finds_the_wiring():
    from laptop.audio.localize import fit_order
    wiring = [2, 3, 0, 1]                                     # geometry mic i arrives on raw column wiring[i]
    rng = np.random.default_rng(9)
    src = rng.standard_normal(2 * FS)
    geo = plane_wave(src, 60.0, snr_db=20) * 2000            # 60 deg: off every mirror axis, so it's unique
    raw = np.zeros_like(geo)
    raw[:, wiring] = geo
    rows = fit_order(raw, [(0, 2 * FS)], 60.0, XVF3800_GEOMETRY)
    assert rows[0]["order"] == wiring and rows[0]["dihedral"] and rows[0]["median_err"] <= 3
    # every other rotation/mirror of the array is far off (scrambled orders can come close but score lower)
    assert all(r["median_err"] > 20 for r in rows[1:] if r["dihedral"])
    assert all(r["coherence"] < rows[0]["coherence"] for r in rows[1:] if not r["dihedral"])

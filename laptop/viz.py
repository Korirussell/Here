"""3D sound-field view in the browser: python -m laptop --real --viz (any mode with raw mics; --odas adds ODAS).

A local page (http://127.0.0.1:8770 by default, laptop only: nothing leaves the machine, README §6) showing, in
the world frame with the wearer looking "into" the screen:
  - a hemisphere heat map: our SRP-PHAT power over azimuth x elevation on the raw mics (a planar array can't tell
    up from down and resolves elevation only coarsely, so the sky is a guide, not a measurement)
  - ODAS's potential sources (energy points) and tracked sources with trails, when --odas runs
  - our SRP tracks (the LED ring's main/second marker candidates) and the contract `sources` the phone gets
  - the array with its 12 LEDs as currently lit
The page (laptop/viz.html) loads three.js from cdn.jsdelivr.net, so the browser needs internet once.

Tunables: config.yaml `viz:`. Works on replays: python -m laptop --replay fixtures/rot_talker_baby --offline --viz
"""

import asyncio
import json
import math
import threading
import time
import webbrowser
from collections import deque
from http import HTTPStatus
from pathlib import Path

import numpy as np

from .audio import config as AC
from .audio.localize import (XVF3800_GEOMETRY, SrpPhat, SrpSources, aec_to_device, in_geometry_order, mic_order,
                             world_to_aec)
from .audio.xvf3800 import to_world

PAGE = Path(__file__).with_name("viz.html")
SR = 16000


def unit_to_world(x, y, z, offset, ccw):
    """ODAS / mic-geometry unit vector -> (world azimuth, elevation) in degrees."""
    az = math.degrees(math.atan2(y, x)) % 360
    el = math.degrees(math.atan2(z, math.hypot(x, y)))
    return round(to_world(aec_to_device(az), offset, ccw), 1), round(el, 1)


class SoundField:
    """Raw mics -> SRP power on a world-frame hemisphere grid (elevation-major rows, azimuth 0 = front, clockwise)."""

    def __init__(self, geometry, offset, ccw, order=None, az_step=6.0, elevations=(0, 15, 30, 45, 60, 75),
                 window_s=0.25, gate_db=4.0):
        from scipy.signal import butter
        self.offset, self.ccw, self.order = offset, ccw, mic_order(order)
        self.az_step, self.elevations = float(az_step), [float(e) for e in elevations]
        self.srp = SrpPhat(geometry, SR, step_deg=self.az_step, elevations_deg=self.elevations)
        self.world_az = np.arange(0.0, 360.0, self.az_step)
        # world column -> nearest grid column in the mic-geometry frame (a fixed rotation / mirror)
        n = len(self.world_az)
        aec = np.array([world_to_aec(w if w <= 180 else w - 360, offset, ccw) for w in self.world_az])
        self.col = np.round(aec / self.az_step).astype(int) % n
        self.window = int(window_s * SR)
        self.gate_db = gate_db
        self.sos = butter(4, list(AC.SRP_BAND_HZ), "bandpass", fs=SR, output="sos")
        self.blocks = deque()
        self.held = 0
        self.lock = threading.Lock()           # feed() on the event loop, compute() on a worker thread
        self.energy = deque(maxlen=100)        # band level history for the noise floor (~8 s at 12 Hz)
        # The room's own map, learnt while quiet and subtracted: diffuse noise looks like it comes from overhead
        # to a planar array (quiet-room map peaks at 60 deg up), which would otherwise light the whole dome.
        self.background = None
        self.bg_alpha = 0.05

    def feed(self, raw_usb):
        with self.lock:
            self.blocks.append(in_geometry_order(raw_usb, self.order))
            self.held += len(raw_usb)
            while self.blocks and self.held - len(self.blocks[0]) >= self.window:
                self.held -= len(self.blocks.popleft())

    def compute(self):
        """-> (power grid [rows][cols] 0..1 or None, activity 0..1, band level dB over the floor)."""
        from scipy.signal import sosfilt
        with self.lock:
            if self.held < self.window:
                return None, 0.0, 0.0
            x = np.concatenate(list(self.blocks))[-self.window:].astype(np.float64)
        band = sosfilt(self.sos, x, axis=0)
        e = 10 * np.log10(np.mean(band ** 2) + 1e-9)
        self.energy.append(e)
        floor = float(np.percentile(self.energy, 10))
        over = e - floor
        activity = float(np.clip((over - self.gate_db) / 15.0, 0.0, 1.0))
        p = self.srp.power(x, top=1.0)
        if p is None:
            return None, activity, over
        grid = np.clip(p, 0.0, 1.0).reshape(len(self.elevations), -1)[:, self.col]
        if self.background is None:
            self.background = grid.copy()
        elif activity == 0.0:
            self.background += self.bg_alpha * (grid - self.background)
        return np.clip(grid - self.background, 0.0, 1.0), activity, over


class Viz:
    """Serves viz.html and streams snapshots over a websocket to every open page."""

    def __init__(self, app, cfg):
        self.app = app
        self.cfg = cfg
        self.directions = app.directions
        self.ears = next((x for x in (app.audio, getattr(app.directions, "ears", None)) if x is not None
                          and hasattr(x, "on_raw")), None)
        src = self.ears if self.ears is not None else self.directions
        self.offset = float(getattr(src, "offset", AC.ARRAY_OFFSET_DEG))
        self.ccw = bool(getattr(src, "ccw", not AC.ARRAY_CLOCKWISE))
        geometry = getattr(self.ears, "geometry", None) or AC.MIC_GEOMETRY or XVF3800_GEOMETRY
        self.geometry = [list(map(float, m)) for m in geometry]
        self.field = SoundField(geometry, self.offset, self.ccw, az_step=cfg.get("az_step_deg", 6.0),
                                elevations=cfg.get("elevations_deg", [0, 15, 30, 45, 60, 75]),
                                window_s=cfg.get("window_s", 0.25), gate_db=cfg.get("gate_db", 4.0))
        # Our SRP tracks: the live array's ring planner already has them; replays get their own tracker.
        self.tracker = None if getattr(self.ears, "planner", None) is not None else SrpSources(geometry, SR)
        self.tracks = (-1e9, [])
        self.clients = set()
        self.raw_seen = False
        app.raw_listeners.append(self.feed_raw)

    @property
    def url(self):
        return f"http://{self.cfg.get('host', '127.0.0.1')}:{int(self.cfg.get('port', 8770))}/"

    def feed_raw(self, t, raw):
        self.raw_seen = True
        self.field.feed(raw)
        if self.tracker is not None:
            for t_hop, found in self.tracker.feed(t, raw):
                self.tracks = (time.time(), [(i, aec_to_device(az), v) for i, az, v in found])

    # ---- what the page gets ------------------------------------------------------

    def hello(self):
        mics = [unit_to_world(m[0], m[1], 0.0, self.offset, self.ccw)[0] for m in self.geometry]
        leds = [round(to_world(AC.RING_LED0_DEG + i * 30.0 * AC.RING_LED_STEP, self.offset, self.ccw), 1)
                for i in range(12)]
        return {"type": "hello", "grid": {"az": self.field.world_az.tolist(), "el": self.field.elevations},
                "mics_az": mics, "leds_az": leds, "odas": hasattr(self.directions, "bridge"),
                "directions": type(self.directions).__name__}

    def odas_part(self, now):
        bridge = getattr(self.directions, "bridge", None)
        out = {"tracks": [], "pots": []}
        if bridge is None:
            return out
        if bridge.tracks and now - bridge.tracks[0] < 1.0:
            for s in bridge.tracks[1].get("src", []):
                if s.get("id") and (s.get("x") or s.get("y")):
                    az, el = unit_to_world(s["x"], s["y"], s.get("z", 0.0), self.offset, self.ccw)
                    out["tracks"].append({"id": int(s["id"]), "az": az, "el": el,
                                          "a": round(float(s.get("activity", 0.0)), 2)})
        if bridge.pots and now - bridge.pots[0] < 1.0:
            for s in bridge.pots[1].get("src", []):
                az, el = unit_to_world(s.get("x", 0.0), s.get("y", 0.0), s.get("z", 0.0), self.offset, self.ccw)
                out["pots"].append({"az": az, "el": el, "e": round(float(s.get("E", 0.0)), 2)})
        return out

    def ring_part(self, now):
        """LED colours as lit (the Ring's markers, faded like Ring.frame, without touching it) + SRP tracks."""
        ring = getattr(self.ears, "ring", None)
        planner = getattr(self.ears, "planner", None)
        leds, srp = None, []
        if ring is not None:
            from .audio.ring import draw
            live = []
            for deg, color, level, t, hold in list(ring.marks.values()):
                left = 1.0 - (now - t) / hold
                if left > 0:
                    live.append((deg, color, level * left))
            leds = [f"#{c:06x}" for c in draw(live)]
        t, tracks = planner.srp if planner is not None else self.tracks
        if now - t < 0.5:
            srp = [{"id": int(i), "az": round(to_world(d, self.offset, self.ccw), 1), "v": round(float(v), 2)}
                   for i, d, v in tracks]
        return leds, srp

    def snapshot(self, grid, activity, over, now=None):
        now = time.time() if now is None else now
        latest = self.app.history.latest()
        sources = []
        if latest and now - latest[0] < 1.0:
            sources = [{"id": s.id, "az": s.bearing_deg, "speech": s.speech, "c": s.confidence}
                       for s in latest[1]]
        leds, srp = self.ring_part(now)
        return {"type": "frame", "t": round(now, 3), "raw": self.raw_seen,
                "map": None if grid is None else np.round(grid, 2).tolist(),
                "activity": round(activity, 2), "level_db": round(over, 1),
                "sources": sources, "srp": srp, "leds": leds, **self.odas_part(now)}

    # ---- serving -----------------------------------------------------------------

    def process_request(self, connection, request):
        path = request.path.split("?")[0]
        if path == "/ws":
            return None
        if path in ("/", "/index.html"):
            resp = connection.respond(HTTPStatus.OK, PAGE.read_text(encoding="utf-8"))
            del resp.headers["Content-Type"]
            resp.headers["Content-Type"] = "text/html; charset=utf-8"
            resp.headers["Cache-Control"] = "no-store"
            return resp
        return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")

    async def handler(self, ws):
        self.clients.add(ws)
        try:
            await ws.send(json.dumps(self.hello()))
            async for _ in ws:                      # the page sends nothing we need; keep the socket open
                pass
        except Exception:                           # noqa: BLE001  a closed tab is normal
            pass
        finally:
            self.clients.discard(ws)

    async def run(self):
        from websockets.asyncio.server import serve
        host, port = self.cfg.get("host", "127.0.0.1"), int(self.cfg.get("port", 8770))
        period = 1.0 / float(self.cfg.get("rate_hz", 12))
        async with serve(self.handler, host, port, process_request=self.process_request):
            print(f"[viz] 3D sound field at {self.url}")
            if self.cfg.get("open_browser", True):
                webbrowser.open(self.url)
            while True:
                await asyncio.sleep(period)
                if not self.clients:
                    continue
                grid, activity, over = await asyncio.to_thread(self.field.compute)
                msg = json.dumps(self.snapshot(grid, activity, over))
                for ws in list(self.clients):
                    try:
                        await asyncio.wait_for(ws.send(msg), timeout=0.5)
                    except Exception:               # noqa: BLE001  slow or gone: drop it
                        self.clients.discard(ws)

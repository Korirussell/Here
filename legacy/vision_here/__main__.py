"""vision_here: video_frame + heading in, detections out (HERE_PLAN.md §8.6).

Only ever processes the newest frame; older ones are dropped while the model is busy.
"""

import argparse
import asyncio
from bisect import bisect_left
from collections import deque

import cv2
import numpy as np
import torch
from ultralytics import YOLO

from here_common.client import DEFAULT_URL, HubClient, RateCounter
from here_common.geometry import angdiff, bearing_from_pixel, wrap

from . import config as C
from .lips import LipReader


class Headings:
    """Raw heading samples; interpolates the calibrated heading at any time t (§7)."""

    def __init__(self):
        self.samples = deque()   # (t, heading_raw_deg)
        self.offset = 0.0

    def add(self, t, raw):
        self.samples.append((t, raw))
        while self.samples and self.samples[0][0] < t - C.HEADING_BUFFER_S:
            self.samples.popleft()

    def at(self, t):
        if not self.samples:
            return 0.0
        times = [s[0] for s in self.samples]
        i = bisect_left(times, t)
        if i == 0:
            raw = self.samples[0][1]
        elif i >= len(times):
            raw = self.samples[-1][1]
        else:
            (t0, a0), (t1, a1) = self.samples[i - 1], self.samples[i]
            k = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            raw = a0 + k * angdiff(a1, a0)
        return wrap(raw - self.offset)


class Vision:
    def __init__(self, url):
        device = C.DEVICE if torch.cuda.is_available() else "cpu"
        try:
            torch.zeros(1, device=device)
        except Exception as e:  # e.g. CUDA runtime newer than the driver
            print(f"[vision_here] CUDA unusable ({e}); using CPU")
            device = "cpu"
        self.device = device
        self.model = YOLO(C.MODEL)
        names = self.model.names
        self.class_ids = [i for i, n in names.items() if n in C.CLASS_MAP]
        print(f"[vision_here] {C.MODEL} on {device}")
        self.lips = None
        self.lips_error = None
        if C.LIPS:
            try:
                self.lips = LipReader()
            except Exception as e:  # missing mediapipe or model download failed: detections still flow
                self.lips_error = f"no lip scoring ({e})"
                print(f"[vision_here] {self.lips_error}")

        self.headings = Headings()
        self.latest = None
        self.new_frame = asyncio.Event()
        self.rate = RateCounter()
        self.hub = HubClient("vision_here", ["video_frame", "heading", "calibrate"], url)
        self.hub.on_message = self.on_message

    async def on_message(self, msg, payload):
        kind = msg["type"]
        if kind == "video_frame" and payload is not None:
            self.latest = (msg, payload)        # overwrite: drop, don't queue
            self.new_frame.set()
        elif kind == "heading":
            self.headings.add(msg["t"], msg["heading_raw_deg"])
        elif kind == "calibrate" and msg.get("src") == "world_here" and msg.get("kind") == "heading_zero":
            self.headings.offset = msg.get("value") or 0.0

    def detect(self, header, jpeg):
        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return []
        res = self.model.track(img, persist=True, tracker=C.TRACKER, classes=self.class_ids,
                               conf=C.CONFIDENCE, imgsz=C.IMGSZ, device=self.device,
                               half=C.HALF and self.device != "cpu", verbose=False)[0]
        boxes = res.boxes
        if boxes is None or boxes.id is None:
            return []
        heading = self.headings.at(header["t"])
        w, hfov = header["width"], header["hfov_deg"]
        objects = []
        for (x1, y1, x2, y2), tid, cls, conf in zip(boxes.xyxy.tolist(), boxes.id.int().tolist(),
                                                     boxes.cls.int().tolist(), boxes.conf.tolist()):
            raw = self.model.names[cls]
            objects.append({
                "track_id": tid,
                "kind": C.CLASS_MAP[raw],
                "raw_class": raw,
                "confidence": round(conf, 3),
                "bbox": [round(x1, 1), round(y1, 1), round(x2 - x1, 1), round(y2 - y1, 1)],
                "bearing_deg": round(bearing_from_pixel((x1 + x2) / 2, w, hfov, heading), 2),
            })
        if self.lips is not None:
            self.add_faces(img, header["t"], objects)
        return objects

    def add_faces(self, img, t, objects):
        """face_bbox and lip-motion `speaking` for the largest people in view."""
        people = sorted((o for o in objects if o["kind"] == "person"),
                        key=lambda o: -o["bbox"][2] * o["bbox"][3])[:C.MAX_FACES]
        for o in people:
            found = self.lips.face(img, o["bbox"])
            if found is not None:
                o["face_bbox"], jaw = found
                o["speaking"] = round(self.lips.update(o["track_id"], t, jaw), 2)
        self.lips.prune(t)

    async def worker(self):
        loop = asyncio.get_running_loop()
        while True:
            await self.new_frame.wait()
            self.new_frame.clear()
            header, jpeg = self.latest
            objects = await loop.run_in_executor(None, self.detect, header, jpeg)
            await self.hub.send("detections", t=header["t"], frame_id=header["frame_id"], objects=objects)
            self.rate.tick()
            self.hub.rate_hz = self.rate.hz
            problems = [m for m in ("running on CPU" if self.device == "cpu" else None, self.lips_error) if m]
            self.hub.status = "degraded" if problems else "ok"
            self.hub.detail = "; ".join(problems) or None

    async def run(self):
        await asyncio.gather(self.hub.run(), self.worker())


if __name__ == "__main__":
    p = argparse.ArgumentParser(prog="python -m vision_here")
    p.add_argument("--url", default=DEFAULT_URL)
    args = p.parse_args()

    async def main():
        await Vision(args.url).run()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

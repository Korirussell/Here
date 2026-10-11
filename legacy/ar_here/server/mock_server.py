"""
ar_here mock server.

Stands in for the real HERE backend so the AR frontend can be built on its own.
It streams real video (your phone via a webcam app, or any webcam) plus FAKE
captions that follow the HERE frame contract (see CLAUDE.md / web/src/contract.ts).

    python mock_server.py                 # camera 0, rough face detection
    python mock_server.py --camera 1      # pick another camera (phone webcam app)
    python mock_server.py --test-pattern  # no camera needed

Faces come from OpenCV's built-in Haar detector: good enough to pin captions
on real heads while the proper vision project (YOLO + ByteTrack) is built
separately. Speech, tone and loudness are a looping script.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from dataclasses import dataclass, field

import cv2
import numpy as np
from websockets.asyncio.server import serve

# --------------------------------------------------------------------------- #
# Script: who says what, when. "who" 0 = leftmost face, 1 = next face,
# 2 = a voice with no face (placed by bearing, off camera to the left).
# --------------------------------------------------------------------------- #

SCRIPT = [
    dict(at=0.0, who=0, text="Hey, did you see the email about the internship fair?", tone="excited", loud=0.6, conf=0.85),
    dict(at=3.6, who=1, text="Wait, it's this Thursday? I thought it was next week.", tone="questioning", loud=0.5, conf=0.8),
    dict(at=8.2, who=0, text="No, it's Thursday at noon in the student center.", tone="neutral", loud=0.5, conf=0.9),
    dict(at=12.4, who=1, text="Don't tell Sam, I still haven't fixed my resume.", tone="whispering", loud=0.12, conf=0.75),
    dict(at=13.6, who=2, text="Excuse me, is anyone sitting here?", tone="questioning", loud=0.55, conf=0.7),
    dict(at=18.4, who=0, text="You said that last month too!", tone="laughing", loud=0.7, conf=0.9),
    dict(at=21.6, who=1, text="Okay, okay, I'm doing it tonight!", tone="shouting", loud=0.95, conf=0.85),
    dict(at=25.6, who=0, text="I missed the last one because of class.", tone="sad", loud=0.3, conf=0.45),
]
WORDS_PER_SEC = 2.8
PENDING_WORDS = 2  # newest words stay "provisional" until the line finishes
LABELS = {0: "Maya", 1: "Jordan", 2: None}
# Bearings used when a speaker has no tracked face (degrees, world frame).
FALLBACK_BEARING = {0: 8.0, 1: 22.0, 2: -80.0}


def line_timing(line: dict) -> tuple[float, float]:
    words = len(line["text"].split())
    speak = words / WORDS_PER_SEC
    hold = max(2.2, 0.3 * words)
    return speak, hold


LOOP_SECONDS = max(l["at"] + sum(line_timing(l)) for l in SCRIPT) + 2.0


def script_state(t: float) -> dict[int, dict]:
    """Current caption per 'who' at time t (seconds since start)."""
    t = t % LOOP_SECONDS
    current: dict[int, dict] = {}
    for line in SCRIPT:
        speak, hold = line_timing(line)
        e = t - line["at"]
        if not (0 <= e < speak + hold):
            continue
        words = line["text"].split()
        shown = min(len(words), int(e * WORDS_PER_SEC) + 1)
        done = e >= speak
        if done:
            text, pending = " ".join(words), ""
        else:
            split = max(0, shown - PENDING_WORDS)
            text, pending = " ".join(words[:split]), " ".join(words[split:shown])
        prev = current.get(line["who"])
        if prev is None or line["at"] >= prev["_at"]:
            current[line["who"]] = dict(
                _at=line["at"], text=text, pending=pending, final=done,
                tone=line["tone"], conf=line["conf"],
                # A little life in the loudness while speaking.
                loud=min(1.0, line["loud"] + (0 if done else 0.05 * math.sin(e * 9))),
            )
    return current


# --------------------------------------------------------------------------- #
# Faces
# --------------------------------------------------------------------------- #

@dataclass
class Track:
    id: int
    box: np.ndarray  # x, y, w, h (float)
    misses: int = 0


@dataclass
class Tracker:
    """Tiny nearest-centre tracker so ids stay stable between frames."""
    tracks: list[Track] = field(default_factory=list)
    next_id: int = 1
    max_misses: int = 10

    def update(self, boxes: list[tuple[int, int, int, int]]) -> list[Track]:
        dets = [np.array(b, dtype=float) for b in boxes]
        unmatched = list(range(len(dets)))
        for tr in self.tracks:
            best, best_d = None, 1e9
            cx, cy = tr.box[0] + tr.box[2] / 2, tr.box[1] + tr.box[3] / 2
            for i in unmatched:
                d = dets[i]
                dist = math.hypot(d[0] + d[2] / 2 - cx, d[1] + d[3] / 2 - cy)
                if dist < best_d and dist < 0.8 * max(tr.box[2], d[2]):
                    best, best_d = i, dist
            if best is None:
                tr.misses += 1
            else:
                tr.box = 0.55 * dets[best] + 0.45 * tr.box  # smooth jitter
                tr.misses = 0
                unmatched.remove(best)
        self.tracks = [t for t in self.tracks if t.misses <= self.max_misses]
        for i in unmatched:
            self.tracks.append(Track(self.next_id, dets[i]))
            self.next_id += 1
        return self.tracks


class HaarFaces:
    def __init__(self) -> None:
        path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        self.cascade = cv2.CascadeClassifier(path)

    def __call__(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
        small = cv2.resize(frame, None, fx=0.5, fy=0.5)
        gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
        found = self.cascade.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=6, minSize=(36, 36))
        return [(int(x * 2), int(y * 2), int(w * 2), int(h * 2)) for (x, y, w, h) in found]


def fixed_faces(w: int, h: int, t: float) -> list[tuple[int, int, int, int]]:
    """Two 'heads' that drift slightly, matching the test pattern."""
    out = []
    for i, fx in enumerate((0.3, 0.68)):
        cx = fx * w + 30 * math.sin(t * 0.6 + i * 2)
        cy = 0.42 * h + 10 * math.sin(t * 0.9 + i)
        size = 0.2 * h
        out.append((int(cx - size / 2), int(cy - size / 2), int(size), int(size * 1.15)))
    return out


def test_pattern(w: int, h: int, faces: list[tuple[int, int, int, int]]) -> np.ndarray:
    y = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    x = np.linspace(0, 1, w, dtype=np.float32)[None, :]
    img = np.zeros((h, w, 3), np.float32)
    img[..., 0] = 90 + 70 * x  # B
    img[..., 1] = 70 + 50 * y  # G
    img[..., 2] = 60 + 90 * (1 - x) * y  # R
    img = img.astype(np.uint8)
    for (fx, fy, fw, fh) in faces:
        cv2.ellipse(img, (fx + fw // 2, fy + fh // 2), (fw // 2, fh // 2), 0, 0, 360, (150, 170, 205), -1, cv2.LINE_AA)
        cv2.ellipse(img, (fx + fw // 2, fy + int(fh * 0.72)), (fw // 6, fh // 14), 0, 0, 360, (90, 90, 140), -1, cv2.LINE_AA)
        cv2.rectangle(img, (fx - fw // 3, fy + fh), (fx + fw + fw // 3, h), (120, 110, 100), -1)
    cv2.putText(img, "HERE test pattern", (24, h - 24), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (230, 230, 230), 2, cv2.LINE_AA)
    return img


# --------------------------------------------------------------------------- #
# Server
# --------------------------------------------------------------------------- #

class Producer:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.cond = asyncio.Condition()
        self.latest: tuple[str, bytes] | None = None
        self.frame_id = 0
        self.t0 = time.time() - args.script_offset
        self.tracker = Tracker()
        self.cap = None
        self.haar = None
        if not args.test_pattern:
            backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
            self.cap = cv2.VideoCapture(args.camera, backend)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
            if not self.cap.isOpened():
                sys.exit(f"Could not open camera {args.camera}. Try --camera 1, or --test-pattern.")
        use_haar = args.faces == "haar" or (args.faces == "auto" and not args.test_pattern)
        if use_haar:
            self.haar = HaarFaces()

    def grab(self) -> tuple[np.ndarray, list[Track]]:
        t = time.time() - self.t0
        w, h = self.args.width, self.args.height
        if self.cap is None:
            raw = fixed_faces(w, h, t)
            frame = test_pattern(w, h, raw)
        else:
            ok, frame = self.cap.read()
            if not ok:
                frame = np.zeros((h, w, 3), np.uint8)
            if frame.shape[1] != w or frame.shape[0] != h:
                frame = cv2.resize(frame, (w, h))
            raw = self.haar(frame) if self.haar else fixed_faces(w, h, t)
        return frame, self.tracker.update(raw)

    def build_meta(self, tracks: list[Track]) -> dict:
        a, w, h = self.args, self.args.width, self.args.height
        f = (w / 2) / math.tan(math.radians(a.hfov / 2))

        def bearing_of(box: np.ndarray) -> float:
            cx = box[0] + box[2] / 2
            return round(a.yaw + math.degrees(math.atan((cx - w / 2) / f)), 2)

        visible = sorted([t for t in tracks if t.misses <= 2], key=lambda t: t.box[0])
        faces = [
            dict(id=t.id, bbox=[round(float(v), 1) for v in t.box], bearing=bearing_of(t.box))
            for t in visible
        ]

        captions = []
        for who, s in script_state(time.time() - self.t0).items():
            # Speaker k is the k-th face from the left, if that many faces exist
            # (faces never stand in for the off-camera voice, who=2).
            face = visible[who] if who < 2 and who < len(visible) else None
            cap = dict(
                speaker_id=face.id if face else 100 + who,
                text=s["text"], pending=s["pending"], final=s["final"],
                tone=s["tone"], tone_confidence=s["conf"], loudness=round(s["loud"], 3),
            )
            if LABELS.get(who):
                cap["speaker_label"] = LABELS[who]
            if face is None:
                cap["bearing"] = FALLBACK_BEARING[who]
            captions.append(cap)

        self.frame_id += 1
        return dict(
            type="frame", frame_id=self.frame_id, t=time.time(), width=w, height=h,
            camera=dict(hfov_deg=a.hfov, yaw_deg=a.yaw), faces=faces, captions=captions,
        )

    async def run(self) -> None:
        period = 1.0 / self.args.fps
        while True:
            start = time.perf_counter()
            frame, tracks = await asyncio.to_thread(self.grab)
            meta = self.build_meta(tracks)
            ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.args.quality])
            if ok:
                async with self.cond:
                    self.latest = (json.dumps(meta), jpg.tobytes())
                    self.cond.notify_all()
            await asyncio.sleep(max(0.0, period - (time.perf_counter() - start)))

    async def handler(self, ws) -> None:
        print(f"client connected: {ws.remote_address}")
        last_sent = None
        try:
            while True:
                async with self.cond:
                    await self.cond.wait_for(lambda: self.latest is not None and self.latest is not last_sent)
                    item = self.latest
                last_sent = item
                await ws.send(item[0])  # FrameMeta JSON
                await ws.send(item[1])  # JPEG for that same frame
        except Exception:
            pass
        finally:
            print(f"client left: {ws.remote_address}")


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--camera", type=int, default=0, help="camera index (phone webcam apps are often 1 or 2)")
    p.add_argument("--test-pattern", action="store_true", help="generate frames instead of using a camera")
    p.add_argument("--faces", choices=["auto", "haar", "fixed"], default="auto")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--fps", type=float, default=30)
    p.add_argument("--quality", type=int, default=80, help="JPEG quality")
    p.add_argument("--hfov", type=float, default=68.0, help="measured horizontal FOV of the video, degrees")
    p.add_argument("--yaw", type=float, default=0.0, help="world bearing the camera points at, degrees")
    p.add_argument("--script-offset", type=float, default=0.0, help="start the fake conversation this many seconds in")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args()

    producer = Producer(args)
    async with serve(producer.handler, args.host, args.port, max_size=None, compression=None):
        src = "test pattern" if args.test_pattern else f"camera {args.camera}"
        print(f"ar_here mock server on ws://localhost:{args.port}  ({src}, {args.width}x{args.height})")
        print("Open the web app (npm run dev) and it will connect automatically.")
        await producer.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

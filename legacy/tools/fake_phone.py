"""Stand-in for ios_here: laptop webcam -> video_frame (+ JPEG) and a fixed heading.

    python tools/fake_phone.py [--camera 0] [--hfov 70] [--heading 0]

Measure your webcam's HFOV for accurate bearings (two objects at the frame edges,
angle between them). Typical laptop webcams are 65-80 degrees.
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from here_common.client import DEFAULT_URL, HubClient, RateCounter  # noqa: E402


async def main(args):
    hub = HubClient("ios_here", ["config"], args.url)
    cap = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    cap.set(cv2.CAP_PROP_FPS, 30)
    if not cap.isOpened():
        sys.exit(f"could not open camera {args.camera}")
    rate = RateCounter()
    loop = asyncio.get_running_loop()

    async def frames():
        frame_id = 0
        while True:
            ok, img = await loop.run_in_executor(None, cap.read)
            t = time.time()
            if not ok:
                await asyncio.sleep(0.1)
                continue
            if args.mirror:
                img = cv2.flip(img, 1)
            ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, args.quality])
            frame_id += 1
            h, w = img.shape[:2]
            sent = await hub.send_with_binary("video_frame", jpg.tobytes(), t=t, frame_id=frame_id, width=w,
                                              height=h, hfov_deg=args.hfov, lens="wide", encoding="jpeg")
            if sent:
                rate.tick()
                hub.rate_hz = rate.hz

    async def headings():
        while True:
            await hub.send("heading", heading_raw_deg=args.heading, quality="good")
            await asyncio.sleep(1 / 60)

    await asyncio.gather(hub.run(), frames(), headings())


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--hfov", type=float, default=70.0)
    p.add_argument("--heading", type=float, default=0.0)
    p.add_argument("--quality", type=int, default=80)
    p.add_argument("--mirror", action="store_true", help="flip horizontally (bearings will be mirrored too)")
    try:
        asyncio.run(main(p.parse_args()))
    except KeyboardInterrupt:
        pass

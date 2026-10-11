"""Plain debug overlay: draws the hub's `frame` messages in an OpenCV window.

Not ar_here; this is the "plain overlay first" debugging view (HERE_PLAN.md §12)
for checking attribution: person boxes with ids, captions on their speaker,
off-camera captions at the screen edge, and the live DoA bearing.

    python tools/debug_view.py          # Esc or q to quit
"""

import argparse
import asyncio
import sys
import textwrap
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from here_common.client import DEFAULT_URL, HubClient  # noqa: E402
from here_common.geometry import angdiff, pixel_from_bearing  # noqa: E402

FONT = cv2.FONT_HERSHEY_SIMPLEX


def label(img, text, x, y, color=(255, 255, 255), scale=0.6, bg=(30, 30, 30)):
    (w, h), base = cv2.getTextSize(text, FONT, scale, 1)
    x = int(min(max(x, 0), img.shape[1] - w - 4))
    y = int(min(max(y, h + 4), img.shape[0] - 4))
    cv2.rectangle(img, (x - 3, y - h - 4), (x + w + 3, y + base), bg, -1)
    cv2.putText(img, text, (x, y), FONT, scale, color, 1, cv2.LINE_AA)
    return h + base + 6


def caption_lines(c):
    who = c.get("speaker_label") or (f"Person {c['speaker_id']}" if c["speaker_id"] else "Unknown")
    tone = f" [{c['tone']}]" if c.get("tone") else ""
    flags = " (overlap)" if c.get("overlap") else ""
    body = (c["text"] + (" " if c["text"] and c["pending"] else "") + c["pending"]).strip()
    return [f"{who}{tone}{flags}:"] + textwrap.wrap(body, 42)[-2:]


def draw(meta, img, doa):
    w, h = meta["width"], meta["height"]
    cam = meta["camera"]
    faces = {f["id"]: f for f in meta["faces"]}
    for f in meta["faces"]:
        x, y, bw, bh = map(int, f["bbox"])
        cv2.rectangle(img, (x, y), (x + bw, y + bh), (0, 200, 255), 2)
        label(img, f"#{f['id']} {f['bearing']:+.0f}deg", x, y + bh + 20, scale=0.5)
    for o in meta.get("objects", []):
        x, y, bw, bh = map(int, o["bbox"])
        cv2.rectangle(img, (x, y), (x + bw, y + bh), (200, 200, 0), 1)
        label(img, f"{o['kind']} #{o['id']}", x, y - 4, scale=0.5)

    edge_y = {"left": 120, "right": 120}
    for c in meta["captions"]:
        lines = caption_lines(c)
        color = (255, 255, 255) if c["final"] else (200, 200, 200)
        f = faces.get(c["speaker_id"])
        if f:
            x, y = f["bbox"][0], f["bbox"][1] - 10 - 22 * (len(lines) - 1)
            for line in lines:
                y += label(img, line, x, y, color)
        else:
            b = c.get("bearing")
            side = "left" if b is not None and angdiff(b, cam["yaw_deg"]) < 0 else "right"
            arrow = "<- " if side == "left" else ""
            arrow_r = " ->" if side == "right" else ""
            px = pixel_from_bearing(b, w, cam["hfov_deg"], cam["yaw_deg"]) if b is not None else None
            x = px if px is not None else (10 if side == "left" else w - 300)
            where = f"{b:+.0f}deg off camera" if b is not None else "direction unknown"
            y = edge_y[side]
            for i, line in enumerate(lines):
                txt = (arrow + line + arrow_r) if i == 0 else line
                y += label(img, txt, x, y, color, bg=(60, 20, 20))
            y += label(img, where, x, y, (180, 180, 180), scale=0.45, bg=(60, 20, 20))
            edge_y[side] = y + 10

    # DoA: a marker on the top edge if in view, else an arrow at the side.
    if doa:
        b, speech = doa["bearing_deg"], doa["speech"]
        color = (0, 0, 255) if speech else (120, 120, 120)
        px = pixel_from_bearing(b, w, cam["hfov_deg"], cam["yaw_deg"])
        if px is not None:
            cv2.line(img, (int(px), 0), (int(px), 40), color, 4)
        label(img, f"DoA {b:+.0f}deg {'speech' if speech else ''}", 10, 30, color)
    label(img, f"yaw {cam['yaw_deg']:+.0f}  hfov {cam['hfov_deg']:.0f}  frame {meta['frame_id']}",
          10, h - 10, scale=0.45)
    health = meta.get("health", {})
    label(img, "  ".join(f"{k}:{v}" for k, v in sorted(health.items())), 10, h - 34, scale=0.45)
    return img


async def main(url):
    state = {"frame": None, "doa": None}
    hub = HubClient("ar_here", ["frame", "doa"], url)

    async def on_message(msg, payload):
        if msg["type"] == "frame" and payload is not None:
            state["frame"] = (msg, payload)
        elif msg["type"] == "doa":
            state["doa"] = msg

    hub.on_message = on_message
    task = asyncio.create_task(hub.run())
    shown = None
    blank = np.zeros((360, 640, 3), np.uint8)
    label(blank, "waiting for frames from the hub...", 20, 180)
    while True:
        if state["frame"] is not None and state["frame"] is not shown:
            shown = state["frame"]
            meta, jpg = shown
            img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                cv2.imshow("HERE debug view", draw(meta, img, state["doa"]))
        elif shown is None:
            cv2.imshow("HERE debug view", blank)
        if cv2.waitKey(1) & 0xFF in (27, ord("q")):
            break
        await asyncio.sleep(0.01)
    task.cancel()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--url", default=DEFAULT_URL)
    try:
        asyncio.run(main(p.parse_args().url))
    except KeyboardInterrupt:
        pass

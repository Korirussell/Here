"""Stand-in for audio_here's direction: a slider window that sends doa messages at 20 Hz.

    python tools/fake_doa.py

Drag the slider (or Left/Right arrows) to set the bearing. Hold SPACE, or tick
"speech", to mark speech as present. Keys 1-8 jump to the belt directions
(1 front, 3 right, 5 back, 7 left). Bearings are world frame, clockwise positive.
"""

import argparse
import json
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

from websockets.sync.client import connect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from here_common.client import DEFAULT_URL  # noqa: E402

RATE_HZ = 20


class State:
    bearing = 0.0
    speech = False
    held = False
    confidence = 0.9
    status = "connecting"


def sender(url, state):
    seq = 0
    while True:
        try:
            with connect(url) as ws:
                ws.send(json.dumps({"type": "hello", "v": 1, "src": "audio_here", "t": time.time(),
                                    "role": "audio_here", "subscribe": []}))
                state.status = "connected"
                last_hb = 0.0
                while True:
                    now = time.time()
                    seq += 1
                    ws.send(json.dumps({"type": "doa", "v": 1, "src": "audio_here", "t": now, "seq": seq,
                                        "bearing_deg": round(state.bearing, 1), "confidence": state.confidence,
                                        "speech": state.speech or state.held, "energy_db": -30.0,
                                        "beams": [{"bearing_deg": round(state.bearing, 1),
                                                   "speech": state.speech or state.held, "energy_db": 40.0}]}))
                    if now - last_hb > 2:
                        ws.send(json.dumps({"type": "heartbeat", "v": 1, "src": "audio_here", "t": now,
                                            "status": "degraded", "detail": "fake DoA", "rate_hz": RATE_HZ}))
                        last_hb = now
                    time.sleep(1 / RATE_HZ)
        except OSError:
            state.status = "hub not reachable, retrying"
            time.sleep(1)
        except Exception as e:  # connection dropped
            state.status = f"disconnected: {e}"
            time.sleep(1)


def main(url):
    state = State()
    threading.Thread(target=sender, args=(url, state), daemon=True).start()

    root = tk.Tk()
    root.title("fake DoA")
    bearing = tk.DoubleVar(value=0.0)
    speech = tk.BooleanVar(value=False)
    label = tk.Label(root, font=("Segoe UI", 14))
    label.pack(padx=12, pady=(12, 0))
    tk.Scale(root, from_=-180, to=180, orient="horizontal", length=420, resolution=1, variable=bearing,
             showvalue=False).pack(padx=12)
    tk.Checkbutton(root, text="speech (or hold SPACE)", variable=speech).pack()
    status = tk.Label(root, fg="gray")
    status.pack(pady=(0, 12))

    def nudge(d):
        v = bearing.get() + d
        bearing.set(v - 360 if v > 180 else v + 360 if v <= -180 else v)

    root.bind("<Left>", lambda e: nudge(-5))
    root.bind("<Right>", lambda e: nudge(5))
    root.bind("<KeyPress-space>", lambda e: setattr(state, "held", True))
    root.bind("<KeyRelease-space>", lambda e: setattr(state, "held", False))
    for k in range(1, 9):
        b = (k - 1) * 45
        root.bind(str(k), lambda e, b=b: bearing.set(b - 360 if b > 180 else b))

    def tick():
        state.bearing, state.speech = bearing.get(), speech.get()
        on = state.speech or state.held
        label.config(text=f"{state.bearing:+.0f}°   {'SPEECH' if on else 'silent'}", fg="red" if on else "black")
        status.config(text=state.status)
        root.after(50, tick)

    tick()
    root.mainloop()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--url", default=DEFAULT_URL)
    main(p.parse_args().url)

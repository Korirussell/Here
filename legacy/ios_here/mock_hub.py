"""Stand-in for the world_here hub, for testing ios_here on its own.

Answers clock pings, prints heading / frame rate / clock offset, and saves the
newest frame to latest.jpg. Requires: pip install websockets

    python mock_hub.py                              # listen on 0.0.0.0:8765 (USB hotspot)
    python mock_hub.py --connect ws://localhost:9000 # iproxy fallback: phone listens
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

LATEST_JPEG = Path(__file__).with_name("latest.jpg")
PRINT_EVERY_S = 1.0


def envelope(msg_type, **fields):
    return json.dumps({"type": msg_type, "v": 1, "src": "world_here", "t": time.time(), **fields})


async def session(ws):
    pending_frame = None
    frames = headings = 0
    last_heading = None
    last_print = time.time()
    latency_ms = []

    async for raw in ws:
        now = time.time()

        if isinstance(raw, bytes):
            if pending_frame is None:
                print("! binary message with no video_frame header before it")
                continue
            LATEST_JPEG.write_bytes(raw)
            latency_ms.append((now - pending_frame["t"]) * 1000)
            frames += 1
            pending_frame = None
        else:
            msg = json.loads(raw)
            kind = msg.get("type")
            if pending_frame is not None:
                print("! video_frame header not followed by its JPEG")
                pending_frame = None

            if kind == "hello":
                print(f"hello from {msg.get('role')} subscribe={msg.get('subscribe')}")
            elif kind == "clock_ping":
                await ws.send(envelope("clock_pong", t_phone=msg["t_phone"], t_hub=now))
            elif kind == "heading":
                headings += 1
                last_heading = msg
            elif kind == "video_frame":
                pending_frame = msg
            elif kind in ("heartbeat", "calibrate", "config", "search_request"):
                print(f"{kind}: {json.dumps(msg)}")
            else:
                print(f"? unknown type {kind!r}: {raw[:200]}")

        if now - last_print >= PRINT_EVERY_S:
            elapsed = now - last_print
            heading = (
                f"{last_heading['heading_raw_deg']:7.1f} deg ({last_heading.get('quality')})"
                if last_heading else "   none"
            )
            lat = f"{sum(latency_ms) / len(latency_ms):6.0f} ms" if latency_ms else "     -"
            print(
                f"heading {heading} @ {headings / elapsed:4.0f} Hz | "
                f"frames {frames / elapsed:4.1f} fps | capture->hub {lat}"
            )
            frames = headings = 0
            latency_ms.clear()
            last_print = now


async def handle(ws):
    print(f"connected: {ws.remote_address}")
    try:
        await session(ws)
    finally:
        print("disconnected")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--connect", help="connect to the phone instead of listening (iproxy fallback)")
    args = parser.parse_args()

    if args.connect:
        async for ws in connect(args.connect, max_size=None):
            try:
                await handle(ws)
            except ConnectionClosed:
                continue
    else:
        async with serve(handle, args.host, args.port, max_size=None) as server:
            print(f"mock hub listening on ws://{args.host}:{args.port}")
            await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())

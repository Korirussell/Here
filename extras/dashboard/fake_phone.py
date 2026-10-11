"""A stand-in for the HERE iPhone app on the relay, for building and testing the dashboard without a phone.

    python -m extras.dashboard.fake_phone --relay ws://127.0.0.1:8080/ws --session demo
    python -m extras.dashboard.fake_phone --people "Person 1:35,Person 2:-35" --heading 0 --thermal fair

It sends what the app sends (ios/CLAUDE.md, ios/HERE/App/AppModel.swift), and nothing else:
- `hello` as role `phone`, with the phone's default subscriptions;
- `heartbeat` every 2 s, with `thermal` (and status `degraded` + `detail` with `--camera-error`);
- with Settings > "Send scene" (on here unless --no-scene): `scene` at 5 Hz (heading + the people it sees,
  `speaking` while a `sources` track points at them), and on every non-empty final `utterance` an
  `attribution`: the nearest person within 20 degrees by bearing, else `off_camera` with no label.
Retractions (empty finals) get no attribution, like the app. Identity, objects and fusion stay on the phone.
The relay URL, session and token come from the environment or laptop/.env, like every other tool (the token is
never printed).
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from contracts.python.here_contract import (  # noqa: E402
    DEFAULT_SUBSCRIBE, Attribution, Heartbeat, Hello, Ref, Scene, ScenePerson, wrap,
)
from laptop.settings import read_dotenv  # noqa: E402

ENV_FILE = ROOT / "laptop" / ".env"
ATTRIBUTE_DEG = 20.0          # README section 13: a voice belongs to the face it points at
SCENE_HZ = 5.0
HEARTBEAT_S = 2.0


def parse_people(spec):
    """'Person 1:35,Person 2:-35' -> [('Person 1', 35.0), ...]"""
    out = []
    for part in filter(None, (p.strip() for p in spec.split(","))):
        label, _, b = part.rpartition(":")
        out.append((label.strip(), float(b)))
    return out


class FakePhone:
    def __init__(self, people, heading=0.0, thermal="nominal", camera_error=None, send_scene=True):
        self.people = people
        self.heading = heading
        self.thermal = thermal
        self.camera_error = camera_error
        self.send_scene = send_scene
        self.speaking_bearings = []                 # bearings of speech tracks in the latest `sources`

    def nearest(self, bearing):
        if bearing is None or not self.people:
            return None, 0.0
        label, b = min(self.people, key=lambda p: abs(wrap(p[1] - bearing)))
        d = abs(wrap(b - bearing))
        return (label, round(max(0.0, 1 - d / ATTRIBUTE_DEG), 3)) if d <= ATTRIBUTE_DEG else (None, 0.0)

    # ---- what the phone sends -----------------------------------------------------------------
    def hello(self):
        return Hello(src="phone", t=time.time(), role="phone", subscribe=DEFAULT_SUBSCRIBE["phone"], app_version="fake-phone")

    def heartbeat(self):
        if self.camera_error:
            return Heartbeat(src="phone", t=time.time(), status="degraded", detail=self.camera_error, thermal=self.thermal)
        return Heartbeat(src="phone", t=time.time(), status="ok", thermal=self.thermal)

    def scene(self):
        speaking = lambda b: any(abs(wrap(s - b)) <= ATTRIBUTE_DEG for s in self.speaking_bearings)  # noqa: E731
        return Scene(src="phone", t=time.time(), heading_deg=self.heading,
                     people=[ScenePerson(label=label, bearing_deg=b, speaking=speaking(b)) for label, b in self.people])

    def on_message(self, m):
        """Messages from the relay -> what the phone sends back (an attribution, or nothing)."""
        if m.get("type") == "sources":
            self.speaking_bearings = [s["bearing_deg"] for s in m.get("sources", []) if s.get("speech")]
        if m.get("type") == "utterance" and m.get("final") and (m.get("text") or "").strip() and self.send_scene:
            label, score = self.nearest(m.get("bearing_deg"))
            return Attribution(src="phone", t=time.time(), ref=Ref(utt_id=m["utt_id"]), person_label=label,
                               method="bearing" if label else "off_camera", score=score)
        return None


async def run(phone, url, duration=None):
    stop = time.time() + duration if duration else None
    async with connect(url) as ws:
        await ws.send(phone.hello().to_json())

        async def reader():
            async for text in ws:
                try:
                    reply = phone.on_message(json.loads(text))
                except (ValueError, KeyError, TypeError):
                    continue
                if reply is not None:
                    await ws.send(reply.to_json())

        async def writer():
            last_hb = 0.0
            while stop is None or time.time() < stop:
                if time.time() - last_hb >= HEARTBEAT_S:
                    await ws.send(phone.heartbeat().to_json())
                    last_hb = time.time()
                if phone.send_scene:
                    await ws.send(phone.scene().to_json())
                await asyncio.sleep(1 / SCENE_HZ)

        r = asyncio.create_task(reader())
        try:
            await writer()
        finally:
            r.cancel()


def relay_url(base, session, token):
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}session={session}&role=phone" + (f"&token={token}" if token else "")


def setting(name, default=""):
    """The environment first, then laptop/.env (the same sources as tools/demo.py and the laptop)."""
    return os.environ.get(name) or read_dotenv(ENV_FILE).get(name) or default


def relay_token():
    return setting("HERE_RELAY_TOKEN")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--relay", default=setting("HERE_RELAY_URL", "ws://127.0.0.1:8080/ws"))
    p.add_argument("--session", default=setting("HERE_SESSION", "demo"))
    p.add_argument("--people", default="Person 1:35,Person 2:-35", help="label:bearing, comma separated")
    p.add_argument("--heading", type=float, default=0.0)
    p.add_argument("--thermal", default="nominal", choices=["nominal", "fair", "serious", "critical"])
    p.add_argument("--camera-error", default=None, help="report status degraded with this detail")
    p.add_argument("--no-scene", action="store_true", help='as with Settings > "Send scene" off')
    p.add_argument("--duration", type=float, default=None, help="seconds, then exit")
    a = p.parse_args(argv)
    phone = FakePhone(parse_people(a.people), a.heading, a.thermal, a.camera_error, not a.no_scene)
    print(f"[fake_phone] {a.relay} session={a.session} people={phone.people} scene={'on' if phone.send_scene else 'off'}")
    try:
        asyncio.run(run(phone, relay_url(a.relay, a.session, relay_token()), a.duration))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

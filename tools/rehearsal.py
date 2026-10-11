"""The final rehearsal order (Shared/SIX_CLAUDE_ORCHESTRATION.md), automated as far as a script can go, N times in a row.

    python tools/rehearsal.py                    # 3 runs, --source replay of fixtures/mock_scene, spare ports
    python tools/rehearsal.py --runs 1 --source real     # on the array (only when K1 isn't using it)

Each run: a light `demo.py reset` (timed), then the core moments from the laptop as the dashboard page receives them
(`/live`), then the dashboard's memory endpoints. The steps that need a person (calibrate, the belt, the wearer turning,
explaining the architecture) are listed as MANUAL. A failed step is logged, and the run carries on so every failure shows.

  1 reset                light reset < 30 s, laptop reconnected
  2 calibrate            MANUAL (phone)
  3 front caption        a final caption from the front (|bearing| < 60), speech_start before its first words
  4 rear speaker         a final caption from behind (|bearing| > 150), speech_start first; belt + turn MANUAL
  5 doorbell / name      a doorbell alert and a name_called alert, both from behind
  6 inspect              the doorbell sound_event names its model, and an enrichment names its attention_model
  7 memory ask           /api/ask answers "did anyone call my name" with evidence
  8 insight              /api/insights has an insight with evidence
  9 replay lab           /api/replay: a stricter policy surfaces fewer events than a looser one (a real counterfactual)
 10 sponsor architecture MANUAL (after the product wow)
"""

import argparse
import asyncio
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
PORTS = {"relay": 18080, "dashboard": 18780, "memory": 18790}
WATCH_S = 75.0           # one pass of mock_scene (27.6 s) plus model load after a cold up, with margin


def demo(*args, timeout=240):
    cmd = [PY, "tools/demo.py", *args, "--relay-port", str(PORTS["relay"]), "--dashboard-port",
           str(PORTS["dashboard"]), "--memory-port", str(PORTS["memory"])]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    return r.returncode, r.stdout + r.stderr


def api(path, **q):
    url = f"http://127.0.0.1:{PORTS['dashboard']}/api/{path}" + (f"?{urllib.parse.urlencode(q)}" if q else "")
    return json.loads(urllib.request.urlopen(url, timeout=20).read())


async def watch_live(seconds, until):
    """Messages the dashboard page receives (the raw contract messages), until `until(msgs)` or the deadline."""
    import websockets
    msgs, end = [], time.time() + seconds
    while time.time() < end and not until(msgs):
        try:
            async with websockets.connect(f"ws://127.0.0.1:{PORTS['dashboard']}/live", max_size=None) as ws:
                while time.time() < end and not until(msgs):
                    try:
                        raw = await asyncio.wait_for(ws.recv(), 0.5)
                    except asyncio.TimeoutError:
                        continue
                    try:
                        m = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(m, dict) and m.get("type"):
                        m["_recv"] = time.time()
                        msgs.append(m)
        except OSError:
            await asyncio.sleep(0.5)
    return msgs


def behind(b):
    return b is not None and abs(b) > 150


def moments(msgs):
    finals = [m for m in msgs if m["type"] == "utterance" and m.get("final") and m.get("text")]
    starts = {}
    for m in msgs:
        if m["type"] == "speech_start":
            starts.setdefault(round(m.get("bearing_deg") or 0), m["_recv"])
    first_words = {}
    for m in msgs:
        if m["type"] == "utterance" and (m.get("text") or m.get("pending")):
            first_words.setdefault(m["utt_id"], m["_recv"])

    def caption(pred):
        f = next((m for m in finals if pred(m.get("bearing_deg"))), None)
        if not f:
            return False, "no final caption"
        had_start = any(m["type"] == "speech_start" and m["_recv"] <= first_words.get(f["utt_id"], f["_recv"])
                        for m in msgs)
        return had_start, f'"{f["text"][:40]}" at {f["bearing_deg"]:+.0f}' + ("" if had_start else ", NO speech_start first")

    alerts = {m["kind"]: m for m in msgs if m["type"] == "alert" and m.get("active", True)}
    door, name = alerts.get("doorbell"), alerts.get("name_called")
    sound = next((m for m in msgs if m["type"] == "sound_event" and m.get("label") == "doorbell"), None)
    enr = next((m for m in msgs if m["type"] == "perception_enrichment"), None)
    return {
        "3 front caption": caption(lambda b: b is not None and abs(b) < 60),
        "4 rear speaker": caption(behind),
        "5 doorbell / name": (bool(door and name and behind(door.get("bearing_deg"))
                                   and (name.get("bearing_deg") is None or behind(name.get("bearing_deg")))),
                              f"doorbell {door and door.get('bearing_deg')}, name_called {name and name.get('bearing_deg')}"),
        "6 inspect": (bool(sound and sound.get("model") and enr and enr.get("attention_model")),
                      f"sound_event.model {sound and sound.get('model')}, attention_model {enr and enr.get('attention_model')}"),
    }


def memory_steps():
    out = {}
    try:
        a = api("ask", q="Did anyone call my name?")
        out["7 memory ask"] = (bool(a.get("answer") and a.get("evidence")),
                               f'{len(a.get("evidence") or [])} evidence, _source {a.get("_source")}: {str(a.get("answer"))[:60]}')
    except Exception as e:  # noqa: BLE001
        out["7 memory ask"] = (False, f"{type(e).__name__}: {e}")
    try:
        ins = api("insights").get("insights") or []
        good = [i for i in ins if i.get("evidence_ids")]
        out["8 insight"] = (bool(good), f"{len(good)}/{len(ins)} with evidence, e.g. {good[0]['title'][:50] if good else '-'}")
    except Exception as e:  # noqa: BLE001
        out["8 insight"] = (False, f"{type(e).__name__}: {e}")
    try:
        loose = api("replay", segment_id="lunch", policy=json.dumps({"sensitivity": 0.9, "crowd_filter": 0.0}))
        strict = api("replay", segment_id="lunch", policy=json.dumps({"sensitivity": 0.2, "crowd_filter": 1.0,
                                                                       "focus_mode": True}))
        out["9 replay lab"] = (strict.get("surfaced", 0) < loose.get("surfaced", 0) and not strict.get("error"),
                               f"lunch surfaced {loose.get('surfaced')} loose vs {strict.get('surfaced')} strict, "
                               f"kept important {strict.get('kept_important')}, _source {strict.get('_source')}")
    except Exception as e:  # noqa: BLE001
        out["9 replay lab"] = (False, f"{type(e).__name__}: {e}")
    return out


def one_run(k):
    steps = {}
    t = time.time()
    code, out = demo("reset")
    dt = time.time() - t
    steps["1 reset"] = (code == 0 and "reconnected" in out and "NOT" not in out and dt < 30,
                        f"{dt:.1f} s" + ("" if "reconnected" in out else "; source not reconnected"))
    steps["2 calibrate"] = (None, "MANUAL")

    def done(ms):
        kinds = {(m["type"], m.get("kind")) for m in ms}
        return ("alert", "doorbell") in kinds and ("alert", "name_called") in kinds and \
            any(m["type"] == "perception_enrichment" for m in ms) and \
            any(m["type"] == "utterance" and m.get("final") and behind(m.get("bearing_deg")) for m in ms)
    msgs = asyncio.run(watch_live(WATCH_S, done))
    steps.update(moments(msgs))
    steps.update(memory_steps())
    steps["10 sponsor architecture"] = (None, "MANUAL")
    return steps, len(msgs)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--source", default="replay", choices=["replay", "real"])
    p.add_argument("--fixture", default="mock_scene")
    p.add_argument("--keep", action="store_true", help="leave the stack up afterwards")
    a = p.parse_args()
    up = ["up", "--source", a.source] + (["--fixture", a.fixture] if a.source == "replay" else [])
    code, out = demo(*up)
    print(out.strip())
    if code != 0:
        sys.exit("demo up failed")
    results, failures = [], 0
    try:
        for k in range(1, a.runs + 1):
            steps, n = one_run(k)
            results.append(steps)
            bad = [s for s, (ok, _) in steps.items() if ok is False]
            failures += len(bad)
            print(f"\n=== run {k}: {n} messages on the dashboard page, {len(bad)} failed")
            for s, (ok, detail) in steps.items():
                print(f"  {'PASS' if ok else ('----' if ok is None else 'FAIL')}  {s:<24} {detail}")
    finally:
        if not a.keep:
            print("\n" + demo("down")[1].strip())
    print(f"\n{a.runs} runs, {failures} failed steps" + ("" if failures else ": every automated step passed every time"))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()

"""Scripted failure drills (TASKS K3 D): the real laptop on a fixture replay, one dependency broken, compared
with a clean run of the same fixture. Repeatable, no hardware, no cloud account needed.

    python tools/drills.py                     # every drill
    python tools/drills.py hf_down gemini_down # some
    python tools/drills.py --list

Each run gets a private local relay on a spare port and its own session; a tap (role tool) records every message.
The laptop is started through a small wrapper (python -c) that applies the drill before `python -m laptop` runs:
  - net_off: every non-loopback connection fails inside the laptop process (DNS and connect), the in-process stand-in
    for airplane mode. The relay on 127.0.0.1 still works, like the demo's local relay on the laptop's own Wi-Fi.
  - config: UPPER_CASE overrides on a subpackage config module (laptop/<pkg>/config.py), e.g. a sound model that
    can't load. Nobody's config.yaml changes.
  - slow_tier: an attention tier that answers after 2 s, put in front of whatever tiers load.
Then: "events unchanged" = the same final captions, sound_event labels and alert kinds as the clean run.
"""

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from contracts.python import here_contract as hc  # noqa: E402

PY = sys.executable
FIXTURE = "mock_scene"
SETTLE_S = 6.0          # after the fixture's last second, wait this long for finals / enrichment
START_MAX_S = 120.0     # model load before the first message

WRAPPER = r'''
import json, os, runpy, socket, sys, time
d = json.loads(os.environ["HERE_DRILL"])
if d.get("net_off"):
    LOCAL = {"localhost", "127.0.0.1", "::1"}
    real_gai, real_connect = socket.getaddrinfo, socket.socket.connect
    def gai(host, *a, **k):
        if host not in LOCAL and not str(host).startswith("127."):
            raise socket.gaierror(socket.EAI_NONAME, f"drill: network off ({host})")
        return real_gai(host, *a, **k)
    def connect(self, addr):
        if isinstance(addr, tuple) and addr and str(addr[0]) not in LOCAL and not str(addr[0]).startswith("127."):
            raise OSError(10051, f"drill: network off ({addr[0]})")
        return real_connect(self, addr)
    socket.getaddrinfo, socket.socket.connect = gai, connect
    try:
        socket.getaddrinfo("huggingface.co", 443)
        print("[drill] network NOT blocked", flush=True)
    except OSError as e:
        print(f"[drill] network off: {e}", flush=True)
import importlib
for mod, values in (d.get("config") or {}).items():
    m = importlib.import_module(mod)
    for k, v in values.items():
        setattr(m, k, v)
if d.get("slow_tier"):
    import laptop.enrich.attention as A, laptop.enrich.enricher as E
    class Slow(A.Decider):
        name = "drill/slow-tier"
        def attention(self, job):
            time.sleep(2.0)
            return {"urgent": 1.0}
    real_build = A.build_decider
    def build(*a, **k):
        t = real_build(*a, **k)
        return A.TieredDecider([Slow(), *t.tiers], {**t.timeouts, Slow.name: 0.3}, t.min_confidence, t.rules)
    E.build_decider = build
sys.argv = ["laptop", *d["argv"]]
runpy.run_module("laptop", run_name="__main__", alter_sys=True)
'''

DRILLS = {
    "baseline": dict(why="clean run, everything this laptop has"),
    "hf_down": dict(why="Hugging Face unreachable (net off) with a warm cache: whisper and PANNs load from disk",
                    net_off=True),
    "hf_sound_model_down": dict(why="the configured sound model is a Hugging Face model that can't load -> PANNs",
                                net_off=True,
                                config={"laptop.sounds.config": {"MODEL": "MIT/ast-finetuned-audioset-10-10-0.4593"}}),
    "gemini_down": dict(why="a Gemini key but no network: calls fail, events unchanged", net_off=True,
                        env={"GEMINI_API_KEY": "drill-dummy"}),
    "attention_slow": dict(why="an attention tier slower than its timeout -> the next tier / rules", slow_tier=True),
    "attention_unloaded": dict(why="no attention models at all -> rules",
                               config={"laptop.enrich.config": {"TIERS": []}}),
    "relay_restart": dict(why="the relay restarts mid-scene: the laptop and the tap reconnect", restart_at_s=10.0),
    "airplane": dict(why="everything off-network at once: net off, Gemini key set, a model that needs HF",
                     net_off=True, env={"GEMINI_API_KEY": "drill-dummy"},
                     config={"laptop.sounds.config": {"MODEL": "MIT/ast-finetuned-audioset-10-10-0.4593"}}),
}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_relay(port, log):
    return subprocess.Popen([PY, "-m", "relay", "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT,
                            stdout=log, stderr=subprocess.STDOUT, env=dict(os.environ, HERE_RELAY_TOKEN=""))


def wait_port(port, up=True, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            if (s.connect_ex(("127.0.0.1", port)) == 0) == up:
                return True
        time.sleep(0.1)
    return False


async def tap(url, session, out, stop):
    """Every message in the session, reconnecting like a phone would. out: list of (t_recv, msg)."""
    import websockets
    while not stop.is_set():
        try:
            async with websockets.connect(f"{url}?session={session}&role=tool") as ws:
                await ws.send(hc.Hello(src="tool", t=time.time(), role="tool", subscribe=["*"]).to_json())
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), 0.5)
                    except asyncio.TimeoutError:
                        continue
                    out.append((time.time(), json.loads(raw)))
        except Exception:  # noqa: BLE001 - relay restarting
            await asyncio.sleep(0.3)


def fixture_seconds(name):
    return float(json.loads((ROOT / "fixtures" / name / "meta.json").read_text(encoding="utf-8"))["seconds"])


async def run_drill(name, spec, fixture, logdir):
    port, session = free_port(), f"drill-{name}"
    url = f"ws://127.0.0.1:{port}/ws"
    relay_log = open(logdir / f"{name}.relay.log", "w", encoding="utf-8")
    laptop_log = open(logdir / f"{name}.laptop.log", "w", encoding="utf-8")
    relay = start_relay(port, relay_log)
    assert wait_port(port), "relay did not start"
    msgs, stop = [], asyncio.Event()
    tap_task = asyncio.create_task(tap(url, session, msgs, stop))
    drill = {"argv": ["--replay", f"fixtures/{fixture}", "--relay", url, "--session", session],
             "net_off": spec.get("net_off", False), "config": spec.get("config"), "slow_tier": spec.get("slow_tier")}
    env = dict(os.environ, HERE_DRILL=json.dumps(drill), PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
               HERE_RELAY_TOKEN="", **spec.get("env", {}))
    laptop = subprocess.Popen([PY, "-c", WRAPPER], cwd=ROOT, stdout=laptop_log, stderr=subprocess.STDOUT, env=env)
    t0, restarted, outage = time.time(), False, None
    try:
        first = None
        while time.time() - t0 < START_MAX_S and first is None:   # the replay starts once the models are loaded
            first = next((t for t, m in msgs if m.get("type") != "heartbeat"), None)
            if laptop.poll() is not None:
                break
            await asyncio.sleep(0.2)
        if first is None:
            return {"drill": name, "error": f"no events within {START_MAX_S:.0f} s (see {laptop_log.name})"}
        end = first + fixture_seconds(fixture) + SETTLE_S
        while time.time() < end:
            if spec.get("restart_at_s") and not restarted and time.time() - first >= spec["restart_at_s"]:
                relay.terminate()
                relay.wait(10)
                wait_port(port, up=False)
                down = time.time()
                await asyncio.sleep(1.0)
                relay = start_relay(port, relay_log)
                wait_port(port)
                outage, restarted = (down, time.time()), True
                end += 4.0                                          # the backoff costs a few seconds
            await asyncio.sleep(0.2)
    finally:
        stop.set()
        await tap_task
        laptop.terminate()
        try:
            laptop.wait(10)
        except subprocess.TimeoutExpired:
            laptop.kill()
        relay.terminate()
        relay.wait(10)
        relay_log.close()
        laptop_log.close()
    return summarize(name, spec, [m for _, m in msgs], (logdir / f"{name}.laptop.log"), outage,
                     [(t, m) for t, m in msgs])


def summarize(name, spec, msgs, laptop_log, outage, timed):
    def of(kind):
        return [m for m in msgs if m.get("type") == kind]
    invalid = sum(1 for m in msgs if hc.errors(m))
    finals = [m["text"] for m in of("utterance") if m.get("final") and m.get("text")]
    hb = of("heartbeat")
    log = laptop_log.read_text(encoding="utf-8", errors="replace")
    s = {
        "drill": name, "why": spec["why"],
        "finals": finals,
        "sounds": sorted({f"{m.get('label')}" for m in of("sound_event")}),
        "sound_models": sorted({m.get("model") or "?" for m in of("sound_event")}),
        "alerts": sorted({m.get("kind") for m in of("alert")}),
        "attention_models": sorted({m.get("attention_model") for m in of("perception_enrichment")}),
        "enrichments": len(of("perception_enrichment")),
        "heartbeat": sorted({(m.get("status"), m.get("engine")) for m in hb}),
        "heartbeat_detail": sorted({m.get("detail") for m in hb if m.get("detail")})[:4],
        "invalid": invalid,
        "log": list(dict.fromkeys(line for line in log.splitlines() if any(k in line for k in (
            "[drill]", "[attention]", "[enrich]", "[sounds]", "[speech] engine", "[gemini", "falling back", "[relay]",
            "Traceback", "Error"))))[:14],
    }
    if outage:
        down, up = outage
        before = [t for t, m in timed if t < down and m.get("type") != "heartbeat"]
        after = [t for t, m in timed if t > up and m.get("type") != "heartbeat"]
        s["relay_restart"] = {"outage_s": round(up - down, 1),
                              "first_event_after_s": round(after[0] - up, 1) if after else None,
                              "events_before": len(before), "events_after": len(after)}
    return s


def compare(base, s):
    """Events unchanged vs the clean run (whisper on the GPU is deterministic on a fixture)."""
    same = {k: base[k] == s[k] for k in ("finals", "sounds", "alerts")}
    return same


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("drills", nargs="*")
    p.add_argument("--list", action="store_true")
    p.add_argument("--fixture", default=FIXTURE)
    p.add_argument("--json", help="write the summaries here")
    a = p.parse_args()
    if a.list:
        for k, v in DRILLS.items():
            print(f"{k:<20} {v['why']}")
        return
    names = a.drills or list(DRILLS)
    unknown = [n for n in names if n not in DRILLS]
    if unknown:
        sys.exit(f"unknown drill(s) {unknown}; --list")
    if "baseline" not in names:
        names = ["baseline", *names]
    logdir = ROOT / "logs" / "drills"
    logdir.mkdir(parents=True, exist_ok=True)
    results = {}
    for n in names:
        print(f"=== {n}: {DRILLS[n]['why']}", flush=True)
        r = asyncio.run(run_drill(n, DRILLS[n], a.fixture, logdir))
        results[n] = r
        if "error" in r:
            print(f"  ERROR {r['error']}")
            continue
        if n != "baseline" and "error" not in results["baseline"]:
            r["unchanged"] = compare(results["baseline"], r)
        for k in ("sounds", "sound_models", "alerts", "attention_models", "enrichments", "heartbeat",
                  "heartbeat_detail", "invalid", "unchanged", "relay_restart"):
            if k in r:
                print(f"  {k:<17} {r[k]}")
        print(f"  {'finals':<17} {len(r['finals'])}")
        for line in r["log"]:
            print(f"  | {line[:150]}")
    if a.json:
        Path(a.json).write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

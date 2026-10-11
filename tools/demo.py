"""One-command demo stack (HERE_36hr_vibecode_plan/Kori/TASKS.md K3 E): bring up, reset, tear down.

    python tools/demo.py up                       # local relay + scripted events (fake) + dashboard + memory API
    python tools/demo.py up --source real         # ReSpeaker instead of the script (only when K1 isn't running it)
    python tools/demo.py up --source replay --fixture mock_scene
    python tools/demo.py up --scene all           # K1's scripted scenes (fixtures/events) on a loop
    python tools/demo.py up --cloud               # no local relay: everything talks to laptop/.env HERE_RELAY_URL
    python tools/demo.py up --no-mirror           # local relay only, even when laptop/.env names a cloud relay

The local relay is the reflex path. When laptop/.env's HERE_RELAY_URL is a cloud relay (wss://, Vultr), `up`
also mirrors the source to it (`--mirror`, laptop/client/fanout.py): the phone can use either relay and cloud
extras see the same stream, but the cloud being down or slow never touches the local path.
    python tools/demo.py reset                    # between judge groups: restart relay + dashboard, laptop stays warm
    python tools/demo.py reset --full             # restart everything (models reload)
    python tools/demo.py status
    python tools/demo.py down

Children run detached with their output in logs/demo/<name>.log; their pids are in logs/demo/pids.json.
The dashboard (K2) and memory API (A3) start only when their module exists (`python -m extras.dashboard`,
`python -m extras.memory`). After stopping a `--source real` laptop, the array's volatile settings are reset
(`laptop/audio/xvf3800.py --reset`). The relay token is passed to children through the environment, never printed.
"""

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from laptop import settings  # noqa: E402

# ---- tunables ---------------------------------------------------------------------------------------------
LOCAL_RELAY_PORT = 8080         # --relay-port
DASHBOARD_PORT = 8780           # --dashboard-port (passed to the dashboard as HERE_DASH_PORT)
MEMORY_PORT = 8790              # --memory-port: A3's memory API (INTEGRATION_CONTRACT 5.3), HERE_MEMORY_PORT/_URL
DEFAULT_FIXTURE = "mock_scene"
RESET_BUDGET_S = 30.0
START_TIMEOUT_S = 15.0         # relay /health up, or a client joined the session
LAPTOP_START_TIMEOUT_S = 90.0  # a --real / --replay laptop loads whisper + PANNs first (~35 s measured with the array)
STOP_GRACE_S = 3.0
STATE_DIR = ROOT / "logs" / "demo"
PIDS = STATE_DIR / "pids.json"
ORDER = ["relay", "memory", "dashboard", "source"]   # start order; stop is the reverse
# -----------------------------------------------------------------------------------------------------------

PY = sys.executable


def module_exists(dotted):
    return (ROOT / Path(*dotted.split(".")) / "__main__.py").exists()


def scene_exists(scene, root=ROOT):
    """K1's fixtures/events/<scene>.jsonl, or 'all' when there is at least one."""
    events = root / "fixtures" / "events"
    return any(events.glob("*.jsonl")) if scene == "all" else (events / f"{scene}.jsonl").exists()


def cloud_mirror(args):
    """The cloud relay to mirror the source to: laptop/.env HERE_RELAY_URL when it's remote (wss://), with a local
    relay as primary. None with --cloud (it is the primary then) or --no-mirror."""
    if args.cloud or getattr(args, "no_mirror", False):
        return None
    url = settings.load()["relay"]["url"]
    return url if url.startswith("wss://") else None


def commands(args, relay_url, session, mirror=None):
    """name -> argv for everything this run should start."""
    cmds = {}
    if not args.cloud:
        cmds["relay"] = [PY, "-m", "relay", "--host", "0.0.0.0", "--port", str(args.relay_port)]
    if module_exists("extras.memory"):
        cmds["memory"] = [PY, "-m", "extras.memory"]
    if module_exists("extras.dashboard"):
        cmds["dashboard"] = [PY, "-m", "extras.dashboard"]
    link = ["--relay", relay_url, "--session", session] + (["--mirror", mirror] if mirror else [])
    if args.source == "fake":
        scene = ["--scene", args.scene] if args.scene else []          # K1's fixtures/events scenes, or 'all'
        cmds["source"] = [PY, "tools/fake_events.py", *scene, "--loop", *link]
    elif args.source == "replay":
        cmds["source"] = [PY, "-m", "laptop", "--replay", f"fixtures/{args.fixture}", "--loop", *link]
    elif args.source == "real":
        cmds["source"] = [PY, "-m", "laptop", "--real", *link]
    return cmds


def relay_target(args):
    cfg = settings.load()["relay"]
    url = f"ws://localhost:{args.relay_port}/ws" if not args.cloud else cfg["url"]
    return url, args.session or cfg["session"], cfg["token"]


def health_url(relay_url):
    scheme = "https" if relay_url.startswith("wss://") else "http"
    netloc = relay_url.split("://", 1)[1].split("/", 1)[0].replace("localhost", "127.0.0.1")   # no IPv6 detour
    return f"{scheme}://{netloc}/health"


def get_json(url, timeout=2.0):
    try:
        return json.loads(urllib.request.urlopen(url, timeout=timeout).read())
    except Exception:
        return None


def lan_ip():
    """This laptop's address on the current network (what the phone dials for the local fallback relay)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))          # no packet is sent; just picks the outbound interface
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def port_busy(port):
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait(cond, timeout=START_TIMEOUT_S):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.2)
    return False


# ---- process bookkeeping ----------------------------------------------------------------------------------

def load_pids():
    try:
        return json.loads(PIDS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_pids(pids):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PIDS.write_text(json.dumps(pids, indent=1), encoding="utf-8")


def alive(pid):
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def spawn(name, argv, env):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log = open(STATE_DIR / f"{name}.log", "w", encoding="utf-8")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            creationflags=flags, start_new_session=os.name != "nt")
    return proc.pid


def kill(pid):
    if not alive(pid):
        return
    if os.name == "nt":                            # console Pythons ignore a polite taskkill; the array gets
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)   # its own reset in stop()
    else:
        os.killpg(pid, signal.SIGTERM)
        if not wait(lambda: not alive(pid), STOP_GRACE_S):
            os.killpg(pid, signal.SIGKILL)
    wait(lambda: not alive(pid), STOP_GRACE_S)


# ---- verbs ------------------------------------------------------------------------------------------------

def child_env(relay_url, session, token, ports, no_mirror=False):
    """The environment every started process gets. With --no-mirror, HERE_RELAY_MIRRORS is set empty: children
    read laptop/.env themselves (where deploy_vultr.py --save-token puts the cloud mirror) and existing vars win."""
    env = dict(os.environ, HERE_RELAY_URL=relay_url, HERE_SESSION=session, HERE_RELAY_TOKEN=token,
               HERE_DASH_PORT=str(ports["dashboard"]), HERE_MEMORY_PORT=str(ports["memory"]),
               HERE_MEMORY_URL=f"http://127.0.0.1:{ports['memory']}", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    if no_mirror:
        env["HERE_RELAY_MIRRORS"] = ""
    return env


def start(names, cmds, relay_url, session, token, ports, no_mirror=False):
    """Start `names` in ORDER. `ports` = {name: port} for the services that listen. Refuses to start a service
    whose port another process already holds (the source would talk to someone else's relay)."""
    env = child_env(relay_url, session, token, ports, no_mirror)
    pids = load_pids()
    health = health_url(relay_url)
    for name in ORDER:
        if name not in names or name not in cmds:
            continue
        if name in pids and alive(pids[name]["pid"]):
            print(f"  {name:<9} already running (pid {pids[name]['pid']})")
            continue
        if name in ports and port_busy(ports[name]):
            print(f"  {name:<9} NOT STARTED: port {ports[name]} is held by another process (--{name}-port)")
            if name == "relay":
                return False
            continue
        pid = spawn(name, cmds[name], env)
        pids[name] = {"pid": pid, "argv": cmds[name][1:]}
        save_pids(pids)
        if name == "relay":
            ok = wait(lambda: (get_json(health) or {}).get("ok") is True)
        elif name == "source":
            ok = wait(lambda: session in ((get_json(health) or {}).get("sessions") or {}) and
                      any(r in ("laptop", "tool") for r in get_json(health)["sessions"][session]),
                      LAPTOP_START_TIMEOUT_S if "laptop" in cmds[name] else START_TIMEOUT_S)
        elif name in ports:
            ok = wait(lambda: port_busy(ports[name]))
        else:
            ok = wait(lambda: alive(pid), 1.0)
        print(f"  {name:<9} {'up' if ok else 'NOT UP (see logs/demo/' + name + '.log)'}  pid {pid}"
              + (f"  :{ports[name]}" if name in ports else ""))
    for name in ("dashboard", "memory"):
        if name not in cmds:
            print(f"  {name:<9} skipped (no extras/{name}/__main__.py yet)")


def stop(names):
    pids = load_pids()
    for name in reversed(ORDER):
        if name not in names or name not in pids:
            continue
        info = pids.pop(name)
        kill(info["pid"])
        print(f"  {name:<9} stopped (pid {info['pid']})")
        if name == "source" and "--real" in info["argv"]:
            r = subprocess.run([PY, "laptop/audio/xvf3800.py", "--reset"], cwd=ROOT, capture_output=True, text=True)
            print(f"  array     {'reset' if r.returncode == 0 else 'RESET FAILED: ' + (r.stderr or r.stdout)[-200:]}")
        save_pids(pids)


def status(relay_url, session):
    pids = load_pids()
    for name in ORDER:
        if name in pids:
            state = "running" if alive(pids[name]["pid"]) else "DEAD"
            print(f"  {name:<9} {state}  pid {pids[name]['pid']}  {' '.join(pids[name]['argv'])}")
    h = get_json(health_url(relay_url))
    print(f"  relay     {health_url(relay_url)}: " + (f"session '{session}' = {(h.get('sessions') or {}).get(session, [])}"
                                                     if h else "unreachable"))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("verb", choices=["up", "down", "reset", "status"])
    p.add_argument("--source", choices=["fake", "replay", "real", "none"], default="fake")
    p.add_argument("--fixture", default=DEFAULT_FIXTURE, help="with --source replay: fixtures/<name>")
    p.add_argument("--scene", help="with --source fake: a fixtures/events scene, or 'all' (default: the core moments)")
    p.add_argument("--cloud", action="store_true", help="use HERE_RELAY_URL from laptop/.env, no local relay")
    p.add_argument("--no-mirror", action="store_true", help="don't mirror the source to the cloud relay")
    p.add_argument("--session")
    p.add_argument("--full", action="store_true", help="reset: restart the source too")
    p.add_argument("--relay-port", type=int, default=LOCAL_RELAY_PORT, help="local relay (reset/status: same as up)")
    p.add_argument("--dashboard-port", type=int, default=DASHBOARD_PORT)
    p.add_argument("--memory-port", type=int, default=MEMORY_PORT)
    args = p.parse_args()

    relay_url, session, token = relay_target(args)
    print(f"  token     {'set' if token else 'NOT SET (HERE_RELAY_TOKEN in laptop/.env): a local relay runs with auth OFF'}")
    print(f"  relay     {relay_url}  session '{session}'")
    mirror = cloud_mirror(args)
    if mirror:
        print(f"  mirror    {mirror}  (cloud copy for the extras and the phone's fallback; never on the local path)")
    cmds = commands(args, relay_url, session, mirror)
    everything = set(ORDER)
    ports = {"relay": args.relay_port, "dashboard": args.dashboard_port, "memory": args.memory_port}

    if args.verb == "up":
        if args.scene and args.source == "fake" and not scene_exists(args.scene):
            print(f"  source    no scene '{args.scene}' in fixtures/events (K1's scenes). Drop --scene for the core moments")
            sys.exit(1)
        if args.cloud and get_json(health_url(relay_url), timeout=5) is None:
            print(f"  relay     cloud relay UNREACHABLE ({health_url(relay_url)}). Fallback: `.\\tasks.ps1 demo-up` "
                  f"(local relay) and set the phone to ws://{lan_ip()}:{args.relay_port}/ws")
            sys.exit(1)
        if not args.cloud:
            print(f"  phone     ws://{lan_ip()}:{args.relay_port}/ws  (Settings > Relay, same session and token)")
        sys.exit(0 if start(everything, cmds, relay_url, session, token, ports, args.no_mirror) is not False else 1)
    elif args.verb == "down":
        stop(everything)
    elif args.verb == "status":
        status(relay_url, session)
    elif args.verb == "reset":
        t0 = time.time()
        names = everything if args.full else {"relay", "dashboard"}
        if args.full and "source" in load_pids():
            cmds["source"] = [PY, *load_pids()["source"]["argv"]]     # restart what was running, not the default
        stop(names)
        start(names, cmds, relay_url, session, token, ports, args.no_mirror)
        if not args.full and "source" in load_pids():               # the laptop reconnects on its own
            ok = wait(lambda: session in ((get_json(health_url(relay_url)) or {}).get("sessions") or {}))
            print(f"  source    {'reconnected' if ok else 'NOT reconnected'}")
        dt = time.time() - t0
        print(f"reset in {dt:.1f} s ({'OK' if dt <= RESET_BUDGET_S else 'OVER'} the {RESET_BUDGET_S:.0f} s budget)")


if __name__ == "__main__":
    main()

"""Deploy the relay (and optionally the memory API) to a Vultr Ubuntu 24.04 server in one command.

    python tools/deploy_vultr.py --ip 203.0.113.7                  # relay + Caddy (HTTPS) + firewall
    python tools/deploy_vultr.py --ip 203.0.113.7 --memory --save-token
    python tools/deploy_vultr.py --ip 203.0.113.7 --dry-run        # print every remote command, touch nothing
    python tools/deploy_vultr.py update --ip 203.0.113.7 [--memory] # re-ship the code, restart the services
    python tools/deploy_vultr.py status --ip 203.0.113.7

Automates relay/DEPLOY.md steps 3-8 and is safe to re-run (every step checks before it changes anything). Uses the
system `ssh` with `~/.ssh/here_vultr` (`--key` to override) as `root` (`--user`). The code goes over as
`git archive HEAD relay contracts extras/memory extras/cloud` (committed files only, never a `.env`).

Secrets never appear in output or on a command line:
- the relay token is generated on the server only if `/opt/here/relay/.env` has none, and is never printed;
  `--save-token` copies it into the local `laptop/.env` (HERE_RELAY_TOKEN) and adds `wss://HOST/ws` to
  HERE_RELAY_MIRRORS, so the laptop mirrors to the cloud while the local relay stays the live path;
- `--memory` sends NEON_DATABASE_URL from the local `extras/memory/.env` (if set) over ssh's stdin.
Stdlib only.
"""

import argparse
import json
import re
import shlex
import ssl
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REMOTE = "/opt/here"
SHIP = ["relay", "contracts", "extras/memory", "extras/cloud"]
DEFAULT_KEY = Path.home() / ".ssh" / "here_vultr"
LAPTOP_ENV = ROOT / "laptop" / ".env"
MEMORY_ENV = ROOT / "extras" / "memory" / ".env"


@dataclass
class Step:
    desc: str
    script: str                 # bash, run remotely as root
    stdin: bytes | None = None  # piped to the remote script (archives, secrets)
    stdin_label: str = ""       # what --dry-run shows instead of the stdin bytes


def sslip_host(ip):
    """203.0.113.7 -> 203-0-113-7.sslip.io (a free hostname, so Caddy can get a certificate)."""
    if not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip):
        raise ValueError(f"not an IPv4 address: {ip}")
    return ip.replace(".", "-") + ".sslip.io"


# ---- remote steps -------------------------------------------------------------------------------------------

def step_packages():
    return Step("packages, Caddy, firewall (22/80/443 only)", """set -e
export DEBIAN_FRONTEND=noninteractive
if ! command -v caddy >/dev/null; then
  apt-get update -q
  apt-get -y -q install python3-venv debian-keyring debian-archive-keyring apt-transport-https curl ufw gnupg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q && apt-get -y -q install caddy
fi
ufw allow OpenSSH >/dev/null && ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null && ufw --force enable >/dev/null
id here >/dev/null 2>&1 || useradd --system --create-home --home-dir /opt/here here
mkdir -p /var/log/here /opt/here/logs /opt/here/extras/memory/data && chown here:here /var/log/here /opt/here/logs
echo ok""")


def step_ship(archive: bytes):
    return Step("ship the code (git archive HEAD " + " ".join(SHIP) + ")",
                f"set -e\ntar -x -C {REMOTE}\nchown -R here:here {REMOTE}\necho ok",
                stdin=archive, stdin_label=f"<git archive HEAD {' '.join(SHIP)}: {len(archive)} bytes>")


# True on the server when a memory service was deployed earlier (so `update` without --memory still updates it).
MEMORY_INSTALLED = "[ -f /etc/systemd/system/here-memory.service ]"


def step_venv(memory):
    mem_reqs = f"-r {REMOTE}/extras/memory/requirements.txt"
    reqs = f"-r {REMOTE}/relay/requirements.txt" + (f" {mem_reqs}" if memory else "")
    return Step("Python venv + requirements", f"""set -e
[ -x {REMOTE}/.venv/bin/python ] || sudo -u here python3 -m venv {REMOTE}/.venv
sudo -u here {REMOTE}/.venv/bin/pip install -q --upgrade pip
sudo -u here {REMOTE}/.venv/bin/pip install -q {reqs}
{"" if memory else f"if {MEMORY_INSTALLED}; then sudo -u here {REMOTE}/.venv/bin/pip install -q {mem_reqs}; fi  # here-memory"}
echo ok""")


def step_token():
    # Generated on the server, only when missing; never echoed.
    return Step("relay token (kept if one exists; never printed)", f"""set -e
f={REMOTE}/relay/.env
if ! grep -qE '^HERE_RELAY_TOKEN=.+' "$f" 2>/dev/null; then
  umask 077
  printf 'HERE_RELAY_TOKEN=%s\\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')" > "$f"
  echo created
else
  echo kept
fi
chown here:here "$f" && chmod 600 "$f"
""")


def step_relay_service():
    return Step("relay service (systemd, 127.0.0.1:8080)", f"""set -e
cp {REMOTE}/relay/here-relay.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable here-relay >/dev/null 2>&1
systemctl restart here-relay
for i in $(seq 20); do curl -sf http://127.0.0.1:8080/health >/dev/null && break; sleep 0.5; done
curl -sf http://127.0.0.1:8080/health >/dev/null && echo ok""")


def step_caddy(host):
    return Step(f"Caddy HTTPS for {host}", f"""set -e
cp {REMOTE}/relay/Caddyfile /etc/caddy/Caddyfile
touch /etc/default/caddy && sed -i '/^HERE_HOST=/d' /etc/default/caddy && echo 'HERE_HOST={host}' >> /etc/default/caddy
mkdir -p /etc/systemd/system/caddy.service.d
printf '[Service]\\nEnvironmentFile=/etc/default/caddy\\n' > /etc/systemd/system/caddy.service.d/env.conf
systemctl daemon-reload && systemctl restart caddy
echo ok""")


def step_memory(neon_url):
    # stdin carries one line: the Neon URL (may be empty -> SQLite). The token is read from the relay's .env there.
    return Step("memory service (systemd, 127.0.0.1:8790, /api via Caddy)", f"""set -e
IFS= read -r NEON || true
# No Neon URL from this laptop: keep the one already on the server (re-deploying from another checkout must not
# silently move memory from Neon to SQLite).
[ -n "$NEON" ] || NEON=$(sed -n 's/^NEON_DATABASE_URL=//p' {REMOTE}/extras/memory/.env 2>/dev/null | head -n 1)
TOKEN=$(sed -n 's/^HERE_RELAY_TOKEN=//p' {REMOTE}/relay/.env)
umask 077
printf 'HERE_RELAY_URL=ws://127.0.0.1:8080/ws\\nHERE_RELAY_TOKEN=%s\\nHERE_SESSION=demo\\nNEON_DATABASE_URL=%s\\nHERE_RELAY_LOG=/var/log/here/relay.jsonl\\n' "$TOKEN" "$NEON" > {REMOTE}/extras/memory/.env
chown here:here {REMOTE}/extras/memory/.env && chmod 600 {REMOTE}/extras/memory/.env
chown -R here:here {REMOTE}/extras/memory/data {REMOTE}/logs
cp {REMOTE}/extras/memory/here-memory.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable here-memory >/dev/null 2>&1
systemctl restart here-memory
echo ok""", stdin=(neon_url + "\n").encode(), stdin_label="<NEON_DATABASE_URL: " + ("set, redacted>" if neon_url else
                                                                                   "empty -> SQLite>"))


def step_restart(memory):
    if memory:
        return Step("restart here-relay here-memory", "set -e\nsystemctl restart here-relay here-memory\necho ok")
    return Step("restart here-relay (+ here-memory if installed)",
                f"set -e\nsystemctl restart here-relay\nif {MEMORY_INSTALLED}; then systemctl restart here-memory; fi\n"
                "echo ok")


def step_status():
    return Step("service status", """for u in here-relay here-memory caddy; do printf '%-12s %s\\n' "$u" "$(systemctl is-active $u)"; done
printf 'relay /health  %s\\n' "$(curl -s http://127.0.0.1:8080/health | head -c 300)"
printf 'memory /health %s\\n' "$(curl -s http://127.0.0.1:8790/api/health | head -c 300)"
printf 'server status  %s\\n' "$(curl -s http://127.0.0.1:8790/api/status | head -c 600)"
printf 'token          %s\\n' "$(grep -qE '^HERE_RELAY_TOKEN=.+' /opt/here/relay/.env 2>/dev/null && echo set || echo MISSING)"
""")


def plan(command, host, memory, archive=b"", neon_url=""):
    """The ordered remote steps for `deploy` / `update` / `status`."""
    if command == "status":
        return [step_status()]
    if command == "update":
        return [step_ship(archive), step_venv(memory), step_restart(memory)]
    steps = [step_packages(), step_ship(archive), step_venv(memory), step_token(), step_relay_service(),
             step_caddy(host)]
    if memory:
        steps.append(step_memory(neon_url))
    return steps


# ---- local helpers ------------------------------------------------------------------------------------------

def read_env_value(path, key):
    """KEY=value from a .env file (trailing ' # comment' stripped); '' if missing."""
    if not Path(path).exists():
        return ""
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            value = re.split(r"\s+#", line.split("=", 1)[1], maxsplit=1)[0].strip()
            return "" if value.startswith("#") else value
    return ""


def update_laptop_env(path, token, mirror_url):
    """Set HERE_RELAY_TOKEN and add mirror_url to HERE_RELAY_MIRRORS (comma list, no duplicates).
    Creates the file from laptop/.env.example if missing. Never prints values."""
    path = Path(path)
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        example = path.with_name(".env.example")
        lines = example.read_text(encoding="utf-8").splitlines() if example.exists() else []
    mirrors = [m for m in read_env_value(path, "HERE_RELAY_MIRRORS").split(",") if m.strip()] if path.exists() else []
    if mirror_url not in mirrors:
        mirrors.append(mirror_url)
    want = {"HERE_RELAY_TOKEN": token, "HERE_RELAY_MIRRORS": ",".join(mirrors)}
    out, seen = [], set()
    for line in lines:
        key = line.split("=", 1)[0]
        if key in want and "=" in line:
            out.append(f"{key}={want[key]}")
            seen.add(key)
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in want.items() if k not in seen]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def ssh_base(args):
    return ["ssh", "-i", str(args.key), "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=15", f"{args.user}@{args.ip}"]


def remote_command(script):
    return "bash -c " + shlex.quote(script)


def git_archive():
    return subprocess.run(["git", "archive", "--format=tar", "HEAD", *SHIP], cwd=ROOT, capture_output=True,
                          check=True).stdout


def get(url, timeout=10):
    try:
        with urllib.request.urlopen(url, timeout=timeout, context=ssl.create_default_context()) as r:
            return r.status, r.read(400).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 - report, don't crash
        return None, f"{type(e).__name__}: {e}"


def health_checks(host, memory, tries=10):
    """https://HOST/health (and /api/health). Caddy needs a few seconds for the first certificate."""
    ok = True
    for path in ["/health"] + (["/api/health"] if memory else []):
        for i in range(tries):
            status, body = get(f"https://{host}{path}")
            if status == 200:
                break
            time.sleep(3)
        print(f"  https://{host}{path}: " + (f"{status} {body[:160]}" if status == 200 else f"FAILED ({body})"))
        ok &= status == 200
    return ok


def run_steps(steps, args):
    for i, s in enumerate(steps, 1):
        print(f"[{i}/{len(steps)}] {s.desc}")
        if args.dry_run:
            print("    $ " + " ".join(shlex.quote(a) for a in ssh_base(args)) + " <<script>>")
            print("    " + s.script.replace("\n", "\n    "))
            if s.stdin_label:
                print(f"    stdin: {s.stdin_label}")
            continue
        r = subprocess.run(ssh_base(args) + [remote_command(s.script)], input=s.stdin or b"", capture_output=True)
        out = r.stdout.decode("utf-8", "replace").strip()
        if r.returncode != 0:
            err = r.stderr.decode("utf-8", "replace").strip()
            print(f"    FAILED (exit {r.returncode})\n    {out[-800:]}\n    {err[-800:]}")
            return False
        if out:
            print("    " + out[-1200:].replace("\n", "\n    "))
    return True


def fetch_token(args):
    r = subprocess.run(ssh_base(args) + [f"sed -n 's/^HERE_RELAY_TOKEN=//p' {REMOTE}/relay/.env"], capture_output=True)
    return r.stdout.decode().strip() if r.returncode == 0 else ""


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("command", nargs="?", default="deploy", choices=["deploy", "update", "status"])
    p.add_argument("--ip", required=True, help="the server's public IPv4")
    p.add_argument("--host", help="hostname pointing at the server (default: <ip-with-dashes>.sslip.io)")
    p.add_argument("--memory", action="store_true", help="also run the memory API (extras/memory) behind /api")
    p.add_argument("--memory-env", type=Path, default=MEMORY_ENV,
                   help="local .env to take NEON_DATABASE_URL from (default extras/memory/.env)")
    p.add_argument("--key", type=Path, default=DEFAULT_KEY, help=f"ssh private key (default {DEFAULT_KEY})")
    p.add_argument("--user", default="root")
    p.add_argument("--dry-run", action="store_true", help="print every remote command (secrets redacted), run nothing")
    p.add_argument("--save-token", "--show-token", dest="save_token", action="store_true",
                   help="write the token into laptop/.env and add wss://HOST/ws to HERE_RELAY_MIRRORS (never printed)")
    args = p.parse_args(argv)
    host = args.host or sslip_host(args.ip)
    url = f"wss://{host}/ws"

    archive = b"" if args.command == "status" or args.dry_run else git_archive()
    neon = read_env_value(args.memory_env, "NEON_DATABASE_URL") if args.memory and args.command == "deploy" else ""
    print(f"{args.command} -> {args.user}@{args.ip}  host {host}  memory {'on' if args.memory else 'off'}"
          + ("  (dry run)" if args.dry_run else ""))
    if not run_steps(plan(args.command, host, args.memory, archive, neon), args):
        return 1
    if args.dry_run:
        print(f"\nThen: health checks on https://{host}/health" + (" and /api/health" if args.memory else "")
              + (f"; laptop/.env gets the token and HERE_RELAY_MIRRORS += {url}" if args.save_token else ""))
        return 0
    if args.command != "status" and not health_checks(host, args.memory):
        print("Health check failed: `python tools/deploy_vultr.py status --ip ...`, then on the server "
              "`journalctl -u here-relay -u caddy -n 50`.")
        return 1
    if args.save_token:
        token = fetch_token(args)
        if not token:
            print("Couldn't read the token from the server; laptop/.env unchanged.")
            return 1
        update_laptop_env(LAPTOP_ENV, token, url)
        print(f"laptop/.env: HERE_RELAY_TOKEN set (not shown), HERE_RELAY_MIRRORS includes {url}")
    print(f"\nRelay: {url}   phone: Settings -> Relay -> that URL + the token from laptop/.env, session demo.")
    print(f"Latency: .\\tasks.ps1 latency --relay {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

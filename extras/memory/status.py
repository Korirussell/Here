"""Server status for Vultr: one read-only view of the relay, the memory store and the insights job.

    GET /api/status        {ok, t, relay:{health, log}, memory:{...}, insights:{...}}
    GET /api/status.html   the same, as a small self-refreshing page

The relay stays a dumb router: nothing here touches relay.py. Its numbers come from `/health` (HTTP, 1 s timeout)
and from the tail of its JSON-lines log (`--log`, /var/log/here/relay.jsonl on Vultr), read at most once per
`STATUS_CACHE_S`. Every part fails on its own: a missing log or a stopped relay gives `null` plus a reason, never
an error page.
"""

import json
import os
import threading
import time
import urllib.request
from pathlib import Path

from . import config

_cache = {"t": 0.0, "body": None}
_lock = threading.Lock()


def percentile(values, p):
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 1)


def tail_lines(path, max_bytes):
    """The last complete lines of a file, reading at most `max_bytes` from its end."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        data = f.read()
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[1:] if size > max_bytes else lines        # the first line may be cut


def log_stats(path, window_s=None, now=None, max_bytes=None):
    """Per-type message counts and rates over the last `window_s`, and the age of each laptop message when the
    relay got it (`rx - t`, ms: laptop clock vs server clock, so it includes any clock offset; the trend matters)."""
    window_s = window_s or config.STATUS_WINDOW_S
    max_bytes = max_bytes or config.STATUS_TAIL_BYTES
    now = time.time() if now is None else now
    path = Path(path)
    if not path.exists():
        return {"ok": False, "reason": f"no relay log at {path}"}
    types, ages, events, roles = {}, [], {"connect": 0, "disconnect": 0, "bad_message": 0}, {}
    last_rx = None
    for line in tail_lines(path, max_bytes):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if "event" in e:
            if e["event"] in events:
                events[e["event"]] += 1
            continue
        rx = e.get("rx")
        if not isinstance(rx, (int, float)) or rx < now - window_s:
            continue
        last_rx = max(last_rx or rx, rx)
        types[e.get("type", "?")] = types.get(e.get("type", "?"), 0) + 1
        roles[e.get("from", "?")] = roles.get(e.get("from", "?"), 0) + 1
        sender = e.get("src") or e.get("from")         # envelope src: a scripted source joins as role tool
        if sender == "laptop" and isinstance(e.get("t"), (int, float)) and e.get("type") != "heartbeat":
            ages.append((rx - e["t"]) * 1000)
    return {"ok": True, "window_s": window_s,
            "messages": sum(types.values()),
            "by_type": {k: {"count": n, "per_s": round(n / window_s, 2)} for k, n in sorted(types.items())},
            "by_role": roles,
            "laptop_age_ms": {"p50": percentile(ages, 0.5), "p95": percentile(ages, 0.95), "n": len(ages)},
            "events_in_tail": events,
            "last_message_s_ago": round(now - last_rx, 1) if last_rx else None}


def relay_health(url=None, timeout=1.0):
    url = url or config.env("HERE_RELAY_HEALTH", config.RELAY_HEALTH_URL)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:                                  # relay down or elsewhere: say so, don't fail
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"[:200]}


def insights_status(memory):
    by_source = {}
    for r in memory.store.insights():
        s = by_source.setdefault(r["source"], {"count": 0, "computed_at": None})
        s["count"] += 1
        if r.get("computed_at"):
            s["computed_at"] = max(s["computed_at"] or 0, r["computed_at"])
    return {"serving": "databricks" if "databricks" in by_source else ("precomputed" if by_source else None),
            "by_source": by_source}


def build(memory, now=None):
    now = time.time() if now is None else now
    log_path = config.env("HERE_RELAY_LOG", str(config.RELAY_LOG))
    parts = {}
    for name, fn in (("health", relay_health), ("log", lambda: log_stats(log_path, now=now))):
        try:
            parts[name] = fn()
        except Exception as e:
            parts[name] = {"ok": False, "reason": type(e).__name__}
    try:
        mem = memory.health()
    except Exception as e:
        mem = {"ok": False, "reason": type(e).__name__}
    try:
        ins = insights_status(memory)
    except Exception as e:
        ins = {"serving": None, "reason": type(e).__name__}
    ok = bool(parts["health"].get("ok")) and bool(mem.get("ok"))
    return {"ok": ok, "t": round(now, 3), "relay": parts, "memory": mem, "insights": ins}


def status(memory):
    """Cached for STATUS_CACHE_S so a refreshing page never re-reads the log on every request."""
    with _lock:
        if _cache["body"] is None or time.time() - _cache["t"] > config.STATUS_CACHE_S:
            _cache["body"], _cache["t"] = build(memory), time.time()
        return _cache["body"]


PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>HERE server status</title>
<style>body{font:14px system-ui,sans-serif;margin:16px;background:#fff;color:#111}
@media (prefers-color-scheme:dark){body{background:#111;color:#eee}}
pre{white-space:pre-wrap;word-break:break-word}.ok{color:#1a7f37}.bad{color:#cf222e}</style>
<h1>HERE server status <span id="s"></span></h1><pre id="o">loading...</pre>
<script>
const q = location.search;
async function tick(){
  try {
    const r = await fetch("status" + q); const j = await r.json();
    document.getElementById("s").textContent = j.ok ? "OK" : "DEGRADED";
    document.getElementById("s").className = j.ok ? "ok" : "bad";
    document.getElementById("o").textContent = JSON.stringify(j, null, 2);
  } catch (e) { document.getElementById("o").textContent = "unreachable: " + e; }
}
tick(); setInterval(tick, 5000);
</script></html>"""

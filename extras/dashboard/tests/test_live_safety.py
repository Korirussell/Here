"""The dashboard during the real demo: Run demo never injects scripted events into a relay a live laptop is using,
a slow memory API is reported (not silently replaced by the seed or marked down), and the stand-in phone finds the
relay token where every other tool does."""

import asyncio
import http.server
import json
import threading
import time

from extras.dashboard import fallback, settings as S
from extras.dashboard.tests.test_dashboard import TOKEN, run, stack


def laptop_heartbeat(src="laptop"):
    return json.dumps({"type": "heartbeat", "v": 3, "src": src, "t": time.time(), "status": "ok",
                       "engine": "faster_whisper", "doa_source": "builtin"})


def test_run_demo_with_a_live_laptop_stays_on_this_screen():
    async def main():
        async with stack() as (dash, rport, dport):
            assert dash.relay_connected
            dash.on_relay_text(laptop_heartbeat())
            r = dash.demo_action({"moment": "1"})
            try:
                assert r["ok"] and r["local"] and dash.demo is None          # no fake_events on the relay
                assert "laptop" in r["why_local"]
            finally:
                dash.stop_demo()
            r = dash.demo_action({"moment": "1", "force_relay": "1"})      # deliberate: still possible
            try:
                assert r["ok"] and not r.get("local") and dash.demo is not None
            finally:
                dash.stop_demo()
    run(main())


def test_run_demo_without_a_laptop_still_uses_the_relay():
    async def main():
        async with stack() as (dash, rport, dport):
            dash.on_relay_text(laptop_heartbeat(src="tool"))               # fake_events' own heartbeat
            r = dash.demo_action({"moment": "1"})
            try:
                assert r["ok"] and not r.get("local") and dash.demo is not None
            finally:
                dash.stop_demo()
    run(main())


def test_stale_laptop_heartbeat_does_not_block_the_relay():
    async def main():
        async with stack() as (dash, rport, dport):
            dash.on_relay_text(laptop_heartbeat())
            dash.laptop_seen -= S.PAGE["heartbeat_stale_s"] + 1
            r = dash.demo_action({"moment": "1"})
            try:
                assert r["ok"] and not r.get("local")
            finally:
                dash.stop_demo()
    run(main())


class SlowAsk(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        time.sleep(1.0)                                                    # slower than the dashboard waits
        try:
            self.reply({"answer": "late", "evidence": [], "cached": False})
        except OSError:
            pass

    def do_GET(self):
        self.reply({"insights": [{"id": "i1", "title": "t", "text": "x", "source": "precomputed"}],
                    "source": "precomputed"})


def test_slow_memory_api_is_reported_and_not_marked_down(monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SlowAsk)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(S, "MEMORY_TIMEOUT_S", 0.3)
        m = fallback.Memory(f"http://127.0.0.1:{srv.server_address[1]}")
        out = m.call("ask", {"q": "What happened at lunch?"})
        assert out["_source"] == "seed" and "slow" in out["_fallback_reason"]
        ins = m.call("insights", {})
        assert ins["_source"] == "api"                                     # one slow Ask doesn't take the API down
    finally:
        srv.shutdown()


def test_fake_phone_reads_the_token_from_laptop_env(monkeypatch, tmp_path):
    from extras.dashboard import fake_phone
    monkeypatch.delenv("HERE_RELAY_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text("HERE_RELAY_TOKEN=from-dotenv   # comment\n", encoding="utf-8")
    monkeypatch.setattr(fake_phone, "ENV_FILE", env)
    assert fake_phone.relay_token() == "from-dotenv"
    monkeypatch.setenv("HERE_RELAY_TOKEN", "from-env")
    assert fake_phone.relay_token() == "from-env"                          # the environment still wins

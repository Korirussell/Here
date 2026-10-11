"""server status, Object Storage signing and session bundles. No network beyond localhost.
Run: python -m pytest extras/cloud"""

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from extras.cloud import export_bundle, s3
from extras.memory import status
from extras.memory.api import make_server
from extras.memory.ingest import Ingestor
from extras.memory.memory import Memory
from extras.memory.store import open_sqlite


def quiet(*_):
    pass


def utt(utt_id, t, session="live1", text="the exam moved to Friday"):
    return {"type": "utterance", "v": 3, "src": "laptop", "t": t + 1, "session": session, "utt_id": utt_id,
            "t_start": t, "t_end": t + 1, "text": text, "pending": "", "final": True, "bearing_deg": 170.0,
            "bearing_spread_deg": 3.0, "source_id": 0, "overlap": False, "tone": "neutral", "tone_confidence": 0.5,
            "loudness": 0.5, "engine": "faster_whisper"}


@pytest.fixture(scope="module")
def mem():
    m = Memory(store=open_sqlite(":memory:"), log=quiet)
    m.seed()
    ing = Ingestor(m.store, log=quiet)
    now = time.time()
    for i in range(3):
        ing.accept(utt(f"u-{i}", now - 60 + i * 5))
    ing.flush_all()
    return m


# ---- SigV4: AWS's published examples ------------------------------------------------------------------

def test_signing_key_matches_the_aws_example():
    k = s3.signing_key("wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "20120215", "us-east-1", "iam")
    assert k.hex() == "f4780e2d9f65fa895f9c67b32ce1baf0b0d8a43505a000a1a9e090d414db404d"


def test_get_object_signature_matches_the_s3_docs_example():
    h = s3.sign("GET", "https://examplebucket.s3.amazonaws.com/test.txt", {"Range": "bytes=0-9"}, s3.EMPTY_SHA256,
                "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "us-east-1", "20130524T000000Z")
    assert h["authorization"].endswith("Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")
    assert "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date" in h["authorization"]


def test_from_env_needs_all_four(monkeypatch):
    for k in ("ENDPOINT", "BUCKET", "ACCESS_KEY", "SECRET_KEY"):
        monkeypatch.delenv(f"VULTR_S3_{k}", raising=False)
    assert s3.S3.from_env() is None
    monkeypatch.setenv("VULTR_S3_ENDPOINT", "ewr1.vultrobjects.com")
    monkeypatch.setenv("VULTR_S3_BUCKET", "b")
    monkeypatch.setenv("VULTR_S3_ACCESS_KEY", "a")
    assert s3.S3.from_env() is None
    monkeypatch.setenv("VULTR_S3_SECRET_KEY", "s")
    assert s3.S3.from_env().url("x/y.json") == "https://ewr1.vultrobjects.com/b/x/y.json"


# ---- a fake bucket that checks every signature --------------------------------------------------------

class FakeBucket:
    def __init__(self, fail=False):
        self.objects, self.order, self.fail = {}, [], fail
        bucket = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_PUT(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                url = f"http://{self.headers['Host']}{self.path}"
                digest = hashlib.sha256(body).hexdigest()
                expect = s3.sign("PUT", url, {"content-type": self.headers["Content-Type"]}, digest, "AK", "SK",
                                 "us-east-1", self.headers["x-amz-date"])["authorization"]
                ok = (not bucket.fail and self.headers["Authorization"] == expect
                      and self.headers["x-amz-content-sha256"] == digest)
                if ok:
                    bucket.objects[self.path] = body
                    bucket.order.append(self.path)
                self.send_response(200 if ok else 403)
                self.send_header("Content-Length", "0")
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.client = s3.S3(f"http://127.0.0.1:{self.server.server_address[1]}", "here", "AK", "SK", timeout=5)

    def close(self):
        self.server.shutdown()


def test_put_is_signed_and_lands():
    b = FakeBucket()
    try:
        assert b.client.put("a/b c.json", b'{"x": 1}', "application/json") == 200
        assert b.objects["/here/a/b%20c.json"] == b'{"x": 1}'
    finally:
        b.close()


# ---- session bundles ---------------------------------------------------------------------------------

def test_bundle_is_text_only_with_checksums(mem, tmp_path):
    folder, m = export_bundle.build_bundle(mem, "live1", tmp_path, now=1_800_000_000)
    assert export_bundle.live_sessions(mem) == ["live1"]
    assert m["events"] == 3 and m["types"] == ["utterance"] and m["audio"] is False and not m["seeded"]
    assert m["contains"] == ["text", "metadata"]
    for name, info in m["files"].items():
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == info["sha256"]
    rows = [json.loads(line) for line in (folder / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {r["session"] for r in rows} == {"live1"} and all(r["payload"]["type"] == "utterance" for r in rows)
    assert json.loads((folder / "manifest.json").read_text(encoding="utf-8")) == m
    assert json.loads((folder / "insights.json").read_text(encoding="utf-8"))["insights"]


def test_upload_puts_the_manifest_last(mem, tmp_path):
    folder, _ = export_bundle.build_bundle(mem, "live1", tmp_path)
    b = FakeBucket()
    try:
        keys = export_bundle.upload_bundle(b.client, folder, "live1")
        assert [k.rsplit("/", 1)[1] for k in keys] == ["events.jsonl", "insights.json", "manifest.json"]
        assert b.order[-1].endswith("/manifest.json") and len(b.objects) == 3
    finally:
        b.close()


def test_failed_upload_keeps_the_local_copy(mem, tmp_path):
    folder, _ = export_bundle.build_bundle(mem, "live1", tmp_path)
    b = FakeBucket(fail=True)
    try:
        with pytest.raises(urllib.error.HTTPError):
            export_bundle.upload_bundle(b.client, folder, "live1")
        assert (folder / "manifest.json").exists()
    finally:
        b.close()


# ---- server status -----------------------------------------------------------------------------------

def write_log(path, now):
    lines = ['{"cut line', json.dumps({"event": "connect", "session": "demo", "role": "laptop"})]
    for i in range(10):          # 10 utterances, laptop -> relay age 10..100 ms
        lines.append(json.dumps({"rx": now - 30 + i, "from": "laptop", "type": "utterance",
                                 "t": now - 30 + i - (i + 1) / 100}))
    lines.append(json.dumps({"rx": now - 5, "from": "laptop", "type": "heartbeat", "t": now - 9}))    # not an age
    lines.append(json.dumps({"rx": now - 6, "from": "tool", "src": "laptop", "type": "alert", "t": now - 6.05}))
    lines.append(json.dumps({"rx": now - 4, "from": "phone", "type": "scene", "t": now - 4}))
    lines.append(json.dumps({"rx": now - 9999, "from": "laptop", "type": "sources", "t": now - 9999}))  # too old
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_log_stats_rates_and_ages(tmp_path):
    now = 1_800_000_000.0
    log = tmp_path / "relay.jsonl"
    write_log(log, now)
    s = status.log_stats(log, window_s=300, now=now)
    assert s["ok"] and s["messages"] == 13
    assert s["by_type"]["utterance"] == {"count": 10, "per_s": 0.03} and "sources" not in s["by_type"]
    assert s["by_role"] == {"laptop": 11, "phone": 1, "tool": 1}
    assert s["laptop_age_ms"]["n"] == 11 and 50 <= s["laptop_age_ms"]["p50"] <= 60
    assert 90 <= s["laptop_age_ms"]["p95"] <= 100
    assert s["events_in_tail"]["connect"] == 1 and s["last_message_s_ago"] == 4.0


def test_log_tail_drops_the_cut_first_line(tmp_path):
    log = tmp_path / "relay.jsonl"
    log.write_text("aaaa\n" + "\n".join(json.dumps({"rx": 1.0, "type": "x"}) for _ in range(50)) + "\n")
    lines = status.tail_lines(log, 200)
    assert lines and all(line.startswith("{") for line in lines)


def test_status_survives_no_relay_and_no_log(mem, tmp_path, monkeypatch):
    monkeypatch.setenv("HERE_RELAY_LOG", str(tmp_path / "missing.jsonl"))
    monkeypatch.setenv("HERE_RELAY_HEALTH", "http://127.0.0.1:9/health")
    s = status.build(mem)
    assert s["ok"] is False and s["relay"]["health"]["ok"] is False
    assert s["relay"]["log"]["ok"] is False and "no relay log" in s["relay"]["log"]["reason"]
    assert s["memory"]["ok"] and s["insights"]["serving"] == "precomputed"


def test_status_endpoint_and_page_are_token_gated(mem, tmp_path, monkeypatch):
    monkeypatch.setenv("HERE_RELAY_TOKEN", "sekret")
    monkeypatch.setenv("HERE_RELAY_LOG", str(tmp_path / "relay.jsonl"))
    monkeypatch.setenv("HERE_RELAY_HEALTH", "http://127.0.0.1:9/health")
    write_log(tmp_path / "relay.jsonl", time.time())
    status._cache["body"] = None
    server = make_server(mem, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def get(path, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.headers.get("Content-Type"), r.read()
        except urllib.error.HTTPError as e:
            return e.code, None, b""

    try:
        code, _, body = get("/api/status")
        j = json.loads(body)
        assert code == 200 and j["relay"]["log"]["by_type"]["utterance"]["count"] == 10
        assert j["memory"]["db"] == "local"
        proxied = {"X-Forwarded-For": "203.0.113.9"}
        assert get("/api/status", proxied)[0] == 401
        assert get("/api/status.html", proxied)[0] == 401
        code, ctype, body = get("/api/status.html?token=sekret", proxied)
        assert code == 200 and ctype.startswith("text/html") and b"HERE server status" in body
    finally:
        server.shutdown()
        status._cache["body"] = None


def test_keys_needing_escapes_are_encoded_once_in_the_signature(monkeypatch):
    # S3 canonical URIs are URI-encoded once: the URL path is already encoded, so "a b" must sign as "a%20b".
    seen = []
    real = hashlib.sha256

    def spy(data=b""):
        seen.append(data)
        return real(data)
    monkeypatch.setattr(s3.hashlib, "sha256", spy)
    client = s3.S3("http://127.0.0.1:1", "bucket", "AK", "SK")
    s3.sign("PUT", client.url("live demo/a:b.json"), {}, s3.EMPTY_SHA256, "AK", "SK", amz_date="20261010T000000Z")
    canonical = next(d.decode() for d in seen if d.startswith(b"PUT\n"))
    assert canonical.split("\n")[1] == "/bucket/live%20demo/a%3Ab.json"

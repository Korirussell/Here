"""HERE World dashboard: seed shapes, offline replay / Ask, and relay -> page end to end. No network beyond 127.0.0.1."""

import asyncio
import contextlib
import json
import time
import urllib.request

import pytest
from websockets.asyncio.client import connect

from contracts.python.here_contract import errors
from extras.dashboard import fallback, settings as S
from extras.dashboard.server import Dashboard, run_dashboard
from relay.relay import serve_relay

TOKEN = "test-token"
STORED = {"person_label", "location_id", "seeded"}     # §5.3 stored-event metadata


@pytest.fixture(scope="module")
def seed():
    return fallback.Seed(S.DEMO_DATA)                  # ours, pinned: A3's bundle wins by default once merged


def a3_bundle(folder):
    """Our seed rewritten in A3's snapshot shapes (extras/memory/seed/api/): location ids + names, Ask keyed by
    question with full evidence, `note` instead of `label`."""
    ours = fallback.Seed(S.DEMO_DATA)
    raw = json.loads((S.DEMO_DATA / "day.json").read_text(encoding="utf-8"))
    names = sorted({g["location"] for g in raw["segments"]})
    ids = {n: f"loc_{i}" for i, n in enumerate(names)}
    day = {"date": raw["date"], "dates": [raw["date"]], "seeded": True, "note": "SEEDED synthetic day",
           "locations": [{"id": ids[n], "name": n} for n in names],
           "segments": [{**g, "location": ids[g["location"]]} for g in raw["segments"]], "events": raw["events"]}
    ask = {a["q"]: {"answer": a["answer"], "evidence": [fallback.evidence(ours.by_id[i]) for i in a["evidence_ids"]],
                    "cached": True} for a in ours.ask}
    folder.mkdir(parents=True, exist_ok=True)
    for name, body in (("day", day), ("ask", ask), ("insights", ours.insights)):
        (folder / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")
    return folder


def test_seeded_day_uses_canonical_shapes(seed):
    events = seed.day["events"]
    assert len(events) > 100 and seed.day["segments"]
    bad = [(e["type"], errors({k: v for k, v in e.items() if k not in STORED})) for e in events]
    assert [b for b in bad if b[1]] == []
    assert any(e.get("label") == "unknown" and e.get("bearing_deg") is not None for e in events)


def test_seeded_answers_and_insights_point_at_real_events(seed):
    for a in seed.ask:
        assert a["evidence_ids"] and all(i in seed.by_id for i in a["evidence_ids"]), a["q"]
    for i in seed.insights["insights"]:
        assert all(x in seed.by_id for x in i["evidence_ids"]), i["id"]


def test_replay_focus_mode_keeps_important_alerts(seed):
    for seg in seed.day["segments"]:
        base = fallback.replay(seed, seg["id"], {})
        focus = fallback.replay(seed, seg["id"], {"focus_mode": True, "sensitivity": 0.2})
        important = {d["event_id"] for d in base["decisions"] if d["important"]}
        assert important == {d["event_id"] for d in focus["decisions"] if d["important"] and d["surfaced"]}
        assert focus["surfaced"] <= base["surfaced"]
        assert base["surfaced"] + base["suppressed"] == len(base["decisions"])
        assert sum(c["surfaced"] for c in base["by_category"].values()) == base["surfaced"]


def test_replay_sensitivity_is_monotonic_and_unknowns_keep_direction(seed):
    seg = "library"
    counts = [fallback.replay(seed, seg, {"sensitivity": s})["surfaced"] for s in (0.0, 0.3, 0.6, 1.0)]
    assert counts == sorted(counts)
    low = fallback.replay(seed, seg, {"sensitivity": 0.0})
    hidden_unknowns = [d for d in low["decisions"] if d["category"] == "unknown" and not d["surfaced"]]
    assert hidden_unknowns and all("direction" in d["reason"] for d in hidden_unknowns)
    assert all(d["bearing_deg"] is not None for d in hidden_unknowns)


def test_replay_crowd_filter_hides_background_talk(seed):
    off = fallback.replay(seed, "lunch", {"crowd_filter": 0.0, "sensitivity": 1.0})
    on = fallback.replay(seed, "lunch", {"crowd_filter": 1.0, "sensitivity": 1.0})
    assert on["by_category"]["speech"]["surfaced"] < off["by_category"]["speech"]["surfaced"]
    assert "error" in fallback.replay(seed, "nope", {})


def test_ask_seeded_answer_then_keyword_fallback(seed):
    r = fallback.ask(seed, "What did Maya say about the fair?")
    assert r["cached"] and "Friday" in r["answer"] and r["evidence"]
    assert all(("utt_id" in e) or ("event_id" in e) for e in r["evidence"])
    k = fallback.ask(seed, "pizza")
    assert k["_mode"] == "keyword search" and k["evidence"]
    assert fallback.ask(seed, "zebra quantum")["evidence"] == []


def test_memory_falls_back_quickly_when_api_down():
    m = fallback.Memory("http://127.0.0.1:9")            # nothing listens on the discard port
    t0 = time.time()
    h = m.call("health", {})
    assert h["_source"] == "seed" and h["db"] == "seed"
    assert m.call("day", {})["_source"] == "seed"
    r = m.call("replay", {"segment_id": "lunch", "policy": json.dumps({"focus_mode": True})})
    assert r["_source"] == "seed" and r["policy"]["focus_mode"] is True
    assert m.call("nope", {}) is None
    assert time.time() - t0 < 2.0


def test_seed_bundle_is_never_mixed(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "MEMORY_SEED", tmp_path / "memory_seed")
    (tmp_path / "memory_seed").mkdir()
    (tmp_path / "memory_seed" / "insights.json").write_text('{"insights": [{"evidence_ids": ["seed-x"]}]}')
    assert fallback.seed_folder() == S.DEMO_DATA      # A3's loose insights.json alone doesn't count
    a3_bundle(tmp_path / "memory_seed" / "api")
    assert fallback.seed_folder() == tmp_path / "memory_seed" / "api"


def test_a3_shapes_normalize_to_the_page_shape(tmp_path):
    a3 = fallback.Seed(a3_bundle(tmp_path / "api"))
    ours = fallback.Seed(S.DEMO_DATA)
    assert [g["location"] for g in a3.day["segments"]] == [g["location"] for g in ours.day["segments"]]
    assert a3.day["label"] and a3.questions == [a["q"] for a in ours.ask]
    for q in a3.questions:                              # every suggestion answers itself, with real evidence
        r = fallback.ask(a3, q)
        assert r["cached"] and r["evidence"] and all(fallback.event_id(e) in a3.by_id for e in r["evidence"]), q
    assert fallback.replay(a3, "lunch", {})["surfaced"] == fallback.replay(ours, "lunch", {})["surfaced"]


def test_a3_replay_is_joined_with_its_own_day(seed):
    lunch = fallback.replay(seed, "lunch", {})["decisions"]
    up = {"segment_id": "lunch", "policy": {"crowd_filter": True}, "surfaced": 1, "suppressed": len(lunch) - 1,
          "kept_important": 1, "missed_important": 0, "by_category": {"crowd": {"surfaced": 0, "suppressed": 9}},
          "decisions": [{"event_id": d["event_id"], "type": "x", "category": "crowd" if i else "name_called",
                         "t": d["t"], "text": "", "surfaced": i == 0, "important": i == 0, "reason": "r", "score": 0.4}
                        for i, d in enumerate(lunch)]}
    out = fallback.join_replay(seed, up, {"focus_mode": True})
    assert {d["category"] for d in out["decisions"]} <= {"speech", "alert", "hazard", "sound", "unknown", "media"}
    assert sum(c["surfaced"] + c["suppressed"] for c in out["by_category"].values()) == len(lunch)
    assert all(d["model_score"] == 0.4 and "bearing_deg" in d for d in out["decisions"])
    assert out["policy"]["focus_mode"] is True and out["api_policy"] == {"crowd_filter": True}
    assert fallback.a3_policy({}) == {"sensitivity": 0.6, "focus_mode": False, "crowd_filter": True,
                                      "hazard_priority": True, "speech_priority": False}


@pytest.mark.skipif(not (S.MEMORY_SEED / "api" / "day.json").exists(), reason="A3's snapshots not merged yet")
def test_a3_snapshots_are_coherent_for_the_page():
    a3 = fallback.Seed(S.MEMORY_SEED / "api")
    assert a3.questions and all(g["location"] for g in a3.day["segments"])
    for q in a3.questions:
        r = fallback.ask(a3, q)
        assert r["cached"] and all(fallback.event_id(e) in a3.by_id for e in r["evidence"]), q
    for i in a3.insights["insights"]:
        assert all(x in a3.by_id for x in i.get("evidence_ids") or []), i["id"]
    for g in a3.day["segments"]:
        r = fallback.replay(a3, g["id"], {})
        assert r["surfaced"] + r["suppressed"] == len(r["decisions"]) > 0, g["id"]


def test_seeded_day_is_shown_on_today_but_live_events_are_not_moved(monkeypatch):
    import datetime as dt
    monkeypatch.setattr(S, "SEED_AS_TODAY", True)
    m = fallback.Memory("http://127.0.0.1:9", today=dt.date(2026, 10, 11))
    m.seed = fallback.Seed(S.DEMO_DATA)
    day = m.call("day", {})
    assert day["date"] == "2026-10-11" and day["seeded_date"] == "2026-10-09"
    first = dt.datetime.fromtimestamp(day["segments"][0]["start"])
    orig = dt.datetime.fromtimestamp(m.seed.day["segments"][0]["start"])
    assert first.date() == dt.date(2026, 10, 11) and first.time() == orig.time()
    ev = m.call("ask", {"q": "When was my name called?"})["evidence"]
    assert all(dt.datetime.fromtimestamp(e["t"]).date() == dt.date(2026, 10, 11) for e in ev)
    live = {"utt_id": "live-1", "t": 1.0}
    assert fallback.shift_times([live], 86400, only=m.seed.by_id) == [live]
    monkeypatch.setattr(S, "SEED_AS_TODAY", False)
    assert m.call("day", {})["date"] == "2026-10-09"


def test_bridge_ignores_garbage_and_caps_history():
    d = Dashboard("ws://127.0.0.1:1/ws", "demo", "", "http://127.0.0.1:9")
    d.on_relay_text("not json")
    d.on_relay_text("[1, 2]")
    d.on_relay_text(json.dumps({"no": "type"}))
    assert d.received == 0
    for i in range(S.HISTORY + 50):
        d.on_relay_text(json.dumps({"type": "sound_event", "v": 3, "src": "laptop", "t": 1.0, "event_id": f"s{i}"}))
    for i in range(20):
        d.on_relay_text(json.dumps({"type": "sources", "v": 3, "src": "laptop", "t": 1.0, "sources": []}))
    assert len(d.history) == S.HISTORY and len(d.latest) == 1
    assert "token" not in json.dumps(d.status())


def test_static_refuses_paths_outside_static():
    d = Dashboard("ws://127.0.0.1:1/ws", "demo", "secret", "http://127.0.0.1:9")
    assert d.static("/").status_code == 200
    assert d.static("/app.js").headers["Content-Type"].startswith("text/javascript")
    for p in ("/../settings.py", "/..%2Fsettings.py", "/../../relay/.env", "/nope.js"):
        assert d.static(p).status_code == 404


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 20))


@contextlib.asynccontextmanager
async def stack():
    """Relay + dashboard on free ports."""
    loop = asyncio.get_running_loop()
    r_ready, d_ready = loop.create_future(), loop.create_future()
    relay_task = asyncio.create_task(serve_relay("127.0.0.1", 0, TOKEN, log_path=None, ready=r_ready))
    _, rport = await r_ready
    dash = Dashboard(f"ws://127.0.0.1:{rport}/ws", "demo", TOKEN, "http://127.0.0.1:9")
    dash_task = asyncio.create_task(run_dashboard(dash, "127.0.0.1", 0, ready=d_ready))
    dport = await d_ready
    try:
        for _ in range(100):
            if dash.relay_connected:
                break
            await asyncio.sleep(0.05)
        yield dash, rport, dport
    finally:
        for t in (dash_task, relay_task):
            t.cancel()
        await asyncio.wait([dash_task, relay_task], timeout=5)


def test_relay_to_page_end_to_end():
    async def main():
        async with stack() as (dash, rport, dport):
            assert dash.relay_connected
            laptop_url = f"ws://127.0.0.1:{rport}/ws?session=demo&role=laptop&token={TOKEN}"
            async with connect(laptop_url) as laptop:
                await laptop.send(json.dumps({"type": "hello", "v": 3, "src": "laptop", "t": time.time(),
                                              "role": "laptop", "subscribe": ["config"]}))
                ev = {"type": "sound_event", "v": 3, "src": "laptop", "t": time.time(), "event_id": "s-1",
                      "label": "unknown", "category": "other", "emoji": "?", "text": "Unknown sound",
                      "confidence": 0.3, "bearing_deg": 120.0, "t_start": 1.0, "t_end": 2.0, "count": 1}
                async with connect(f"ws://127.0.0.1:{dport}/live") as page:
                    first = json.loads(await page.recv())
                    assert first["k"] == "status" and first["relay"]["connected"]
                    assert TOKEN not in json.dumps(first)
                    await laptop.send(json.dumps(ev))
                    while True:
                        w = json.loads(await asyncio.wait_for(page.recv(), 3))
                        if w["k"] == "msg":
                            break
                    assert w["m"]["event_id"] == "s-1" and w["m"]["bearing_deg"] == 120.0 and w["rx"] >= ev["t"]
                # a page opened later gets the recent history
                async with connect(f"ws://127.0.0.1:{dport}/live") as late:
                    got = [json.loads(await asyncio.wait_for(late.recv(), 3)) for _ in range(2)]
                    assert any(g["k"] == "msg" and g["m"]["event_id"] == "s-1" for g in got)

            def get(path):
                with urllib.request.urlopen(f"http://127.0.0.1:{dport}{path}", timeout=5) as r:
                    return json.loads(r.read())
            cfg = await asyncio.to_thread(get, "/dash/config")
            assert TOKEN not in json.dumps(cfg) and cfg["page"]["stream_max"] > 0
            ans = await asyncio.to_thread(get, "/api/ask?q=when%20was%20my%20name%20called")
            assert ans["_source"] == "seed" and ans["evidence"]
    run(main())


def test_scene_rows_rebase_onto_now_with_fresh_ids():
    from extras.dashboard.server import load_scene, rebase_row
    rows = load_scene("doorbell_rear")
    assert rows and load_scene("../secrets") is None and load_scene("nope") is None
    base = time.time()
    out = [rebase_row(d, base, "x1") for d in rows]
    assert all(abs(o["t"] - (base + d["t"])) < 0.01 for o, d in zip(out, rows))
    se = next(o for o in out if o["type"] == "sound_event")
    assert se["event_id"].endswith("-x1") and se["t_start"] >= base
    al = next(o for o in out if o["type"] == "alert")
    assert al["alert_id"].endswith("-x1") and (al.get("ref") or {}).get("event_id", "-x1").endswith("-x1")
    assert all(not errors(o) for o in out)                      # still canonical v3 messages


def test_no_relay_run_demo_plays_a_labelled_local_replay():
    """The venue Wi-Fi is down: no relay. Run demo must still put the scene on the page, labelled as local."""
    async def main():
        loop = asyncio.get_running_loop()
        ready = loop.create_future()
        dash = Dashboard("ws://127.0.0.1:9/ws", "demo", TOKEN, "http://127.0.0.1:9")   # nothing listens there
        task = asyncio.create_task(run_dashboard(dash, "127.0.0.1", 0, ready=ready))
        dport = await ready
        try:
            async with connect(f"ws://127.0.0.1:{dport}/live") as page:
                first = json.loads(await page.recv())
                assert first["k"] == "status" and not first["relay"]["connected"]

                def get(path):
                    with urllib.request.urlopen(f"http://127.0.0.1:{dport}{path}", timeout=5) as r:
                        return json.loads(r.read())
                r = await asyncio.to_thread(get, "/dash/demo?scene=unknown_sound")
                assert r["ok"] and r["local"] and "local replay" in r["label"]
                got, status_local = [], False
                while len(got) < 5:
                    w = json.loads(await asyncio.wait_for(page.recv(), 5))
                    if w["k"] == "status" and w["demo"]["local"]:
                        status_local = True
                    if w["k"] == "msg":
                        assert w.get("local") is True and not errors(w["m"])
                        got.append(w["m"])
                assert status_local
                assert any(m["type"] == "sound_event" and m["label"] == "unknown" and m["bearing_deg"] is not None
                           for m in got) or all(m["type"] == "sources" for m in got)
                r = await asyncio.to_thread(get, "/dash/demo?moment=3")      # core moments map onto scenes
                assert r["ok"] and r["local"]
                await asyncio.to_thread(get, "/dash/demo?action=stop")
                assert not dash.local_running()
        finally:
            task.cancel()
            await asyncio.wait([task], timeout=5)
    run(main())


def test_relay_lost_mid_demo_continues_as_local_replay():
    async def main():
        async with stack() as (dash, rport, dport):
            assert dash.relay_connected
            # a scripted demo is playing (stand-in process), then the relay goes away
            import subprocess
            import sys
            dash.demo = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            dash.demo_q = {"scene": "doorbell_rear"}
            try:
                relay_task = next(t for t in asyncio.all_tasks() if "serve_relay" in repr(t.get_coro()))
                relay_task.cancel()
                for _ in range(100):
                    if dash.local_running():
                        break
                    await asyncio.sleep(0.05)
                assert not dash.relay_connected and dash.local_running()
                assert "doorbell_rear" in dash.status()["demo"]["label"] and dash.status()["demo"]["local"]
            finally:
                dash.stop_demo()
    run(main())


def test_unknown_demo_action_never_starts_a_demo():
    dash = Dashboard("ws://127.0.0.1:9/ws", "demo", TOKEN, "http://127.0.0.1:9")
    r = dash.demo_action({"action": "status"})
    assert r["ok"] is False and "unknown action" in r["error"] and dash.demo is None and not dash.local_running()


def test_judge_tour_is_always_a_local_replay():
    async def main():
        async with stack() as (dash, rport, dport):
            assert dash.relay_connected
            r = dash.demo_action({"moment": "tour", "local": "1"})
            assert r["ok"] and r["local"] and r["label"].startswith("judge tour") and dash.demo is None
            dash.stop_demo()
    run(main())


def test_memory_status_and_token_stay_server_side():
    local = fallback.Memory("http://127.0.0.1:9", token="secret-xyz")
    assert local.token == ""                                  # a local memory API needs no token
    st = local.call("status", {})
    assert st["ok"] is False and st["where"] == "local" and st["reason"] and "secret" not in json.dumps(st)
    remote = fallback.Memory("https://here.example.org", token="secret-xyz")
    assert remote.remote and remote.token == "secret-xyz"      # proxied A3 API wants the relay token (Bearer)
    cleaned = fallback.scrub({"relay": {"token": "x", "health": {"ok": True, "clients": [{"role": "laptop", "api_key": "k"}]}}})
    assert cleaned == {"relay": {"health": {"ok": True, "clients": [{"role": "laptop"}]}}}


@pytest.mark.skipif(not __import__("shutil").which("node"), reason="node not installed")
def test_page_logic_in_node():
    """The page's own logic under node:test: ingest (static/core.js: retractions, voice numbers, steadied bearings)
    and the Moment view's builder (static/moment.js: steps, offsets, providers, honest labels)."""
    import subprocess
    from pathlib import Path
    js = [str(Path(__file__).parent / "js" / f) for f in ("ingest.test.mjs", "moment.test.mjs")]
    major = int(subprocess.run(["node", "--version"], capture_output=True, text=True).stdout.strip().lstrip("v")
                .split(".")[0] or 0)
    # static/*.js are ES modules with no package.json: node 22 detects that itself, 20/21 need the flag
    detect = ["--experimental-detect-module"] if major in (20, 21) else []
    r = subprocess.run(["node", *detect, "--test", *js], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]


def test_offline_replay_ignores_retracted_utterances(seed):
    seg = next(g for g in seed.day["segments"] if g["id"] == "lunch")
    retraction = {"type": "utterance", "v": 3, "src": "laptop", "t": seg["start"] + 1, "utt_id": "u-retracted",
                  "text": "", "pending": "", "final": True, "bearing_deg": 10.0}
    before = fallback.replay(seed, "lunch", {})
    seed.day["events"].append(retraction)
    try:
        after = fallback.replay(seed, "lunch", {})
    finally:
        seed.day["events"].remove(retraction)
    assert after["surfaced"] + after["suppressed"] == before["surfaced"] + before["suppressed"]
    assert all(d["event_id"] != "u-retracted" for d in after["decisions"])


@pytest.mark.skipif(not __import__("shutil").which("node"), reason="node not installed")
def test_every_page_module_parses_as_an_es_module(tmp_path):
    """`node --check x.js` parses as CommonJS and misses errors the browser rejects; check each one as .mjs."""
    import shutil
    import subprocess
    bad = []
    for js in sorted(S.STATIC.glob("*.js")):
        copy = tmp_path / (js.stem + ".mjs")
        shutil.copy(js, copy)
        r = subprocess.run(["node", "--check", str(copy)], capture_output=True, text=True, timeout=30)
        if r.returncode:
            bad.append(f"{js.name}: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else r.returncode}")
    assert not bad, bad


def test_fake_phone_speaks_the_contract_like_the_app():
    from extras.dashboard.fake_phone import FakePhone, parse_people
    phone = FakePhone(parse_people("Person 1:35,Person 2:-35"), heading=10, thermal="fair")
    for msg in (phone.hello(), phone.heartbeat(), phone.scene()):
        assert errors(msg.to_dict()) == [], msg
    final = {"type": "utterance", "utt_id": "u1", "text": "Hey", "pending": "", "final": True, "bearing_deg": 31.0}
    att = phone.on_message(final).to_dict()
    assert errors(att) == [] and att["person_label"] == "Person 1" and att["method"] == "bearing" and att["ref"] == {"utt_id": "u1"}
    off = phone.on_message({**final, "utt_id": "u2", "bearing_deg": 180.0}).to_dict()
    assert off["person_label"] is None and off["method"] == "off_camera" and errors(off) == []
    assert phone.on_message({**final, "text": ""}) is None                 # a retraction gets no attribution
    assert phone.on_message({**final, "final": False}) is None             # only finals
    phone.on_message({"type": "sources", "sources": [{"id": 0, "bearing_deg": -33.0, "speech": True}]})
    assert [p.speaking for p in phone.scene().people] == [False, True]
    degraded = FakePhone([], camera_error="camera unavailable").heartbeat().to_dict()
    assert degraded["status"] == "degraded" and degraded["detail"] == "camera unavailable" and errors(degraded) == []
    assert FakePhone([], send_scene=False).on_message(final) is None      # Settings > Send scene off: no attribution


def test_phone_messages_reach_the_page_unchanged():
    from extras.dashboard.fake_phone import FakePhone, parse_people, relay_url, run as run_phone

    async def main():
        async with stack() as (dash, rport, dport):
            phone = FakePhone(parse_people("Person 1:35"), thermal="fair")
            async with connect(f"ws://127.0.0.1:{dport}/live") as page:
                ptask = asyncio.create_task(run_phone(phone, relay_url(f"ws://127.0.0.1:{rport}/ws", "demo", TOKEN), 3.0))
                await asyncio.sleep(0.6)
                async with connect(f"ws://127.0.0.1:{rport}/ws?session=demo&role=laptop&token={TOKEN}") as laptop:
                    await laptop.send(json.dumps({"type": "hello", "v": 3, "src": "laptop", "t": time.time(), "role": "laptop", "subscribe": ["config"]}))
                    await laptop.send(json.dumps({"type": "utterance", "v": 3, "src": "laptop", "t": time.time(), "utt_id": "u-35",
                                                  "t_start": time.time() - 1, "t_end": time.time(), "text": "Over here", "pending": "",
                                                  "final": True, "bearing_deg": 35.0, "bearing_spread_deg": 3.0, "source_id": 0,
                                                  "overlap": False, "tone": None, "tone_confidence": 0.0, "loudness": 0.5,
                                                  "engine": "faster_whisper"}))
                    seen = {}
                    t_end = time.time() + 4
                    while time.time() < t_end and not {"heartbeat", "scene", "attribution"} <= seen.keys():
                        w = json.loads(await asyncio.wait_for(page.recv(), 4))
                        if w["k"] == "msg" and w["m"].get("src") == "phone":
                            seen[w["m"]["type"]] = w["m"]
                ptask.cancel()
            assert seen["heartbeat"]["thermal"] == "fair"
            assert seen["scene"]["people"][0]["label"] == "Person 1"
            assert seen["attribution"]["person_label"] == "Person 1" and seen["attribution"]["ref"]["utt_id"] == "u-35"
            assert all(errors(m) == [] for m in seen.values())
    run(main())

"""Stage timings (K1 D): logged per message, merged into utterance.stage_ms, never on the wire; dashboard lines."""

import asyncio
import contextlib
import json
from types import SimpleNamespace

from contracts.python import here_contract as hc
from contracts.python.here_contract import Source, Sources, Utterance
from laptop.client.relay import ConsoleSink, RelayClient
from laptop.dashboard import Dashboard
from laptop.events.stats import STATS, StageStats


def utt(stage=None):
    return Utterance(src="laptop", t=1.0, utt_id="u-1", t_start=0.5, t_end=1.0, text="hi", pending="", final=True,
                     bearing_deg=10.0, bearing_spread_deg=1.0, source_id=0, overlap=False, tone=None,
                     tone_confidence=0.0, loudness=0.5, engine="faster_whisper", stage_ms=stage)


def test_stage_stats_summary():
    s = StageStats()
    for v in range(1, 101):
        s.add("doa", v)
    s.add("capture", 12.0)
    summ = s.summary()
    assert list(summ)[:2] == ["capture", "doa"]
    assert summ["doa"][0] == 51.0 and summ["doa"][1] == 96.0 and summ["doa"][2] == 100
    assert "doa 51.0/96.0" in s.line()


def test_sources_stage_goes_to_the_log_not_the_wire(tmp_path):
    log = tmp_path / "l.jsonl"
    sink = ConsoleSink(quiet_types=("sources", "utterance"), log_path=log)
    STATS.add("capture", 9.5)
    STATS.add("doa", 3.25)
    msg = Sources(src="laptop", t=1.0, sources=[Source(id=0, bearing_deg=1.0, energy=0.5, speech=True,
                                                       confidence=0.8)])
    msg._stage = {"capture": 9.5, "doa": 3.25}
    sink.send(msg)
    sink.send(utt({"asr": 120.0, "capture": 1.0}))
    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["_stage"]["capture"] == 9.5 and lines[0]["_stage"]["doa"] == 3.25
    assert "serialize" in lines[0]["_stage"]
    wire = {k: v for k, v in lines[0].items() if not k.startswith("_")}
    assert hc.errors(wire) == [] and "_stage" not in msg.to_json()
    # utterance.stage_ms: the speech pipeline's keys win, the latest doa is added
    assert lines[1]["stage_ms"]["asr"] == 120.0 and lines[1]["stage_ms"]["capture"] == 1.0
    assert lines[1]["stage_ms"]["doa"] == 3.2 or lines[1]["stage_ms"]["doa"] == 3.3


def test_relay_client_logs_dropped_while_disconnected_and_socket_send_when_sent(tmp_path):
    from relay.relay import serve_relay
    log = tmp_path / "l.jsonl"

    async def main():
        c = RelayClient("ws://127.0.0.1:9/ws", log_path=log)
        c.send(utt())                                             # not connected: dropped, logged
        ready = asyncio.get_running_loop().create_future()
        server = asyncio.create_task(serve_relay("127.0.0.1", 0, "", log_path=None, ready=ready))
        _, port = await ready
        c2 = RelayClient(f"ws://127.0.0.1:{port}/ws", log_path=log)
        runner = asyncio.create_task(c2.run())
        while not c2.connected:
            await asyncio.sleep(0.02)
        c2.send(utt())
        await asyncio.sleep(0.2)
        for t in (runner, server):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        return c, c2

    c, c2 = asyncio.run(asyncio.wait_for(main(), 20))
    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    utts = [x for x in lines if x["type"] == "utterance"]
    assert utts[0].get("_dropped") is True and c.dropped == 1
    assert "socket_send" in utts[1]["_stage"] and not utts[1].get("_dropped") and c2.queue_depth == 0


def test_dashboard_shows_latency_and_pipeline_counts():
    from laptop.audio.base import SourcesThrottle
    from laptop.events.onsets import OnsetCandidate, OnsetDetector
    from laptop.events.tracker import SourceTracker
    tr = SourceTracker()
    tr.update(1.0, [Source(id=1, bearing_deg=180.0, energy=0.6, speech=True, confidence=0.9)])
    on = OnsetDetector()
    on.count, on.recent = 1, [OnsetCandidate(t=1.0, bearing_deg=-179.2, energy=0.7, source_id=1, rise_db=21.0)]
    th = SourcesThrottle()
    th.sent, th.suppressed = 10, 30
    app = SimpleNamespace(audio=SimpleNamespace(level_db=-30.0), speech=None, tracker=tr, onsets=on, throttle=th,
                          queue_depth=lambda: (2, 0), audio_dropped=lambda: 0)
    client = SimpleNamespace(connected=True, sent=5, dropped=1, max_queue=3, send=lambda m: True)
    STATS.add("socket_send", 0.4)
    text = "\n".join(Dashboard(app, client).lines()[1])
    assert "latency" in text and "socket_send" in text
    assert "suppressed 30/40" in text and "dropped 1" in text and "queue audio 2 relay 0 (max 3)" in text
    assert "onsets 1" in text and "id 1 at -179 deg" in text and "tracks [1]" in text

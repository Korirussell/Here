"""Speech engines without the network or models: Scribe Realtime against a local fake server, the fallback,
and the pipeline driven by FakeTranscriber (SPEC §10 fast path, §12.6 utterance)."""

import asyncio
import base64
import json
import threading
import time

import numpy as np
import pytest

from contracts.python import here_contract as hc
from contracts.python.here_contract import Source
from laptop.audio.base import DirectionHistory
from laptop.settings import load
from laptop.speech.pipeline import SpeechPipeline
from laptop.speech.transcriber import (FakeTranscriber, FallbackTranscriber, Hypothesis, ScribeRealtimeTranscriber,
                                       TranscriberUnavailable, make_transcriber)

CFG = load()
SR = 16000
SLOW = 30.0          # max_latency_s for tests not about slowness: a stalled thread (low memory) must not count as a slow engine
LINE = "hey Kori the pizza is here"


# ---- a local stand-in for wss://api.elevenlabs.io/v1/speech-to-text/realtime --------------------------

class FakeScribe:
    """Speaks the documented protocol: session_started, partial_transcript per chunk (words revealed as audio
    arrives), committed_transcript on commit. mode: ok | slow (never commits) | drop (closes after drop_after
    samples) | silent (insufficient_audio_activity on commit) | down (refuses connections)."""

    def __init__(self, key="good-key"):
        from websockets.sync.server import serve

        self.key, self.mode, self.drop_after, self.line = key, "ok", SR, LINE
        self.connections = self.commits = 0
        self.received = []                       # samples per connection
        self.server = serve(self.handler, "127.0.0.1", 0)
        self.url = f"ws://127.0.0.1:{self.server.socket.getsockname()[1]}/v1/speech-to-text/realtime"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def handler(self, ws):
        if self.mode == "down":
            ws.close()
            return
        if ws.request.headers.get("xi-api-key") != self.key:
            ws.send(json.dumps({"message_type": "auth_error", "error": "Invalid API key"}))
            ws.close()
            return
        self.connections += 1
        k = len(self.received)
        self.received.append(0)
        ws.send(json.dumps({"message_type": "session_started", "session_id": f"s{k}", "config": {}}))
        n = 0
        for raw in ws:
            m = json.loads(raw)
            assert m["message_type"] == "input_audio_chunk" and m["sample_rate"] == SR
            got = len(base64.b64decode(m["audio_base_64"])) // 2
            n += got
            self.received[k] += got
            if self.mode == "drop" and n >= self.drop_after:
                ws.close()
                return
            if got:
                ws.send(json.dumps({"message_type": "partial_transcript",
                                    "text": " ".join(self.line.split()[:int(n / SR * 2.5)])}))
            if m["commit"]:
                self.commits += 1
                if self.mode == "slow":
                    continue
                if self.mode == "silent":
                    ws.send(json.dumps({"message_type": "insufficient_audio_activity", "error": "no speech"}))
                else:
                    ws.send(json.dumps({"message_type": "committed_transcript", "text": self.line}))
                n = 0

    def close(self):
        self.server.shutdown()


@pytest.fixture
def server():
    s = FakeScribe()
    yield s
    s.close()


def scribe(server, key="good-key", **kw):
    opts = dict(api_key=key, url=server.url, connect_timeout_s=2.0, partial_wait_s=0.3, final_timeout_s=0.5,
                stall_s=2.0, pool=2, chunk_s=0.25)
    opts.update(kw)
    return ScribeRealtimeTranscriber(**opts)


def audio(seconds):
    return np.zeros(int(seconds * SR), np.float32)


class Whisper:
    """Stands in for faster-whisper: decodes whatever whole audio it gets."""
    engine = "faster_whisper"

    def __init__(self):
        self.calls = []

    def decode(self, audio, final, use_hint=True):
        self.calls.append((len(audio), final))
        return Hypothesis(text="whisper heard it" if final else "whisper", engine=self.engine)


# ---- Scribe Realtime ---------------------------------------------------------------------------------

def test_scribe_streams_partials_then_one_committed_final(server):
    s = scribe(server)
    s.warm()
    p1 = s.decode(audio(0.8), False, utt_id="u1")
    p2 = s.decode(audio(1.6), False, utt_id="u1")
    assert p1.engine == "scribe_realtime" and p1.text == "hey Kori" and p2.text == "hey Kori the pizza"
    f = s.decode(audio(2.0), True, utt_id="u1")
    assert f.text == LINE and "commit" in f.stage and "first_partial" in f.stage
    assert server.received[0] == sum(server.received) == int(2.0 * SR)   # only new audio each time, never resent
    assert server.commits == 1 and server.connections <= 2                # + one warm spare, opened in background
    s.decode(audio(1.0), False, utt_id="u2")           # the next utterance uses a warm connection: no new one
    assert server.connections <= 2
    s.close()


def test_two_utterances_at_once_get_their_own_sessions(server):
    s = scribe(server)
    s.decode(audio(0.8), False, utt_id="beam0")
    s.decode(audio(0.8), False, utt_id="beam1")
    assert len(s.live) == 2
    assert s.decode(audio(1.2), True, utt_id="beam0").text == LINE
    assert s.decode(audio(1.2), True, utt_id="beam1").text == LINE
    s.close()


def test_no_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(TranscriberUnavailable):
        ScribeRealtimeTranscriber(api_key="")


def test_no_key_runs_whisper_and_says_so(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    import laptop.speech.transcriber as T
    monkeypatch.setattr(T, "WhisperTranscriber", lambda *a, **k: Whisper())
    t, detail = make_transcriber(dict(CFG["speech"], engine="scribe_realtime"))
    assert t.engine == "faster_whisper" and "ELEVENLABS_API_KEY" in detail


def test_bad_key_falls_back_at_startup_without_leaking_the_key(server, capsys):
    f = FallbackTranscriber(scribe(server, key="sk-secret-123"), Whisper(), retry_s=60, max_latency_s=SLOW)
    f.warm()
    assert f.engine == "faster_whisper" and "auth_error" in f.reason
    assert f.decode(audio(1.0), True, utt_id="u1").text == "whisper heard it"
    assert "sk-secret-123" not in f.reason and "sk-secret-123" not in capsys.readouterr().out


def test_network_drop_mid_utterance_hands_the_whole_utterance_to_whisper(server):
    server.mode, server.drop_after = "drop", int(1.0 * SR)
    whisper = Whisper()
    f = FallbackTranscriber(scribe(server), whisper, retry_s=60, max_latency_s=SLOW)
    f.warm()
    assert f.decode(audio(0.8), False, utt_id="u1").engine == "scribe_realtime"
    t = time.perf_counter()
    hyps = [f.decode(audio(1.2 + 0.4 * k), False, utt_id="u1") for k in range(3)]
    final = f.decode(audio(2.5), True, utt_id="u1")
    assert time.perf_counter() - t < 2.0                 # noticed fast (connection closed), not after timeouts
    assert final.text == "whisper heard it" and final.engine == "faster_whisper"
    assert whisper.calls[-1] == (int(2.5 * SR), True)    # every word of the utterance, from its first sample
    assert hyps[-1].engine == "faster_whisper" and "connection" in f.reason and f.engine == "faster_whisper"
    assert "u1" not in f.primary.live


def test_slow_commit_times_out_to_whisper(server):
    server.mode = "slow"
    f = FallbackTranscriber(scribe(server, final_timeout_s=0.3), Whisper(), retry_s=60, max_latency_s=SLOW)
    f.decode(audio(1.0), False, utt_id="u1")
    t = time.perf_counter()
    assert f.decode(audio(1.5), True, utt_id="u1").engine == "faster_whisper"
    assert time.perf_counter() - t < 1.0
    assert "no committed transcript" in f.reason


def test_scribe_comes_back_after_an_outage(server):
    f = FallbackTranscriber(scribe(server), Whisper(), retry_s=0.2, max_latency_s=SLOW)
    server.mode = "down"
    f.warm()
    assert f.engine == "faster_whisper"
    server.mode = "ok"
    for _ in range(50):                                  # the background probe notices, with no speech needed
        if f.reason is None:
            break
        time.sleep(0.05)
    assert f.engine == "scribe_realtime" and f.reason is None
    assert f.decode(audio(1.0), True, utt_id="u2").engine == "scribe_realtime"
    assert [e for _, e in f.switches] == ["faster_whisper", "scribe_realtime"]


def test_silence_is_an_empty_final_not_an_outage(server):
    server.mode = "silent"
    f = FallbackTranscriber(scribe(server), Whisper(), retry_s=60, max_latency_s=SLOW)
    hyp = f.decode(audio(0.5), True, utt_id="u1")
    assert hyp.text == "" and hyp.engine == "scribe_realtime" and f.reason is None


def test_a_dropped_utterance_never_leaks_into_the_next(server):
    s = scribe(server, pool=1)
    s.decode(audio(1.6), False, utt_id="blip")
    s.end("blip")                                        # the pipeline retracted it: no commit, session gone
    hyp = s.decode(audio(0.4), False, utt_id="next")
    assert hyp.text == "hey"                             # 0.4 s worth of words, not the blip's 2 s
    assert server.received[0] == int(1.6 * SR) and sum(server.received) == int(2.0 * SR)


# ---- fallback ------------------------------------------------------------------------------------------

def test_fallback_keeps_old_two_argument_engines_working():
    class Old:
        engine = "scribe_realtime"

        def decode(self, audio, final):
            return Hypothesis(text="old", engine=self.engine)

    assert FallbackTranscriber(Old(), Whisper()).decode(audio(1), True, utt_id="u").text == "old"


def test_slow_primary_switches_after_answering():
    slow = FakeTranscriber(engine="scribe_realtime", delay_s=0.2)
    f = FallbackTranscriber(slow, Whisper(), max_latency_s=0.1, retry_s=60)
    assert f.decode(audio(1), True, utt_id="u").engine == "scribe_realtime"   # this answer still counts
    assert f.engine == "faster_whisper" and "slow" in f.reason


# ---- the pipeline with FakeTranscriber -----------------------------------------------------------------

def src(b, speech=True, conf=0.8, id_=0):
    return Source(id=id_, bearing_deg=b, energy=0.6, speech=speech, confidence=conf)


def drive(engine, talk, total_s=None, names=("Kori",), extra=None, speed=None):
    """talk: [(start_s, end_s, bearing)] of speech; extra: [(start_s, end_s, bearing, id)] another talker heard
    by the array at the same time; speed: feed audio at this multiple of real time (None: as fast as possible,
    so slow engines only get to the final). Returns (messages with the decode calls made before each, name alerts)."""
    sent, alerts = [], []
    h, t0 = DirectionHistory(), 1000.0
    total_s = total_s or max(b for _, b, _ in talk) + 2.0
    for i in range(int(total_s * 20)):
        t = i * 0.05
        srcs = [src(bearing) for a, b, bearing in talk if a <= t < b]
        srcs += [src(bearing, id_=k) for a, b, bearing, k in (extra or []) if a <= t < b]
        h.add(t0 + t, srcs)

    def vad(win):
        vad.n += 512
        t = vad.n / SR
        return 0.9 if any(a <= t < b for a, b, _ in talk) else 0.0
    vad.n = 0
    calls = getattr(engine, "calls", [])
    p = SpeechPipeline(dict(CFG["speech"], partial_every_s=0.4), engine, h,
                       lambda m: sent.append((len(calls), m)), on_name=lambda **a: alerts.append(a), vad=vad,
                       names=list(names))

    async def main():
        x = (np.random.default_rng(0).standard_normal(int(total_s * SR)) * 3000).astype(np.int16)
        for i in range(0, len(x), 512):
            await p.feed(t0 + i / SR, x[i:i + 512])
            await asyncio.sleep(512 / SR / speed if speed else 0)
        for _ in range(100):
            await asyncio.sleep(0.01)
        await asyncio.gather(*p.tasks)
    asyncio.run(main())
    for _, m in sent:
        assert hc.errors(m.to_dict()) == []
    return sent, alerts


def finals(sent):
    return [m for _, m in sent if m.TYPE == "utterance" and m.final]


def test_speech_start_goes_out_before_any_transcription():
    engine = FakeTranscriber([LINE], engine="scribe_realtime", delay_s=0.05)
    sent, _ = drive(engine, [(0.0, 2.0, 150.0)], speed=4.0)
    calls_before, first = sent[0]
    assert first.TYPE == "speech_start" and calls_before == 0 and first.bearing_deg == 150.0
    f = finals(sent)
    assert len(f) == 1 and f[0].text == LINE and f[0].engine == "scribe_realtime" and "asr" in f[0].stage_ms
    partials = [m for _, m in sent if m.TYPE == "utterance" and not m.final]
    assert partials and all(m.utt_id == first.utt_id for m in partials)
    assert any(m.text for m in partials) and any(m.pending for m in partials)


def test_mid_utterance_failure_keeps_the_utterance_on_whisper():
    primary = FakeTranscriber([LINE], engine="scribe_realtime")
    primary.fail = lambda final: ConnectionError("network down") if final else None
    whisper = FakeTranscriber([LINE], engine="faster_whisper")
    whisper.streaming = False                           # like the real one: (audio, final) only
    whisper.decode = lambda a, final, use_hint=True, f=whisper.decode: f(a, final, use_hint)
    sent, _ = drive(FallbackTranscriber(primary, whisper, retry_s=60, max_latency_s=SLOW), [(0.0, 2.0, 150.0)])
    f = finals(sent)
    assert len(f) == 1 and f[0].text == LINE and f[0].engine == "faster_whisper"
    assert {m.engine for _, m in sent if m.TYPE == "utterance" and not m.final} == {"scribe_realtime"}
    assert whisper.calls[-1][2] >= 2.0 * SR             # the whole utterance went to whisper


def test_rapid_speaker_switch_keeps_each_utterance_its_own():
    engine = FakeTranscriber(["first talker line here", "second talker answers"], engine="scribe_realtime",
                             delay_s=0.15)               # late finals: decoded while the next talker starts
    sent, _ = drive(engine, [(0.0, 1.6, 90.0), (2.3, 4.0, -90.0)])
    f = finals(sent)
    assert [m.text for m in f] == ["first talker line here", "second talker answers"]
    assert [m.bearing_deg for m in f] == [90.0, -90.0] and f[0].utt_id != f[1].utt_id
    assert all(sum(1 for m in finals(sent) if m.utt_id == u.utt_id) == 1 for u in f)   # final exactly once


def test_two_talkers_at_once_is_overlap():
    sent, _ = drive(FakeTranscriber([LINE]), [(0.0, 2.0, 30.0)], extra=[(0.0, 2.0, -120.0, 1)])
    assert finals(sent)[0].overlap


def test_name_alias_alerts_once_with_the_bearing():
    engine = FakeTranscriber(["hey coach over here please"], engine="scribe_realtime")
    sent, alerts = drive(engine, [(0.0, 2.5, 175.0)], names=("Kori", "Coach"))
    assert len(alerts) == 1 and alerts[0]["name"] == "coach" and alerts[0]["bearing"] == 175.0


class Garbage:
    engine, streaming = "scribe_realtime", True

    def __init__(self, partials, final):
        self.partials, self.final = list(partials), final

    def decode(self, audio, final, use_hint=True, utt_id=None):
        if final:
            return Hypothesis(text=self.final, engine=self.engine, segments=[(self.final, 0.0)] if self.final else [])
        return Hypothesis(text=self.partials.pop(0) if len(self.partials) > 1 else self.partials[0],
                          engine=self.engine)


def test_empty_or_garbage_partials_show_nothing():
    sent, alerts = drive(Garbage(["", "...", " - ", "hey", "hey there"], "Hey there."), [(0.0, 2.8, 0.0)])
    shown = [f"{m.text} {m.pending}".strip() for _, m in sent if m.TYPE == "utterance"]
    assert all(any(c.isalnum() for c in s) for s in shown if s)
    assert finals(sent)[-1].text == "Hey there."


def test_empty_final_from_the_engine_clears_the_typing_dots():
    sent, alerts = drive(Garbage(["", "..."], ""), [(0.0, 1.5, 0.0)])
    assert finals(sent) and finals(sent)[-1].text == "" and not alerts

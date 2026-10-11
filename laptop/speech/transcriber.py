"""Speech-to-text engines behind one interface (README §3, §14, §18: Scribe first, whisper as fallback).

The pipeline re-decodes the growing utterance every partial_every_s and once more for the final, so the
interface is one blocking call (run in the pipeline's worker thread), plus an optional session lifecycle:

    decode(audio, final, use_hint=True, utt_id=None) -> Hypothesis
        audio   the whole utterance so far (16 kHz float32); a streaming engine sends only what's new
        final   False: the newest partial (text may still change); True: the committed text, exactly once
        utt_id  given to engines with `streaming = True`: one remote session per utterance
    end(utt_id)    the utterance was dropped without a final (a blip): free its session
    warm()         open what the engine needs before live audio (and probe a recovered remote engine)
    close()        shutdown
    errors         raise any exception: FallbackTranscriber treats it as "this engine is down"

Engines: WhisperTranscriber (faster-whisper, local), ScribeRealtimeTranscriber (ElevenLabs Scribe v2
Realtime over a WebSocket), FakeTranscriber (deterministic, for tests and --engine fake).
`FallbackTranscriber` uses the primary and switches to the fallback when it errors or answers slower than
`max_latency_s`; it probes the primary in the background every `retry_s` and switches back when it answers.
The fallback always gets the whole utterance, so an engine dying mid-utterance loses no words.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlencode

import numpy as np

SR = 16000


@dataclass
class Hypothesis:
    text: str
    words: list[tuple[str, float]] = field(default_factory=list)   # (word, start seconds into the audio)
    no_speech: bool = False
    ms: float = 0.0
    engine: str = "faster_whisper"
    segments: list[tuple[str, float]] = field(default_factory=list)  # (text, avg_logprob) of the kept segments
    stage: dict[str, float] = field(default_factory=dict)            # extra timings for the utterance's stage_ms


class TranscriberUnavailable(RuntimeError):
    pass


class Transcriber(Protocol):
    engine: str                       # contract `engine` value

    def decode(self, audio: np.ndarray, final: bool, use_hint: bool = True) -> Hypothesis: ...


def decode_call(engine, audio, final, use_hint=True, utt_id=None):
    """Call any engine: older ones take (audio, final[, use_hint]); streaming ones also get the utterance id."""
    if getattr(engine, "streaming", False):
        return engine.decode(audio, final, use_hint, utt_id=utt_id)
    return engine.decode(audio, final) if use_hint else engine.decode(audio, final, False)


class WhisperTranscriber:
    """faster-whisper on CUDA (large-v3-turbo, float16); CPU int8 with a small model when there's no GPU."""

    engine = "faster_whisper"

    def __init__(self, model="large-v3-turbo", device="cuda", compute_type="float16", cpu_model="base.en",
                 cpu_compute_type="int8", language="en", final_beam_size=5, no_speech_drop=0.6, name_hints="prompt",
                 compression_max=2.4):
        import torch  # noqa: F401  (must load before faster_whisper: provides cuBLAS/cuDNN DLLs on Windows)
        import ctranslate2
        from faster_whisper import WhisperModel

        self.degraded = None
        if device == "cuda" and ctranslate2.get_cuda_device_count() == 0:
            device, compute_type, model = "cpu", cpu_compute_type, cpu_model
            self.degraded = f"faster_whisper {model} on CPU (no GPU)"
            print("[speech] WARNING: no CUDA device; falling back to CPU. Captions will be slow and less "
                  "accurate. On the demo laptop see laptop/README.md and run tools/check_gpu.py.")
        self.model = WhisperModel(model, device=device, compute_type=compute_type)
        self.language, self.final_beam_size, self.no_speech_drop = language, final_beam_size, no_speech_drop
        # hotwords | prompt | *_final | off: how the user's name reaches the decoder (YAML reads a bare off as False)
        self.name_hints = str(name_hints).lower() if name_hints not in (None, False) else "off"
        # A final segment with a compression ratio above compression_max is whisper looping ("I am the medium.
        # I am the medium."): dropped. avg_logprob is reported per segment; the pipeline drops low ones only
        # when the array didn't hear a real talker (real speech under laughter scores low too).
        self.compression_max = compression_max
        self.hint = None
        self.description = f"{model} on {device} ({compute_type})"
        # Warm up: the first CUDA decode takes seconds; do it before live audio arrives.
        self.decode(np.zeros(16000, np.float32), final=False)

    def set_names(self, names):
        """The user's name + aliases as a decoding hint, so "Kori" isn't heard as "Corey" or "the" in noise."""
        names = list(dict.fromkeys(n.strip() for n in names if n and n.strip()))
        self.hint = ", ".join(names) or None

    @property
    def hinting(self):
        return self.hint is not None and self.name_hints != "off"

    def decode(self, audio, final, use_hint=True):
        t = time.perf_counter()
        hint = {}
        mode = self.name_hints if use_hint else "off"
        if mode.endswith("_final"):          # hint the final decode only (partials of short audio leak the name)
            mode = mode[: -len("_final")] if final else "off"
        if self.hint and mode == "hotwords":
            hint["hotwords"] = self.hint
        elif self.hint and mode == "prompt":
            hint["initial_prompt"] = f"{self.hint}."
        segs, _ = self.model.transcribe(audio, language=self.language, beam_size=self.final_beam_size if final else 1,
                                        condition_on_previous_text=False, vad_filter=False,
                                        word_timestamps=final, without_timestamps=not final, **hint)
        segs = list(segs)
        kept = [s for s in segs if s.no_speech_prob < self.no_speech_drop
                and s.compression_ratio <= self.compression_max] if final else segs
        words = [(w.word.strip(), w.start) for s in kept for w in (s.words or [])]
        text = " ".join(s.text.strip() for s in kept).strip()
        return Hypothesis(text=text, words=words, no_speech=bool(segs) and not kept,
                          ms=(time.perf_counter() - t) * 1000, engine=self.engine,
                          segments=[(s.text.strip(), s.avg_logprob) for s in kept])




# ---- ElevenLabs Scribe v2 Realtime -------------------------------------------------------------------

# Server messages that end a session (API reference, "Warnings and errors"). insufficient_audio_activity is
# handled apart: it means "no speech in that audio", an empty final, not a broken engine.
SCRIBE_ERRORS = {"error", "auth_error", "quota_exceeded", "commit_throttled", "unaccepted_terms", "rate_limited",
                 "queue_overflow", "resource_exhausted", "session_time_limit_exceeded", "input_error",
                 "invalid_request", "chunk_size_exceeded", "transcriber_error"}


class ScribeError(RuntimeError):
    pass


class ScribeSession:
    """One Scribe Realtime WebSocket (manual commits). Leased to one utterance at a time, then reused.

    A reader thread keeps the newest partial and the committed text; decode() waits on them with timeouts.
    Protocol (elevenlabs.io/docs/api-reference/speech-to-text/v-1-speech-to-text-realtime): the server opens
    with session_started; the client sends input_audio_chunk {audio_base_64, commit, sample_rate}; the server
    answers partial_transcript {text} (the whole uncommitted segment so far) and, after a commit,
    committed_transcript {text}."""

    def __init__(self, url, api_key, connect_timeout_s):
        from websockets.sync.client import connect

        # .__enter__(): the connection on every websockets >= 14 (17.1 deprecates using connect() bare);
        # close() ends it.
        self.ws = connect(url, additional_headers={"xi-api-key": api_key}, open_timeout=connect_timeout_s,
                          close_timeout=1.0, max_size=2 ** 22).__enter__()
        try:
            first = json.loads(self.ws.recv(timeout=connect_timeout_s))
        except Exception:
            self.ws.close()
            raise
        if first.get("message_type") != "session_started":
            self.ws.close()
            raise ScribeError(f"{first.get('message_type')}: {first.get('error', first)}")
        self.session_id = first.get("session_id", "")
        self.cond = threading.Condition()
        self.error = None
        self.reset()
        threading.Thread(target=self._read, name="scribe-reader", daemon=True).start()

    def reset(self):
        """Ready for the next utterance."""
        self.utt_id = None
        self.sent = 0                 # samples of this utterance already sent
        self.partial, self.partials = "", 0
        self.committed = None
        self.listening = False        # partials count only once this utterance's audio went out
        self.last_msg = self.t_first_audio = self.t_first_partial = self.t_commit = None

    def _read(self):
        try:
            for raw in self.ws:
                msg = json.loads(raw)
                kind = msg.get("message_type")
                with self.cond:
                    self.last_msg = time.perf_counter()
                    if kind == "partial_transcript" and self.listening and self.committed is None:
                        self.partial, self.partials = msg.get("text", ""), self.partials + 1
                        if self.t_first_partial is None:
                            self.t_first_partial = self.last_msg
                    elif kind == "committed_transcript" and self.listening:
                        self.committed = msg.get("text", "")
                    elif kind == "insufficient_audio_activity" and self.t_commit is not None:
                        self.committed = ""
                    elif kind in SCRIBE_ERRORS:
                        self.error = f"{kind}: {msg.get('error', '')}"[:160]
                    self.cond.notify_all()
                if self.error:
                    break
        except Exception as e:  # noqa: BLE001 - a dropped connection, a bad frame: the session is dead
            with self.cond:
                self.error = self.error or f"connection lost ({type(e).__name__})"
                self.cond.notify_all()
        finally:
            with self.cond:
                self.error = self.error or "connection closed"
                self.cond.notify_all()
            self.close()

    @property
    def alive(self):
        return self.error is None

    def send(self, pcm16, commit, chunk_n):
        """Audio as input_audio_chunk messages of at most chunk_n samples; commit=True on the last one."""
        pieces = [pcm16[i:i + chunk_n] for i in range(0, len(pcm16), chunk_n)] or [pcm16[:0]]
        now = time.perf_counter()
        with self.cond:
            self.listening = True
            if self.t_first_audio is None and len(pcm16):
                self.t_first_audio = now
            if commit:
                self.t_commit = now
        for k, piece in enumerate(pieces):
            try:
                self.ws.send(json.dumps({"message_type": "input_audio_chunk", "sample_rate": SR,
                                         "audio_base_64": base64.b64encode(piece.astype("<i2").tobytes()).decode(),
                                         "commit": bool(commit and k == len(pieces) - 1)}))
            except Exception as e:  # noqa: BLE001 - closed under us before the reader noticed: same honest reason
                raise ConnectionError(self.error or f"connection closed ({type(e).__name__})") from e

    def close(self):
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


class ScribeRealtimeTranscriber:
    """ElevenLabs Scribe v2 Realtime: streaming partials, committed finals, one WebSocket per live utterance.

    Only utterance audio goes out (what the VAD and the array kept), never the room in between (README §25).
    Connections are kept warm in a small pool so an utterance doesn't pay the TLS handshake; a session is
    reused after its commit and thrown away after anything odd (timeouts, errors, a dropped utterance), so a
    late answer can never land in the next utterance. Any failure raises: FallbackTranscriber hands the whole
    utterance to whisper. Needs ELEVENLABS_API_KEY (environment or laptop/.env); it is only ever sent as the
    xi-api-key header and never printed."""

    engine = "scribe_realtime"
    streaming = True
    hinting = False                   # keyterms don't loop the way whisper's prompt does (pipeline strip_echo)

    def __init__(self, api_key=None, url="wss://api.elevenlabs.io/v1/speech-to-text/realtime",
                 model_id="scribe_v2_realtime", language_code="en", connect_timeout_s=3.0, partial_wait_s=0.25,
                 final_timeout_s=1.2, stall_s=3.0, pool=2, chunk_s=0.5, keyterms=False, settle_s=0.04,
                 session_factory=None):
        self.api_key = api_key if api_key is not None else os.environ.get("ELEVENLABS_API_KEY", "")
        if not self.api_key:
            raise TranscriberUnavailable("ELEVENLABS_API_KEY not set")
        self.base_url, self.model_id, self.language_code = url, model_id, language_code
        self.connect_timeout_s, self.partial_wait_s, self.final_timeout_s = connect_timeout_s, partial_wait_s, final_timeout_s
        self.settle_s = settle_s
        self.stall_s, self.pool_size, self.chunk_n = stall_s, int(pool), max(1600, int(chunk_s * SR))
        self.use_keyterms, self.keyterms = bool(keyterms), []
        self.session_factory = session_factory or ScribeSession
        self.lock = threading.Lock()
        self.idle: list[ScribeSession] = []
        self.live: dict[str, ScribeSession] = {}
        self.refilling = False

    @property
    def url(self):
        q = {"model_id": self.model_id, "audio_format": "pcm_16000", "commit_strategy": "manual"}
        if self.language_code:
            q["language_code"] = self.language_code
        if self.use_keyterms and self.keyterms:
            q["keyterms"] = self.keyterms
        return f"{self.base_url}?{urlencode(q, doseq=True)}"

    def set_names(self, names):
        """The user's name + aliases as Scribe keyterms (speech.scribe.keyterms); used by new connections."""
        terms = list(dict.fromkeys(n.strip() for n in names if n and n.strip()))
        if terms != self.keyterms:
            self.keyterms = terms
            if self.use_keyterms:
                with self.lock:
                    stale, self.idle = self.idle, []
                for s in stale:
                    s.close()

    # ---- connections -------------------------------------------------------------------------------

    def _open(self):
        return self.session_factory(self.url, self.api_key, self.connect_timeout_s)

    def warm(self):
        """Open one session now (raises on no network / a bad key) and keep it for the next utterance."""
        s = self._open()
        with self.lock:
            self.idle = [x for x in self.idle if x.alive]
            if len(self.idle) < self.pool_size:
                self.idle.append(s)
                return
        s.close()

    def _refill(self):
        """Top the idle pool up in the background (never on the caption path)."""
        with self.lock:
            if self.refilling or len([x for x in self.idle if x.alive]) >= 1:
                return
            self.refilling = True

        def run():
            try:
                self.warm()
            except Exception as e:  # noqa: BLE001 - the next decode opens its own, or fails and falls back
                print(f"[speech] scribe pool refill failed: {e}")
            finally:
                self.refilling = False
        threading.Thread(target=run, name="scribe-refill", daemon=True).start()

    def _lease(self, utt_id):
        with self.lock:
            s = self.live.get(utt_id)
            if s is not None:
                return s
            while self.idle:
                s = self.idle.pop()
                if s.alive:
                    break
                s = None
        if s is None:
            s = self._open()
        s.utt_id = utt_id
        with self.lock:
            self.live[utt_id] = s
        self._refill()
        return s

    def _drop(self, utt_id):
        with self.lock:
            s = self.live.pop(utt_id, None)
        if s is not None:
            s.close()

    def _release(self, utt_id, s):
        with self.lock:
            self.live.pop(utt_id, None)
            if s.alive and len(self.idle) < self.pool_size:
                with s.cond:
                    s.reset()
                self.idle.append(s)
                return
        s.close()

    def end(self, utt_id):
        """The pipeline dropped this utterance (a blip): its uncommitted audio must not reach the next one."""
        self._drop(utt_id)

    def close(self):
        with self.lock:
            sessions, self.idle, self.live = self.idle + list(self.live.values()), [], {}
        for s in sessions:
            s.close()

    # ---- decoding ----------------------------------------------------------------------------------

    def decode(self, audio, final, use_hint=True, utt_id=None):
        t = time.perf_counter()
        utt_id = utt_id or "_"
        try:
            s = self._lease(utt_id)
            if not s.alive:
                raise ScribeError(s.error)
            if len(audio) < s.sent:              # not the audio this session has seen: start over
                self._drop(utt_id)
                s = self._lease(utt_id)
            new = np.clip(audio[s.sent:] * 32768.0, -32768, 32767).astype(np.int16)
            with s.cond:
                seen = s.partials
            s.send(new, final, self.chunk_n)
            s.sent = len(audio)
            hyp = self._final(s, t) if final else self._partial(s, seen, t)
        except Exception:
            self._drop(utt_id)
            raise
        if final:
            self._release(utt_id, s)
        return hyp

    def _partial(self, s, seen, t):
        deadline = t + self.partial_wait_s
        with s.cond:
            # Wait for a fresher partial, then let any that are still streaming in land (settle_s), so the
            # answer reflects the newest audio rather than the first reply to an early chunk.
            while s.alive and time.perf_counter() < deadline:
                left = deadline - time.perf_counter()
                if not s.cond.wait_for(lambda: s.partials > seen or not s.alive,
                                       timeout=left if s.partials == seen else min(left, self.settle_s)):
                    break
                seen = s.partials
            if not s.alive:
                raise ScribeError(s.error)
            quiet_s = time.perf_counter() - (s.last_msg or s.t_first_audio or t)
            if s.t_first_audio is not None and quiet_s > self.stall_s:
                raise ScribeError(f"no answer for {quiet_s:.1f} s")
            stage = {}
            if s.t_first_partial is not None and s.t_first_audio is not None:
                stage["first_partial"] = round((s.t_first_partial - s.t_first_audio) * 1000, 1)
            return Hypothesis(text=s.partial.strip(), ms=(time.perf_counter() - t) * 1000, engine=self.engine,
                              stage=stage)

    def _final(self, s, t):
        with s.cond:
            if not s.cond.wait_for(lambda: s.committed is not None or not s.alive, timeout=self.final_timeout_s):
                raise ScribeError(f"no committed transcript within {self.final_timeout_s} s")
            if s.committed is None:
                raise ScribeError(s.error)
            text = s.committed.strip()
            stage = {"commit": round((time.perf_counter() - s.t_commit) * 1000, 1)}
            if s.t_first_partial is not None and s.t_first_audio is not None:
                stage["first_partial"] = round((s.t_first_partial - s.t_first_audio) * 1000, 1)
            # Scribe has no per-segment logprob here: report the text as one confident segment, so the
            # pipeline's whisper-doubt filter (logprob_min) doesn't apply; inventions/diffuse/nonspeech still do.
            return Hypothesis(text=text, no_speech=not text, ms=(time.perf_counter() - t) * 1000, engine=self.engine,
                              segments=[(text, 0.0)] if text else [], stage=stage)


# ---- a deterministic engine for tests ----------------------------------------------------------------

class FakeTranscriber:
    """Deterministic, offline: each utterance reads the next line of `lines` (cycling), revealing words as the
    audio grows (`words_per_s`), the whole line on the final. Failure injection for tests: set `fail` to an
    exception (or a callable(final) -> exception or None) to raise it, `delay_s` to answer slowly. `engine` is
    the contract value it reports (the contract only knows faster_whisper / scribe_realtime); with --engine fake
    the heartbeat detail says it's fake."""

    streaming = True

    def __init__(self, lines=("hello there",), words_per_s=2.5, engine="faster_whisper", delay_s=0.0, hinting=False):
        self.lines, self.words_per_s, self.engine = list(lines), words_per_s, engine
        self.delay_s, self.hinting, self.fail = delay_s, hinting, None
        self.assigned: dict[str, str] = {}
        self.calls: list[tuple[str | None, bool, int]] = []      # (utt_id, final, samples)
        self.ended: list[str] = []
        self.count = 0
        self.names = []

    def set_names(self, names):
        self.names = list(names)

    def warm(self):
        f = self.fail(None) if callable(self.fail) else self.fail
        if f:
            raise f

    def end(self, utt_id):
        self.ended.append(utt_id)
        self.assigned.pop(utt_id, None)

    def decode(self, audio, final, use_hint=True, utt_id=None):
        self.calls.append((utt_id, final, len(audio)))
        f = self.fail(final) if callable(self.fail) else self.fail
        if f:
            raise f
        if self.delay_s:
            time.sleep(self.delay_s)
        key = utt_id or f"_{self.count}"
        if key not in self.assigned:
            self.assigned[key] = self.lines[self.count % len(self.lines)]
            self.count += 1
        words = self.assigned[key].split()
        if not final:
            words = words[:int(len(audio) / SR * self.words_per_s)]
        else:
            self.assigned.pop(key, None)
        text = " ".join(words)
        return Hypothesis(text=text, ms=self.delay_s * 1000, engine=self.engine,
                          segments=[(text, -0.1)] if final and text else [])


# ---- fallback ----------------------------------------------------------------------------------------

class FallbackTranscriber:
    """Primary engine with automatic fallback (errors or answers slower than max_latency_s), mid-session.

    While the primary is down every decode goes to the fallback, and every retry_s a background thread calls
    primary.warm(); when that succeeds the next decode is back on the primary. `reason` is the honest
    heartbeat detail (None while the primary is healthy)."""

    streaming = True

    def __init__(self, primary: Transcriber, fallback: Transcriber, max_latency_s=1.5, retry_s=30.0):
        self.primary, self.fallback = primary, fallback
        self.max_latency_s, self.retry_s = max_latency_s, retry_s
        self.down_until = 0.0
        self.reason = None
        self.probing = False
        self.probe_lock = threading.Lock()
        self.switches = []            # (time, engine) each time captions changed engine

    @property
    def up(self):
        return self.reason is None or (time.time() >= self.down_until and not hasattr(self.primary, "warm"))

    @property
    def engine(self):
        return self.primary.engine if self.up else self.fallback.engine

    @property
    def hinting(self):
        return getattr(self.primary if self.up else self.fallback, "hinting", False)

    def set_names(self, names):
        for engine in (self.primary, self.fallback):
            if hasattr(engine, "set_names"):
                engine.set_names(names)

    def warm(self):
        """At startup: try the primary once (synchronously); if it's down, start on the fallback."""
        if hasattr(self.primary, "warm"):
            try:
                self.primary.warm()
            except Exception as e:  # noqa: BLE001
                self._down(f"{self.primary.engine} unavailable: {e}")

    def end(self, utt_id):
        for engine in (self.primary, self.fallback):
            if hasattr(engine, "end"):
                engine.end(utt_id)

    def close(self):
        for engine in (self.primary, self.fallback):
            if hasattr(engine, "close"):
                engine.close()

    def decode(self, audio, final, use_hint=True, utt_id=None):
        if self.up:
            t = time.perf_counter()
            try:
                hyp = decode_call(self.primary, audio, final, use_hint, utt_id)
                took = time.perf_counter() - t
                if took > self.max_latency_s:
                    self._down(f"{self.primary.engine} slow ({took:.1f} s)")
                elif self.reason is not None:          # a primary without warm(): retried on the caption path
                    self.reason = None
                    self.switches.append((time.time(), self.primary.engine))
                return hyp
            except Exception as e:  # noqa: BLE001 - any failure means fall back, with the whole utterance
                self._down(f"{self.primary.engine} failed: {e}")
                if hasattr(self.primary, "end") and utt_id is not None:
                    self.primary.end(utt_id)
        return decode_call(self.fallback, audio, final, use_hint, utt_id)

    def _down(self, reason):
        was_up = self.reason is None
        self.down_until = time.time() + self.retry_s
        self.reason = f"{reason} -> {self.fallback.engine}"
        if was_up:
            self.switches.append((time.time(), self.fallback.engine))
        print(f"[speech] {self.reason}")
        self._start_probe()

    def _start_probe(self):
        with self.probe_lock:
            if hasattr(self.primary, "warm") and not self.probing and self.reason is not None:
                self.probing = True
                threading.Thread(target=self._probe, name="transcriber-probe", daemon=True).start()

    def _probe(self):
        """Background, while the primary is down: try primary.warm() every retry_s (speech or not, so the
        heartbeat recovers on its own); the first success puts captions back on the primary."""
        try:
            while self.reason is not None:
                time.sleep(max(0.0, self.down_until - time.time()))
                try:
                    self.primary.warm()
                except Exception as e:  # noqa: BLE001
                    self.down_until = time.time() + self.retry_s
                    print(f"[speech] {self.primary.engine} still down ({e}); next try in {self.retry_s:.0f} s")
                else:
                    self.reason = None
                    self.switches.append((time.time(), self.primary.engine))
                    print(f"[speech] {self.primary.engine} back: captions use it again")
        finally:
            with self.probe_lock:
                self.probing = False
            self._start_probe()             # it went down again while this probe was finishing


def make_transcriber(cfg) -> tuple[Transcriber, str | None]:
    """(transcriber, degraded detail or None) from the `speech:` config section.

    engine: faster_whisper | scribe_realtime (whisper fallback) | fake (deterministic, no models)."""
    engine = cfg.get("engine", "faster_whisper")
    if engine == "fake":
        return FakeTranscriber(**(cfg.get("fake") or {})), "fake transcriber (no ASR)"
    whisper = WhisperTranscriber(cfg["model"], cfg["device"], cfg["compute_type"], cfg["cpu_model"],
                                 cfg["cpu_compute_type"], cfg["language"], cfg["final_beam_size"],
                                 cfg["no_speech_drop"], cfg.get("name_hints", "hotwords"),
                                 cfg.get("compression_max", 2.4))
    if engine == "scribe_realtime":
        try:
            scribe = ScribeRealtimeTranscriber(**(cfg.get("scribe") or {}))
        except TranscriberUnavailable as e:
            print(f"[speech] Scribe Realtime unavailable ({e}); using faster-whisper")
            return whisper, f"scribe unavailable ({e}) -> whisper"
        fb = FallbackTranscriber(scribe, whisper, cfg.get("fallback_max_latency_s", 1.5),
                                 cfg.get("fallback_retry_s", 30.0))
        fb.warm()
        print(f"[speech] engine: {fb.engine}" + (f" ({fb.reason})" if fb.reason else ""))
        return fb, None                  # fb.reason is the live detail (heartbeat) while whisper stands in
    return whisper, whisper.degraded

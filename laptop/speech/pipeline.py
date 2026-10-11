"""Streaming speech: audio in, `speech_start` + `utterance` out (SPEC §10, §12.6, §13).

Silero VAD segments speech. At onset the pipeline sends `speech_start` with the current direction
(fast path, before any words). While the utterance grows it is re-decoded every partial_every_s;
words that two consecutive decodes agree on move from `pending` into `text` (local agreement). A
final decode follows end_silence_s after speech stops. Each update carries the circular-mean bearing
of the speech directions during the utterance, its spread, an overlap flag, loudness and a tone.

The wearer stands within 0.5 m of the array (SPEC §5), so their own voice arrives from one fixed world
bearing (`audio.wearer_bearing_deg`, set by `python -m laptop.audio.calibrate --wearer`). Speech from
within `wearer_ignore_deg` of it is never captioned and never sends speech_start (so the belt stays
still); an utterance that only turns out to be the wearer later is retracted with an empty final.

Fixed beams (`audio.fixed_beams`): the firmware's DoA names the beam (0.8 / -179.2), not the talker. With the
raw mics, `locate_speech` (laptop/audio/localize.SpeechBearings) finds the talker on that beam's side of the
room; speech_start and every utterance update carry that bearing and its spread. Without a confident raw-mic
bearing the beam's bearing goes out with the beam's width (`audio.fixed_beam_spread_deg`) as the spread.

Adapted from v1 speech_here (same segmentation and agreement logic); the engine is now behind
transcriber.py so Scribe can replace whisper with automatic fallback.
"""

import asyncio
import math
import re
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from functools import partial as bind

import numpy as np

from contracts.python.here_contract import Source, SpeechStart, Utterance
from laptop.geometry import angdiff

from . import tone as tones
from .names import NameMatcher
from .transcriber import decode_call

SR = 16000


def norm(word):
    return re.sub(r"[^\w']", "", word.lower())


def common_prefix(a, b):
    n = 0
    for x, y in zip(a, b):
        if norm(x) != norm(y):
            break
        n += 1
    return n


def sentences(text):
    """Normalized sentences: "Thank you. Thanks!" -> ["thank you", "thanks"]."""
    return [" ".join(norm(w) for w in part.split()) for part in re.split(r"[.!?]+", text) if part.strip()]


def db(x):
    rms = float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0
    return 20 * math.log10(max(rms, 1e-6))


def speech_db(x, frame=512):
    """Level of the voiced part: mean dB of the louder half of 32 ms frames (pauses don't drag it down)."""
    n = len(x) // frame
    if n < 2:
        return db(x)
    frames = x[:n * frame].reshape(n, frame)
    levels = np.sort(20 * np.log10(np.maximum(np.sqrt(np.mean(frames ** 2, axis=1)), 1e-6)))
    return float(np.mean(levels[n // 2:]))


# Sounds that are never someone talking: a stream whose own classifier hears these (more than speech) during
# an utterance isn't captioned (a baby crying, the doorbell the array flags as speech). Laughter isn't here.
NONSPEECH = ("doorbell", "dog_barking", "baby_crying", "knock", "alarm", "loud_bang", "phone_ringing", "music", "tv",
             "car_horn", "vehicle", "siren", "door")


class Crosstalk:
    """Several streams (beams) hear one talker: the same words at the same time get one caption.

    Every published text is registered by stream with its level; a stream's text that overlaps another
    stream's in time with mostly the same words, where the other stream heard it at least as loud, is a
    duplicate: the beam pointing at the talker keeps it, the one it leaked into drops it."""

    def __init__(self, keep_s=20.0, same=0.5):
        self.items = {}                   # utt_id -> (stream, t0, t1, words, time)
        self.keep_s, self.same = keep_s, same

    def register(self, stream, utt_id, t0, t1, text, level=0.0):
        now = time.time()
        self.items = {k: v for k, v in self.items.items() if now - v[4] < self.keep_s}
        words = {norm(w) for w in text.split()} - {""}
        if words:
            self.items[utt_id] = (stream, t0, t1, words, now, level)

    def duplicate(self, stream, utt_id, t0, t1, text, level=0.0):
        words = {norm(w) for w in text.split()} - {""}
        if not words:
            return False
        for uid, (s, a0, a1, w, _, lvl) in self.items.items():
            if s == stream or uid == utt_id:
                continue
            overlap = min(t1, a1) - max(t0, a0)
            if (overlap > 0.3 * min(t1 - t0, a1 - a0) and len(words & w) / len(words | w) >= self.same
                    and lvl >= level - 1.0):
                return True
        return False


class SileroVad:
    def __init__(self):
        import torch
        from silero_vad import load_silero_vad

        self.torch = torch
        self.model = load_silero_vad()

    def __call__(self, win):
        return self.model(self.torch.from_numpy(win), SR).item()


def beam_mask(own, other, power=4.0, nfft=512, hop=128):
    """Keep what this beam hears louder than the other beam, bin by bin (time-frequency masking).

    A 4-mic beamformer separates two talkers by only ~6-10 dB, and whisper happily transcribes the other
    talker leaking in at -9 dB (tools/stress_test.py --two-source). Speech is sparse in time-frequency, so
    giving each STFT bin to the beam where it's stronger turns that into far cleaner per-talker audio."""
    from scipy.signal import istft, stft

    if len(own) < nfft or other is None:
        return own
    _, _, a = stft(own, SR, nperseg=nfft, noverlap=nfft - hop)
    _, _, b = stft(other[: len(own)], SR, nperseg=nfft, noverlap=nfft - hop)
    pa, pb = np.abs(a) ** power, np.abs(b) ** power
    _, y = istft(a * (pa / (pa + pb + 1e-12)), SR, nperseg=nfft, noverlap=nfft - hop)
    return y[: len(own)].astype(np.float32)


class Segment:
    def __init__(self, utt_id, t_start, audio, other=None):
        self.utt_id = utt_id
        self.t_start = t_start
        self.chunks = [audio]
        self.other_chunks = None if other is None else [other]   # the other beam, sample-aligned (beams mode)
        self.n = len(audio)
        self.preroll_s = len(audio) / SR
        self.silence_s = 0.0
        self.committed = []        # words that won't change before the final
        self.prev_words = None
        self.decoded_n = 0
        self.final = False
        self.onset = None          # Source used for speech_start
        self.started = False       # speech_start sent (False while it looks like the wearer talking)
        self.echo = False          # a decode looped on the name hint: its name isn't trusted
        self.name_alerted = False
        self.doa_speech_s = 0.0    # time the array also heard speech during this segment
        self.windows = 0

    def add(self, audio, other=None):
        self.chunks.append(audio)
        if self.other_chunks is not None and other is not None:
            self.other_chunks.append(other)
        self.n += len(audio)

    @property
    def other(self):
        if self.other_chunks is None:
            return None
        if len(self.other_chunks) > 1:
            self.other_chunks = [np.concatenate(self.other_chunks)]
        return self.other_chunks[0]

    def decode_audio(self, n=None):
        """What the transcriber gets: this beam, masked against the other one when there is one."""
        audio = self.audio if n is None else self.audio[:n]
        other = self.other
        return audio if other is None else beam_mask(audio, other[: len(audio)])

    @property
    def audio(self):
        if len(self.chunks) > 1:
            self.chunks = [np.concatenate(self.chunks)]
        return self.chunks[0]

    @property
    def duration(self):
        return self.n / SR


class SpeechPipeline:
    """cfg = the `speech:` section. emit(Message) sends to the relay; on_name(...) raises name_called."""

    def __init__(self, cfg, transcriber, history, emit, on_name=None, vad=None, names=(),
                 wearer_bearing=None, wearer_ignore_deg=20.0, locate_speech=None):
        self.cfg = cfg
        # (utt_id, t0, t1, beam bearing) -> (bearing, coherence, spread, looks) or None: the talker's bearing on the
        # raw mics in a fixed beam's half of the room (ReSpeaker / FixturePlayer .locate_speech)
        self.locate_speech = locate_speech
        self.wearer_bearing = wearer_bearing        # world bearing of the wearer's mouth; None = filter off
        self.wearer_ignore_deg = wearer_ignore_deg
        self.transcriber = transcriber
        self.history = history
        self.emit = emit
        self.on_name = on_name
        self.vad = vad if vad is not None else SileroVad()
        self.names = NameMatcher(names, cfg["name_match_ratio"])
        self.inventions = {" ".join(norm(w) for w in p.split()) for p in cfg.get("hallucinations", [])}
        self.pool = ThreadPoolExecutor(max_workers=1)   # one decode at a time, in order
        self.busy = False
        self.buf = np.zeros(0, np.float32)              # not yet VAD'd
        self.buf_t = None                               # time of buf[0]
        self.preroll = np.zeros(0, np.float32)
        self.obuf = np.zeros(0, np.float32)             # the other beam, aligned with buf / preroll (beams mode)
        self.opreroll = np.zeros(0, np.float32)
        self.array_since = None                         # start of the array's current speech run
        self.seg = None
        self.utt_count = 0
        self.prefix = f"u{int(time.time()) % 100000}"
        self.room_db = -50.0
        self.speech_ref_db = None                       # running "normal talking" level; loudness is relative to it
        self.laughs = deque(maxlen=120)                 # (w0, w1, PANNs laughter score), from the sounds pipeline
        self.nonspeech = deque(maxlen=120)              # (w0, w1, label) windows a non-speech sound dominated
        self.stream = 0                                 # audio stream (beam) this pipeline listens to
        self.crosstalk = None                           # shared Crosstalk when there are several streams
        self.tasks = set()

    # ---- input --------------------------------------------------------------

    def note_sounds(self, w0, w1, scores):
        """Sounds pipeline hook: every classified window's scores (only laughter is used, for the tone)."""
        score = scores.get("laughter", (0.0, ""))[0]
        if score >= 0.05:
            self.laughs.append((w0, w1, score))
        speech = scores.get("speech", (0.0, ""))[0]
        labels = self.cfg.get("nonspeech_labels") or NONSPEECH
        label, best = max(((k, scores.get(k, (0.0, ""))[0]) for k in labels), key=lambda kv: kv[1])
        if best >= self.cfg.get("nonspeech_min", 0.3) and best > speech:
            self.nonspeech.append((w0, w1, label))

    def nonspeech_share(self, t0, t1):
        """Share of [t0, t1] covered by windows where a non-speech sound beat speech (that stream's classifier)."""
        if t1 <= t0 or not self.nonspeech:
            return 0.0, None
        covered, labels = 0.0, []
        for w0, w1, label in self.nonspeech:
            o = min(t1, w1) - max(t0, w0)
            if o > 0:
                covered += o
                labels.append(label)
        return min(1.0, covered / (t1 - t0)), (max(set(labels), key=labels.count) if labels else None)

    def laughter(self, t0, t1):
        """Highest laughter score in windows overlapping the utterance (and just after it: people laugh at the end)."""
        return max((s for w0, w1, s in self.laughs if w1 > t0 and w0 < t1 + 0.3), default=0.0)

    def set_names(self, names):
        self.names.set_names(names)
        if hasattr(self.transcriber, "set_names"):     # bias the decoder toward the name (name called in noise)
            self.transcriber.set_names(names)

    async def run(self, queue):
        while True:
            t, pcm = await queue.get()
            await self.feed(t, pcm)

    async def feed(self, t, pcm):
        """pcm: this stream (1-D), or every stream (2-D, beams mode): column `stream` is ours, the others are
        what the other beam hears (for leak gating and masking)."""
        x = pcm.astype(np.float32) / 32768.0 if pcm.dtype != np.float32 else pcm
        other = None
        if x.ndim == 2:
            other = np.delete(x, self.stream, axis=1).sum(axis=1) if x.shape[1] > 1 else None
            x = x[:, self.stream]
        if self.buf_t is None or len(self.buf) == 0 or abs(t - (self.buf_t + len(self.buf) / SR)) > 0.25:
            self.buf, self.buf_t = np.zeros(0, np.float32), t      # first block, or a gap: restart the clock
            self.obuf = np.zeros(0, np.float32)
        self.buf = np.concatenate([self.buf, x])
        if other is not None:
            self.obuf = np.concatenate([self.obuf, other])
        win_n = self.cfg["vad_window"]
        while len(self.buf) >= win_n:
            win, self.buf = self.buf[:win_n], self.buf[win_n:]
            owin = None
            if other is not None:
                owin, self.obuf = self.obuf[:win_n], self.obuf[win_n:]
            wt = self.buf_t
            self.buf_t += win_n / SR
            self.on_window(win, wt, owin)

    def doa_gating(self, t):
        """True when the direction source reports speech at all (the ReSpeaker, ODAS, fixtures), so it
        can gate segmentation. In crowd babble Silero hears speech all the time; the array tells us
        whether *a talker near it* is speaking (tools/stress_test.py). Mic + slider without the speech
        flag set, or no direction source: VAD alone, as before.
        `history.reports_speech` (the input can flag speech: the ReSpeaker, ODAS, a fixture that has the flag)
        gates from the first second; before, gating waited for the first flagged speech, so babble at startup
        went through VAD alone and was glued onto the first real line (real-array stress test)."""
        return self.cfg["endpoint_use_doa"] and (getattr(self.history, "reports_speech", False)
                                                 or t - self.history.last_speech_t < 30.0)

    def doa_speaking(self, t):
        return t - self.history.last_speech_t <= self.cfg["doa_quiet_s"]

    def on_window(self, win, t, owin=None):
        c = self.cfg
        p = self.vad(win)
        if owin is not None and db(win) < db(owin) - c.get("beam_leak_db", 3.0):
            p = 0.0                      # the other beam is louder: that's its talker leaking into ours
        end_t = t + len(win) / SR
        gating = self.doa_gating(end_t)
        if self.seg is None:
            keep = max(c["preroll_s"], c.get("array_lookback_s", 0.0))
            self.preroll = np.concatenate([self.preroll, win])[-int(keep * SR):]
            if owin is not None:
                self.opreroll = np.concatenate([self.opreroll, owin])[-int(keep * SR):]
            array_hears = self.doa_speaking(end_t)
            if not array_hears:
                self.array_since = None
            elif self.array_since is None:
                self.array_since = t                       # the array started hearing a talker here
            if p < c["vad_continue"]:
                self.room_db = 0.98 * self.room_db + 0.02 * db(win)
            if p >= c["vad_start"] and (not gating or array_hears):
                self.utt_count += 1
                lead = c["preroll_s"]
                if gating and self.array_since is not None:
                    # On the real array Silero often only fires a few words in ("Hey Kori, the" scored
                    # 0.01-0.32 while the array already heard speech): start where the array did.
                    lead = min(max(lead, end_t - self.array_since + c["preroll_s"]), c["array_lookback_s"])
                audio = self.preroll[-int(lead * SR):]
                other = self.opreroll[-len(audio):] if owin is not None and len(self.opreroll) >= len(audio) else None
                self.seg = Segment(f"{self.prefix}-{self.utt_count:04d}", end_t - len(audio) / SR, audio, other)
                self.seg.preroll_s = min(self.seg.preroll_s, c["preroll_s"])   # the array-heard lead is speech
                self.preroll = np.zeros(0, np.float32)
                self.opreroll = np.zeros(0, np.float32)
                self.start(self.seg, end_t)
            return

        seg = self.seg
        seg.add(win, owin)
        seg.windows += 1
        array_hears = self.doa_speaking(end_t)
        if array_hears:
            seg.doa_speech_s += len(win) / SR
        quiet = p < c["vad_continue"] or (gating and not array_hears)
        seg.silence_s = seg.silence_s + len(win) / SR if quiet else 0.0
        if gating and not array_hears:     # the talker went quiet when the array last heard them
            seg.silence_s = max(seg.silence_s, end_t - self.history.last_speech_t)
        turn = gating and seg.windows % 4 == 0 and self.turn_changed(seg, end_t)
        if seg.silence_s >= c["end_silence_s"] or seg.duration >= c["max_utterance_s"] or turn:
            self.seg = None
            seg.final = True
            voiced = seg.duration - seg.silence_s - seg.preroll_s
            if voiced >= c["min_utterance_s"] and (not gating or seg.doa_speech_s >= c["doa_min_speech_s"]):
                self.spawn(self.finalize(seg))
            else:   # a blip, or crowd noise the array never heard as a talker: clear the typing dots
                self.spawn(self.retract(seg))
            if turn:  # someone else took over without a pause: their utterance starts now
                self.utt_count += 1
                self.seg = Segment(f"{self.prefix}-{self.utt_count:04d}", end_t - len(win) / SR, win.copy(),
                                   None if owin is None else owin.copy())
                self.start(self.seg, end_t)
        elif not self.busy and seg.n - seg.decoded_n >= self.partial_step(seg) * SR:
            self.busy = True                  # set now, not in the task, or several partials queue up
            self.spawn(self.partial(seg))

    def partial_step(self, seg):
        """Audio between partial decodes: `first_words_every_s` until a decode has produced words (whisper finds
        none in the first ~0.2 s of a voice, and the next try used to wait partial_every_s), then partial_every_s."""
        c = self.cfg
        if not seg.prev_words and not seg.committed:
            return c.get("first_words_every_s", c["partial_every_s"])
        return c["partial_every_s"]

    def turn_changed(self, seg, t):
        """The speech direction moved > turn_change_deg and stayed there: a new talker, even with no pause."""
        c = self.cfg
        if t - seg.t_start < 1.0:
            return False
        recent = self.history.speech_bearing(t - 0.5, t, c["bearing_min_confidence"], c["overlap_spread_deg"])
        before = self.history.speech_bearing(seg.t_start, t - 0.5, c["bearing_min_confidence"], c["overlap_spread_deg"])
        if recent is None or before is None or recent.samples < 6:
            return False
        return abs(angdiff(recent.bearing_deg, before.bearing_deg)) > c["turn_change_deg"]

    def heard_talker(self, seg):
        """The array heard a real talker for longer than hallucination_array_s during this utterance."""
        return seg.doa_speech_s > self.cfg.get("hallucination_array_s", 1.0)

    def invented(self, seg, text):
        """Whisper's classic inventions on noise ("Thank you.", "Thanks for watching!", a lone "um"), unless the
        array heard a real talker."""
        parts = sentences(text)
        filler = all(re.fullmatch(r"(?:m+|h+m+|a+h+|o+h+|u+h+|u+m+|e+h+|h+a+h+)( (?:m+|h+m+|a+h+|o+h+|u+h+|u+m+))*", p)
                     for p in parts)                  # "Mmmmmmm." is a cry or a hum, never a caption
        return bool(parts) and (filler or (all(p in self.inventions for p in parts) and not self.heard_talker(seg)))

    def is_name(self, token):
        return self.names.find(token) is not None

    def respell(self, text):
        """speech.spellings: names the way we write them ("Corey" -> "Kori"). Whole words only and never adds
        text. Every name in whisper's prompt was tried instead (2026-10-10): it wrote the prompt back on noise."""
        spellings = self.cfg.get("spellings") or {}
        if not spellings:
            return text
        if getattr(self, "_spell_key", None) is not spellings:
            import re
            self._spell = [(re.compile(rf"\b{re.escape(v)}\b", re.IGNORECASE), name)
                           for name, variants in spellings.items() for v in variants]
            self._spell_key = spellings
        for rx, name in self._spell:
            text = rx.sub(name, text)
        return text

    def strip_echo(self, text):
        """With the name as a decoding hint, whisper sometimes writes it back on noise, in a loop ("Kori. Kori.
        Kori."): collapse runs of 2+ name tokens to one. A name in a sentence stays ("Hey Kori, the pizza").
        A collapsed name is untrusted: name called then needs the decode without the hint to agree."""
        if not getattr(self.transcriber, "hinting", False):
            return text
        tokens, out, i = text.split(), [], 0
        while i < len(tokens):
            j = i
            while j < len(tokens) and self.is_name(tokens[j]):
                j += 1
            out += tokens[i:min(j, i + 1)] if j > i else []
            out += tokens[j:j + 1]
            i = j + 1
        return " ".join(out)

    def name_only(self, text):
        """Just the name, or the name plus whisper's inventions ("Kori. Thank you."): the hint written back on noise
        looks like this (real array, 2 false name alerts in one run), so it needs the decode without the hint."""
        tokens = text.split()
        if not tokens or not getattr(self.transcriber, "hinting", False):
            return False
        if all(self.is_name(t) for t in tokens):
            return True
        parts = [p for p in re.split(r"[.!?,]+", text) if p.strip()]
        named = [p for p in parts if all(self.is_name(t) for t in p.split())]
        rest = [" ".join(norm(w) for w in p.split()) for p in parts if p not in named]
        return bool(named) and bool(rest) and all(r in self.inventions for r in rest)

    def diffuse(self, seg, t_end):
        """Confident speech readings from all over the place: crowd babble, not one talker (a real talker's
        readings spread ~0.5 deg on the array)."""
        c = self.cfg
        if not self.doa_gating(t_end):
            return False
        est = self.history.speech_bearing(seg.t_start, t_end, c["bearing_min_confidence"], c["overlap_spread_deg"])
        return est is not None and est.samples >= 6 and est.spread_deg > c.get("diffuse_spread_deg", 60.0)

    def clean(self, seg, hyp, final, t_end):
        """What may be shown of a decode: '' when it's noise (inventions, name echoes, diffuse babble)."""
        text = hyp.text
        if final and hyp.segments and not self.heard_talker(seg):   # whisper guessing, and no talker to back it
            text = " ".join(t for t, lp in hyp.segments if lp >= self.cfg.get("logprob_min", -1.0)).strip()
        text = self.respell(self.strip_echo(text))
        if not any(norm(w) for w in text.split()):     # empty, or only punctuation ("...", " - ")
            return ""
        if self.invented(seg, text) or self.diffuse(seg, t_end):
            return ""
        share, label = self.nonspeech_share(seg.t_start, t_end)
        if final and share >= self.cfg.get("nonspeech_share", 0.5):
            print(f"[speech] {seg.utt_id} not captioned: {label} for {share:.0%} of it ({text[:40]!r})")
            return ""
        if self.crosstalk is not None and self.crosstalk.duplicate(self.stream, seg.utt_id, seg.t_start, t_end, text,
                                                                   speech_db(seg.audio)):
            if final:
                print(f"[speech] {seg.utt_id} beam {self.stream}: same words as the other beam, not repeated")
            return ""
        return text

    def spawn(self, coro):
        task = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    # ---- fast path ------------------------------------------------------------

    def is_wearer(self, bearing):
        return (self.wearer_bearing is not None and bearing is not None
                and abs(angdiff(bearing, self.wearer_bearing)) <= self.wearer_ignore_deg)

    def start(self, seg, t):
        """speech_start the moment VAD hears speech, with the newest direction (SPEC §10)."""
        src = self.history.onset(t, self.cfg["onset_lookback_s"])
        seg.onset = src
        if src is not None and src.speech and self.is_wearer(src.bearing_deg):
            return                     # the wearer: no typing dots, no buzz (sent later if it's someone else)
        if src is not None:            # fixed beam: where the talker is (the segment so far: Silero fires words in)
            found = self.talker_bearing(seg, t, src.bearing_deg)
            if found is not None:
                src = replace(src, bearing_deg=found[0])
        self.send_start(seg, t, src)

    def talker_bearing(self, seg, t1, beam_deg):
        """Fixed beams only: the talker's raw-mic bearing for this utterance, restricted to the beam's half of the
        room (localize.SpeechBearings), or None (no raw mics, not confident yet, not a fixed beam)."""
        if self.locate_speech is None or not getattr(self.history, "fixed_beam", False):
            return None
        try:
            return self.locate_speech(seg.utt_id, seg.t_start, t1, beam_deg)
        except Exception as e:          # never lose a caption over a bearing
            print(f"[speech] raw-mic bearing failed: {e!r}")
            return None

    def send_start(self, seg, t, src):
        seg.started = True
        self.emit(SpeechStart(src="laptop", t=t, utt_id=seg.utt_id,
                              bearing_deg=src.bearing_deg if src else 0.0,
                              source_id=src.id if src else 0,
                              confidence=src.confidence if src and src.speech else 0.0))

    # ---- decoding -------------------------------------------------------------

    def decode(self, seg, audio, final, use_hint=True):
        """Run in the worker thread: the engine gets the utterance id when it streams (one Scribe session each)."""
        return asyncio.get_running_loop().run_in_executor(
            self.pool, bind(decode_call, self.transcriber, audio, final, use_hint, seg.utt_id))

    async def partial(self, seg):
        try:
            n = seg.n
            hyp = await self.decode(seg, seg.decode_audio(n), False)
            seg.decoded_n = n
        finally:
            self.busy = False
        if seg.final:
            return                                       # the final already went out (or will)
        text = self.clean(seg, hyp, False, seg.t_start + n / SR)
        words = [] if self.name_only(text) else text.split()    # a lone name waits for the final's check
        seg.echo = seg.echo or self.strip_echo(hyp.text) != hyp.text   # the hint looped: no name alert from it
        if not words and not seg.committed:
            seg.prev_words = words
            return                                       # nothing to show yet (the typing indicator covers it)
        if seg.prev_words is not None:
            agreed = common_prefix(seg.prev_words, words)
            if agreed > len(seg.committed):
                seg.committed = words[:agreed]
        seg.prev_words = words
        k = len(seg.committed)
        self.publish(seg, " ".join(seg.committed), " ".join(words[k:]), False, seg.t_start + n / SR, hyp)

    async def finalize(self, seg):
        audio = seg.decode_audio()
        hyp = await self.decode(seg, audio, True)
        t_end = seg.t_start + seg.duration - seg.silence_s
        text = self.clean(seg, hyp, True, t_end)
        echoed = self.strip_echo(hyp.text) != hyp.text
        seg.echo = False                                 # the final decides afresh
        if echoed or (text and self.name_only(text)):
            # The hint looped ("Kori. Kori. Kori.") or it's just the name ("Kori!"): real, or the hint written
            # back on noise (a doorbell in babble did exactly that)? Decode once more without the hint.
            plain = await self.decode(seg, audio, True, False)
            confirmed = self.names.find(plain.text) is not None
            if text and self.name_only(text):
                if confirmed:                            # really called: show what was said around it
                    hyp, text = plain, self.clean(seg, plain, True, t_end) or text
                else:
                    print(f"[speech] {seg.utt_id} dropped {hyp.text!r} (name hint echo; plain: {plain.text!r})")
                    text = ""
            elif not confirmed:
                seg.echo = True                          # keep the words, but no name alert from them
                print(f"[speech] {seg.utt_id} hint loop {hyp.text!r}: name not confirmed (plain: {plain.text!r})")
        if not text:                                     # noise after all: retract caption / typing indicator
            if hyp.text:
                print(f"[speech] {seg.utt_id} dropped {hyp.text!r} (noise)")
            self.publish(seg, "", "", True, t_end, hyp)
            return
        self.publish(seg, text, "", True, t_end, hyp)
        print(f"[speech] {seg.utt_id} {text}")

    async def retract(self, seg):
        self.publish(seg, "", "", True, seg.t_start + seg.duration, None)
        if hasattr(self.transcriber, "end"):     # a streaming engine frees this utterance's session
            await asyncio.get_running_loop().run_in_executor(self.pool, self.transcriber.end, seg.utt_id)

    def publish(self, seg, text, pending, final, t_end, hyp):
        c = self.cfg
        est = self.history.speech_bearing(seg.t_start, t_end, c["bearing_min_confidence"], c["overlap_spread_deg"])
        wearer = est is not None and self.is_wearer(est.bearing_deg)
        if not wearer and est is not None and self.wearer_bearing is not None:
            # someone else: their bearing without the wearer's readings pulling it
            est = self.history.speech_bearing(seg.t_start, t_end, c["bearing_min_confidence"],
                                              c["overlap_spread_deg"], (self.wearer_bearing, self.wearer_ignore_deg)) or est
        if est is not None:
            bearing, spread, source_id, overlap = est.bearing_deg, est.spread_deg, est.source_id, est.overlap
        else:   # no confident speech direction during the utterance: keep the onset guess, flag it as wide
            o = seg.onset
            bearing = o.bearing_deg if o else 0.0
            source_id = o.id if o else 0
            spread = 0.0 if o is not None and o.speech else 180.0
            overlap = False
        wearer = wearer or (est is None and spread == 0.0 and self.is_wearer(bearing))
        if not wearer and getattr(self.history, "fixed_beam", False):
            # The fixed beam's readings all sit on the beam's own bearing (spread 0 claimed a precision they don't
            # have): the talker's raw-mic bearing when there is one, else the beam with the beam's width.
            found = self.talker_bearing(seg, t_end, bearing)
            if found is not None:
                bearing, spread = found[0], found[2]
                overlap = overlap or spread > c["overlap_spread_deg"]
            else:
                spread = max(spread, self.history.fixed_beam_spread_deg)
        if wearer:
            if not (final and seg.started):
                return                   # the wearer talking: nothing on the phone
            text = pending = ""          # it started as someone else: retract the caption / typing dots
        elif not seg.started:
            if final and not text:
                return                   # never shown, nothing to retract
            # Looked like the wearer at onset, but it's someone else: start it now, from where they are.
            self.send_start(seg, t_end, Source(id=source_id, bearing_deg=bearing, energy=0.5, speech=True,
                                               confidence=est.confidence if est is not None else 0.0))
        audio = seg.audio
        voiced = audio[int(seg.preroll_s * SR): len(audio) - int(seg.silence_s * SR)]   # drop pre-roll + tail
        level = speech_db(voiced if len(voiced) >= 1024 else audio)
        ref = self.speech_ref_db if self.speech_ref_db is not None else level
        # 0.5 = this room's normal talking level; +-loudness_range_db/2 spans the rest (SPEC §12.6 loudness)
        loud = min(max(0.5 + (level - ref) / c["loudness_range_db"], 0.0), 1.0)
        if final and text:
            self.speech_ref_db = level if self.speech_ref_db is None else 0.85 * self.speech_ref_db + 0.15 * level
        laugh = self.laughter(seg.t_start, t_end)
        tone, tone_conf = tones.classify(f"{text} {pending}", loud, final, c["tone_shout_loudness"],
                                         c["tone_whisper_loudness"], laugh, c.get("tone_laugh_min", 0.3))
        if len(voiced) < 0.5 * SR and tone in ("shouting", "whispering"):
            tone, tone_conf = tones.classify(f"{text} {pending}", 0.5, final)   # too short to judge level
        stage = {"lag": round((time.time() - t_end) * 1000, 1)}
        if hyp is not None:
            stage["asr"] = round(hyp.ms, 1)
            stage.update(hyp.stage)
        if self.crosstalk is not None and (text or pending):
            self.crosstalk.register(self.stream, seg.utt_id, seg.t_start, t_end, f"{text} {pending}", level)
        self.emit(Utterance(
            src="laptop", t=t_end, utt_id=seg.utt_id, t_start=round(seg.t_start, 3), t_end=round(t_end, 3),
            text=text, pending=pending, final=final, bearing_deg=bearing, bearing_spread_deg=spread,
            source_id=source_id, overlap=overlap, tone=tone, tone_confidence=tone_conf, loudness=round(loud, 2),
            engine=hyp.engine if hyp is not None else getattr(self.transcriber, "engine", "faster_whisper"),
            stage_ms=stage))
        # Name called: only on words that won't change (committed or final), once per utterance, never from a
        # decode that looped on the name hint (unless the decode without the hint heard the name too).
        if self.on_name is not None and not seg.name_alerted and not seg.echo:
            hit = self.names.find(text)
            if hit:
                seg.name_alerted = True
                self.on_name(name=hit[0], score=hit[1], bearing=bearing if spread < 180 else None,
                             utt_id=seg.utt_id, t=t_end)

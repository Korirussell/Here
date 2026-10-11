"""Enrolled sounds: "teach HERE your doorbell" (SPEC §15 doorbell, made robust for a loud hall).

A general classifier (PANNs) hears a doorbell under crowd babble only weakly (tools/stress_test.py:
score 0.26 at +10 dB SNR, 0.05 at 0 dB). A doorbell, though, is one specific sound per home (and per
demo: the file the Bluetooth speaker plays). So we also match the enrolled recording itself: a
spectrogram template, whitened against the room (each frequency minus its running median, which removes
steady babble and hum), slid over the last few seconds, scored by normalized correlation. Tonal chimes
survive babble that buries them for a classifier.

    sounds.enrolled: [{label: doorbell, file: fixtures/sounds/doorbell.wav}]   # laptop/config.yaml
"""

from collections import deque
from pathlib import Path

import numpy as np

SR = 16000
NFFT, HOP = 512, 256
BAND = (300.0, 5000.0)


def spectrogram(x):
    """log-magnitude STFT frames (time x freq) in BAND."""
    x = np.asarray(x, np.float32)
    if len(x) < NFFT:
        return np.zeros((0, 1), np.float32)
    win = np.hanning(NFFT).astype(np.float32)
    n = 1 + (len(x) - NFFT) // HOP
    frames = np.lib.stride_tricks.as_strided(x, (n, NFFT), (x.strides[0] * HOP, x.strides[0]))
    mag = np.abs(np.fft.rfft(frames * win, axis=1))
    freqs = np.fft.rfftfreq(NFFT, 1 / SR)
    keep = (freqs >= BAND[0]) & (freqs <= BAND[1])
    return np.log1p(mag[:, keep] / 32.0)


def whiten(spec):
    """Remove what's steady in this stretch (babble floor, hum): each frequency minus its median."""
    return spec - np.median(spec, axis=0, keepdims=True)


class EnrolledSound:
    def __init__(self, label, pcm):
        self.label = label
        t = whiten(spectrogram(pcm))
        energy = t.sum(axis=1)
        t = t[energy > np.percentile(energy, 25)] if len(t) > 8 else t     # drop the quiet tail frames
        # (Tried: matching only the chime's strongest bins. Speech harmonics then matched too: 0.67 in a
        # quiet room. All bins in BAND separate far better; see tools/stress_test.py notes.)
        self.template = (t - t.mean()) / (t.std() + 1e-6)
        self.frames = len(self.template)
        self.seconds = (self.frames * HOP + NFFT) / SR
        self.head = None                 # the sound's first head_s (set_head): an early, partial match

    def set_head(self, seconds):
        """Also match just the first `seconds` of the sound (the "ding" of a ding-dong), so an alert can go out
        before the whole template has been heard."""
        k = max(4, min(self.frames, int(seconds * SR / HOP)))
        h = self.template[:k]
        self.head = (h - h.mean()) / (h.std() + 1e-6)

    def score(self, pcm, head=False):
        """Best normalized correlation of the template (or its head) anywhere in pcm (int16 or float), 0..1."""
        template = self.head if head else self.template
        s = whiten(spectrogram(pcm))
        if template is None or len(s) < len(template):
            return 0.0
        best = 0.0
        for i in range(0, len(s) - len(template) + 1, 2):
            w = s[i:i + len(template)]
            w = (w - w.mean()) / (w.std() + 1e-6)
            best = max(best, float(np.mean(w * template)))
        return max(0.0, best)


class EnrolledMatcher:
    """Keeps its own audio buffer (templates are longer than the classifier's window)."""

    def __init__(self, entries, root):
        self.sounds = []
        for e in entries or []:
            path = Path(e["file"])
            path = path if path.is_absolute() else Path(root) / path
            if path.exists():
                from laptop.audio.mock import read_wav
                snd = EnrolledSound(e["label"], read_wav(path))
                if e.get("head_s"):
                    snd.set_head(float(e["head_s"]))
                    snd.head_threshold = float(e.get("head_threshold", 0.25))
                self.sounds.append((snd, float(e.get("threshold", 0.12))))
            else:
                print(f"[sounds] enrolled sound not found: {path}")
        longest = max((s.seconds for s, _ in self.sounds), default=0.0)
        self.keep = int((longest + 1.5) * SR)
        self.buf = deque()
        self.n = 0

    def __bool__(self):
        return bool(self.sounds)

    def add(self, pcm):
        self.buf.append(pcm)
        self.n += len(pcm)
        while self.buf and self.n - len(self.buf[0]) >= self.keep:
            self.n -= len(self.buf.popleft())

    def scores(self):
        """{label: (score, "enrolled")} for sounds above their threshold in the buffered audio."""
        if not self.sounds or not self.buf:
            return {}
        x = np.concatenate(list(self.buf)).astype(np.float32)
        out = {}
        for snd, threshold in self.sounds:
            sc = snd.score(x)
            if sc >= threshold:
                # Correlation isn't a probability: map a hit onto >= 0.5 so it passes the same event/alert
                # thresholds as the classifier (0.12 -> 0.5, 0.37 -> 1.0).
                out[snd.label] = (min(1.0, 0.5 + (sc - threshold) * 2), "enrolled")
        return out

    def head_scores(self, seconds):
        """Early check: {label: (score, "enrolled_head")} for sounds whose head matches in the last `seconds`."""
        if not self.buf:
            return {}
        x = np.concatenate(list(self.buf))[-int(seconds * SR):].astype(np.float32)
        out = {}
        for snd, _ in self.sounds:
            if snd.head is None:
                continue
            sc = snd.score(x, head=True)
            if sc >= snd.head_threshold:
                out[snd.label] = (min(1.0, 0.5 + (sc - snd.head_threshold)), "enrolled_head")
        return out

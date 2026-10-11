"""SRP-PHAT direction finding on the array's raw mics, for sounds the device's speech-gated DoA ignores.

audio_here keeps the last RAW_BUFFER_S of raw 4-mic audio; when sound_here reports a
`sound_event`, it localizes that event's window here and publishes `sound_doa`.

    python -m audio_here.localize --check     # live: compare the device's DoA with the beam and SRP angles,
                                              # then print AEC_TO_DEVICE_* for config.py (Ctrl+C to finish)
"""

from collections import deque

import numpy as np

try:
    from . import config as C
except ImportError:
    import config as C


class SrpPhat:
    """Far-field, azimuth-only SRP-PHAT for a small planar array."""

    def __init__(self, mics, fs=16000, nfft=C.SRP_NFFT, band=C.SRP_BAND_HZ, c=C.SPEED_OF_SOUND, step_deg=1.0,
                 elevations_deg=(0.0,)):
        """elevations_deg: one row of the grid per elevation (the 3D view uses a hemisphere; a planar array
        can't tell up from down and resolves elevation only coarsely). Grid order: elevation-major."""
        mics = np.asarray(mics, float)
        mics = np.pad(mics, ((0, 0), (0, max(0, 3 - mics.shape[1]))))[:, :3]
        mics = mics - mics.mean(axis=0)
        n = len(mics)
        self.nfft = nfft
        self.pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
        freqs = np.fft.rfftfreq(nfft, 1.0 / fs)
        self.band = (freqs >= band[0]) & (freqs <= band[1])
        az_row = np.arange(0.0, 360.0, step_deg)
        self.el_deg = np.repeat(np.asarray(elevations_deg, float), len(az_row))
        self.az_deg = np.tile(az_row, len(elevations_deg))
        az, el = np.radians(self.az_deg), np.radians(self.el_deg)
        toward = np.stack([np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), np.sin(el)], axis=1)  # toward the source
        toa = -(toward @ mics.T) / c                                               # arrival time vs centre, (A, M)
        dt = np.stack([toa[:, i] - toa[:, j] for i, j in self.pairs], axis=1)     # (A, P)
        # X_i X_j* carries exp(-jw dt_ij) for the true direction; the steering vector undoes it.
        self.steer = np.exp(2j * np.pi * freqs[self.band][None, None, :] * dt[:, :, None])  # (A, P, F)

    def power(self, x, top=C.SRP_TOP_FRAMES, beta=C.SRP_PHAT_BETA):
        """SRP over the azimuth grid for x of shape (samples, mics). Values in [-1, 1]; 1 = fully coherent."""
        x = np.asarray(x, float)
        if len(x) < self.nfft:
            return None
        hop = self.nfft // 2
        win = np.hanning(self.nfft)[:, None]
        X = np.stack([np.fft.rfft(x[s:s + self.nfft] * win, axis=0)[self.band]
                      for s in range(0, len(x) - self.nfft + 1, hop)])            # (T, F, M)
        energy = np.sum(np.abs(X) ** 2, axis=(1, 2))
        keep = max(1, int(round(len(X) * top)))
        X = X[np.argsort(energy)[-keep:]]
        G = np.stack([X[:, :, i] * np.conj(X[:, :, j]) for i, j in self.pairs], axis=1)   # (T, P, F)
        # Partial whitening (PHAT-beta): full PHAT gives noise-only bins the same weight as a doorbell's
        # few tonal bins and loses tones; beta < 1 keeps loud bins heavier.
        G = (G / (np.abs(G) ** beta + 1e-12)).mean(axis=0)
        # Normalised by the total weight: 1 when every pair agrees perfectly on one direction.
        return np.real(np.einsum("apf,pf->a", self.steer, G)) / (np.sum(np.abs(G)) + 1e-12)

    def locate(self, x):
        """(azimuth_deg in the mic-geometry frame, confidence 0..1), or (None, 0.0) if too short."""
        p = self.power(x)
        if p is None:
            return None, 0.0
        k = int(np.argmax(p))
        return float(self.az_deg[k]), float(max(0.0, p[k]))


# AEC_MIC_ARRAY_GEO read from the demo unit (firmware 2.1.1): a 66.6 mm square, mics at -45/45/135/-135 deg.
XVF3800_GEOMETRY = [(0.0333, -0.0333, 0.0), (0.0333, 0.0333, 0.0), (-0.0333, 0.0333, 0.0), (-0.0333, -0.0333, 0.0)]


def aec_calibration(meta=None):
    """(aec_to_device_offset_deg, aec_to_device_sign): a recording's own (meta.json) when it has them, else the
    live config. A replay must map raw-mic azimuths with the calibration it was RECORDED under, the same way it
    already takes array_offset_deg / array_clockwise from meta; mixing the two frames shifted every replayed
    SRP bearing by the difference (2.9 deg after the 2026-10-11 recalibration) and would mirror it outright if
    the fitted sign ever changed."""
    meta = meta or {}
    off = meta.get("aec_to_device_offset_deg")
    sign = meta.get("aec_to_device_sign")
    return (float(C.AEC_TO_DEVICE_OFFSET_DEG if off is None else off),
            int(C.AEC_TO_DEVICE_SIGN if sign is None else sign))


def aec_to_device(aec_deg, aec=None):
    """Mic-geometry-frame azimuth -> the device's DOA_VALUE angle (0..360). aec: (offset, sign), default config."""
    off, sign = aec if aec is not None else (C.AEC_TO_DEVICE_OFFSET_DEG, C.AEC_TO_DEVICE_SIGN)
    return (off + sign * aec_deg) % 360.0


def mic_order(order=None):
    """audio.raw_mic_order: geometry mic i is raw column order[i] (raw columns = USB ch raw_mic_channels)."""
    order = list(getattr(C, "RAW_MIC_ORDER", None) or [0, 1, 2, 3]) if order is None else list(order)
    if sorted(order) != list(range(len(order))):
        raise ValueError(f"raw_mic_order must be a permutation, got {order}")
    return order


def in_geometry_order(raw, order=None):
    """Raw-mic columns (USB order) -> columns in AEC_MIC_ARRAY_GEO order, ready for SrpPhat / ODAS."""
    return np.asarray(raw)[:, mic_order(order)]


class RawLocator:
    """Raw 4-mic ring buffer + SRP-PHAT: the world bearing of a sound over a time window (live or replayed)."""

    def __init__(self, geometry, fs=16000, seconds=C.RAW_BUFFER_S, order=None, aec=None):
        """aec: (aec_to_device offset, sign) for a replay (aec_calibration(meta)); None = the live config."""
        self.fs = fs
        self.order = mic_order(order)
        self.aec = aec
        self.buffer = RawBuffer(fs, seconds=seconds)
        self.srp = SrpPhat(geometry, fs)

    def add(self, t, block):
        self.buffer.add(t, block)

    def locate(self, t0, t1, offset, ccw):
        """(world bearing, device angle, coherence) over [t0, t1]; None if there's too little audio.
        The caller decides whether the coherence is high enough to trust."""
        seg = self.buffer.segment(t0, t1)
        if seg is None or len(seg) < 2 * self.srp.nfft:
            return None
        seg = loudest_window(in_geometry_order(seg, self.order), self.fs, C.SRP_MAX_WINDOW_S)
        az, conf = self.srp.locate(seg)
        if az is None:
            return None
        device_deg = aec_to_device(az, self.aec)
        from .xvf3800 import to_world
        return to_world(device_deg, offset, ccw), device_deg, conf

    def locate_within(self, t0, t1, offset, ccw, center_deg, half_width_deg, window_s=1.0, peak_rel=0.5,
                      floor=C.SRP_MIN_CONFIDENCE):
        """Like locate(), but only a talker within half_width_deg of center_deg (world) counts: the strongest SRP
        peak on that side of the room, so another beam's louder talker or noise can't take it. A peak is a local
        maximum of the whole map (the edge of a louder source's lobe spilling over the boundary isn't one),
        >= peak_rel x the map's best and >= floor. (world bearing, device angle, coherence) or None."""
        seg = self.buffer.segment(t0, t1)
        if seg is None or len(seg) < 2 * self.srp.nfft:
            return None
        seg = loudest_window(in_geometry_order(seg, self.order), self.fs, window_s)
        p = self.srp.power(seg)
        if p is None:
            return None
        world = self.world_grid(offset, ccw)
        for az, v in peaks(p, self.srp.az_deg, k=4, min_sep_deg=20.0, rel=peak_rel, floor=floor):
            k = int(round(az / (self.srp.az_deg[1] - self.srp.az_deg[0]))) % len(world)
            w = float(world[k])
            if abs(((w - center_deg + 180.0) % 360.0) - 180.0) <= half_width_deg:
                return round(w, 1), aec_to_device(az, self.aec), v
        return None

    def world_grid(self, offset, ccw):
        """World bearing of every SRP grid azimuth (cached per calibration)."""
        # Same chain as locate() (aec_to_device then xvf3800.to_world), vectorised; keyed on the aec mapping too.
        key = (offset, ccw, self.aec or (C.AEC_TO_DEVICE_OFFSET_DEG, C.AEC_TO_DEVICE_SIGN))
        if getattr(self, "_world", (None,))[0] != key:
            device = aec_to_device(self.srp.az_deg, self.aec)
            rel = device - offset
            w = ((-rel if ccw else rel) + 180.0) % 360.0 - 180.0
            self._world = (key, np.where(w == -180.0, 180.0, w))
        return self._world[1]


class SpeechBearings:
    """A fixed beam's talker, where they really are: per-utterance bearing from the raw mics (audio.speech_srp).

    With fixed beams the firmware's DoA names the beam (0.8 / -179.2), not the talker, so two people in front at
    +-30 deg both read 0.8 and the phone can't put either caption on a face. This runs SRP-PHAT on the newest
    `window_s` of the utterance, restricted to the beam's half of the room (`half_width_deg` around the beam), at
    most every `relocate_s`, and keeps one bearing per utterance the way EventBearings keeps one per sound event:
    the circular median of its looks, so it doesn't jitter from partial to partial. `spread` = robust spread of the
    looks (1.4826 x median absolute deviation), floored at `min_spread_deg`; `single_spread_deg` while there is
    only one look. None until a look clears `min_confidence` (the caller then keeps the beam's bearing and width).
    """

    def __init__(self, raw, offset, ccw, cfg=None):
        cfg = dict(getattr(C, "SPEECH_SRP", None) or {}, **(cfg or {}))
        self.raw, self.offset, self.ccw = raw, offset, ccw
        self.half_width = float(cfg.get("half_width_deg", 100.0))
        self.window_s = float(cfg.get("window_s", 1.0))
        self.min_conf = float(cfg.get("min_confidence", 0.6))
        self.peak_rel = float(cfg.get("peak_rel", 0.5))
        self.min_spread = float(cfg.get("min_spread_deg", 8.0))
        self.single_spread = float(cfg.get("single_spread_deg", 15.0))
        self.outlier_deg = float(cfg.get("outlier_deg", 45.0))
        self.outlier_share = float(cfg.get("outlier_share", 0.2))
        self.enabled = bool(cfg.get("enabled", True))
        self.looks = EventBearings(float(cfg.get("relocate_s", 0.3)), keep_s=30.0)

    def locate(self, key, t0, t1, center_deg, now=None):
        """(bearing, coherence, spread_deg, looks) for utterance `key` over [t0, t1], or None."""
        if not self.enabled or self.raw is None:
            return None
        import time
        t0 = max(t0, t1 - self.window_s)
        self.looks.locate(key, time.time() if now is None else now,
                          lambda: self.raw.locate_within(t0, t1, self.offset, self.ccw, center_deg, self.half_width,
                                                         self.window_s, self.peak_rel, self.min_conf),
                          self.min_conf)
        found = self.looks.events.get(key, (0.0, []))[1]
        med = EventBearings.median(found)
        if med is None:
            return None
        from laptop.geometry import circular_mean
        bearings = [b for b, _ in found]
        near = [b for b in bearings if abs(((b - med[0] + 180.0) % 360.0) - 180.0) <= self.outlier_deg]
        # One stray look (a cough, a door) shouldn't widen it; looks that keep landing elsewhere (two talkers in
        # this half of the room) should: then the spread covers all of them.
        use = near if len(bearings) - len(near) < self.outlier_share * len(bearings) else bearings
        spread = self.single_spread if len(found) < 2 else max(self.min_spread, circular_mean(use)[1])
        coh = sorted(c for _, c in found)[len(found) // 2]
        return med[0], round(coh, 2), round(spread, 1), len(found)


def loudest_window(x, fs, seconds):
    """The loudest stretch of `seconds` (RMS over 50 ms hops) of a long recording."""
    n = int(seconds * fs)
    if len(x) <= n:
        return x
    hop = fs // 20
    e = np.add.reduceat(np.sum(np.asarray(x, float) ** 2, axis=1), np.arange(0, len(x), hop))
    span = max(1, n // hop)
    best = int(np.argmax(np.convolve(e, np.ones(span), "valid")))
    return x[best * hop: best * hop + n]


class EventBearings:
    """One bearing per sound event, shared by the ReSpeaker and the fixture player (replays = live).

    `locate(key, now, compute, min_conf)`: `compute()` runs SRP ((bearing, device_deg, conf) or None) unless the
    event was localized less than `relocate_s` ago. Every call returns the circular median of the event's bearings
    so far (best confidence) and the new SRP result (for logging / the LED ring). A call that can't localize
    (rate-limited, low coherence, outside the buffer) returns the event's existing bearing, never None once it has
    one. Before, a None sent the sound pipeline to the beam's bearing: one doorbell press read
    28.8 / 27.8 / 0.8 / 24.8 on the wire (real array 2026-10-10), and the belt buzzed at the beam.
    """

    def __init__(self, relocate_s=0.5, keep_s=12.0):
        self.relocate_s, self.keep_s = relocate_s, keep_s
        self.events = {}                     # key -> (last SRP time, [(bearing, conf)])

    def locate(self, key, now, compute, min_conf):
        last_t, found = self.events.get(key, (0.0, []))
        if now - last_t < self.relocate_s:
            return self.median(found), None
        self.events = {k: v for k, v in self.events.items() if now - v[0] < self.keep_s}
        self.events[key] = (now, found)
        new = compute()
        if new is not None and new[2] >= min_conf:
            found.append((new[0], new[2]))
        return self.median(found), new

    @staticmethod
    def median(found):
        """Circular median (the bearing closest to all the others), best confidence; or None."""
        if not found:
            return None
        best = min(found, key=lambda bc: sum(abs(((bc[0] - o + 180.0) % 360.0) - 180.0) for o, _ in found))[0]
        return round(best, 1), round(min(1.0, max(c for _, c in found)), 2)


class RawBuffer:
    """The last few seconds of raw multichannel audio with capture times (§7), for event lookups."""

    def __init__(self, fs=16000, seconds=C.RAW_BUFFER_S):
        self.fs = fs
        self.seconds = seconds
        self.blocks = deque()   # (t of first sample, int16 array (samples, mics))

    def add(self, t, block):
        self.blocks.append((t, block))
        while self.blocks and self.blocks[0][0] < t - self.seconds:
            self.blocks.popleft()

    def segment(self, t0, t1):
        """Samples captured in [t0, t1], or None if the buffer holds none of it."""
        parts = []
        for tb, b in list(self.blocks):
            if tb + len(b) / self.fs <= t0 or tb >= t1:
                continue
            i0 = max(0, int(round((t0 - tb) * self.fs)))
            i1 = min(len(b), int(round((t1 - tb) * self.fs)))
            if i1 > i0:
                parts.append(b[i0:i1])
        return np.concatenate(parts) if parts else None


def peaks(power, az_deg, k=2, min_sep_deg=30.0, rel=0.6, floor=0.15):
    """Up to k SRP peaks [(azimuth, value)]: local maxima >= floor and >= rel x the best, min_sep_deg apart."""
    if power is None:
        return []
    n = len(power)
    order = np.argsort(power)[::-1]
    out = []
    for i in order:
        v = float(power[i])
        if v < floor or (out and v < rel * out[0][1]):
            break
        if power[(i - 1) % n] > v or power[(i + 1) % n] > v:
            continue
        a = float(az_deg[i])
        if all(abs((a - b + 180) % 360 - 180) >= min_sep_deg for b, _ in out):
            out.append((a, v))
            if len(out) == k:
                break
    return out


class SrpTracker:
    """Track A step 2: SRP-PHAT peaks every hop -> tracks with ids.

    Gating gate_deg, a track is born after birth_hits consecutive-ish hits (within 2 hops) and dies after death_s
    without a hit. Bearings are smoothed (alpha). Works in any frame; the caller converts to world bearings.
    """

    def __init__(self, gate_deg=20.0, birth_hits=3, death_s=1.5, alpha=0.5):
        self.gate, self.birth_hits, self.death_s, self.alpha = gate_deg, birth_hits, death_s, alpha
        self.tracks = []          # dicts: id, az, hits, last, born, value
        self.next_id = 1

    def update(self, t, found):
        """found: [(azimuth, value)] at time t. Returns live tracks [(id, azimuth, value)] (born only)."""
        used = set()
        for tr in sorted(self.tracks, key=lambda tr: -tr["value"]):
            best = None
            for j, (a, v) in enumerate(found):
                d = abs((a - tr["az"] + 180) % 360 - 180)
                if j not in used and d <= self.gate and (best is None or d < best[0]):
                    best = (d, j)
            if best is not None:
                a, v = found[best[1]]
                used.add(best[1])
                delta = (a - tr["az"] + 180) % 360 - 180
                tr["az"] = (tr["az"] + self.alpha * delta) % 360
                tr["hits"] += 1
                tr["last"], tr["value"] = t, v
                if tr["hits"] >= self.birth_hits:
                    tr["born"] = True
        for j, (a, v) in enumerate(found):
            if j not in used:
                self.tracks.append({"id": None, "az": a, "hits": 1, "last": t, "born": False, "value": v,
                                    "first": t})
        keep = []
        for tr in self.tracks:
            gone = t - tr["last"]
            if (tr["born"] and gone <= self.death_s) or (not tr["born"] and gone <= 0.25):
                keep.append(tr)
        self.tracks = keep
        out = []
        for tr in self.tracks:
            if tr["born"] and tr["last"] == t:
                if tr["id"] is None:
                    tr["id"], self.next_id = self.next_id, self.next_id + 1
                out.append((tr["id"], tr["az"], tr["value"]))
        return out


class SrpSources:
    """Streaming SRP-PHAT sources on the raw mics: every hop_s, the top-2 peaks of the last win_s -> SrpTracker.

    A noise gate keeps the room's stationary noise (the laptop fan is coherent too) from being a permanent
    source: band energy must be gate_db over a running floor (10th percentile of the last 5 s of hops).
    feed(t, raw) takes USB-order raw blocks (t = capture time of the first sample) and returns the hops it
    finished: [(t, [(track id, azimuth in the mic-geometry frame, value)])].
    """

    def __init__(self, geometry, fs=16000, order=None, hop_s=0.1, win_s=0.25, gate_db=4.0):
        from scipy.signal import butter
        self.fs, self.order, self.gate_db = fs, mic_order(order), gate_db
        self.srp = SrpPhat(geometry, fs)
        self.tracker = SrpTracker()
        self.hop, self.win = int(hop_s * fs), int(win_s * fs)
        self.sos = butter(4, list(C.SRP_BAND_HZ), "bandpass", fs=fs, output="sos")
        self.zi = None
        self.geo = np.zeros((0, len(self.order)))
        self.band = np.zeros((0, len(self.order)))
        self.energy = deque(maxlen=max(1, int(5.0 * fs / self.hop)))
        self.t_end = None           # capture time just after the last sample held
        self.due = self.win         # samples held when the next hop is due

    def feed(self, t, raw):
        from scipy.signal import sosfilt
        x = in_geometry_order(raw, self.order).astype(np.float64)
        if self.zi is None:
            self.zi = np.zeros((self.sos.shape[0], 2, x.shape[1]))
        y, self.zi = sosfilt(self.sos, x, axis=0, zi=self.zi)
        self.geo = np.concatenate([self.geo, x])
        self.band = np.concatenate([self.band, y])
        self.t_end = t + len(x) / self.fs
        out = []
        while len(self.geo) >= self.due:
            end = self.due
            t_hop = self.t_end - (len(self.geo) - end) / self.fs
            out.append((t_hop, self.step(t_hop, self.geo[end - self.win:end], self.band[end - self.win:end])))
            self.due += self.hop
        drop = max(0, self.due - self.win - self.hop)       # keep what the next hops still need
        if drop:
            self.geo, self.band, self.due = self.geo[drop:], self.band[drop:], self.due - drop
        return out

    def step(self, t, geo, band):
        e = 10 * np.log10(np.mean(band ** 2) + 1e-9)
        self.energy.append(e)
        if e < np.percentile(self.energy, 10) + self.gate_db:
            return self.tracker.update(t, [])
        p = self.srp.power(geo, top=1.0)
        return self.tracker.update(t, peaks(p, self.srp.az_deg, k=2, floor=C.SRP_MIN_CONFIDENCE + 0.1))


# ---- live check (needs the array; the SRP column also needs the 6-channel firmware) -------------------

def fit_mapping(pairs):
    """Best (offset, sign, spread) for device = offset + sign * aec over (device_deg, aec_deg) pairs."""
    from laptop.geometry import circular_mean

    best = None
    for sign in (1, -1):
        mean, std = circular_mean([dev - sign * aec for dev, aec in pairs])
        if mean is not None and (best is None or std < best[2]):
            best = (mean % 360.0, sign, std)
    return best


def world_to_aec(world_deg, offset, ccw, aec=None):
    """World bearing -> mic-geometry (AEC) azimuth, the inverse of to_world(aec_to_device(az, aec))."""
    off, sign = aec if aec is not None else (C.AEC_TO_DEVICE_OFFSET_DEG, C.AEC_TO_DEVICE_SIGN)
    device = ((-world_deg if ccw else world_deg) + offset) % 360.0
    return ((device - off) * sign) % 360.0


def is_dihedral(order):
    """True when the permutation keeps the square's neighbours neighbours (a rotation or a mirror of the array)."""
    n = len(order)
    return all((order[(i + 1) % n] - order[i]) % n in (1, n - 1) for i in range(n)) and \
        len({(order[(i + 1) % n] - order[i]) % n for i in range(n)}) == 1


def fit_order(raw, windows, truth_aec, geometry, fs=16000):
    """Score every raw mic permutation against a source at a known AEC azimuth.

    raw: (n, 4) in USB order; windows: [(i0, i1)] sample ranges with the source active. For each of the 24
    orders, SRP-PHAT per window -> (order, median error deg, mean peak coherence, azimuths), best first.
    """
    from itertools import permutations

    from laptop.geometry import angdiff, circular_mean

    srp = SrpPhat(geometry, fs)
    out = []
    for order in permutations(range(raw.shape[1])):
        az, coh = [], []
        for i0, i1 in windows:
            seg = in_geometry_order(raw[i0:i1], order)
            for s in range(0, len(seg) - fs // 2 + 1, fs // 2):     # 0.5 s pieces, like a live lookup
                a, c = srp.locate(seg[s:s + fs // 2])
                if a is not None:
                    az.append(a)
                    coh.append(c)
        mean, _ = circular_mean(az)
        errs = sorted(abs(angdiff(a, truth_aec)) for a in az)
        out.append({"order": list(order), "median_err": round(errs[len(errs) // 2], 1),
                    "within_10": round(sum(e <= 10 for e in errs) / len(errs), 2),
                    "mean_az": round(mean % 360, 1), "coherence": round(float(np.mean(coh)), 3),
                    "dihedral": is_dihedral(order)})
    return sorted(out, key=lambda r: (r["median_err"], -r["coherence"]))


def fit_fixture(folder):
    """--fit: the raw-mic order from a raw recording of one loudspeaker at a known world bearing."""
    import json
    from pathlib import Path

    from .mock import read_streams

    folder = Path(folder)
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    raw = read_streams(folder / "raw.wav").astype(np.float64)
    truth = meta.get("true_bearing_deg")
    if truth is None:
        raise SystemExit(f"{folder}/meta.json has no true_bearing_deg")
    truth_aec = world_to_aec(truth, meta.get("array_offset_deg", C.ARRAY_OFFSET_DEG),
                             not meta.get("array_clockwise", C.ARRAY_CLOCKWISE), aec_calibration(meta))
    windows = [(int(l["t_start"] * 16000), int(l["t_end"] * 16000)) for l in meta["lines"]]
    geometry = meta.get("mic_geometry") or C.MIC_GEOMETRY or XVF3800_GEOMETRY
    rows = fit_order(raw, windows, truth_aec, geometry)
    print(f"{folder.name}: source at world {truth} = AEC {truth_aec:.1f} deg; {len(windows)} windows\n")
    print("| raw_mic_order | rotation/mirror | median error | within 10 deg | mean azimuth | mean coherence |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['order']} | {'yes' if r['dihedral'] else 'no'} | {r['median_err']} | {r['within_10']:.0%} "
              f"| {r['mean_az']} | {r['coherence']:.3f} |")
    return rows, truth_aec


def mirror_order(order):
    """The order that mirrors the array across its x axis (geometry mics 0<->1, 2<->3): the one candidate a
    single loudspeaker near 0/180 deg can't tell from `order` (tools: --fit)."""
    return [order[1], order[0], order[3], order[2]]


def check(seconds=None):
    """Live: device DoA vs beam vs SRP (fitted raw_mic_order and its mirror). Talk from the front, then the right.

    Prints per reading, then: the AEC_TO_DEVICE fit (beam and SRP) and which raw-mic order agrees with the
    device's own DoA. The fitted order should win clearly from the right (the mirror lands on the left).
    """
    import time

    import sounddevice as sd

    from laptop.geometry import angdiff

    from .builtin_doa import RAW_OPS, find_input
    from .xvf3800 import XVF3800, sample

    dev = XVF3800()
    geometry = C.MIC_GEOMETRY or dev.mic_geometry()
    print(f"mic geometry (m): {[tuple(round(v, 4) for v in m) for m in geometry]}")
    idx = find_input()
    channels = sd.query_devices(idx)["max_input_channels"]
    raw = channels > max(C.RAW_MIC_CHANNELS)
    print(f"audio input has {channels} channels: "
          f"{'raw mics available' if raw else 'no raw mics (2-channel firmware), beam check only'}")
    category = getattr(C, "RAW_MIC_CATEGORY", 1)
    if raw:
        for k, reg in enumerate(RAW_OPS):
            dev.write(reg, int(category), k)
    order = mic_order()
    candidates = {"fitted": order, "mirror": mirror_order(order)}
    print(f"raw_mic_order {order} (mirror candidate {candidates['mirror']}), raw category {category}")
    srp = SrpPhat(geometry)
    buf = RawBuffer(seconds=2.0)

    def callback(indata, frames, time_info, status):
        buf.add(time.time() - frames / C.SAMPLE_RATE, indata[:, list(C.RAW_MIC_CHANNELS)].copy())

    stream = sd.InputStream(samplerate=C.SAMPLE_RATE, channels=channels, dtype="int16",
                            blocksize=C.CHUNK_SAMPLES, device=idx, callback=callback) if raw else None
    if stream:
        stream.start()
    beam_pairs, srp_pairs = [], []
    errors = {name: [] for name in candidates}
    print("Talk from the front of the array for ~10 s, then from its right for ~10 s. Ctrl+C to finish.\n")
    end = time.time() + seconds if seconds else None
    try:
        while end is None or time.time() < end:
            time.sleep(0.5)
            s = sample(dev)
            if not s["speech"] or max(s["beam_energy"][:2]) <= 0:
                continue
            k = int(np.argmax(s["beam_energy"][:2]))
            beam_pairs.append((s["doa_deg"], s["beam_az_deg"][k]))
            line = f"device {s['doa_deg']:3d}  beam {s['beam_az_deg'][k]:5.1f}"
            if stream:
                now = time.time()
                seg = buf.segment(now - 0.5, now)
                if seg is None:
                    continue
                for name, o in candidates.items():
                    az, conf = srp.locate(in_geometry_order(seg, o))
                    if az is None:
                        continue
                    expected = ((s["doa_deg"] - C.AEC_TO_DEVICE_OFFSET_DEG) * C.AEC_TO_DEVICE_SIGN) % 360.0
                    errors[name].append(abs(angdiff(az, expected)))
                    if name == "fitted":
                        srp_pairs.append((s["doa_deg"], az))
                    line += f"  srp[{name}] {az:5.1f} ({conf:.2f})"
            print(line)
    except KeyboardInterrupt:
        pass
    finally:
        if stream:
            stream.close()
        if raw and category != 1:
            for k, reg in enumerate(RAW_OPS):
                dev.write(reg, 1, k)
        dev.close()
    for name, pairs in (("beam", beam_pairs), ("srp", srp_pairs)):
        if len(pairs) >= 5:
            off, sign, std = fit_mapping(pairs)
            print(f"\n{name}: AEC_TO_DEVICE_OFFSET_DEG = {off:.1f}, AEC_TO_DEVICE_SIGN = {sign}  "
                  f"(residual spread {std:.1f} deg over {len(pairs)} samples)")
    for name, errs in errors.items():
        if errs:
            errs = sorted(errs)
            print(f"raw_mic_order {candidates[name]} ({name}): median error vs device DoA {errs[len(errs) // 2]:.0f} deg, "
                  f"within 20 deg {sum(e <= 20 for e in errs) / len(errs):.0%} of {len(errs)} readings")
    if all(errors.values()):
        med = {n: sorted(e)[len(e) // 2] for n, e in errors.items()}
        best = min(med, key=med.get)
        print(f"-> keep {candidates[best]}" if best == "fitted" else
              f"-> the MIRROR fits better: set audio.raw_mic_order: {candidates['mirror']} in laptop/config.yaml")


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(prog="python -m laptop.audio.localize")
    p.add_argument("--check", action="store_true", help="live comparison and AEC_TO_DEVICE fit")
    p.add_argument("--seconds", type=float, help="with --check: stop after this long instead of Ctrl+C")
    p.add_argument("--fit", metavar="FIXTURE", help="raw-mic order from a raw recording at a known bearing")
    args = p.parse_args()
    if args.fit:
        fit_fixture(args.fit)
    elif args.check:
        check(args.seconds)
    else:
        p.print_help()

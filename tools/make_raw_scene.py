"""Real-acoustics raw-mic scenes without people: rotate a real recording by permuting the square array's mics.

The XVF3800's 4 mics sit on a square (AEC_MIC_ARRAY_GEO: -45/45/135/-135 deg). A far-field source rotated by
+90 deg reaches geometry mic i exactly as it reached mic i-1, so permuting the raw channels of ONE real recording
of the laptop speaker at world 180 gives real sources at -90, 0 and +90 too (the room isn't rotated: its
reflections turn with the source; acceptable). Summing two pieces of the recording (different content, different
rotation) gives a two-source scene with known truth (DOA_VS_ODAS.md "shared infrastructure" 2).

Only raw-mic consumers can use these (our SRP, ODAS): the XVF3800's beams can't be simulated this way.

    python tools/make_raw_scene.py                      # all scenes from fixtures/raw_speaker_180 -> fixtures/rot_*
    python tools/make_raw_scene.py --scene talker_baby  # one scene
    python tools/make_raw_scene.py --list

Each scene folder: raw.wav (4 raw mics, USB order, like a real recording), audio.wav (mono mix of the raw mics,
so the speech/sound pipelines can run on it), meta.json with `lines` (stress_test shape: who, bearing_deg, text,
t_start, t_end) and `truth` (per source id: bearing and active intervals), `rotation_scene: true`.
"""

import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from laptop.audio.localize import aec_calibration, aec_to_device, mic_order, world_to_aec  # noqa: E402
from laptop.audio.mock import read_streams  # noqa: E402
from laptop.audio.xvf3800 import to_world  # noqa: E402

SR = 16000
SOURCE = ROOT / "fixtures" / "raw_speaker_180"
MARGIN_S = 0.15            # around each aligned line: onsets/tails the envelope alignment trims
NOISE_BED = (0.3, 4.5)     # seconds of the source recording with nothing playing (room + laptop fan)
AUDIO_GAIN = 4.0           # audio.wav = mean of the raw mics x this (raw speech sits near -38 dBFS)


def rotate(geo, steps):
    """Raw mics in geometry order -> the same sound arriving from `steps` x 90 deg further (AEC frame, +ccw)."""
    return np.roll(geo, steps, axis=1)


def to_usb(geo, order):
    """Geometry-order columns back to USB order (inverse of in_geometry_order)."""
    out = np.empty_like(geo)
    out[:, order] = geo
    return out


class Source:
    """The source recording, in geometry order, with its aligned lines."""

    def __init__(self, folder=SOURCE, order=None):
        self.folder = Path(folder)
        self.meta = json.loads((self.folder / "meta.json").read_text(encoding="utf-8"))
        self.order = mic_order(order if order is not None else self.meta.get("raw_mic_order"))
        raw = read_streams(self.folder / "raw.wav").astype(np.float32)
        self.geo = raw[:, self.order]
        self.truth_world = float(self.meta["true_bearing_deg"])
        self.offset = float(self.meta.get("array_offset_deg", 0.0))
        self.ccw = not bool(self.meta.get("array_clockwise", True))
        self.aec = aec_calibration(self.meta)      # the recording's own frame, like offset / clockwise
        self.truth_aec = world_to_aec(self.truth_world, self.offset, self.ccw, self.aec)
        self.lines = self.meta["lines"]

    def world_after(self, steps):
        """World bearing of the source after rotating it by steps x 90 deg (AEC frame)."""
        return to_world(aec_to_device((self.truth_aec + 90.0 * steps) % 360.0, self.aec), self.offset, self.ccw)

    def steps_for(self, world):
        """Rotation steps that bring the recorded source closest to a world bearing (0 / 90 / 180 / -90)."""
        return min(range(4), key=lambda k: abs(((self.world_after(k) - world + 180) % 360) - 180))

    def piece(self, k, seconds=None):
        """Line k (geometry order) with margins, optionally cut to `seconds`."""
        l = self.lines[k]
        i0 = max(0, int((l["t_start"] - MARGIN_S) * SR))
        i1 = min(len(self.geo), int((l["t_end"] + MARGIN_S) * SR))
        if seconds is not None:
            i1 = min(i1, i0 + int(seconds * SR))
        return self.geo[i0:i1], l

    def bed(self, n, seed=0):
        """Room noise for n samples: the quiet stretch, looped with random offsets (not rotated: it's the room)."""
        q = self.geo[int(NOISE_BED[0] * SR): int(NOISE_BED[1] * SR)]
        rng = np.random.default_rng(seed)
        out = np.zeros((n, q.shape[1]), np.float32)
        pos = 0
        while pos < n:
            start = int(rng.integers(0, len(q) // 2))
            chunk = q[start:][: n - pos]
            fade = min(160, len(chunk) // 2)
            w = np.ones(len(chunk), np.float32)
            w[:fade] = np.linspace(0, 1, fade)
            w[len(w) - fade:] = np.linspace(1, 0, fade)
            out[pos:pos + len(chunk)] += chunk * w[:, None]
            pos += len(chunk) - fade
        return out


def speech_ids(src, who=None):
    return [k for k, l in enumerate(src.lines) if l["text"] and (who is None or l["who"] in who)]


def sound_id(src, who):
    return next(k for k, l in enumerate(src.lines) if l["who"] == who)


# Scene recipes: [(source id, world bearing, [line ids], start s, gap s, max seconds per piece)].
# The same line can't overlap itself (identical content at two bearings would be a perfect echo).
def recipes(src):
    zira = speech_ids(src, {"Person 1"})
    david = speech_ids(src, {"Person 2", "Speaker B"})
    every = speech_ids(src)
    door, baby = sound_id(src, "doorbell"), sound_id(src, "baby_cry")
    out = {}
    for world, tag in ((0, "0"), (90, "90"), (180, "180"), (-90, "m90")):
        out[f"single_{tag}"] = [(0, world, every[:4] + [door] + every[4:] + [baby], 1.0, 1.0, 5.0)]
    out["talker_baby"] = [(0, 0, every, 1.0, 0.8, None), (1, 180, [baby], 3.0, 0.0, None)]
    out["two_talkers_90"] = [(0, 0, zira, 1.0, 2.2, None), (1, 90, david, 2.5, 1.5, None)]
    out["two_talkers_180"] = [(0, 0, zira, 1.0, 2.2, None), (1, 180, david, 2.5, 1.5, None)]
    return out


def build(src, recipe, seed=0):
    """Mix the recipe into (raw USB-order int16, audio int16, lines, truth)."""
    placed, lines, truth = [], [], {}
    for sid, world, ids, start, gap, cut in recipe:
        steps = src.steps_for(world)
        bearing = src.world_after(steps)
        t = start
        intervals = []
        for k in ids:
            x, l = src.piece(k, cut)
            placed.append((int(t * SR), rotate(x, steps)))
            t_on = t + MARGIN_S
            t_off = t + len(x) / SR - MARGIN_S
            who = l["who"] if l["text"] else l["who"]
            lines.append({"who": who if l["text"] else ("baby" if who == "baby_cry" else who),
                          "source_id": sid, "bearing_deg": bearing, "text": l["text"],
                          "t_start": round(t_on, 2), "t_end": round(t_off, 2)})
            intervals.append([round(t_on, 2), round(t_off, 2)])
            t += len(x) / SR + gap
        truth[str(sid)] = {"bearing_deg": bearing, "rotation_steps": steps, "active": intervals}
    n = max(i + len(x) for i, x in placed) + int(1.5 * SR)
    mix = src.bed(n, seed)
    for i, x in placed:
        mix[i:i + len(x)] += x
    raw = np.clip(np.round(to_usb(mix, src.order)), -32768, 32767).astype(np.int16)
    audio = np.clip(np.round(mix.mean(axis=1) * AUDIO_GAIN), -32768, 32767).astype(np.int16)
    lines.sort(key=lambda l: l["t_start"])
    return raw, audio, lines, truth


def write_wav(path, x):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1 if x.ndim == 1 else x.shape[1])
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.ascontiguousarray(x, "<i2").tobytes())


def make(name, src=None, out_root=ROOT / "fixtures", seed=0):
    src = src or Source()
    recipe = recipes(src)[name]
    raw, audio, lines, truth = build(src, recipe, seed)
    folder = Path(out_root) / f"rot_{name}"
    folder.mkdir(parents=True, exist_ok=True)
    write_wav(folder / "raw.wav", raw)
    write_wav(folder / "audio.wav", audio)
    (folder / "sources.jsonl").write_text("", encoding="utf-8")
    keep = {k: src.meta[k] for k in ("firmware", "array_offset_deg", "array_clockwise", "aec_to_device_offset_deg",
                                     "aec_to_device_sign", "mic_geometry", "raw_mic_category") if k in src.meta}
    meta = {"name": folder.name, **keep, "raw_mic_order": src.order, "raw": {"file": "raw.wav", "channels": 4,
            "gain": 1.0}, "rotation_scene": True, "synthetic": "rotation", "source_fixture": src.folder.name,
            "user_name": "Kori", "seconds": round(len(raw) / SR, 1), "truth": truth, "lines": lines,
            "notes": f"tools/make_raw_scene.py: {src.folder.name} (laptop speaker at world {src.truth_world:g}) rotated "
                     "by mic permutation; room noise bed from its quiet start. audio.wav = mean of the raw mics, "
                     "not the XVF3800's beams."}
    (folder / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return folder, meta


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scene", action="append", help="scene name (repeatable); default all")
    p.add_argument("--source", default=str(SOURCE), help="raw recording of one loudspeaker at a known bearing")
    p.add_argument("--list", action="store_true")
    a = p.parse_args()
    src = Source(a.source)
    names = list(recipes(src))
    if a.list:
        print("\n".join(names))
        return
    for name in a.scene or names:
        folder, meta = make(name, src)
        srcs = ", ".join(f"id {k} at {v['bearing_deg']:+.0f} ({len(v['active'])} pieces)" for k, v in meta["truth"].items())
        print(f"{folder.relative_to(ROOT)}: {meta['seconds']} s, {srcs}")


if __name__ == "__main__":
    main()

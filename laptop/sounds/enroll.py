"""Enroll a sound from the array: "teach HERE your doorbell" (README §15; matcher in enrolled.py).

Records the array's processed audio (what the pipeline hears) while you play the sound once, trims the
recording to the sound, checks the template against the room noise it was recorded in, saves it to
fixtures/sounds/<name>.wav and points `sounds.enrolled` in laptop/config.yaml at it.

    python -m laptop.sounds.enroll doorbell --seconds 8              # play the doorbell once when asked
    python -m laptop.sounds.enroll doorbell --name doorbell_hall     # keep the synthetic doorbell.wav
    python -m laptop.sounds.enroll doorbell --from-wav rec.wav       # trim + enroll an existing recording
    python -m laptop.sounds.enroll doorbell --no-save                # just show what it would do

Then check it: `python tools/stress_test.py --audio-only` (and `--scene <fixture>` for a real recording).
Play the sound from where it will be at the demo (the Bluetooth speaker on its tape mark), at demo volume.
Run from a normal terminal (sandboxed processes record silence); `python -m laptop` may keep running only
if it isn't holding the audio device exclusively (WASAPI shared mode is fine).
"""

import argparse
import re
import sys
import time
import wave
from pathlib import Path

import numpy as np

from contracts.python.here_contract import SOUND_LABELS

from . import config as C
from .enrolled import EnrolledSound

SR = 16000
ROOT = Path(__file__).resolve().parents[2]
FRAME = 320                      # 20 ms


def frame_db(x):
    n = len(x) // FRAME
    f = x[:n * FRAME].astype(np.float32).reshape(n, FRAME)
    return 20 * np.log10(np.sqrt(np.mean(f ** 2, axis=1)) + 1e-3)


def trim(x, above_db=10.0, pre_s=0.05, tail_s=0.3, max_gap_s=1.0):
    """The sound inside a recording: frames more than `above_db` over the floor (10th percentile), grouped
    into events (gaps under max_gap_s join, so ding-dong + decay + ding-dong stays one), and the strongest event
    kept, plus a little lead and the decay tail. Returns (pcm, t0_s, t1_s) or None."""
    lv = frame_db(x)
    if len(lv) < 10:
        return None
    floor = float(np.percentile(lv, 10))
    loud = np.nonzero(lv > floor + above_db)[0]
    if len(loud) < 5:                            # < 0.1 s above the floor: nothing was played
        return None
    gap = int(max_gap_s * SR / FRAME)
    groups, start = [], loud[0]
    for a, b in zip(loud, loud[1:]):
        if b - a > gap:
            groups.append((start, a))
            start = b
    groups.append((start, loud[-1]))
    f0, f1 = max(groups, key=lambda g: float(np.sum(np.maximum(lv[g[0]:g[1] + 1] - floor, 0))))
    i0 = max(0, int(f0 * FRAME - pre_s * SR))
    i1 = min(len(x), int((f1 + 1) * FRAME + tail_s * SR))
    return x[i0:i1], i0 / SR, i1 / SR


def check(pcm, recording, t0, t1, label):
    """Score of the template on its own recording vs. on the noise around it -> a suggested threshold."""
    snd = EnrolledSound(label, pcm)
    hit = snd.score(recording)
    noise = np.concatenate([recording[: max(0, int(t0 * SR) - SR // 10)], recording[int(t1 * SR) + SR // 10:]])
    # Just above what the room itself scores, NOT half-way to the self-match: in crowd babble the sound's match
    # drops to ~0.15-0.2 while everything else stays <= ~0.13 (tools/stress_test.py, CHANGELOG Task 6).
    if len(noise) <= len(pcm) + SR // 2:
        miss = None          # not enough room noise around the sound to test against: record longer
        threshold = 0.12
    else:
        miss = snd.score(noise)
        threshold = round(min(max(0.12, miss + 0.08), 0.5 * hit), 2) if hit > miss else 0.12
    return hit, miss, threshold


def record(seconds, device=None):
    import sounddevice as sd
    from laptop.audio.builtin_doa import find_input
    from laptop.audio import config as A

    dev = device if device is not None else find_input()
    ch = sd.query_devices(dev)["max_input_channels"]
    input(f"Recording {seconds:.0f} s from [{dev}] {sd.query_devices(dev)['name']}. "
          "Press Enter, then play the sound ONCE (room otherwise quiet)...")
    x = sd.rec(int(seconds * SR), samplerate=SR, channels=ch, dtype="int16", device=dev)
    for left in np.arange(seconds, 0, -0.5):
        print(f"\r  recording... {left:3.1f} s", end="", flush=True)
        time.sleep(0.5)
    sd.wait()
    print()
    return x[:, A.AUDIO_CHANNEL].copy()


def read_any(path):
    import soundfile as sf
    from scipy.signal import resample_poly
    a, sr = sf.read(str(path), dtype="float32", always_2d=True)
    a = a.mean(axis=1)
    if sr != SR:
        a = resample_poly(a, SR, sr)
    return (np.clip(a, -1, 1) * 32767).astype(np.int16)


def write_wav(path, pcm):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(SR)
        w.writeframes(pcm.astype("<i2").tobytes())


def update_config(label, rel_file, threshold, path=None):
    """Point sounds.enrolled at the file for `label` (replace that label's line, else add one), and make sure
    the label is in sounds.publish. Edits the YAML text in place, keeping comments."""
    path = Path(path or C.CONFIG_PATH)
    text = path.read_text(encoding="utf-8")
    entry = f"{{label: {label}, file: {rel_file}, threshold: {threshold}}}"
    stamp = f"# enrolled {time.strftime('%Y-%m-%d')} with laptop.sounds.enroll"
    pat = re.compile(rf"^(\s+)- \{{label: {re.escape(label)},[^}}]*\}}.*$", re.M)
    if pat.search(text):
        text = pat.sub(lambda m: f"{m.group(1)}- {entry}   {stamp}", text, count=1)
    else:
        m = re.search(r"^(\s+)enrolled:\s*(\[\s*\])?\s*$", text, re.M)
        if m is None:
            raise SystemExit(f"couldn't find sounds.enrolled in {path}")
        indent = m.group(1) + "  "
        text = text[:m.start()] + f"{m.group(1)}enrolled:\n{indent}- {entry}   {stamp}" + text[m.end():]
    pm = re.search(r"^(\s+publish:\s*)\[([^\]]*)\](.*)$", text, re.M)
    if pm and label not in [s.strip() for s in pm.group(2).split(",")]:
        items = [s.strip() for s in pm.group(2).split(",") if s.strip()] + [label]
        text = text[:pm.start()] + f"{pm.group(1)}[{', '.join(items)}]{pm.group(3)}" + text[pm.end():]
    path.write_text(text, encoding="utf-8")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="python -m laptop.sounds.enroll", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("label", help=f"contract SoundLabel ({', '.join(l for l in SOUND_LABELS if l != 'speech')})")
    p.add_argument("--seconds", type=float, default=8.0, help="recording length (leave room noise around the sound)")
    p.add_argument("--name", help="file name in fixtures/sounds/ (default: the label)")
    p.add_argument("--from-wav", help="enroll from a recording instead of the array")
    p.add_argument("--device", type=int, help="audio input index (default: the ReSpeaker)")
    p.add_argument("--threshold", type=float, help="override the suggested match threshold")
    p.add_argument("--no-save", action="store_true")
    p.add_argument("--no-config", action="store_true", help="save the template but leave laptop/config.yaml alone")
    a = p.parse_args(argv)
    if a.label not in SOUND_LABELS or a.label == "speech":
        raise SystemExit(f"{a.label!r} isn't a contract SoundLabel (README §12.3)")

    recording = read_any(a.from_wav) if a.from_wav else record(a.seconds, a.device)
    found = trim(recording)
    if found is None:
        raise SystemExit("no sound found above the room noise: play it louder or closer, and check the input")
    pcm, t0, t1 = found
    hit, miss, threshold = check(pcm, recording, t0, t1, a.label)
    threshold = a.threshold if a.threshold is not None else threshold
    peak = 20 * np.log10(np.abs(pcm).max() / 32768 + 1e-9)
    noise_txt = "n/a (record longer for a noise check)" if miss is None else f"{miss:.2f}"
    print(f"sound at {t0:.2f}-{t1:.2f} s ({t1 - t0:.2f} s, peak {peak:.0f} dBFS); "
          f"template matches itself {hit:.2f}, the room noise {noise_txt} -> threshold {threshold}")
    if peak > -1:
        print("warning: it clipped; play it a little quieter and enroll again")
    if miss is not None and hit - miss < 0.2:
        print("warning: the sound barely stands out from the room; enroll in a quieter moment or louder")
    rel = Path("fixtures") / "sounds" / f"{a.name or a.label}.wav"
    if a.no_save:
        print(f"not saved (--no-save); would write {rel} and sounds.enrolled in laptop/config.yaml")
        return pcm, threshold
    write_wav(ROOT / rel, pcm)
    if a.no_config:
        print(f"saved {rel} (config unchanged: sounds.enrolled {{label: {a.label}, file: {rel.as_posix()}, "
              f"threshold: {threshold}}})")
        return pcm, threshold
    update_config(a.label, rel.as_posix(), threshold)
    print(f"saved {rel}; laptop/config.yaml sounds.enrolled -> {a.label}: {rel.as_posix()} (threshold {threshold})")
    print("check: python tools/stress_test.py --audio-only")
    return pcm, threshold


if __name__ == "__main__":
    main()

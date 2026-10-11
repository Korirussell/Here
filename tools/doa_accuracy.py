"""DoA accuracy spike (SPEC §21, §11): error per position, settle time, and the second beam.

Guided (a person walks around the array):

    python tools/doa_accuracy.py                              # 0, 45, ... 315 deg at 1 m and 1.5 m, 10 s each
    python tools/doa_accuracy.py --angles 0 90 180 --distances 1.5 --seconds 8
    python tools/doa_accuracy.py --noise crowd.wav            # crowd noise from the laptop speakers meanwhile

Loudspeaker (no people: TTS from a speaker you place at each spot, or left where it is):

    python tools/doa_accuracy.py --speaker --angles 135 --distances 0.4 --trials 6 --auto

Angles are world bearings (SPEC §8: 0 = the array's marked front, clockwise, so 90 = its right); measure
them with the floor tape. Per position it prints the median bearing and its error, the spread, the share
of speech readings within 15 deg (attribution's face window, SPEC §13) and 30 deg, the settle time (talk
start -> the bearing holds within 15 deg of where it ends up), and how often the XVF3800's second focused
beam reports another talker >= 30 deg away (with one talker: a ghost rate; with two: a detection rate).
Results go to logs/doa_accuracy/<time>.json.

Uses the array's control interface: stop `python -m laptop` first. Run from a normal terminal.
"""

import argparse
import json
import math
import sys
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from laptop.audio import config as C  # noqa: E402
from laptop.audio.localize import aec_to_device  # noqa: E402
from laptop.audio.xvf3800 import ENERGY_HOLD_S, XVF3800, sample, to_world  # noqa: E402
from laptop.geometry import angdiff, circular_mean  # noqa: E402

SR = 16000
MIN_CONF = 0.4          # speech readings the pipeline uses for bearings (speech.bearing_min_confidence)
LINES = ["Hello, can you hear me from over here?", "The weather is lovely today, let's go outside.",
         "I think the demo is going to work really well.", "Why is nobody looking at the person holding the pizza?",
         "One, two, three, four, five, six, seven, eight.", "Can somebody please open the window for me?"]


# ---- measurement ------------------------------------------------------------------------------

class Poller:
    """20 Hz DoA readings with the same confidence as builtin_doa.py, in world bearings."""

    def __init__(self, dev, offset, ccw):
        self.dev, self.offset, self.ccw = dev, offset, ccw
        self.rows, self.stop, self.lock = [], False, threading.Lock()
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def loop(self):
        held, held_t = 0.0, 0.0
        period = 1.0 / C.DOA_RATE_HZ
        nxt = time.perf_counter()
        while not self.stop:
            s = sample(self.dev)
            peak = max(s["beam_energy"])
            if peak > 0 or s["t"] - held_t > ENERGY_HOLD_S:
                held, held_t = peak, s["t"]
            lvl = 10 * math.log10(held) if held > 0 else 0.0
            conf = min(1.0, max(0.0, (lvl - C.CONF_DB_LOW) / (C.CONF_DB_HIGH - C.CONF_DB_LOW))) if s["speech"] else 0.0
            main = to_world(s["doa_deg"], self.offset, self.ccw)
            second = None
            for az, e in zip(s["beam_az_deg"][:2], s["beam_energy"][:2]):
                b = to_world(aec_to_device(az), self.offset, self.ccw)
                if e > 0 and abs(angdiff(b, main)) >= C.SECOND_BEAM_MIN_SEP_DEG:
                    second = b
                    break
            with self.lock:
                self.rows.append({"t": s["t"], "bearing": main, "speech": s["speech"], "conf": round(conf, 2),
                                  "second": second})
            nxt += period
            time.sleep(max(0.0, nxt - time.perf_counter()))

    def window(self, t0, t1):
        with self.lock:
            return [r for r in self.rows if t0 <= r["t"] <= t1]


def settle_time(rows, t_talk, final, tol=15.0, hold_s=0.5):
    """Seconds from talk start until the bearing stays within tol of `final` for hold_s."""
    speech = [r for r in rows if r["speech"] and r["conf"] >= MIN_CONF]
    for i, r in enumerate(speech):
        if abs(angdiff(r["bearing"], final)) > tol:
            continue
        run = [x for x in speech[i:] if x["t"] <= r["t"] + hold_s]
        if run and all(abs(angdiff(x["bearing"], final)) <= tol for x in run) and run[-1]["t"] - r["t"] >= hold_s * 0.8:
            return round(r["t"] - t_talk, 2)
    return None


def summarize(rows, expected, t_talk):
    speech = [r for r in rows if r["speech"] and r["conf"] >= MIN_CONF]
    if len(speech) < 5:
        return {"n": len(speech), "error": None}
    b = [r["bearing"] for r in speech]
    mean, spread = circular_mean(b)
    med = float(np.median([angdiff(x, mean) for x in b])) + mean
    out = {"n": len(speech), "median": round(((med + 180) % 360) - 180, 1), "spread": round(spread, 1),
           "settle_s": settle_time(rows, t_talk, mean),
           "second_beam": round(sum(1 for r in speech if r["second"] is not None) / len(speech), 2),
           "speech_flagged": round(sum(1 for r in rows if r["speech"] and r["conf"] >= MIN_CONF) / max(1, len(rows)), 2)}
    if expected is not None:
        errs = [abs(angdiff(x, expected)) for x in b]
        out.update(error=round(angdiff(out["median"], expected), 1), within15=round(np.mean(np.array(errs) <= 15), 2),
                   within30=round(np.mean(np.array(errs) <= 30), 2))
    else:
        errs = [abs(angdiff(x, mean)) for x in b]
        out.update(error=None, within15=round(np.mean(np.array(errs) <= 15), 2),
                   within30=round(np.mean(np.array(errs) <= 30), 2))
    return out


# ---- sound ------------------------------------------------------------------------------------

def output_device(spec):
    import sounddevice as sd
    if spec is None:
        return sd.default.device[1]
    if str(spec).isdigit():
        return int(spec)
    return next(i for i, d in enumerate(sd.query_devices())
                if d["max_output_channels"] > 0 and str(spec).lower() in d["name"].lower())


def to_rate(x, rate):
    from scipy.signal import resample_poly
    return resample_poly(x, rate // 1000, SR // 1000).astype(np.float32) if rate != SR else x.astype(np.float32)


def speech_clip(seconds):
    """TTS lines back to back (Windows voices), about `seconds` long, as one buffer (no stream clicks)."""
    from make_mock_scene import tts
    work = ROOT / "logs" / "doa_accuracy"
    work.mkdir(parents=True, exist_ok=True)
    parts, total, i = [], 0.0, 0
    while total < seconds:
        voice = "Microsoft Zira Desktop" if i % 2 == 0 else "Microsoft David Desktop"
        path = work / f"tts{i % len(LINES)}_{i % 2}.wav"
        x = tts(LINES[i % len(LINES)], voice, path).astype(np.float32)
        parts += [x, np.zeros(int(0.25 * SR), np.float32)]
        total += len(x) / SR + 0.25
        i += 1
    return np.concatenate(parts)[: int(seconds * SR)]


class NoiseLoop:
    """Crowd noise looping on an output device while positions are measured."""

    def __init__(self, path, device, gain):
        import sounddevice as sd
        from laptop.audio.mock import read_wav
        self.sd = sd
        self.dev = output_device(device)
        self.rate = int(sd.query_devices(self.dev)["default_samplerate"])
        self.x = to_rate(np.clip(read_wav(path).astype(np.float32) * gain / 32768.0, -1, 1), self.rate)
        self.pos = 0
        self.stream = sd.OutputStream(samplerate=self.rate, channels=1, dtype="float32", device=self.dev,
                                      callback=self.cb)

    def cb(self, out, frames, ti, status):
        idx = (np.arange(frames) + self.pos) % len(self.x)
        out[:, 0] = self.x[idx]
        self.pos = (self.pos + frames) % len(self.x)

    def __enter__(self):
        self.stream.start()
        return self

    def __exit__(self, *a):
        self.stream.stop()
        self.stream.close()


# ---- run --------------------------------------------------------------------------------------

def measure_position(poller, angle, distance, seconds, args, clip):
    where = f"{angle:+.0f} deg, {distance} m"
    if not args.auto:
        who = "Put the loudspeaker" if args.speaker else "Stand"
        input(f"\n> {who} at {where}, facing the array. Press Enter, then "
              f"{'it plays' if args.speaker else 'talk continuously'} for {seconds:.0f} s.")
    else:
        print(f"\n> {where} (auto)")
        time.sleep(args.gap)
    t_talk = time.time()
    if args.speaker:
        import sounddevice as sd
        dev = output_device(args.play_device)
        rate = int(sd.query_devices(dev)["default_samplerate"])
        y = to_rate(np.clip(clip * args.gain / 32768.0, -1, 1), rate)
        sd.play(y, rate, device=dev)
        sd.wait()
        # Playback starts 0.6-2.4 s after the call on Windows: count settle time from the first speech reading
        # after the call instead (printed as settle_from_flag).
    else:
        end = t_talk + seconds
        while time.time() < end:
            print(f"\r  listening... {end - time.time():4.1f} s", end="", flush=True)
            time.sleep(0.2)
        print()
    rows = poller.window(t_talk, time.time() + 0.6)
    if args.speaker:
        first = next((r["t"] for r in rows if r["speech"] and r["conf"] >= MIN_CONF), t_talk)
        t_talk = first
    res = summarize(rows, None if args.no_truth else angle, t_talk)
    res.update(angle=angle, distance=distance, seconds=seconds, noise=bool(args.noise))
    err = "-" if res.get("error") is None else f"{res['error']:+.1f}"
    print(f"  n={res['n']} median {res.get('median', '-')} err {err} spread {res.get('spread', '-')} "
          f"within15 {res.get('within15', '-')} settle {res.get('settle_s', '-')} s second-beam {res.get('second_beam', '-')}")
    return res


def table(results):
    lines = ["| angle | dist | noise | readings | median | error | spread | within 15° | within 30° | settle | 2nd beam |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        f = lambda k, fmt="{}": "-" if r.get(k) is None else fmt.format(r[k])  # noqa: E731
        lines.append(f"| {r['angle']:+.0f}° | {r['distance']} m | {'yes' if r['noise'] else 'no'} | {r['n']} | "
                     f"{f('median', '{:+.1f}°')} | {f('error', '{:+.1f}°')} | {f('spread', '{:.1f}°')} | "
                     f"{f('within15', '{:.0%}')} | {f('within30', '{:.0%}')} | {f('settle_s', '{:.2f} s')} | "
                     f"{f('second_beam', '{:.0%}')} |")
    ok = [r for r in results if r.get("error") is not None]
    if ok:
        errs = [abs(r["error"]) for r in ok]
        lines.append(f"\nmean |error| {np.mean(errs):.1f}°, max {max(errs):.1f}°, "
                     f"positions within 15°: {sum(e <= 15 for e in errs)}/{len(errs)}")
    settle = [r["settle_s"] for r in results if r.get("settle_s") is not None]
    if settle:
        lines.append(f"settle time median {np.median(settle):.2f} s, max {max(settle):.2f} s")
    return "\n".join(lines)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--angles", type=float, nargs="*", default=[0, 45, 90, 135, 180, -135, -90, -45])
    p.add_argument("--distances", type=float, nargs="*", default=[1.0, 1.5])
    p.add_argument("--seconds", type=float, default=10.0)
    p.add_argument("--trials", type=int, default=1, help="repeat each position (settle-time statistics)")
    p.add_argument("--noise", help="crowd noise WAV (16 kHz mono) to loop on --noise-device while measuring")
    p.add_argument("--noise-device", help="output for the noise (index or name substring; default output)")
    p.add_argument("--noise-gain", type=float, default=1.0)
    p.add_argument("--speaker", action="store_true", help="play TTS speech from a loudspeaker instead of a person")
    p.add_argument("--play-device", help="loudspeaker output (index or name substring; default output)")
    p.add_argument("--gain", type=float, default=1.5, help="TTS playback gain")
    p.add_argument("--auto", action="store_true", help="don't wait for Enter between positions")
    p.add_argument("--gap", type=float, default=3.0, help="silence before each auto position, seconds")
    p.add_argument("--no-truth", action="store_true", help="the angles are unknown: report consistency only")
    p.add_argument("--offset", type=float, default=C.ARRAY_OFFSET_DEG)
    p.add_argument("--ccw", action=argparse.BooleanOptionalAction, default=not C.ARRAY_CLOCKWISE)
    p.add_argument("--note", default="", help="free text saved with the results (room, noise level, who talked)")
    args = p.parse_args()

    dev = XVF3800()
    poller = Poller(dev, args.offset, args.ccw)
    poller.thread.start()
    clip = speech_clip(args.seconds) if args.speaker else None
    results = []
    noise = NoiseLoop(args.noise, args.noise_device, args.noise_gain) if args.noise else None
    try:
        if noise:
            noise.__enter__()
        for d in args.distances:
            for a in args.angles:
                for _ in range(args.trials):
                    results.append(measure_position(poller, a, d, args.seconds, args, clip))
    except (KeyboardInterrupt, EOFError):
        print("\nstopped; keeping what was measured")
    finally:
        if noise:
            noise.__exit__()
        poller.stop = True
        poller.thread.join(timeout=2)
        dev.close()
    out = ROOT / "logs" / "doa_accuracy" / time.strftime("%Y%m%d_%H%M%S.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"args": vars(args), "results": results}, indent=1), encoding="utf-8")
    print("\n" + table(results))
    print(f"\n(saved {out})")


if __name__ == "__main__":
    main()

"""Record a scripted scene through the REAL array, played from a loudspeaker (no people needed).

Plays the README §4 lines (Windows TTS), the enrolled doorbell and the user's name from one loudspeaker
(the laptop's own speakers, or a Bluetooth speaker you place somewhere), while the ReSpeaker records
exactly what `python -m laptop --real --record` records: the processed audio plus the built-in DoA.
Then it finds every clip in the recording by cross-correlation (playback start-up latency on Windows
is 0.6-2.4 s and varies, so play-call times are useless as ground truth) and writes meta.json `lines`
in the same shape as fixtures/mock_scene, so tools/stress_test.py can score it as a golden condition.

    python tools/speaker_scene.py --out array_tts                  # laptop speakers (default output)
    python tools/speaker_scene.py --out array_tts --play-device Realtek --gain 1.5
    python tools/speaker_scene.py --list                           # output devices
    python tools/speaker_scene.py --out raw_speaker_180 --raw      # 6-ch firmware: + raw.wav, + a baby cry

With the 6-channel firmware the recording also has raw.wav (the 4 raw mics) and the device meta (firmware,
routing, fixed beams, calibration, mic geometry); --raw adds the baby cry to the scene and plays every clip from
the same spot, so the raw-mic recording is the source material for tools/make_raw_scene.py (rotation scenes).

Each line's `bearing_deg` is the measured DoA median while it played (`bearing_source: measured`):
one loudspeaker means one direction, so this checks captions, timing, doorbell and name called on the
real array's audio, not direction accuracy (tools/doa_accuracy.py does that).
Run from a normal terminal: mic capture from a sandboxed process returns silence.
"""

import torch  # noqa: F401  (first: Windows DLL order, as everywhere that may load models)

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from laptop import settings  # noqa: E402
from laptop.audio.mock import read_sources, read_streams, read_wav  # noqa: E402
from laptop.geometry import circular_mean  # noqa: E402

SR = 16000
ZIRA, DAVID = "Microsoft Zira Desktop", "Microsoft David Desktop"
SCENE = [   # (who, voice, text or None for the doorbell, pause before it in seconds)
    ("Person 1", ZIRA, "Hey, did you see the email about the internship fair?", 2.0),
    ("Person 2", DAVID, "Yes! It's on Friday. Are you going?", 1.0),
    ("Person 1", ZIRA, "Definitely. I'm bringing twenty copies of my resume.", 1.0),
    ("Speaker B", DAVID, "Why is nobody looking at the person holding all the pizza?", 1.2),
    ("doorbell", None, None, 1.5),
    ("Speaker B", DAVID, "Hey Kori, the pizza is here!", 1.5),
    ("Person 1", ZIRA, "That was honestly the best pizza I have had all week.", 1.2),
    ("Person 2", DAVID, "Can somebody please open the window?", 1.2),
]


class NullSink:
    """Stands in for the relay: the recording is all we want."""

    connected = True

    def __init__(self):
        self.on_config = self.on_message = None
        self.heartbeat_fields = lambda: {"status": "ok"}

    def send(self, msg):
        return True

    async def run(self):
        while True:
            await asyncio.sleep(3600)


RAW_EXTRA = [("baby_cry", None, None, 1.5)]      # --raw: a non-speech source for the rotation scenes


def clips(scene=SCENE):
    from make_mock_scene import tts
    work = ROOT / "logs" / "speaker_scene"
    work.mkdir(parents=True, exist_ok=True)
    out = []
    for i, (who, voice, text, pause) in enumerate(scene):
        if text is None:
            x = read_wav(ROOT / "fixtures" / "sounds" / f"{who}.wav").astype(np.float32)
        else:
            x = tts(text, voice, work / f"line{i}.wav")
        out.append((who, text, pause, x))
    return out


def play_scene(scene, device, gain):
    import sounddevice as sd
    from scipy.signal import resample_poly

    if isinstance(device, str):                  # name substring -> first matching output (indexes shift between runs)
        device = next(i for i, d in enumerate(sd.query_devices())
                      if d["max_output_channels"] > 0 and device.lower() in d["name"].lower())
    if device is None:
        device = sd.default.device[1]
    print(f"  output: [{device}] {sd.query_devices(device)['name']}")
    rate = int(sd.query_devices(device)["default_samplerate"])
    # One continuous buffer, played once: opening a stream per line clicks ~0.6 s before the audio, and the
    # array flags clicks as speech (they bridged pauses between lines in the first recording).
    parts, offsets, pos = [], [], 0
    for who, text, pause, x in scene:
        parts += [np.zeros(int(pause * SR), np.float32), x.astype(np.float32)]
        offsets.append(pos + int(pause * SR))
        pos += int(pause * SR) + len(x)
    parts.append(np.zeros(int(3.0 * SR), np.float32))
    y = np.clip(np.concatenate(parts) * gain / 32768.0, -1, 1)
    if rate != SR:
        y = resample_poly(y, rate // 1000, SR // 1000)
    print(f"  playing the scene ({len(y) / rate:.0f} s)", flush=True)
    t0 = time.time()
    sd.play(y.astype(np.float32), rate, device=device)
    sd.wait()
    return [t0 + o / SR for o in offsets]


def envelope(x, frame=160):
    """log RMS per 10 ms frame: survives reverb, AEC and noise suppression, unlike the waveform."""
    n = len(x) // frame
    f = x[:n * frame].astype(np.float32).reshape(n, frame)
    return np.log(np.sqrt(np.mean(f ** 2, axis=1)) + 30.0)


def locate_clip(rec_env, clip, lo, hi):
    """Start (seconds) of `clip` in the recording, searched in [lo, hi]: best normalized envelope correlation."""
    c = envelope(clip)
    c = (c - c.mean()) / (c.std() + 1e-9)
    best = (lo, -1.0)
    for i in range(max(0, int(lo * 100)), min(len(rec_env) - len(c), int(hi * 100)) + 1):
        w = rec_env[i:i + len(c)]
        sc = float(np.mean((w - w.mean()) / (w.std() + 1e-9) * c))
        if sc > best[1]:
            best = (i / 100, sc)
    return best


def align(folder, scene, plays):
    """Ground-truth lines for meta.json from the recording itself, near each logged play time."""
    audio = read_streams(folder / "audio.wav")              # beams mode: 2 channels; align on the louder beam
    rec_env = envelope(audio[:, int(np.argmax(np.std(audio.astype(np.float32), axis=0)))])
    sources = read_sources(folder / "sources.jsonl")
    lines = []
    for (who, text, _, x), played in zip(scene, plays):
        nz = np.nonzero(np.abs(x) > 300)[0]
        lead = nz[0] / SR if len(nz) else 0.0
        x = x[nz[0]: nz[-1] + 1] if len(nz) else x            # voiced part only
        t0, score = locate_clip(rec_env, x, played + lead - 0.3, played + lead + 2.5)
        t1 = t0 + len(x) / SR
        speech = [s for t, ss in sources if t0 <= t <= t1 for s in ss if s.speech and s.confidence >= 0.4]
        bearing = round(circular_mean([s.bearing_deg for s in speech])[0], 1) if speech else None
        lines.append({"who": who, "bearing_deg": bearing, "text": text, "t_start": round(t0, 2),
                      "t_end": round(t1, 2), "match": round(score, 2), "play_lag_s": round(t0 - played - lead, 2),
                      "doa_readings": len(speech)})
        print(f"  {who:9s} at {t0:6.2f}-{t1:6.2f} s (match {score:.2f}, playback lag {t0 - played - lead:.2f} s), "
              f"DoA {bearing} from {len(speech)} readings")
    for line in lines:                   # sounds aren't speech: the built-in DoA rarely flags them
        if line["text"] is None and line["bearing_deg"] is None:
            line["bearing_deg"] = lines[0]["bearing_deg"]
    return lines


async def record(folder, device, gain, scene):
    from laptop.app import Laptop, Recorder
    from laptop.audio.builtin_doa import ReSpeaker

    cfg = settings.load()
    ears = ReSpeaker()
    raw_channels = len(cfg["audio"]["raw_mic_channels"]) if ears.raw is not None else 0
    rec = Recorder(folder, {"name": folder.name, "mode": "real", **ears.meta()}, channels=ears.streams,
                   raw_channels=raw_channels, raw_gain=cfg["audio"].get("raw_record_gain", 1.0))
    app = Laptop(cfg, (ears, ears), NullSink(), recorder=rec)
    task = asyncio.create_task(app.run())
    await asyncio.sleep(1.5)                     # stream and DoA poll running
    plays = await asyncio.to_thread(play_scene, scene, device, gain)
    task.cancel()
    await asyncio.wait([task], timeout=5)
    return [round(t - rec.t0, 3) for t in plays]      # seconds into the recording


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="array_tts", help="fixtures/<out>")
    p.add_argument("--play-device", help="output device index or name substring (default: the system default)")
    p.add_argument("--gain", type=float, default=1.5, help="playback gain on the TTS level")
    p.add_argument("--position", default="laptop speakers", help="where the loudspeaker is, for meta.json notes")
    p.add_argument("--align-only", action="store_true", help="re-run the alignment on an existing recording")
    p.add_argument("--list", action="store_true", help="list output devices")
    p.add_argument("--raw", action="store_true", help="add the baby cry (raw-mic source material)")
    p.add_argument("--true-bearing", type=float, help="the loudspeaker's known world bearing, for meta.json")
    a = p.parse_args()
    if a.list:
        import sounddevice as sd
        for i, d in enumerate(sd.query_devices()):
            if d["max_output_channels"] > 0:
                print(i, d["name"], sd.query_hostapis(d["hostapi"])["name"])
        return
    if a.play_device is not None and a.play_device.isdigit():
        a.play_device = int(a.play_device)
    folder = ROOT / "fixtures" / a.out
    scene = clips(SCENE + RAW_EXTRA if a.raw else SCENE)
    if not a.align_only:
        print(f"recording {folder} ...")
        plays = asyncio.run(record(folder, a.play_device, a.gain, scene))
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    plays = meta["plays"] if a.align_only else plays
    meta.update(plays=plays, synthetic=False, user_name="Kori", bearing_source="measured",
                notes=f"tools/speaker_scene.py: TTS + enrolled doorbell from one loudspeaker ({a.position}) "
                      "recorded through the real XVF3800. No people's voices. Line times from envelope correlation.",
                lines=align(folder, scene, plays))
    if a.true_bearing is not None:
        meta["true_bearing_deg"] = a.true_bearing
    (folder / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"saved {folder / 'meta.json'}")


if __name__ == "__main__":
    main()

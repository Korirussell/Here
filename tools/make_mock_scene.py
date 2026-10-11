"""Builds fixtures/mock_scene: a synthetic recording of README §4's three core moments.

Speech comes from Windows text-to-speech (two voices), the doorbell is a synthesized ding-dong,
and `sources.jsonl` scripts the directions a ReSpeaker would report. `python -m laptop --mock`
replays it, so the whole system (laptop -> relay -> phone -> belt) runs with no hardware.

    python tools/make_mock_scene.py            # Windows only (System.Speech); output is committed

Scene (world bearings, 0 = array front, clockwise):
    Person 1  front-right  +35 deg
    Person 2  front-left   -35 deg
    Speaker B behind       180 deg   (the turn-around line, then the name call)
    doorbell  behind-right +165 deg
"""

import json
import random
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

SR = 16000
OUT = Path(__file__).resolve().parents[1] / "fixtures" / "mock_scene"

# (who, bearing, voice, text) or ("doorbell", bearing, None, None) or ("pause", seconds)
SCRIPT = [
    ("pause", 1.0),
    ("Person 1", 35.0, "Microsoft David Desktop", "Hey, did you see the email about the internship fair?"),
    ("pause", 0.9),
    ("Person 2", -35.0, "Microsoft Zira Desktop", "Yes! It's on Friday. Are you going?"),
    ("pause", 0.9),
    ("Person 1", 35.0, "Microsoft David Desktop", "Definitely. I'm bringing twenty copies of my resume."),
    ("pause", 1.2),
    ("Speaker B", 180.0, "Microsoft Zira Desktop", "Why is nobody looking at the person holding all the pizza?"),
    ("pause", 1.5),
    ("doorbell", 165.0, None, None),
    ("pause", 1.5),
    ("Speaker B", 180.0, "Microsoft Zira Desktop", "Hey Kori, the pizza is here!"),
    ("pause", 2.0),
]


def tts(text, voice, path):
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SelectVoice('{voice}'); $s.Rate = 1; "
        "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, "
        "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono); "
        f"$s.SetOutputToWaveFile('{path}', $f); $s.Speak(\"{text}\"); $s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    with wave.open(str(path)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32)
    nz = np.nonzero(np.abs(x) > 200)[0]                      # trim TTS leading/trailing silence
    return x[max(0, nz[0] - 800): nz[-1] + 1600] if len(nz) else x


def doorbell():
    """Two-tone ding-dong with harmonics and exponential decay (~1.6 s), played twice like a real bell press."""
    def tone(f, dur):
        t = np.arange(int(SR * dur)) / SR
        env = np.exp(-t * 3.2) * np.minimum(1, t * 200)
        return env * sum(a * np.sin(2 * np.pi * f * k * t) for k, a in ((1, 1.0), (2, 0.35), (3, 0.15), (4.2, 0.08)))
    ding, dong = tone(659.3, 0.8), tone(523.3, 1.1)
    press = np.concatenate([ding, dong])
    return np.concatenate([press, np.zeros(int(SR * 0.3)), press]) * 9000


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(7)
    audio, sources, lines = [], [], []
    t = 0.0
    tmp = Path(tempfile.mkdtemp())
    last_bearing = 0.0
    for i, step in enumerate(SCRIPT):
        if step[0] == "pause":
            n = int(step[1] * SR)
            audio.append(np.random.default_rng(i).standard_normal(n) * 40)     # room tone
            t0 = t
            t += step[1]
            k = t0
            while k < t:                                                         # idle sources at keepalive rate
                sources.append({"t": round(k, 3), "sources": [{"id": 0, "bearing_deg": last_bearing, "energy": 0.03,
                                                               "speech": False, "confidence": 0.0}]})
                k += 1.0
            continue
        who, bearing, voice, text = step
        x = doorbell() if who == "doorbell" else tts(text, voice, tmp / f"{i}.wav")
        dur = len(x) / SR
        audio.append(x)
        lines.append({"who": who, "bearing_deg": bearing, "text": text, "t_start": round(t, 2),
                      "t_end": round(t + dur, 2)})
        k = t
        while k < t + dur:                                                       # 20 Hz while active
            jitter = rng.gauss(0, 3.0)
            b = ((bearing + jitter + 180) % 360) - 180 or 0.0
            if who == "doorbell":   # the built-in DoA is speech-gated: weak, non-speech reading
                src = {"id": 0, "bearing_deg": round(b, 1), "energy": 0.7, "speech": False, "confidence": 0.3}
            else:
                src = {"id": 0, "bearing_deg": round(b, 1), "energy": 0.6, "speech": True,
                       "confidence": round(rng.uniform(0.7, 0.9), 2)}
            sources.append({"t": round(k, 3), "sources": [src]})
            k += 0.05
        last_bearing = bearing
        t += dur
    pcm = np.clip(np.concatenate(audio), -32768, 32767).astype(np.int16)
    with wave.open(str(OUT / "audio.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    with open(OUT / "sources.jsonl", "w", encoding="utf-8") as f:
        for row in sources:
            f.write(json.dumps(row) + "\n")
    meta = {"name": "mock_scene", "synthetic": True, "seconds": round(len(pcm) / SR, 1), "user_name": "Kori",
            "notes": "Generated by tools/make_mock_scene.py: README §4 core moments. Not a real recording.",
            "lines": lines}
    (OUT / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"wrote {OUT} ({meta['seconds']} s, {len(sources)} source frames)")
    for line in lines:
        print(f"  {line['t_start']:5.1f}-{line['t_end']:5.1f}s {line['who']:9s} {line['bearing_deg']:+6.0f}  {line['text'] or ''}")


if __name__ == "__main__":
    main()

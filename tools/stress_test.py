"""Stress lab: the laptop pipeline in a simulated loud hall, scored against the scene's ground truth.

Two parts (SPEC §21 spikes, §24 risks):

1. Audio: fixtures/mock_scene mixed with synthetic crowd babble (Windows TTS voices talking over each
   other) at falling SNRs, run through the real pipeline (Silero + faster-whisper + PANNs) in real time.
   Scores per condition: caption word error rate, phantom captions (babble transcribed as speech),
   speech_start / final latency, utterance bearing error, doorbell hit/false alarms, name-called hit/false alarms.
2. Directions (no models, fast): how much DoA jitter and how many stray readings the utterance bearing
   survives before attribution (face within 15 deg, SPEC §13) picks the wrong person.

    python tools/stress_test.py                 # both parts (~4 min on the RTX)
    python tools/stress_test.py --doa-only      # part 2 only (seconds)
    python tools/stress_test.py --snr 10 0      # chosen SNRs only
    python tools/stress_test.py --golden        # pass/fail regression gate (exit 1 on a regression)

Synthetic noise is not the hall: use it to compare settings and catch failure modes, then confirm with
real recordings (fixtures/README.md).
"""

import torch  # noqa: F401  (first: cuBLAS/cuDNN DLLs for faster-whisper on Windows)

import argparse
import asyncio
import json
import random
import re
import shutil
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from contracts.python.here_contract import Source  # noqa: E402
from laptop import settings  # noqa: E402
from laptop.app import Laptop  # noqa: E402
from laptop.audio.base import DirectionHistory  # noqa: E402
from laptop.audio.mock import FixturePlayer, read_wav  # noqa: E402
from laptop.geometry import angdiff  # noqa: E402

SR = 16000
SCENE = ROOT / "fixtures" / "mock_scene"
WORK = ROOT / "logs" / "stress"
BABBLE_LINES = [
    "So I told him the meeting was moved to Thursday afternoon.",
    "Did anyone else get the free shirts at the entrance?",
    "The wifi in this building is honestly terrible today.",
    "We should grab dinner after the judging ends tonight.",
    "My team is still debugging the database connection.",
    "Have you seen the robot that plays chess near the stage?",
    "I think the keynote speaker was from a startup in Austin.",
    "Can you send me the slides when you get a chance?",
    "The coffee ran out an hour ago and nobody refilled it.",
    "We pivoted twice already and it is only Saturday.",
    "Wait, where did you put the charger for the laptop?",
    "That demo with the drone was actually really impressive.",
]
NUMBER_WORDS = {"20": "twenty", "2": "two", "1": "one"}


# ---- noise ------------------------------------------------------------------------------------

def babble(seconds, seed=0):
    """Crowd babble: 5 overlapping TTS talkers at random offsets, plus low pink-ish room noise."""
    cache = WORK / "babble.wav"
    if cache.exists():
        x = read_wav(cache).astype(np.float32)
    else:
        from make_mock_scene import tts
        WORK.mkdir(parents=True, exist_ok=True)
        voices = ["Microsoft David Desktop", "Microsoft Zira Desktop"]
        clips = [tts(line, voices[i % 2], WORK / f"b{i}.wav") for i, line in enumerate(BABBLE_LINES)]
        rng = random.Random(seed)
        n = int(60 * SR)
        x = np.zeros(n, np.float32)
        for _ in range(5):                                   # 5 simultaneous talkers
            pos = rng.randint(0, SR)
            while pos < n:
                c = clips[rng.randrange(len(clips))] * rng.uniform(0.5, 1.0)
                end = min(n, pos + len(c))
                x[pos:end] += c[:end - pos]
                pos = end + rng.randint(0, SR // 3)
        rng_np = np.random.default_rng(seed)
        pink = np.cumsum(rng_np.standard_normal(n)).astype(np.float32)
        pink -= np.convolve(pink, np.ones(400) / 400, mode="same")   # crude high-pass of a random walk
        x += pink / (np.std(pink) + 1e-9) * np.std(x) * 0.3
        with wave.open(str(cache), "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(SR)
            w.writeframes(np.clip(x / np.max(np.abs(x)) * 20000, -32768, 32767).astype(np.int16).tobytes())
        x = read_wav(cache).astype(np.float32)
    reps = int(np.ceil(seconds * SR / len(x)))
    return np.tile(x, reps)[: int(seconds * SR)]


def make_condition(name, snr_db, scene=SCENE):
    """Copy a scene fixture into logs/stress/<name> with babble mixed at snr_db (None = clean)."""
    out = WORK / name
    out.mkdir(parents=True, exist_ok=True)
    for f in ("sources.jsonl", "meta.json"):
        shutil.copy(scene / f, out / f)
    speech = read_wav(scene / "audio.wav").astype(np.float32)
    mix = speech
    if snr_db is not None:
        meta = json.loads((scene / "meta.json").read_text(encoding="utf-8"))
        voiced = np.concatenate([speech[int(l["t_start"] * SR): int(l["t_end"] * SR)]
                                 for l in meta["lines"] if l["who"] != "doorbell"])
        noise = babble(len(speech) / SR)
        gain = np.sqrt(np.mean(voiced ** 2) / (np.mean(noise ** 2) * 10 ** (snr_db / 10)))
        mix = speech + noise * gain
    with wave.open(str(out / "audio.wav"), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(SR)
        w.writeframes(np.clip(mix, -32768, 32767).astype(np.int16).tobytes())
    return out


def make_room_condition(name, snr_db, scene, room, offset_s=19.0):
    """A beams-mode scene (2 channels, e.g. raw_speaker_180) with a real room recorded through the same array
    in beams mode (e.g. k1_positions: people chatting around the desk) mixed in per beam at snr_db.

    SNR is measured on the beam that carries the scene's speech (the louder one while lines play), voiced
    parts vs the room on that beam. The array's readings are merged too: a beam flags speech when either
    recording does (the chatter keeps the array's speech flags busy, as it did live), energies add as powers.
    The XVF3800's processing isn't linear (AGC, noise suppression), so this is close to, not the same as,
    the scene played in that room.
    """
    from laptop.audio.mock import read_streams
    out = WORK / name
    out.mkdir(parents=True, exist_ok=True)
    meta = json.loads((scene / "meta.json").read_text(encoding="utf-8"))
    speech = read_streams(scene / "audio.wav").astype(np.float32)
    noise = read_streams(room / "audio.wav").astype(np.float32)
    if speech.shape[1] != 2 or noise.shape[1] != 2:
        raise SystemExit("--room needs two beams-mode recordings (2-channel audio.wav)")
    i0 = int(offset_s * SR)
    noise = np.concatenate([noise[i0:], noise[:i0]])
    noise = np.tile(noise, (int(np.ceil(len(speech) / len(noise))), 1))[: len(speech)]
    voiced = np.concatenate([speech[int(l["t_start"] * SR): int(l["t_end"] * SR)]
                             for l in meta["lines"] if l["text"]])
    beam = int(np.argmax(np.mean(voiced ** 2, axis=0)))
    seg = np.concatenate([noise[int(l["t_start"] * SR): int(l["t_end"] * SR), beam]
                          for l in meta["lines"] if l["text"]])
    gain = 0.0 if snr_db is None else np.sqrt(np.mean(voiced[:, beam] ** 2) / (np.mean(seg ** 2) * 10 ** (snr_db / 10)))
    mix = speech + noise * gain
    with wave.open(str(out / "audio.wav"), "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(SR)
        w.writeframes(np.clip(mix, -32768, 32767).astype(np.int16).tobytes())
    # readings: the scene's frames, each merged with the room frame at the same (shifted) time
    lo, hi = settings.load()["audio"]["energy_dbfs_low"], settings.load()["audio"]["energy_dbfs_high"]
    to_db = lambda e: lo + e * (hi - lo)  # noqa: E731
    room_rows = [json.loads(x) for x in (room / "sources.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    room_t = np.array([r["t"] for r in room_rows])
    room_len = len(read_streams(room / "audio.wav")) / SR
    g_db = 20 * np.log10(max(gain, 1e-9))
    with open(out / "sources.jsonl", "w", encoding="utf-8") as f:
        for line in (scene / "sources.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            r = room_rows[int(np.argmin(np.abs(room_t - (d["t"] + offset_s) % room_len)))]
            by_id = {s["id"]: s for s in r["sources"]}
            for s in d["sources"]:
                n = by_id.get(s["id"])
                if n is None or not gain:
                    continue
                p = 10 ** (to_db(s["energy"]) / 10) + 10 ** ((to_db(n["energy"]) + g_db) / 10)
                s["energy"] = round(float(np.clip((10 * np.log10(p) - lo) / (hi - lo), 0, 1)), 2)
                s["speech"] = bool(s["speech"] or n["speech"])
                s["confidence"] = max(s["confidence"], n["confidence"])
            f.write(json.dumps(d) + "\n")
    meta.update(name=name, room=room.name, room_offset_s=offset_s, snr_db=snr_db, snr_beam=beam)
    (out / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return out


def make_babble_only(seconds=40.0, seed=3):
    """Crowd babble and nobody talking to the wearer (0 captions expected), at the 0 dB condition's babble level.

    The DoA track is a model of the array in a crowd, not a recording (no crowd_noise fixture yet): speech
    flagged 60% of the time from random directions with weak confidence (distant talkers), 20 Hz.
    """
    out = WORK / "babble_only"
    out.mkdir(parents=True, exist_ok=True)
    speech = read_wav(SCENE / "audio.wav").astype(np.float32)
    meta = json.loads((SCENE / "meta.json").read_text(encoding="utf-8"))
    voiced = np.concatenate([speech[int(l["t_start"] * SR): int(l["t_end"] * SR)]
                             for l in meta["lines"] if l["who"] != "doorbell"])
    noise = babble(seconds)
    noise *= np.sqrt(np.mean(voiced ** 2) / np.mean(noise ** 2))
    with wave.open(str(out / "audio.wav"), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(SR)
        w.writeframes(np.clip(noise, -32768, 32767).astype(np.int16).tobytes())
    rng = np.random.default_rng(seed)
    with open(out / "sources.jsonl", "w", encoding="utf-8") as f:
        for i in range(int(seconds * 20)):
            sp = bool(rng.random() < 0.6)
            s = {"id": 0, "bearing_deg": round(float(rng.uniform(-179, 180)), 1), "energy": 0.5, "speech": sp,
                 "confidence": round(float(rng.uniform(0.1, 0.55)), 2) if sp else 0.0}
            f.write(json.dumps({"t": round(i * 0.05, 3), "sources": [s]}) + "\n")
    (out / "meta.json").write_text(json.dumps({"name": "babble_only", "synthetic": True, "user_name": "Kori",
                                               "lines": []}, indent=1), encoding="utf-8")
    return out


def score_empty(msgs, t0=None, meta=None):
    """Nobody talking: everything shown is a phantom."""
    finals = [m for _, m in msgs if m.TYPE == "utterance" and m.final and m.text]
    shown = {m.utt_id for _, m in msgs if m.TYPE == "utterance" and (m.text or m.pending)}
    return {"captions": [m.text for m in finals], "partials_shown": len(shown),
            "speech_starts": sum(1 for _, m in msgs if m.TYPE == "speech_start"),
            "alerts": [m.kind for _, m in msgs if m.TYPE == "alert" and m.active]}


# ---- scoring ----------------------------------------------------------------------------------

def words(text):
    toks = re.sub(r"[^\w' ]", " ", text.lower()).split()
    return [NUMBER_WORDS.get(t, t) for t in toks]


def wer(ref, hyp):
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)] / max(1, len(r))


class Collector:
    """Stands in for the relay client: records every message with its send time."""

    connected = True

    def __init__(self):
        self.msgs = []
        self.on_config = self.on_message = None
        self.heartbeat_fields = lambda: {"status": "ok"}

    def send(self, msg):
        self.msgs.append((time.time(), msg))
        return True

    async def run(self):
        while True:
            await asyncio.sleep(3600)


def score(msgs, t0, meta):
    lines = meta["lines"]
    speech_lines = [l for l in lines if l["text"]]
    rel = lambda t: t - t0  # noqa: E731
    finals = [(rel(sent), m) for sent, m in msgs if m.TYPE == "utterance" and m.final and m.text]
    starts = [(rel(sent), m) for sent, m in msgs if m.TYPE == "speech_start"]
    alerts = [(rel(sent), m) for sent, m in msgs if m.TYPE == "alert" and m.active]
    used = set()
    rows = []
    for l in speech_lines:
        win = [(s, m) for s, m in finals if m.t_start - t0 < l["t_end"] + 0.3 and m.t_end - t0 > l["t_start"] - 0.3]
        used |= {m.utt_id for _, m in win}
        text = " ".join(m.text for _, m in sorted(win, key=lambda x: x[1].t_start))
        first_start = next((s for s, m in starts if l["t_start"] - 0.3 <= s <= l["t_end"]), None)
        last_final = max((s for s, _ in win), default=None)
        bearing_err = [abs(angdiff(m.bearing_deg, l["bearing_deg"])) for _, m in win]
        rows.append({"who": l["who"], "wer": wer(l["text"], text), "text": text,
                     "start_lat": None if first_start is None else first_start - l["t_start"],
                     "final_lat": None if last_final is None else last_final - l["t_end"],
                     "bearing_err": max(bearing_err) if bearing_err else None})
    phantoms = [m.text for _, m in finals if m.utt_id not in used]
    door = next(l for l in lines if l["who"] == "doorbell")
    name_line = next(l for l in lines if l["text"] and "Kori" in l["text"])
    door_alerts = [(s, m) for s, m in alerts if m.kind == "doorbell"]
    name_alerts = [(s, m) for s, m in alerts if m.kind == "name_called"]
    door_hit = [(s, m) for s, m in door_alerts if door["t_start"] <= s <= door["t_end"] + 2]
    name_hit = [(s, m) for s, m in name_alerts if name_line["t_start"] <= s <= name_line["t_end"] + 3]
    long_utts = [m for _, m in finals if m.t_end - m.t_start > 8]
    return {
        "rows": rows,
        "wer": float(np.mean([r["wer"] for r in rows])),
        "phantoms": phantoms,
        "long_utterances": len(long_utts),
        "start_lat": [r["start_lat"] for r in rows if r["start_lat"] is not None],
        "final_lat": [r["final_lat"] for r in rows if r["final_lat"] is not None],
        "bearing_err": [r["bearing_err"] for r in rows if r["bearing_err"] is not None],
        "door_hit": bool(door_hit),
        "door_lat": door_hit[0][0] - door["t_start"] if door_hit else None,
        "door_bearing_err": abs(angdiff(door_hit[0][1].bearing_deg, door["bearing_deg"])) if door_hit and door_hit[0][1].bearing_deg is not None else None,
        "door_false": len({m.alert_id for _, m in door_alerts} - {m.alert_id for _, m in door_hit}),
        "name_hit": bool(name_hit),
        "name_lat": name_hit[0][0] - name_line["t_start"] if name_hit else None,
        "name_false": len({m.alert_id for _, m in name_alerts} - {m.alert_id for _, m in name_hit}),
        "other_alerts": sorted({m.kind for _, m in alerts if m.kind not in ("doorbell", "name_called")}),
        # what was on screen for each false name alert (the utterance's updates, partials included)
        "name_false_text": [
            [f"{'final' if u.final else 'partial'}: {u.text} | {u.pending}" for _, u in msgs
             if u.TYPE == "utterance" and a.ref and u.utt_id == a.ref.utt_id][-3:] + [f"alert at {a.t - t0:.1f} s"]
            for a in {m.alert_id: m for _, m in name_alerts}.values()
            if a.alert_id not in {m.alert_id for _, m in name_hit}],
    }


# ---- part 1: audio ----------------------------------------------------------------------------

async def run_condition(folder, cfg, transcriber, classifier, scorer=None):
    from laptop.sounds.classifier import SoundPipeline
    from laptop.speech.pipeline import SpeechPipeline

    sink = Collector()
    player = FixturePlayer(folder, loop=False, chunk=cfg["audio"]["chunk_samples"])
    app = Laptop(cfg, (player, player), sink,
                 speech=lambda a, stream=0: SpeechPipeline(cfg["speech"], transcriber, a.stream_history(stream),
                                                           sink.send, on_name=a.alerts.name_called,
                                                           locate_speech=player.locate_speech),
                 sounds=lambda a, stream=0: SoundPipeline(classifier, a.stream_history(stream), sink.send, a.alerts),
                 names=["Kori"])
    task = asyncio.create_task(app.run())
    await asyncio.sleep(player.duration + 4.0)
    task.cancel()
    await asyncio.wait([task], timeout=5)
    return scorer(sink.msgs, player.t0, player.meta) if scorer else score(sink.msgs, player.t0, player.meta)


def fmt(xs, unit="s"):
    if not xs:
        return "-"
    return f"{np.median(xs):.2f}{unit} (max {max(xs):.2f})"


def apply_overrides(cfg, sets):
    """--set speech.array_lookback_s=0 style overrides (YAML values), for A/B runs without editing config.yaml."""
    import yaml
    for item in sets or []:
        key, value = item.split("=", 1)
        *path, last = key.split(".")
        node = cfg
        for k in path:
            node = node[k]
        node[last] = yaml.safe_load(value)
        print(f"override {key} = {node[last]!r}")
    return cfg


def audio_part(snrs, scene=SCENE, sets=None, room=None, room_offset=19.0, empty=True):
    from laptop.sounds.classifier import Classifier
    from laptop.speech.transcriber import make_transcriber

    cfg = apply_overrides(settings.load(), sets)
    transcriber, _ = make_transcriber(cfg["speech"])
    classifier = Classifier()
    results = {}
    for snr in snrs:
        name = "clean" if snr is None else f"{room.name if room else 'babble'}_{snr:+d}dB"
        if scene != SCENE:
            name = f"{scene.name}_{name}"
        folder = (make_room_condition(name, snr, scene, room, room_offset) if room
                  else make_condition(name, snr, scene))
        print(f"\n=== {name}: running {FixturePlayer(folder).duration:.0f} s in real time ...", flush=True)
        r = asyncio.run(run_condition(folder, cfg, transcriber, classifier))
        results[name] = r
        for row in r["rows"]:
            print(f"   {row['who']:9s} WER {row['wer']:.2f}  {row['text'][:70]!r}")
        if r["phantoms"]:
            print(f"   phantom captions: {r['phantoms'][:4]}")
        for shown in r["name_false_text"]:
            print(f"   false name called: {shown}")
    print("\n| condition | WER | phantom captions | >8 s utterances | speech_start lag | final lag | bearing err | doorbell | door false | name called | name false |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for name, r in results.items():
        door = f"hit {r['door_lat']:.1f}s, {r['door_bearing_err'] or 0:.0f}°" if r["door_hit"] else "MISS"
        name_c = f"hit {r['name_lat']:.1f}s" if r["name_hit"] else "MISS"
        print(f"| {name} | {r['wer']:.2f} | {len(r['phantoms'])} | {r['long_utterances']} | {fmt(r['start_lat'])} | "
              f"{fmt(r['final_lat'])} | {fmt(r['bearing_err'], '°')} | {door} | {r['door_false']} | {name_c} | {r['name_false']} |")
    if not empty:
        return results
    print("\n=== babble_only (crowd, nobody talking to the wearer): running 40 s ...", flush=True)
    empty = asyncio.run(run_condition(make_babble_only(), cfg, transcriber, classifier, score_empty))
    results["babble_only"] = empty
    print(f"| babble only, 0 dB level | captions {len(empty['captions'])} (expected 0) | partials shown "
          f"{empty['partials_shown']} | speech_starts {empty['speech_starts']} | alerts {empty['alerts'] or 0} |")
    if empty["captions"]:
        print(f"   phantom captions: {empty['captions'][:6]}")
    out = WORK / "results.json"
    out.write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    print(f"\n(details: {out})")


# ---- two sources at once (beams mode) ---------------------------------------------------------

TWO_LINES = {   # who: (voice, bearing, [(start s, text)])
    "Person 1": ("Microsoft Zira Desktop", 35.0, [
        (1.0, "Hey, did you see the email about the internship fair?"),
        (5.2, "I am bringing twenty copies of my resume to the booth."),
        (9.6, "Do you know if the recruiters are there all afternoon?")]),
    "Person 2": ("Microsoft David Desktop", -35.0, [
        (2.6, "Yes, it is on Friday in the main hall downstairs."),
        (7.0, "Hey Kori, can you grab me a coffee on the way?")]),
}
BABY = ("baby", 180.0, 2.0, 14.0)          # (who, bearing, start, end): crying behind the wearer


def two_source_scene(kind, leak_db=-9.0, beams=True):
    """A scene the SPEC §3 'not doing' list used to skip: two sources at once.

    kind "baby": Person 1 at +35 talks while a baby cries at 180 for 12 s. kind "talkers": Person 1 at +35
    and Person 2 at -35 with overlapping lines. Beams mode: one WAV channel per focused beam, each carrying
    its own source plus the other at leak_db (a 4-mic beamformer separates roughly 6-10 dB), and sources
    id 0/1 with each beam's direction. beams=False: the processed single stream (the beam on Person 1, so the
    other source is attenuated by leak_db too) with one DoA that follows the stronger source, i.e. today's
    behaviour without beams. Ground truth goes to meta.json `lines` (bearing per line; the baby as a sound).
    """
    from make_mock_scene import tts

    work = WORK / "tts2"
    work.mkdir(parents=True, exist_ok=True)
    seconds = 16.0
    n = int(seconds * SR)
    beam = [np.zeros(n, np.float32), np.zeros(n, np.float32)]
    active = [np.zeros(int(seconds * 20), bool), np.zeros(int(seconds * 20), bool)]
    bearings = [35.0, -35.0 if kind == "talkers" else BABY[1]]
    lines = []
    people = ["Person 1"] + (["Person 2"] if kind == "talkers" else [])
    for k, who in enumerate(people):
        voice, bearing, said = TWO_LINES[who]
        for j, (t0, text) in enumerate(said):
            x = tts(text, voice, work / f"{who[-1]}_{j}.wav").astype(np.float32)
            i0 = int(t0 * SR)
            x = x[: n - i0]
            beam[k][i0:i0 + len(x)] += x
            active[k][int(t0 * 20): int((t0 + len(x) / SR) * 20) + 1] = True
            lines.append({"who": who, "bearing_deg": bearing, "text": text, "t_start": t0,
                          "t_end": round(t0 + len(x) / SR, 2)})
    if kind == "baby":
        cry = read_wav(ROOT / "fixtures" / "sounds" / "baby_cry.wav").astype(np.float32)
        i0, i1 = int(BABY[2] * SR), int(BABY[3] * SR)
        cry = cry[: i1 - i0] * (np.sqrt(np.mean(beam[0][beam[0] != 0] ** 2)) / np.sqrt(np.mean(cry ** 2)))
        beam[1][i0:i0 + len(cry)] += cry
        active[1][int(BABY[2] * 20): int(BABY[3] * 20)] = True
        lines.append({"who": "baby", "bearing_deg": BABY[1], "text": None, "t_start": BABY[2], "t_end": BABY[3]})
    leak = 10 ** (leak_db / 20)
    out0, out1 = beam[0] + leak * beam[1], beam[1] + leak * beam[0]
    rng = np.random.default_rng(5)
    room = rng.standard_normal(n).astype(np.float32) * 40
    name = f"two_{kind}_{'beams' if beams else 'processed'}_{int(-leak_db)}dB"
    folder = WORK / name
    folder.mkdir(parents=True, exist_ok=True)
    with wave.open(str(folder / "audio.wav"), "wb") as w:
        w.setnchannels(2 if beams else 1), w.setsampwidth(2), w.setframerate(SR)
        data = np.stack([out0 + room, out1 + room], axis=1) if beams else (out0 + room)[:, None]
        w.writeframes(np.clip(data, -32768, 32767).astype(np.int16).tobytes())
    with open(folder / "sources.jsonl", "w", encoding="utf-8") as f:
        for i in range(int(seconds * 20)):
            jitter = rng.normal(0, 3, 2)
            src = [{"id": k, "bearing_deg": round(float(((bearings[k] + jitter[k] + 180) % 360) - 180), 1),
                    "energy": 0.6 if active[k][i] else 0.05, "speech": bool(active[k][i]),
                    "confidence": round(float(rng.uniform(0.6, 0.9)), 2) if active[k][i] else 0.0}
                   for k in (0, 1)]
            if not beams:          # one DoA: the talker while they talk, else whatever else is loud
                src = [dict(src[0] if active[0][i] or not active[1][i] else src[1], id=0)]
            f.write(json.dumps({"t": round(i * 0.05, 3), "sources": src}) + "\n")
    (folder / "meta.json").write_text(json.dumps({"name": name, "synthetic": True, "user_name": "Kori",
                                                  "leak_db": leak_db, "lines": lines}, indent=1), encoding="utf-8")
    return folder


def score_two(msgs, t0, meta):
    """Per line: the caption from its talker's direction (WER); phantoms (captions matching no line, e.g. the
    baby, or the other talker's words repeated); the baby alert and its direction."""
    finals = [m for _, m in msgs if m.TYPE == "utterance" and m.final and m.text]
    lines = [l for l in meta["lines"] if l["text"]]
    used, rows = set(), []
    for l in lines:
        win = [m for m in finals if m.t_start - t0 < l["t_end"] + 0.3 and m.t_end - t0 > l["t_start"] - 0.3
               and abs(angdiff(m.bearing_deg, l["bearing_deg"])) <= 25]
        used |= {m.utt_id for m in win}
        text = " ".join(m.text for m in sorted(win, key=lambda m: m.t_start))
        rows.append({"who": l["who"], "wer": wer(l["text"], text), "text": text})
    phantoms = [f"{m.bearing_deg:+.0f}: {m.text}" for m in finals if m.utt_id not in used]
    alerts = [m for _, m in msgs if m.TYPE == "alert" and m.active]
    baby = [a for a in alerts if a.kind == "baby_crying"]
    baby_ok = [a for a in baby if a.bearing_deg is not None and abs(angdiff(a.bearing_deg, BABY[1])) <= 30]
    has_baby = any(l["who"] == "baby" for l in meta["lines"])
    first = min((s for s, m in msgs if m.TYPE == "alert" and m.active and m.kind == "baby_crying"), default=None)
    return {"rows": rows, "wer": float(np.mean([r["wer"] for r in rows])), "phantoms": phantoms,
            "baby_hit": bool(baby_ok) if has_baby else None,
            "baby_bearing": None if not baby else baby[0].bearing_deg,
            "baby_lat": None if first is None or not has_baby else round(first - t0 - BABY[2], 2),
            "baby_wrong_dir": len({a.alert_id for a in baby} - {a.alert_id for a in baby_ok}),
            "name_hit": any(a.kind == "name_called" for a in alerts) if any("Kori" in l["text"] for l in lines) else None,
            # the belt turns the wearer toward this: the caller's direction, not the other talker's (or a raw-mic) one
            "name_bearing_err": max((abs(angdiff(a.bearing_deg, l["bearing_deg"])) if a.bearing_deg is not None else 180.0
                                     for a in alerts if a.kind == "name_called"
                                     for l in lines if "Kori" in l["text"]), default=None)}


def two_source_part(leaks=(-9.0, -6.0), sets=None):
    from laptop.sounds.classifier import Classifier
    from laptop.speech.transcriber import make_transcriber

    cfg = apply_overrides(settings.load(), sets)
    transcriber, _ = make_transcriber(cfg["speech"])
    classifier = Classifier()
    results = {}
    cases = [("baby", None, False)] + [(k, leak, True) for leak in leaks for k in ("baby", "talkers")]
    for kind, leak, beams in cases:
        folder = two_source_scene(kind, leak if leak is not None else -9.0, beams)
        print(f"\n=== {folder.name} ...", flush=True)
        r = asyncio.run(run_condition(folder, cfg, transcriber, classifier,
                                      scorer=lambda msgs, t0, meta: score_two(msgs, t0, meta)))
        results[folder.name] = r
        for row in r["rows"]:
            print(f"   {row['who']:9s} WER {row['wer']:.2f}  {row['text'][:80]!r}")
        if r["phantoms"]:
            print(f"   phantom/duplicate captions: {r['phantoms'][:4]}")
    print("\n| scene | streams | caption WER | phantom / duplicate captions | baby crying alert | name called |")
    print("|---|---|---|---|---|---|")
    for name, r in results.items():
        baby = "-" if r["baby_hit"] is None else (
            (f"first at {r['baby_bearing']:+.0f}° after {r['baby_lat']}s" if r["baby_bearing"] is not None else "MISS")
            + (f", {r['baby_wrong_dir']} alert(s) pointing elsewhere" if r["baby_wrong_dir"] else ""))
        name_c = "-" if r["name_hit"] is None else ("hit" if r["name_hit"] else "MISS")
        print(f"| {name} | {'2 beams' if 'beams' in name else '1 processed'} | {r['wer']:.2f} | "
              f"{len(r['phantoms'])} | {baby} | {name_c} |")
    (WORK / "two_source.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    return results


# ---- golden: pass/fail regression gate ----------------------------------------------------------

# (fixture, babble SNR or None, thresholds). array_tts is the real XVF3800 (tools/speaker_scene.py).
# Limits sit just outside what the branch measured, so a regression fails.
NAME_BEARING_MAX_DEG = 25.0      # two talkers: name_called must point at the caller (SPEC §13 face window ~15)
CLEAN = {"wer": 0.05, "phantoms": 0, "door_hit": True, "name_hit": True, "door_false": 0, "name_false": 0,
         "final_lag": 1.3}
NOISY = {"wer": 0.10, "phantoms": 0, "door_hit": True, "name_hit": True, "door_false": 0, "name_false": 0,
         "final_lag": 1.6}
GOLDEN = [("mock_scene", None, CLEAN), ("mock_scene", 10, NOISY), ("array_tts", None, CLEAN), ("array_tts", 10, NOISY)]
# + babble only, and two sources at once in beams mode (a talker + a baby crying behind; two talkers overlapping)


def check(r, limits):
    """Failed checks of one condition: [(name, measured, limit)]."""
    fails = []
    for key, limit in limits.items():
        if key == "wer":
            got, ok = round(r["wer"], 3), r["wer"] <= limit
        elif key == "phantoms":
            got, ok = len(r["phantoms"]), len(r["phantoms"]) <= limit
        elif key == "final_lag":
            got = round(float(np.median(r["final_lat"])), 2) if r["final_lat"] else None
            ok = got is not None and got <= limit
        elif isinstance(limit, bool):
            got, ok = r[key], r[key] == limit
        else:
            got, ok = r[key], r[key] <= limit
        if not ok:
            fails.append((key, got, limit))
    return fails


def golden(sets=None):
    """python tools/stress_test.py --golden: exit 1 if any golden condition regresses (~4 min on the RTX)."""
    from laptop.sounds.classifier import Classifier
    from laptop.speech.transcriber import make_transcriber

    cfg = apply_overrides(settings.load(), sets)
    transcriber, _ = make_transcriber(cfg["speech"])
    classifier = Classifier()
    rows = []
    for name, snr, limits in GOLDEN:
        cond = f"golden_{name}_{'clean' if snr is None else f'{snr:+d}dB'}"
        folder = make_condition(cond, snr, ROOT / "fixtures" / name)
        print(f"=== {cond} ...", flush=True)
        r = asyncio.run(run_condition(folder, cfg, transcriber, classifier))
        rows.append((cond, f"WER {r['wer']:.2f}, phantoms {len(r['phantoms'])}, door {'hit' if r['door_hit'] else 'MISS'}, "
                           f"name {'hit' if r['name_hit'] else 'MISS'}", check(r, limits)))
    print("=== golden_babble_only ...", flush=True)
    e = asyncio.run(run_condition(make_babble_only(), cfg, transcriber, classifier, score_empty))
    fails = [(k, v, 0) for k, v in (("captions", len(e["captions"])), ("alerts", len(e["alerts"]))) if v]
    rows.append(("golden_babble_only", f"captions {len(e['captions'])}, alerts {len(e['alerts'])}", fails))
    for kind in ("baby", "talkers"):          # two sources at once, beams mode (-9 dB beam leakage)
        folder = two_source_scene(kind, -9.0, True)
        print(f"=== golden_{folder.name} ...", flush=True)
        r = asyncio.run(run_condition(folder, cfg, transcriber, classifier, scorer=score_two))
        fails = [(k, g, l) for k, g, l in (("wer", round(r["wer"], 3), 0.05), ("phantoms", len(r["phantoms"]), 0))
                 if g > l]
        if kind == "baby" and (not r["baby_hit"] or r["baby_wrong_dir"]):
            fails.append(("baby alert", f"{r['baby_bearing']} ({r['baby_wrong_dir']} elsewhere)", "at 180 only"))
        if kind == "talkers" and not r["name_hit"]:
            fails.append(("name_hit", False, True))
        if kind == "talkers" and r["name_bearing_err"] is not None and r["name_bearing_err"] > NAME_BEARING_MAX_DEG:
            fails.append(("name bearing error", round(r["name_bearing_err"], 1), NAME_BEARING_MAX_DEG))
        rows.append((f"golden_{folder.name}", f"WER {r['wer']:.2f}, phantoms {len(r['phantoms'])}"
                     + (f", baby at {r['baby_bearing']}" if kind == "baby" else "")
                     + (f", name bearing err {r['name_bearing_err']:.0f} deg" if kind == "talkers" and
                        r["name_bearing_err"] is not None else ""), fails))
    print("\n| golden condition | measured | result |\n|---|---|---|")
    for cond, summary, fails in rows:
        verdict = "PASS" if not fails else "FAIL: " + "; ".join(f"{k} {g} (limit {l})" for k, g, l in fails)
        print(f"| {cond} | {summary} | {verdict} |")
    bad = sum(1 for *_, f in rows if f)
    print(f"\n{len(rows) - bad}/{len(rows)} golden conditions pass")
    return bad == 0


# ---- part 2: directions -----------------------------------------------------------------------

def doa_part(trials=400, seed=1):
    """Utterance bearing under DoA noise -> does attribution still pick the right person?

    Person 1 at +35, Person 2 at -35 (SPEC §4 positions), faces exactly there. Each trial: a 2.5 s
    utterance; DoA at 20 Hz = truth + gaussian jitter, and with probability `stray` a reading from a
    random direction (babble pulling the array), with lower confidence. Correct = the face within 15 deg
    of the bearing and nearest to it is the true speaker. Also checks the speech_start bearing (first
    0.15 s only), which is what the belt and the typing dots use.
    """
    cfg = settings.load()["speech"]
    rng = np.random.default_rng(seed)
    faces = {"Person 1": 35.0, "Person 2": -35.0}
    print("\n| jitter | stray readings | utterance correct | off-camera (no face in 15°) | wrong person | speech_start correct |")
    print("|---|---|---|---|---|---|")
    for jitter in (3, 8, 15, 25):
        for stray in (0.0, 0.2, 0.4):
            ok = off = wrong = start_ok = 0
            for _ in range(trials):
                who, truth = list(faces.items())[rng.integers(2)]
                h = DirectionHistory()
                for i in range(50):
                    if rng.random() < stray:
                        b, conf = float(rng.uniform(-180, 180)), float(rng.uniform(0.3, 0.6))
                    else:
                        b, conf = truth + float(rng.normal(0, jitter)), float(rng.uniform(0.6, 0.9))
                    b = ((b + 180) % 360) - 180 or 0.0
                    h.add(i * 0.05, [Source(id=0, bearing_deg=b, energy=0.6, speech=True, confidence=conf)])
                est = h.speech_bearing(0, 2.5, cfg["bearing_min_confidence"], cfg["overlap_spread_deg"])
                onset = h.onset(0.15, cfg["onset_lookback_s"])

                def pick(bearing):
                    d = {k: abs(angdiff(v, bearing)) for k, v in faces.items()}
                    best = min(d, key=d.get)
                    return best if d[best] <= 15 else None
                p = pick(est.bearing_deg)
                ok += p == who
                off += p is None
                wrong += p is not None and p != who
                start_ok += pick(onset.bearing_deg) == who
            n = trials
            print(f"| ±{jitter}° | {stray:.0%} | {ok / n:.0%} | {off / n:.0%} | {wrong / n:.0%} | {start_ok / n:.0%} |")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--doa-only", action="store_true")
    p.add_argument("--audio-only", action="store_true")
    p.add_argument("--snr", type=int, nargs="*", help="babble SNRs in dB (default: clean, 10, 5, 0)")
    p.add_argument("--set", action="append", metavar="KEY=VALUE",
                   help="override a laptop/config.yaml value for this run, e.g. speech.array_lookback_s=0")
    p.add_argument("--scene", help="score another fixture with meta.json lines instead of mock_scene "
                                   "(e.g. fixtures/array_tts from tools/speaker_scene.py)")
    p.add_argument("--room", help="noise = a real beams-mode room recording (e.g. fixtures/k1_positions) mixed per "
                                  "beam into a beams-mode --scene, instead of TTS babble")
    p.add_argument("--room-offset", type=float, default=19.0, help="start of the room recording to use (s)")
    p.add_argument("--no-empty", action="store_true", help="skip the babble-only condition")
    p.add_argument("--two-source", action="store_true",
                   help="two sources at once (beams): a talker + a baby crying behind, two talkers overlapping")
    p.add_argument("--golden", action="store_true",
                   help="pass/fail gate: mock + real-array scenes clean and +10 dB, babble only; exit 1 on a regression")
    a = p.parse_args()
    if a.golden:
        sys.exit(0 if golden(a.set) else 1)
    if a.two_source:
        two_source_part(sets=a.set)
        sys.exit(0)
    if not a.audio_only:
        doa_part()
    if not a.doa_only:
        scene = SCENE if not a.scene else (Path(a.scene) if Path(a.scene).exists() else ROOT / "fixtures" / a.scene)
        room = None if not a.room else (Path(a.room) if Path(a.room).exists() else ROOT / "fixtures" / a.room)
        audio_part([None, *(a.snr if a.snr else [10, 5, 0])], scene, a.set, room, a.room_offset, not a.no_empty)

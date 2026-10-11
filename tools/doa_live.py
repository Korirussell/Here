"""Live and caption parts of tools/doa_compare.py (they need models and/or the array).

live_part: the real XVF3800 + the laptop speaker at one spot (world 180 here). Three short runs, the same scene each:
  builtin  processed capture, DOA_VALUE (+ second focused beam as id 1): the device's own directions
  fixed    the current demo config: beams mode, fixed beams [0, 180] (directions = beam bearings; captions)
  hybrid   ODAS tracks on the raw mics (directions = `odas`) steering the fixed beams; captions from the beams
Every run records the raw mics (via the app's raw tap) to find the clips' onsets in the array's own audio, so
latency = (when a source within 25 deg of the speaker first reaches Windows) - (capture time of the onset).

captions_part: rotation scenes replayed through ODAS live (bridge + odaslive) with ODAS's separated (or
post-filtered) audio, one speech + sound pipeline per track slot, scored per line (WER from the right direction),
phantoms and the baby alert.
"""

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
from laptop.app import Laptop  # noqa: E402
from laptop.geometry import angdiff, circular_mean  # noqa: E402

SR = 16000
TRUTH_BEARING = 180.0       # where Kori put the laptop speakers (nominal; SRP/device say ~174-178)


def live_scene():
    import speaker_scene
    scene = speaker_scene.clips(speaker_scene.SCENE[:4] + [speaker_scene.SCENE[4], speaker_scene.SCENE[5]] +
                                speaker_scene.RAW_EXTRA)
    who, text, pause, x = scene[-1]
    scene[-1] = (who, text, pause, x[: 6 * SR])       # 6 s of the baby is enough
    return scene


def align_onsets(raw_log, scene, plays):
    """Capture-time onsets of each clip, found in the raw mics' envelope (playback lag varies 0.6-2.4 s)."""
    import speaker_scene
    t_first = raw_log[0][0]
    x = np.concatenate([b for _, b in raw_log]).astype(np.float32).mean(axis=1)
    env = speaker_scene.envelope(x)
    out = []
    for (who, text, _, clip), played in zip(scene, plays):
        nz = np.nonzero(np.abs(clip) > 300)[0]
        lead = nz[0] / SR if len(nz) else 0.0
        clip = clip[nz[0]: nz[-1] + 1] if len(nz) else clip
        rel = played - t_first + lead
        t0, match = speaker_scene.locate_clip(env, clip, max(0.0, rel - 0.3), rel + 2.6)
        out.append({"who": who, "text": text, "t_start": t_first + t0, "t_end": t_first + t0 + len(clip) / SR,
                    "match": round(match, 2)})
    return out


def save_raw(path, raw_log):
    import wave
    x = np.concatenate([b for _, b in raw_log]).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(x.shape[1])
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(x.tobytes())


def caption_score(msgs, lines, truth_bearing, tol=25.0):
    """Per speech line: finals overlapping it from within tol deg (WER); phantoms; alerts with bearings."""
    from stress_test import wer
    finals = [m for _, m in msgs if m.TYPE == "utterance" and m.final and m.text]
    used, rows = set(), []
    for l in [l for l in lines if l["text"]]:
        win = [m for m in finals if m.t_start < l["t_end"] + 0.3 and m.t_end > l["t_start"] - 0.3
               and abs(angdiff(m.bearing_deg, truth_bearing)) <= tol]
        used |= {m.utt_id for m in win}
        rows.append(wer(l["text"], " ".join(m.text for m in sorted(win, key=lambda m: m.t_start))))
    phantoms = [f"{m.bearing_deg:+.0f}: {m.text}" for m in finals if m.utt_id not in used]
    alerts = [(m.kind, None if m.bearing_deg is None else round(m.bearing_deg)) for _, m in msgs
              if m.TYPE == "alert" and m.active]
    return {"wer": round(float(np.mean(rows)), 2) if rows else None, "phantoms": phantoms, "alerts": alerts}


async def one_run(kind, scene, cfg, transcriber, classifier, device, gain):
    import speaker_scene
    from stress_test import Collector

    from laptop.audio.builtin_doa import ReSpeaker
    from laptop.sounds.classifier import SoundPipeline
    from laptop.speech.pipeline import SpeechPipeline

    if kind == "builtin":
        ears = ReSpeaker(capture="processed", beams_as_sources=True)
        directions = ears
    else:
        ears = ReSpeaker(capture="beams")
        directions = ears
        if kind == "hybrid":
            from laptop.audio.odas import OdasSource
            directions = OdasSource(ears.offset, ears.ccw, ears=ears, audio="beams")
    sink = Collector()
    app = Laptop(cfg, (directions, ears), sink,
                 speech=lambda a, stream=0: SpeechPipeline(cfg["speech"], transcriber, a.stream_history(stream),
                                                           sink.send, on_name=a.alerts.name_called),
                 sounds=lambda a, stream=0: SoundPipeline(classifier, a.stream_history(stream), sink.send, a.alerts,
                                                          locate=ears.locate),
                 names=["Kori"])
    raw_log, arrivals = [], []
    app.raw_listeners.append(lambda t, b: raw_log.append((t, b)))
    orig = directions.on_sources

    def tap(t, sources):
        arrivals.append((time.time(), t, sources))
        orig(t, sources)
    directions.on_sources = tap
    task = asyncio.create_task(app.run())
    if kind == "hybrid":
        try:
            await asyncio.wait_for(directions.bridge.ready.wait(), 40)
        except asyncio.TimeoutError:
            print("[live] ODAS never connected")
    await asyncio.sleep(2.0)
    plays = await asyncio.to_thread(speaker_scene.play_scene, scene, device, gain)
    await asyncio.sleep(2.5)
    extra = {}
    if kind == "hybrid":
        lat = sorted(directions.bridge.latency)
        extra = {"odas_frame_latency_ms": round(1000 * lat[len(lat) // 2], 1) if lat else None,
                 "odas_frames": directions.bridge.frames}
    task.cancel()
    await asyncio.wait([task], timeout=10)
    return raw_log, arrivals, sink.msgs, plays, extra


def live_part(args):
    from doa_compare import score, table

    from laptop.sounds.classifier import Classifier
    from laptop.speech.transcriber import make_transcriber

    cfg = settings.load()
    transcriber, _ = make_transcriber(cfg["speech"])
    classifier = Classifier()
    scene = live_scene()
    rows, notes = [], []
    out_dir = ROOT / "logs" / "doa_live"
    out_dir.mkdir(parents=True, exist_ok=True)
    for kind in (getattr(args, "live_kinds", None) or "builtin,fixed,hybrid").split(","):
        print(f"\n[live] {kind} ...", flush=True)
        raw_log, arrivals, msgs, plays, extra = asyncio.run(
            one_run(kind, scene, cfg, transcriber, classifier, getattr(args, "play_device", None) or "Realtek", 1.5))
        lines = align_onsets(raw_log, scene, plays)
        t_first = raw_log[0][0]
        save_raw(out_dir / f"{kind}_raw.wav", raw_log)
        truth = {"0": {"bearing_deg": TRUTH_BEARING,
                       "active": [[l["t_start"] - t_first, l["t_end"] - t_first] for l in lines]}}
        stream = [(t - t_first, s) for _, t, s in arrivals]
        seconds = (raw_log[-1][0] - t_first)
        label = "odas (hybrid)" if kind == "hybrid" else kind
        r = score(stream, truth, seconds, label)
        # end-to-end: first source within 25 deg ARRIVING on Windows after each onset (capture time)
        e2e = []
        ok = lambda ss: any(abs(angdiff(s.bearing_deg, TRUTH_BEARING)) <= 25 and s.confidence >= 0.2 for s in ss)
        for l in lines:
            if any(l["t_start"] - 0.5 <= t < l["t_start"] - 0.05 and ok(ss) for _, t, ss in arrivals):
                continue                      # a source was already there: not a fresh onset
            first = next((w for w, t, ss in arrivals if w >= l["t_start"] - 0.1 and ok(ss)), None)
            if first is not None and first - l["t_start"] <= 2.5:
                e2e.append(first - l["t_start"])
        caps = caption_score([(w, m) for w, m in msgs], [{**l, "t_start": l["t_start"], "t_end": l["t_end"]}
                                                            for l in lines], TRUTH_BEARING)
        speechy = [s.bearing_deg for _, _, ss in arrivals for s in ss if s.speech and s.confidence >= 0.3]
        med = circular_mean(speechy)[0] if speechy else None
        r.update(e2e_latency_median=round(float(np.median(e2e)), 2) if e2e else None, e2e_found=f"{len(e2e)}/{len(lines)}",
                 measured_bearing=None if med is None else round(med, 1), **extra, captions=caps)
        rows.append(("laptop speaker 180 (live)", r))
        print(f"[live] {kind}: {json.dumps({k: v for k, v in r.items() if k != 'ghosts'}, default=str)}", flush=True)
        (out_dir / f"{kind}.json").write_text(json.dumps({"lines": lines, "result": r}, indent=1, default=str),
                                              encoding="utf-8")
        frame_lat = r.get("odas_frame_latency_ms")
        notes.append(f"- {label}: measured bearing {r['measured_bearing']}, end-to-end onset -> first correct source "
                     f"on Windows {r['e2e_latency_median']} s ({r['e2e_found']} onsets)"
                     f"{'' if frame_lat is None else f', ODAS frame latency {frame_lat} ms'}"
                     f"; captions WER {caps['wer']}, phantoms {len(caps['phantoms'])}, alerts {caps['alerts']}")
        time.sleep(1.0)
    return table(rows, "Live: real array, laptop speaker at world 180 (one source)") + "\n\n" + "\n".join(notes)


async def caption_run(folder, cfg, transcriber, classifier, audio_mode):
    from stress_test import Collector

    from laptop.audio.mock import FixturePlayer
    from laptop.audio.odas import OdasSource
    from laptop.sounds.classifier import SoundPipeline
    from laptop.speech.pipeline import SpeechPipeline

    player = FixturePlayer(folder, chunk=cfg["audio"]["chunk_samples"])
    # mix: ODAS directions, the unseparated mono mix of the mics (audio.wav) as the only stream
    src = OdasSource(player.offset, player.ccw, ears=player, audio="beams" if audio_mode == "mix" else audio_mode)
    sink = Collector()
    app = Laptop(cfg, (src, player if audio_mode == "mix" else src), sink,
                 speech=lambda a, stream=0: SpeechPipeline(cfg["speech"], transcriber, a.stream_history(stream),
                                                           sink.send, on_name=a.alerts.name_called),
                 sounds=lambda a, stream=0: SoundPipeline(classifier, a.stream_history(stream), sink.send, a.alerts,
                                                          locate=player.locate),
                 names=["Kori"])
    task = asyncio.create_task(app.run())
    await asyncio.wait_for(src.bridge.ready.wait(), 60)
    while player.t0 is None:
        await asyncio.sleep(0.05)
    await asyncio.sleep(player.duration + 4.0)
    task.cancel()
    await asyncio.wait([task], timeout=10)
    return sink.msgs, player.t0, player.meta


def captions_part(args, overrides=None):
    """Rotation scenes with talkers: ODAS separated / post-filtered audio -> captions per track."""
    from stress_test import wer

    from laptop.sounds.classifier import Classifier
    from laptop.speech.transcriber import make_transcriber

    cfg = settings.load()
    transcriber, _ = make_transcriber(cfg["speech"])
    classifier = Classifier()
    out = ["### Captions on rotation scenes (ODAS audio per track; whisper + PANNs as live)", "",
           "| scene | audio | WER (lines from the right direction) | phantom captions | baby alert (bearing) | other alerts |",
           "|---|---|---|---|---|---|"]
    for name in ("talker_baby", "two_talkers_90", "two_talkers_180"):
        folder = ROOT / "fixtures" / f"rot_{name}"
        modes = getattr(args, "caption_modes", None)
        for mode in (modes.split(",") if modes else ("mix", "separated", "postfiltered")):
            print(f"[captions] {name} / {mode} ...", flush=True)
            msgs, t0, meta = asyncio.run(caption_run(folder, cfg, transcriber, classifier, mode))
            finals = [m for _, m in msgs if m.TYPE == "utterance" and m.final and m.text]
            used, wers = set(), []
            for l in [l for l in meta["lines"] if l["text"]]:
                win = [m for m in finals if m.t_start - t0 < l["t_end"] + 0.3 and m.t_end - t0 > l["t_start"] - 0.3
                       and abs(angdiff(m.bearing_deg, l["bearing_deg"])) <= 25]
                used |= {m.utt_id for m in win}
                wers.append(wer(l["text"], " ".join(m.text for m in sorted(win, key=lambda m: m.t_start))))
            phantoms = [f"{m.bearing_deg:+.0f}: {m.text}" for m in finals if m.utt_id not in used]
            alerts = [(m.kind, None if m.bearing_deg is None else round(m.bearing_deg)) for _, m in msgs
                      if m.TYPE == "alert" and m.active]
            baby = [b for k, b in alerts if k == "baby_crying"]
            other = [a for a in alerts if a[0] != "baby_crying"]
            w = round(float(np.mean(wers)), 2) if wers else None
            print(f"[captions] {name} {mode}: WER {w}, phantoms {phantoms[:4]}, alerts {alerts}", flush=True)
            out.append(f"| {name} | {mode} | {w} | {len(phantoms)} | {baby or '-'} | {other or '-'} |")
    return "\n".join(out)

"""Sound classifier bakeoff on our sounds (A2): per-class accuracy, latency, warmup, memory, calibration, licence.

    python tools/sound_bench.py                              # every candidate in laptop/config.yaml sounds.models
    python tools/sound_bench.py --models panns_cnn14 mispeech/ced-base --device cuda   # K3: GPU latency, finalists
    python tools/sound_bench.py --no-babble                  # skip the +5 dB crowd-babble condition

Clips (cached in logs/sound_bench/clips, downloaded once):
- ours: fixtures/sounds/*, cuts from fixtures/mock_scene, array_tts, raw_speaker_180 (real XVF3800), rot_single_0
  and rot_talker_baby (baby under a talker);
- Wikimedia Commons, public domain / CC0 / CC BY (doorbells, door slams, knocks, car horns, sirens, smoke alarm);
- ESC-50 (CC BY-NC 3.0, benchmark use only, never committed): 6 clips per class for horns, sirens, knocks, door
  creaks, babies, alarms, dogs, laughter, engines, fireworks, plus ambience (vacuum, washing machine, rain,
  typing, wind, clock tick) and weird sounds (can opening, pouring water, rooster, frog, glass breaking).

Every model runs in its own subprocess (clean memory numbers) on 1 s windows, hop 0.5 s, keeping the windows within
12 dB of the clip's loudest one (what an onset would trigger). A clip's score per label = its best window.

Metrics (speech clips count as negatives; speech / music / tv never alert, so they don't compete for top-1):
- top-1: the clip's best non-background SoundLabel is the expected one (or a near-equivalent: knock ~ door);
- detect@2%FPR: each label gets the lowest threshold that fires on <= 2% of the clips (at least 0.10) that aren't that label, and the
  clip's strongest label above threshold is right. Those thresholds are the starting values for config.yaml;
- +babble: the same detection on the positives with TTS crowd babble at +5 dB, thresholds from the clean clips;
- false alerts: negative clips (ambient, weird, speech, babble) where an alerting label crosses its threshold;
- AUC: per label, its score ranks its own clips above the rest (macro mean); ECE: the top-1 score as P(correct);
- latency per 1 s window (mean / p95), load + warmup, process memory (RSS) after loading.
"""

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

SR = 16000
WORK = ROOT / "logs" / "sound_bench"
CLIPS = WORK / "clips"
MAX_S = 6.0
UA = {"User-Agent": "HERE-sound-bench/1.0 (hackathon research; https://github.com/)"}

LICENCES = {
    "panns_cnn14": "MIT code; CNN14 weights CC BY 4.0 (Zenodo)",
    "MIT/ast-finetuned-audioset-10-10-0.4593": "BSD-3-Clause",
    "mispeech/ced-base": "Apache-2.0 (custom code, pinned revision)",
    "mispeech/ced-mini": "Apache-2.0 (custom code, pinned revision)",
    "laion/larger_clap_general": "Apache-2.0",
}

# expected label -> accepted predictions (near-equivalents a wearer would still read correctly)
ACCEPT = {
    "knock": {"knock", "door"},
    "door": {"door", "knock", "loud_bang"},
    "loud_bang": {"loud_bang", "door"},
    "alarm": {"alarm", "phone_ringing"},
}
NEGATIVE = {"ambient", "weird"}           # nothing alerting should fire; speech clips are negatives for alerts too

COMMONS = [  # (expected, file) public domain / CC0 / CC BY on Wikimedia Commons
    ("doorbell", "Doorbell-old-tring.ogg"), ("doorbell", "Doorbell-classic-dingdong.ogg"),
    ("doorbell", "Doorbell-cheap-dingdong.ogg"), ("doorbell", "Electronic doorbell.ogg"),
    ("doorbell", "Doorbell.wav"), ("doorbell", "Mechanical doorbel.ogg"),
    ("door", "Slam closet door (Gravity Sound).wav"), ("door", "Closing an old door.ogg"),
    ("door", "Close door (Gravity Sound).wav"), ("door", "Close door 2 (Gravity Sound).wav"),
    ("car_horn", "Car Horn.wav"),
    ("siren", "Civil-defense-siren-waver.ogg"), ("siren", "Siren.ogg"), ("siren", "American police siren i.ogg"),
    ("siren", "Motorsirene - Feuerwehralarm.ogg"),
    ("knock", "Knock on door.wav"), ("knock", "Knocking on wood or door.ogg"), ("knock", "Door knocker audio.ogg"),
    ("alarm", "Smoke alarm.ogg"),
    ("weird", "Toy siren alarm.ogg"), ("weird", "Squeaky door.ogg"),
]
ESC50 = {  # ESC-50 category -> expected
    "car_horn": "car_horn", "siren": "siren", "door_wood_knock": "knock", "door_wood_creaks": "weird",
    "crying_baby": "baby_crying", "clock_alarm": "alarm", "dog": "dog_barking", "laughing": "laughter",
    "engine": "vehicle", "fireworks": "loud_bang",
    "vacuum_cleaner": "ambient", "washing_machine": "ambient", "rain": "ambient", "keyboard_typing": "ambient",
    "wind": "ambient", "clock_tick": "ambient",
    "can_opening": "weird", "pouring_water": "weird", "rooster": "weird", "frog": "weird", "glass_breaking": "weird",
}
ESC50_PER_CLASS = 6
ESC50_RAW = "https://raw.githubusercontent.com/karolpiczak/ESC-50/master"
# (expected, fixture, start s, end s, channel)  real-array cuts are the ones that matter most
FIXTURE_CUTS = [
    ("doorbell", "sounds/doorbell.wav", 0, None, 0), ("doorbell", "sounds/doorbell_array.wav", 0, None, 0),
    ("baby_crying", "sounds/baby_cry.wav", 0, 6, 0), ("baby_crying", "sounds/baby_cry.wav", 6, 12, 0),
    ("doorbell", "mock_scene/audio.wav", 17.98, 22.08, 0), ("speech", "mock_scene/audio.wav", 1.0, 3.9, 0),
    ("doorbell", "array_tts/audio.wav", 22.06, 26.16, 0), ("speech", "array_tts/audio.wav", 4.92, 7.78, 0),
    ("speech", "array_tts/audio.wav", 30.8, 33.1, 0),
    ("doorbell", "raw_speaker_180/audio.wav", 22.13, 26.23, 1), ("baby_crying", "raw_speaker_180/audio.wav", 38, 44, 1),
    ("baby_crying", "raw_speaker_180/audio.wav", 44, 49.6, 1), ("speech", "raw_speaker_180/audio.wav", 9.0, 12.18, 1),
    ("doorbell", "rot_single_0/audio.wav", 18.26, 22.36, 0), ("baby_crying", "rot_single_0/audio.wav", 33.15, 37.85, 0),
    ("speech", "rot_single_0/audio.wav", 14.37, 16.96, 0),
    ("baby_crying", "rot_talker_baby/audio.wav", 12.67, 14.9, 0),   # baby alone
    ("baby_crying", "rot_talker_baby/audio.wav", 5.11, 8.29, 0),    # baby under a talker
]


# ---- clips ------------------------------------------------------------------------------------

def load_audio(path, start=0.0, end=None, channel=0):
    import soundfile as sf
    from scipy.signal import resample_poly
    x, sr = sf.read(str(path), dtype="float32", always_2d=True)
    x = x[:, min(channel, x.shape[1] - 1)] if channel or x.shape[1] == 1 else x.mean(axis=1)
    x = x[int(start * sr): None if end is None else int(end * sr)]
    if sr != SR:
        x = resample_poly(x, SR, sr).astype(np.float32)
    return x


def trim(x):
    """From just before the first loud part, at most MAX_S."""
    if len(x) < SR // 2:
        return x
    frames = np.sqrt(np.mean(x[: len(x) // 160 * 160].reshape(-1, 160) ** 2, axis=1) + 1e-12)
    start = max(0, int(np.argmax(frames > frames.max() * 0.1)) * 160 - SR // 10)
    return x[start: start + int(MAX_S * SR)]


def fetch(url, dest):
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers=UA)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            dest.write_bytes(data)
            return dest
        except Exception as e:  # noqa: BLE001  (rate limits: back off)
            print(f"  retry {url}: {e}", flush=True)
            time.sleep(3 * (attempt + 1))
    return None


def build_clips():
    """[{id, expected, source, audio float32 16 kHz}] (downloads cached)."""
    clips = []
    for expected, rel, s, e, ch in FIXTURE_CUTS:
        p = ROOT / "fixtures" / rel
        if not p.exists():
            print(f"  (missing {p}: skipped)")
            continue
        clips.append({"id": f"fx:{rel}@{s}", "expected": expected, "source": "ours",
                      "audio": load_audio(p, s, e, ch)[: int(MAX_S * SR)]})
    for expected, name in COMMONS:
        url = "https://commons.wikimedia.org/wiki/Special:FilePath/" + urllib.parse.quote(name.replace(" ", "_"))
        p = fetch(url, CLIPS / "commons" / name)
        if p is not None:
            try:
                audio = load_audio(p)
            except Exception:  # noqa: BLE001  (an Ogg codec libsndfile lacks: use Commons' MP3 transcode)
                fn = name.replace(" ", "_")
                h = hashlib.md5(fn.encode("utf-8")).hexdigest()
                q = urllib.parse.quote(fn)
                mp3 = fetch(f"https://upload.wikimedia.org/wikipedia/commons/transcoded/{h[0]}/{h[:2]}/{q}/{q}.mp3",
                            CLIPS / "commons" / (name + ".mp3"))
                try:
                    audio = load_audio(mp3)
                except Exception as e:  # noqa: BLE001
                    print(f"  (unreadable {name}: {e})")
                    continue
            clips.append({"id": f"commons:{name}", "expected": expected, "source": "commons", "audio": trim(audio)})
    meta = fetch(f"{ESC50_RAW}/meta/esc50.csv", CLIPS / "esc50" / "esc50.csv")
    if meta is not None:
        rows = list(csv.DictReader(open(meta, encoding="utf-8")))
        for cat, expected in ESC50.items():
            seen, picked = set(), []
            for r in sorted((r for r in rows if r["category"] == cat), key=lambda r: (r["fold"], r["filename"])):
                if r["src_file"] not in seen:          # one take per original recording
                    seen.add(r["src_file"])
                    picked.append(r["filename"])
            for name in picked[:ESC50_PER_CLASS]:
                p = fetch(f"{ESC50_RAW}/audio/{name}", CLIPS / "esc50" / name)
                if p is not None:
                    clips.append({"id": f"esc50:{cat}/{name}", "expected": expected, "source": "esc50",
                                  "audio": trim(load_audio(p))})
    try:
        from stress_test import babble
        b = babble(30.0)
        b = b / (np.max(np.abs(b)) + 1e-9) * 0.5
        for k in range(3):
            clips.append({"id": f"babble:{k}", "expected": "ambient", "source": "ours",
                          "audio": b[int(k * 8 * SR): int((k * 8 + MAX_S) * SR)].astype(np.float32)})
    except Exception as e:  # noqa: BLE001  (needs Windows TTS the first time)
        print(f"  (no babble clips: {e})")
    return clips


def mix_babble(clips, snr_db=5.0):
    """The positives again with crowd babble at snr_db (signal power over its active part)."""
    try:
        from stress_test import babble
        noise = babble(120.0).astype(np.float32)
    except Exception as e:  # noqa: BLE001
        print(f"  (babble condition skipped: {e})")
        return []
    out, pos = [], 0
    for c in clips:
        if c["expected"] in NEGATIVE or c["expected"] == "speech" or c["id"].startswith("babble"):
            continue
        x = c["audio"]
        n = noise[pos: pos + len(x)]
        pos = (pos + len(x)) % (len(noise) - int(MAX_S * SR) - 1)
        power = np.mean(np.stack(windows(x)) ** 2)          # over the windows the classifier sees
        gain = np.sqrt(power / (np.mean(n ** 2) * 10 ** (snr_db / 10) + 1e-12))
        out.append({**c, "id": c["id"] + "+babble", "audio": (x + n * gain).astype(np.float32)})
    return out


# ---- one model (subprocess) --------------------------------------------------------------------

def windows(x, win=SR, hop=SR // 2):
    if len(x) < win:
        x = np.pad(x, (0, win - len(x)))
    starts = range(0, len(x) - win + 1, hop)
    ws = [x[s: s + win] for s in starts]
    rms = np.array([np.sqrt(np.mean(w ** 2) + 1e-12) for w in ws])
    return [w for w, r in zip(ws, rms) if r >= rms.max() * 0.25]      # within 12 dB of the loudest


def run_one(model, device, clips_path, out_path):
    import psutil
    from laptop.sounds.models import load_backend
    proc = psutil.Process()
    rss0 = proc.memory_info().rss
    t0 = time.perf_counter()
    backend = load_backend(model, device=device)
    load_total = time.perf_counter() - t0
    rss1 = proc.memory_info().rss
    data = np.load(clips_path, allow_pickle=True)
    results, lat = [], []
    for c in data["clips"]:
        best = {}
        for w in windows(c["audio"]):
            pcm = (np.clip(w, -1, 1) * 32767).astype(np.int16)
            t = time.perf_counter()
            scores, _ = backend(pcm)
            lat.append(time.perf_counter() - t)
            for k, (s, raw) in scores.items():
                if s > best.get(k, (-1, ""))[0]:
                    best[k] = (s, raw)
        results.append({"id": c["id"], "expected": c["expected"], "source": c["source"],
                        "scores": {k: float(v[0]) for k, v in best.items()},
                        "raw": {k: v[1] for k, v in best.items()}})
    gpu_mb = None
    try:
        import torch
        if torch.cuda.is_available() and backend.device != "cpu":
            gpu_mb = torch.cuda.max_memory_allocated() / 2 ** 20
    except Exception:  # noqa: BLE001
        pass
    json.dump({"model": model, "device": backend.device, "load_s": load_total,
               "warm_s": getattr(backend, "warm_s", 0.0), "rss_mb": (rss1 - rss0) / 2 ** 20, "gpu_mb": gpu_mb,
               "lat_ms": [x * 1000 for x in lat], "results": results}, open(out_path, "w"), indent=0)


# ---- scoring --------------------------------------------------------------------------------------

BACKGROUND_LABELS = {"speech", "music", "tv"}      # never alert; a chime that is also "music" is fine
FPR = 0.02                                          # per-label threshold: fires on <= 2% of clips not of that label
MIN_T = 0.10


def is_ok(expected, pred):
    return pred in ACCEPT.get(expected, {expected})


def event_top1(r):
    """The best non-background SoundLabel of a clip: what the card would say."""
    s = {k: v for k, v in r["scores"].items() if k not in BACKGROUND_LABELS}
    return max(s.items(), key=lambda kv: kv[1]) if s else ("none", 0.0)


def label_thresholds(rs, labels):
    """Per label: the lowest threshold firing on <= FPR of the clips that don't accept that label (min MIN_T)."""
    out = {}
    for lab in labels:
        others = sorted((r["scores"].get(lab, 0.0) for r in rs if lab not in ACCEPT.get(r["expected"],
                                                                                         {r["expected"]})),
                        reverse=True)
        k = int(FPR * len(others))
        out[lab] = max(MIN_T, (others[k] + 1e-3) if k < len(others) else MIN_T)
    return out


def detected(r, thr):
    """The labels above their thresholds, best (score / threshold) first."""
    hits = [(s / thr[k], k) for k, s in r["scores"].items() if k in thr and s >= thr[k]]
    return [k for _, k in sorted(hits, reverse=True)]


def auc(pos, neg):
    if not pos or not neg:
        return None
    pos, neg = np.array(pos), np.array(neg)
    return float(((pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum())
                 / (len(pos) * len(neg)))


def ece(rs, bins=5):
    """Calibration of the event top-1 score as P(correct), on the positive clips."""
    if not rs:
        return None
    conf = np.clip(np.array([event_top1(r)[1] for r in rs]), 0, 1)
    ok = np.array([is_ok(r["expected"], event_top1(r)[0]) for r in rs], float)
    edges = np.linspace(0, 1, bins + 1)
    e = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (conf >= lo) & ((conf < hi) if hi < 1 else (conf <= hi))
        if m.any():
            e += m.mean() * abs(conf[m].mean() - ok[m].mean())
    return float(e)


def summarize(run):
    from laptop.sounds.labels import ALERTING_LABELS
    rs = [r for r in run["results"] if not r["id"].endswith("+babble")]
    rb = [r for r in run["results"] if r["id"].endswith("+babble")]
    pos = [r for r in rs if r["expected"] not in NEGATIVE and r["expected"] != "speech"]
    classes = sorted({r["expected"] for r in pos})
    thr = label_thresholds(rs, sorted({k for r in rs for k in r["scores"]} - BACKGROUND_LABELS))
    per = {}
    for c in classes:
        cr = [r for r in pos if r["expected"] == c]
        crb = [r for r in rb if r["expected"] == c]
        det = lambda r, c=c: bool(set(detected(r, thr)[:1]) & ACCEPT.get(c, {c}))  # noqa: E731
        per[c] = {"n": len(cr), "top1": sum(is_ok(c, event_top1(r)[0]) for r in cr),
                  "detect": sum(det(r) for r in cr), "nb": len(crb), "detect_babble": sum(det(r) for r in crb),
                  "median_true": float(np.median([r["scores"].get(c, 0.0) for r in cr])),
                  "threshold": thr.get(c), "auc": auc([r["scores"].get(c, 0.0) for r in cr],
                                                      [r["scores"].get(c, 0.0) for r in rs
                                                       if c not in ACCEPT.get(r["expected"], {r["expected"]})])}
    negs = [r for r in rs if r["expected"] in NEGATIVE or r["expected"] == "speech"]
    fa = [(r["id"], [k for k in detected(r, thr) if k in ALERTING_LABELS]) for r in negs]
    fa = [(i, ks) for i, ks in fa if ks]
    lat = np.array(run["lat_ms"]) if run["lat_ms"] else np.zeros(1)
    aucs = [v["auc"] for v in per.values() if v["auc"] is not None]
    n = max(1, len(pos))
    return {"thresholds": thr, "per": per, "false_alerts": fa, "n_neg": len(negs),
            "top1": sum(v["top1"] for v in per.values()) / n, "detect": sum(v["detect"] for v in per.values()) / n,
            "detect_babble": (sum(v["detect_babble"] for v in per.values()) / len(rb)) if rb else None,
            "auc": float(np.mean(aucs)) if aucs else None, "ece": ece(pos),
            "lat_mean": float(lat.mean()), "lat_p95": float(np.percentile(lat, 95))}


def report(runs):
    print("\n## Overall\n")
    print("| model | device | top-1 | detect@2%FPR | +babble 5 dB | false alerts (negatives) | macro AUC | ECE "
          "| ms/window (mean, p95) | load+warm s | RSS MB | GPU MB | licence |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    sums = {}
    for run in runs:
        s = sums[run["model"]] = summarize(run)
        rb = "-" if s["detect_babble"] is None else f"{s['detect_babble']:.0%}"
        gpu = "-" if run["gpu_mb"] is None else f"{run['gpu_mb']:.0f}"
        print(f"| {run['model']} | {run['device']} | {s['top1']:.0%} | {s['detect']:.0%} | {rb} "
              f"| {len(s['false_alerts'])}/{s['n_neg']} | {s['auc']:.3f} | {s['ece']:.2f} "
              f"| {s['lat_mean']:.0f}, {s['lat_p95']:.0f} | {run['load_s']:.1f} | {run['rss_mb']:.0f} | {gpu} "
              f"| {LICENCES.get(run['model'], '?')} |")
    classes = sorted({c for s in sums.values() for c in s["per"]})
    print("\n## Per class: top-1 / detected (+babble detected), median true-label score, threshold\n")
    print("| class | n | " + " | ".join(r["model"] for r in runs) + " |")
    print("|---|---|" + "---|" * len(runs))
    for c in classes:
        n = next((s["per"][c]["n"] for s in sums.values() if c in s["per"]), 0)
        cells = []
        for run in runs:
            v = sums[run["model"]]["per"].get(c)
            if v is None:
                cells.append("-")
                continue
            b = f" ({v['detect_babble']}/{v['nb']})" if v["nb"] else ""
            cells.append(f"{v['top1']}/{v['detect']}{b}, {v['median_true']:.2f}, t {v['threshold']:.2f}")
        print(f"| {c} | {n} | " + " | ".join(cells) + " |")
    print("\n## False alerts on negatives (ambient, weird, speech, babble) at those thresholds\n")
    for run in runs:
        s = sums[run["model"]]
        print(f"- {run['model']}: " + (", ".join(f"{i} -> {'/'.join(k)}" for i, k in s["false_alerts"]) or "none"))
    return sums


def main():
    p = argparse.ArgumentParser(prog="python tools/sound_bench.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--models", nargs="*", help="model ids (default: every laptop/config.yaml sounds.models entry)")
    p.add_argument("--device", default="cpu", help="cpu or cuda")
    p.add_argument("--no-babble", action="store_true")
    p.add_argument("--one", nargs=3, metavar=("MODEL", "CLIPS", "OUT"), help=argparse.SUPPRESS)
    p.add_argument("--report", action="store_true", help="re-print the tables from the last run's JSON")
    a = p.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if a.one:
        run_one(a.one[0], a.device, a.one[1], a.one[2])
        return
    from laptop.sounds import config as C
    models = a.models or list(C.MODELS)
    WORK.mkdir(parents=True, exist_ok=True)
    outs = {m: WORK / f"{m.replace('/', '__')}.{a.device}.json" for m in models}
    if not a.report:
        print("building clips ...", flush=True)
        clips = build_clips()
        clips += [] if a.no_babble else mix_babble(clips)
        from collections import Counter
        print(f"{len(clips)} clips: " + ", ".join(f"{k} {v}" for k, v in sorted(
            Counter(c["expected"] for c in clips).items())), flush=True)
        clips_path = WORK / "clips.npz"
        np.savez(clips_path, clips=np.array(clips, dtype=object))
        for m in models:
            print(f"=== {m} on {a.device} ...", flush=True)
            env = {**os.environ, "HF_HUB_DISABLE_SYMLINKS_WARNING": "1"}
            r = subprocess.run([sys.executable, __file__, "--device", a.device, "--one", m, str(clips_path),
                                str(outs[m])], env=env)
            if r.returncode:
                print(f"    {m} failed (exit {r.returncode})")
    runs = [json.load(open(o)) for m, o in outs.items() if o.exists()]
    sums = report(runs)
    (WORK / f"summary.{a.device}.json").write_text(json.dumps(sums, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()

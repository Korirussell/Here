"""Built-in DoA vs ODAS vs hybrid on the same audio (laptop/audio/DOA_VS_ODAS.md, README §11).

One scorer for any `sources` stream against ground truth: localization error, detection, two-source detection,
track id stability, ghost sources per minute, onset latency.

    python tools/doa_compare.py --scenes                 # rotation scenes (fixtures/rot_*): srp + odas (offline)
    python tools/doa_compare.py --scenes --set probMin=0.3   # ODAS config override (audio.odas keys)
    python tools/doa_compare.py --live                   # real array + laptop speaker: builtin, odas, hybrid
    python tools/doa_compare.py --replay two_real2       # a recorded beams fixture's own sources (builtin baseline)
    python tools/doa_compare.py --captions               # captions/alerts on rotation scenes: ODAS separated audio

Methods: `builtin` = the XVF3800's DOA_VALUE (+ second focused beam as id 1), `srp` = our SRP-PHAT top-2 peaks +
SrpTracker on the raw mics (Track A's planned tracker), `odas` = ODAS tracks, `hybrid` = ODAS tracks steering the
fixed beams (directions identical to odas; differs in audio/captions). Rotation scenes are raw-mic only, so builtin
and hybrid run live (laptop speaker at one spot) and on two_real2 (beams fixture) instead.
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
from contracts.python.here_contract import Source  # noqa: E402
from laptop import settings  # noqa: E402
from laptop.audio import config as C  # noqa: E402
from laptop.audio.localize import SrpSources, XVF3800_GEOMETRY, aec_calibration, aec_to_device  # noqa: E402
from laptop.audio.mock import read_sources, read_streams  # noqa: E402
from laptop.audio.xvf3800 import to_world  # noqa: E402
from laptop.geometry import angdiff  # noqa: E402

SR = 16000
STEP = 0.1                 # scoring grid (s)
MATCH_DEG = 25.0           # a report within this of a truth source detects it
GHOST_DEG = 35.0           # a confident report farther than this from every truth source is a ghost
GHOST_CONF = 0.3           # (stale = a confident report at a truth bearing while that source is silent)
TAIL_S = 0.6               # reports this long after a source stops still count as that source (reverb, holds)
SCENES = ["single_0", "single_90", "single_180", "single_m90", "talker_baby", "two_talkers_90", "two_talkers_180"]


# ---- scoring ----------------------------------------------------------------------------------------------

def at(stream, t, hold=0.15):
    """Reports in effect at t: the newest frame within `hold` s before t (streams arrive at 10-125 Hz)."""
    best = None
    for ts, ss in stream:
        if ts > t + 1e-6:
            break
        if t - ts <= hold:
            best = ss
    return best or []


def active(truth, sid, t, tail=0.0):
    return any(t0 <= t <= t1 + tail for t0, t1 in truth[sid]["active"])


def score(stream, truth, seconds, label=""):
    """stream: [(t seconds into the scene, [Source])] sorted; truth: {sid: {bearing_deg, active: [[t0, t1]]}}."""
    stream = sorted(stream, key=lambda x: x[0])
    sids = list(truth)
    det = {s: [0, 0] for s in sids}
    errs = []
    both = [0, 0]
    ids = {s: [] for s in sids}
    ghosts, stale = [], []
    grid = np.arange(0.0, seconds, STEP)
    idx = 0
    current = []
    for t in grid:
        while idx < len(stream) and stream[idx][0] <= t + 1e-6:
            current = stream[idx][1] if t - stream[idx][0] <= 0.15 else current
            idx += 1
        if idx and t - stream[idx - 1][0] > 0.15:
            current = []
        hit_now = []
        for s in sids:
            if not active(truth, s, t):
                continue
            det[s][1] += 1
            near = [(abs(angdiff(r.bearing_deg, truth[s]["bearing_deg"])), r) for r in current]
            near = [x for x in near if x[0] <= MATCH_DEG]
            if near:
                d, r = min(near, key=lambda x: x[0])
                det[s][0] += 1
                errs.append(d)
                ids[s].append((t, r.id))
                hit_now.append(s)
        live = [s for s in sids if active(truth, s, t)]
        if len(live) >= 2:
            both[1] += 1
            both[0] += len(hit_now) >= 2
        for r in current:
            if r.confidence < GHOST_CONF:
                continue
            near = [s for s in sids if abs(angdiff(r.bearing_deg, truth[s]["bearing_deg"])) <= GHOST_DEG]
            if not near:
                ghosts.append((round(float(t), 1), round(r.bearing_deg)))      # a direction nobody is at
            elif not any(active(truth, s, t, TAIL_S) for s in near):
                stale.append((round(float(t), 1), round(r.bearing_deg)))       # right place, nobody talking

    def episodes(items):
        """Consecutive samples within 0.5 s and 30 deg are one episode."""
        out = []
        for t, b in items:
            if out and t - out[-1][0] <= 0.5 and abs(angdiff(b, out[-1][1])) <= 30:
                out[-1] = (t, b)
            else:
                out.append((t, b))
        return out
    ghost_ep, stale_ep = episodes(ghosts), episodes(stale)
    switches, nids = 0, set()
    for s in sids:
        seq = ids[s]
        nids |= {i for _, i in seq}
        for (t0, a), (t1, b) in zip(seq, seq[1:]):
            if a != b and t1 - t0 <= 0.35:          # changed id while continuously tracked
                switches += 1
    lat = []
    for s in sids:
        for t0, t1 in truth[s]["active"]:
            # only fresh onsets: nothing reported at this bearing in the 0.5 s before (a lingering track isn't a detection)
            if any(t0 - 0.5 <= ts < t0 - 0.05 and any(abs(angdiff(r.bearing_deg, truth[s]["bearing_deg"])) <= MATCH_DEG
                                                      for r in ss) for ts, ss in stream):
                continue
            first = next((ts for ts, ss in stream if t0 - 0.2 <= ts <= min(t1, t0 + 2.0)
                          and any(abs(angdiff(r.bearing_deg, truth[s]["bearing_deg"])) <= MATCH_DEG for r in ss)), None)
            lat.append(None if first is None else max(0.0, first - t0))
    found = [x for x in lat if x is not None]
    return {"label": label, "detect": {s: round(det[s][0] / max(det[s][1], 1), 2) for s in sids},
            "median_err": round(float(np.median(errs)), 1) if errs else None,
            "two_source": round(both[0] / both[1], 2) if both[1] else None,
            "ghosts_per_min": round(len(ghost_ep) / (seconds / 60.0), 1), "ghosts": ghost_ep[:8],
            "stale_s_per_min": round(len(stale) * STEP / (seconds / 60.0), 1),
            "id_switches_per_min": round(switches / (seconds / 60.0), 1), "ids": len(nids),
            "latency_median": round(float(np.median(found)), 2) if found else None,
            "onsets_found": f"{len(found)}/{len(lat)}"}


def table(rows, title):
    out = [f"### {title}", "",
           "| scene | method | detected (per source) | median error | two-source | ghosts/min | stale s/min | id switches/min | ids | onset latency (median, found) |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for scene, r in rows:
        det = " / ".join(f"{v:.0%}" for v in r["detect"].values())
        two = "-" if r["two_source"] is None else f"{r['two_source']:.0%}"
        out.append(f"| {scene} | {r['label']} | {det} | {r['median_err']} | {two} | "
                   f"{r['ghosts_per_min']} | {r['stale_s_per_min']} | {r['id_switches_per_min']} | {r['ids']} | "
                   f"{r['latency_median']} s ({r['onsets_found']}) |")
    return "\n".join(out)


# ---- methods on raw audio -----------------------------------------------------------------------------------

def srp_stream(raw_usb, meta, order=None):
    """Our SRP-PHAT on the raw mics (SrpSources: noise gate, top-2 peaks, SrpTracker) -> sources (world)."""
    geometry = meta.get("mic_geometry") or C.MIC_GEOMETRY or XVF3800_GEOMETRY
    offset = float(meta.get("array_offset_deg", C.ARRAY_OFFSET_DEG))
    ccw = not bool(meta.get("array_clockwise", C.ARRAY_CLOCKWISE))
    live = SrpSources(geometry, SR, order=order if order is not None else meta.get("raw_mic_order"))
    hop = SR // 10
    out = []
    for i in range(0, len(raw_usb), hop):
        out += [(t, tracker_sources(tracks, offset, ccw, aec_calibration(meta)))
                for t, tracks in live.feed(i / SR, raw_usb[i:i + hop])]
    return out


def tracker_sources(tracks, offset, ccw, aec=None):
    return [Source(id=i, bearing_deg=to_world(aec_to_device(a, aec), offset, ccw), energy=round(v, 2), speech=True,
                   confidence=round(min(1.0, v), 2)) for i, a, v in tracks]


def odas_stream(raw_usb, meta, name, overrides=None, rate_hz=20.0):
    """ODAS offline on the raw mics -> sources at rate_hz like OdasSource (frame t = end of its hop)."""
    from laptop.audio.odas import ActivityHold, frame_to_sources
    from laptop.audio.odas_cfg import settings
    from laptop.audio.odas_wsl import run_offline

    s = settings(overrides)
    res = run_offline(raw_usb, name, overrides, order=meta.get("raw_mic_order"))
    offset = float(meta.get("array_offset_deg", C.ARRAY_OFFSET_DEG))
    ccw = not bool(meta.get("array_clockwise", C.ARRAY_CLOCKWISE))
    hold = ActivityHold(float(s.get("activity_hold_s", 0.4)))
    out, last = [], -1e9
    for f in res["tracks"]:
        t = f.get("timeStamp", 0) * res["hop"] / SR
        for src in f.get("src", []):
            if src.get("id"):
                hold(t, int(src["id"]), float(src.get("activity", 0.0)))
        if t - last < 1.0 / rate_hz:
            continue
        last = t
        out.append((t, frame_to_sources(f, offset, ccw, float(s.get("min_activity", 0.1)),
                                        float(s.get("speech_activity", 0.5)), hold, t, aec_calibration(meta))))
    return out, res


def scenes_part(names, overrides=None, methods=("srp", "odas")):
    import make_raw_scene

    src = make_raw_scene.Source()
    rows = []
    for name in names:
        folder = ROOT / "fixtures" / f"rot_{name}"
        if not (folder / "raw.wav").exists():
            make_raw_scene.make(name, src)
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        raw = read_streams(folder / "raw.wav")
        truth, seconds = meta["truth"], meta["seconds"]
        for m in methods:
            t0 = time.time()
            if m == "srp":
                stream = srp_stream(raw, meta)
            else:
                stream, _ = odas_stream(raw, meta, f"rot_{name}", overrides)
            r = score(stream, truth, seconds, m)
            rows.append((name, r))
            print(f"{name:16s} {m:5s} detect {r['detect']} err {r['median_err']} two {r['two_source']} "
                  f"ghosts/min {r['ghosts_per_min']} {r['ghosts'][:3]} stale {r['stale_s_per_min']} ids {r['ids']} sw/min {r['id_switches_per_min']} "
                  f"lat {r['latency_median']} ({r['onsets_found']})  [{time.time() - t0:.1f} s]", flush=True)
    return rows


# ---- recorded beams fixture (builtin baseline) ----------------------------------------------------------------

def replay_part(name):
    """Describe a recorded fixture's own `sources` (no line truth): how often 1 / 2 confident sources, where."""
    folder = ROOT / "fixtures" / name
    rows = read_sources(folder / "sources.jsonl")
    n = len(rows)
    conf = [[s for s in ss if s.speech and s.confidence >= GHOST_CONF] for _, ss in rows]
    two = sum(len(c) >= 2 for c in conf)
    one = sum(len(c) == 1 for c in conf)
    hist = {}
    for c in conf:
        for s in c:
            k = int(round(s.bearing_deg / 30.0) * 30) % 360
            hist[k] = hist.get(k, 0) + 1
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    print(f"{name}: {n} readings over {rows[-1][0]:.0f} s; capture {meta.get('capture')}, fixed beams "
          f"{meta.get('fixed_beams', 'n/a')}; confident speech sources: none {n - one - two}, one {one}, two {two}")
    print("  bearing histogram (30 deg bins, world): " +
          ", ".join(f"{(k + 180) % 360 - 180:+d}: {v}" for k, v in sorted(hist.items(), key=lambda kv: -kv[1])[:6]))
    return {"readings": n, "one": one, "two": two, "hist": hist}


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scenes", action="store_true", help="rotation scenes, offline: srp + odas")
    p.add_argument("--scene", action="append", help="with --scenes: only these (e.g. talker_baby)")
    p.add_argument("--methods", default="srp,odas")
    p.add_argument("--set", action="append", default=[], help="audio.odas override key=value")
    p.add_argument("--replay", help="describe a recorded fixture's sources (builtin baseline)")
    p.add_argument("--live", action="store_true", help="real array + laptop speaker: builtin, odas, hybrid")
    p.add_argument("--live-kinds", default="builtin,fixed,hybrid", help="with --live: which runs")
    p.add_argument("--captions", action="store_true", help="captions/alerts on rotation scenes (ODAS separated)")
    p.add_argument("--caption-modes", help="with --captions: mix,separated,postfiltered (default all)")
    p.add_argument("--out", help="append the markdown table to this file")
    a = p.parse_args()
    over = {}
    for kv in a.set:
        k, v = kv.split("=", 1)
        over[k] = json.loads(v) if v[:1] in "[{0123456789-.tf" else v
    md = []
    if a.scenes:
        rows = scenes_part(a.scene or SCENES, over, tuple(a.methods.split(",")))
        md.append(table(rows, f"Rotation scenes (fixtures/rot_*, raw mics){' ' + str(over) if over else ''}"))
    if a.replay:
        replay_part(a.replay)
    if a.live:
        import doa_live
        md.append(doa_live.live_part(a))
    if a.captions:
        import doa_live
        md.append(doa_live.captions_part(a, over))
    if md:
        text = "\n\n".join(md)
        print("\n" + text)
        if a.out:
            with open(a.out, "a", encoding="utf-8") as f:
                f.write(text + "\n\n")


if __name__ == "__main__":
    main()

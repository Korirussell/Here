"""Run ODAS (odaslive, built in WSL2 Ubuntu by tools/odas/setup_wsl.sh) from Windows.

    python -m laptop.audio.odas_wsl --offline fixtures/rot_talker_baby     # raw.wav -> ODAS tracks vs truth
    python -m laptop.audio.odas_wsl --offline fixtures/rot_single_0 --set probMin=0.3

Offline: the fixture's raw.wav (USB order) -> AEC geometry order x input_gain -> s16le file under logs/odas/<name>/
-> odaslive with file interfaces (laptop/audio/odas_cfg.py) -> tracks.json, pots.json, separated.raw,
postfiltered.raw, parsed back here. Frames are timestamped by hop (timeStamp k = k * hop / 16000 s into the input).
"""

import json
import math
import subprocess
from pathlib import Path

import numpy as np

from . import config as C
from .localize import aec_to_device, in_geometry_order
from .odas_cfg import make_cfg, settings
from .xvf3800 import to_world

ROOT = Path(__file__).resolve().parents[2]
SR = 16000


def win_to_wsl(path):
    """C:\\x\\y -> /mnt/c/x/y (WSL's default automount)."""
    p = Path(path).resolve()
    drive = p.drive.rstrip(":").lower()
    return f"/mnt/{drive}" + p.as_posix()[len(p.drive):]


def wsl_cmd(script, distro=None):
    s = settings()
    return ["wsl", "-d", distro or s.get("wsl_distro", "Ubuntu"), "--", "bash", "-lc", script]


def binary():
    return settings().get("binary", "~/here/odas/build/bin/odaslive")


def parse_frames(text):
    """ODAS JSON sinks write one object per hop, back to back: -> [dict]."""
    dec = json.JSONDecoder()
    out, i = [], 0
    while True:
        while i < len(text) and text[i] in " \r\n\t,":
            i += 1
        if i >= len(text):
            return out
        try:
            obj, i = dec.raw_decode(text, i)
        except json.JSONDecodeError:
            return out                      # a frame cut short at the end (killed run)
        out.append(obj)


def frame_world(src, offset, ccw, aec=None):
    """One ODAS source dict -> world bearing, or None for an empty slot. aec: a recording's aec_to_device."""
    x, y = src.get("x", 0.0), src.get("y", 0.0)
    if x == 0.0 and y == 0.0:
        return None
    return to_world(aec_to_device(math.degrees(math.atan2(y, x)) % 360.0, aec), offset, ccw)


def tracks_table(frames, hop, offset, ccw, min_activity=0.0, aec=None):
    """Tracked frames -> rows (t, id, world bearing, activity) for every live slot."""
    rows = []
    for f in frames:
        t = f.get("timeStamp", 0) * hop / SR
        for s in f.get("src", []):
            if not s.get("id"):
                continue
            b = frame_world(s, offset, ccw, aec)
            act = float(s.get("activity", 0.0))
            if b is not None and act >= min_activity:
                rows.append((t, int(s["id"]), b, act))
    return rows


def read_raw_audio(path, channels):
    if not Path(path).exists():
        return None
    x = np.fromfile(path, "<i2")
    return x[: len(x) // channels * channels].reshape(-1, channels)


def run_offline(raw_usb, name, overrides=None, order=None, timeout=600):
    """raw_usb: (n, 4) int16 in USB order. Returns dict(tracks, pots, separated, postfiltered, workdir, cfg)."""
    s = settings(overrides)
    work = ROOT / "logs" / "odas" / name
    work.mkdir(parents=True, exist_ok=True)
    geo = in_geometry_order(raw_usb, order).astype(np.float32) * float(s["input_gain"])
    np.clip(np.round(geo), -32768, 32767).astype("<i2").tofile(work / "raw.s16")
    for f in ("tracks.json", "pots.json", "separated.raw", "postfiltered.raw"):
        (work / f).unlink(missing_ok=True)
    w = win_to_wsl(work)
    cfg = make_cfg(("file", f"{w}/raw.s16"), ("file", f"{w}/tracks.json"), ("file", f"{w}/pots.json"),
                   ("file", f"{w}/separated.raw"), ("file", f"{w}/postfiltered.raw"), overrides=overrides)
    (work / "here.cfg").write_bytes(cfg.encode("utf-8"))
    r = subprocess.run(wsl_cmd(f"{binary()} -c {w}/here.cfg"), capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"odaslive failed ({r.returncode}): {(r.stdout + r.stderr)[-800:]}")
    k = int(s["max_sources"])
    read = lambda f: (work / f).read_text(encoding="utf-8", errors="ignore") if (work / f).exists() else ""
    return {"tracks": parse_frames(read("tracks.json")), "pots": parse_frames(read("pots.json")),
            "separated": read_raw_audio(work / "separated.raw", k),
            "postfiltered": read_raw_audio(work / "postfiltered.raw", k), "workdir": work, "hop": int(s["hop"]),
            "log": r.stdout[-2000:]}


def summarize(result, meta):
    """Per truth source: share of its active time with a track within 20 deg, and the median error there."""
    from laptop.geometry import angdiff

    offset = float(meta.get("array_offset_deg", C.ARRAY_OFFSET_DEG))
    ccw = not bool(meta.get("array_clockwise", C.ARRAY_CLOCKWISE))
    from .localize import aec_calibration
    rows = tracks_table(result["tracks"], result["hop"], offset, ccw, aec=aec_calibration(meta))
    print(f"{len(result['tracks'])} track frames, {len({r[1] for r in rows})} track ids: "
          f"{sorted({r[1] for r in rows})}")
    for sid, truth in (meta.get("truth") or {}).items():
        hits, errs, total = 0, [], 0
        for t0, t1 in truth["active"]:
            for t in np.arange(t0, t1, 0.1):
                total += 1
                near = [abs(angdiff(b, truth["bearing_deg"])) for tt, _, b, _ in rows if abs(tt - t) <= 0.05]
                if near and min(near) <= 20:
                    hits += 1
                    errs.append(min(near))
        med = float(np.median(errs)) if errs else None
        print(f"  truth {sid} at {truth['bearing_deg']:+.0f}: tracked {hits / max(total, 1):.0%} of active time"
              f"{'' if med is None else f', median error {med:.1f} deg'}")
    by_id = {}
    for t, i, b, a in rows:
        by_id.setdefault(i, []).append((t, b, a))
    for i, v in sorted(by_id.items()):
        from laptop.geometry import circular_mean
        print(f"  track {i}: {v[0][0]:.1f}-{v[-1][0]:.1f} s, mean {circular_mean([b for _, b, _ in v])[0]:+.0f} deg, "
              f"activity {np.mean([a for *_, a in v]):.2f}")


def main():
    import argparse

    from .mock import read_streams

    p = argparse.ArgumentParser(prog="python -m laptop.audio.odas_wsl")
    p.add_argument("--offline", metavar="FIXTURE", help="fixture folder with raw.wav (+ meta truth)")
    p.add_argument("--set", action="append", default=[], help="audio.odas override, key=value (repeatable)")
    a = p.parse_args()
    if not a.offline:
        p.print_help()
        return
    folder = Path(a.offline)
    if not folder.exists():
        folder = ROOT / "fixtures" / a.offline
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    over = {}
    for kv in a.set:
        k, v = kv.split("=", 1)
        over[k] = json.loads(v) if v[:1] in "[{0123456789-." else v
    raw = read_streams(folder / "raw.wav")
    res = run_offline(raw, folder.name, over, order=meta.get("raw_mic_order"))
    summarize(res, meta)


if __name__ == "__main__":
    main()

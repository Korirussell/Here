"""Numbers for the pitch from a laptop log: latencies, captions, alerts, retractions, engine/heartbeat problems.

    python tools/run_report.py                       # logs/laptop.jsonl
    python tools/run_report.py logs/laptop.jsonl --last 1     # only the newest session
    python tools/run_report.py logs/laptop.jsonl --json       # machine-readable

Reads the JSON-lines log every laptop run writes (`relay.log` in laptop/config.yaml; `--offline` runs too).
Each line is a message exactly as sent plus `_sent`, the laptop time it went to the link (older logs
without `_sent` still report everything except send latencies). A new session starts where `seq` restarts
or the log goes quiet for more than a minute.

Latencies (all laptop clock):
- speech_start detect: speech_start.t - the utterance's first audio (t_start; includes the 0.25 s pre-roll,
  or the array look-back): how far into the speech VAD + the array fired.
- speech_start send: _sent - speech_start.t (processing between detection and the link).
- final after speech end: final _sent - t_end (stage_ms.lag when _sent is missing); includes end_silence_s.
- first words: first non-empty partial _sent - t_start.
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
QUIET_GAP_S = 60.0


def read_log(path):
    msgs = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msgs.append(json.loads(line))
        except json.JSONDecodeError:
            continue                                   # a half-written last line from a killed run
    return msgs


def sessions(msgs):
    """Split on seq restarting or a long quiet gap."""
    out, cur, last_seq, last_t = [], [], None, None
    for m in msgs:
        t = m.get("_sent", m.get("t", 0.0))
        seq = m.get("seq")
        new = cur and ((seq is not None and last_seq is not None and seq < last_seq)
                       or (last_t is not None and t - last_t > QUIET_GAP_S))
        if new:
            out.append(cur)
            cur = []
        cur.append(m)
        last_seq = seq if seq is not None else last_seq
        last_t = t
    if cur:
        out.append(cur)
    return out


def stats(xs):
    if not xs:
        return None
    a = np.asarray(xs, float)
    return {"n": len(a), "median": round(float(np.median(a)), 3), "p90": round(float(np.percentile(a, 90)), 3),
            "max": round(float(a.max()), 3)}


def histogram(xs, edges):
    """Text histogram: one row per bucket."""
    if not xs:
        return ["    (no data)"]
    counts = np.histogram(np.clip(xs, edges[0], edges[-1] - 1e-9), bins=edges)[0]
    top = max(counts.max(), 1)
    rows = []
    for lo, hi, c in zip(edges, edges[1:], counts):
        rows.append(f"    {lo:5.2f}-{hi:5.2f} s {'#' * int(round(30 * c / top)):30s} {c}")
    return rows


def analyze(msgs):
    by_type = Counter(m.get("type") for m in msgs)
    utts = defaultdict(list)
    starts = {}
    for m in msgs:
        if m.get("type") == "utterance":
            utts[m["utt_id"]].append(m)
        elif m.get("type") == "speech_start":
            starts[m["utt_id"]] = m

    detect, send, final_lag, first_words = [], [], [], []
    captions = retractions = silent_retractions = 0
    for uid, ms in utts.items():
        final = next((m for m in ms if m.get("final")), None)
        first_audio = ms[0]["t_start"]
        st = starts.get(uid)
        if st is not None:
            detect.append(st["t"] - first_audio)
            if "_sent" in st:
                send.append(st["_sent"] - st["t"])
        words = next((m for m in ms if not m.get("final") and (m.get("text") or m.get("pending"))), None)
        if words is not None and "_sent" in words:
            first_words.append(words["_sent"] - first_audio)
        if final is None:
            continue
        if final.get("text"):
            captions += 1
            lag = final["_sent"] - final["t_end"] if "_sent" in final else (final.get("stage_ms") or {}).get("lag", 0) / 1000
            final_lag.append(lag)
        else:
            retractions += 1
            if not any(m.get("text") or m.get("pending") for m in ms):
                silent_retractions += 1                # only typing dots were ever shown
    orphan_starts = len(set(starts) - set(utts))       # speech_start with nothing after it

    alerts = [m for m in msgs if m.get("type") == "alert" and m.get("active")]
    alert_ids = {}
    for a in alerts:
        alert_ids.setdefault(a["alert_id"], a["kind"])
    sound_ids = {}
    for e in (m for m in msgs if m.get("type") == "sound_event"):
        sound_ids[e["event_id"]] = e["label"]

    beats = [m for m in msgs if m.get("type") == "heartbeat"]
    beat_t = [m.get("_sent", m["t"]) for m in beats]
    gaps = [b - a for a, b in zip(beat_t, beat_t[1:])]
    problems = Counter(m.get("detail", "") for m in beats if m.get("status") != "ok" and m.get("detail"))
    engines = Counter(m.get("engine") for m in beats if m.get("engine"))
    switches = sum(1 for a, b in zip(beats, beats[1:]) if a.get("engine") != b.get("engine"))
    t0 = min(m.get("_sent", m.get("t", 0)) for m in msgs)
    t1 = max(m.get("_sent", m.get("t", 0)) for m in msgs)

    tones = Counter(m.get("tone") for ms in utts.values() for m in ms if m.get("final") and m.get("text"))
    return {
        "start": t0, "minutes": round((t1 - t0) / 60, 2), "messages": dict(by_type),
        "utterances": len(utts), "captions": captions, "retractions": retractions,
        "retractions_dots_only": silent_retractions, "speech_start_without_utterance": orphan_starts,
        "alerts": dict(Counter(alert_ids.values())), "sound_events": dict(Counter(sound_ids.values())),
        "tones": {k or "none": v for k, v in tones.items()},
        "latency_s": {"speech_start_detect": stats(detect), "speech_start_send": stats(send),
                      "first_words": stats(first_words), "final_after_speech_end": stats(final_lag)},
        "raw": {"detect": detect, "first_words": first_words, "final_lag": final_lag},
        "heartbeats": len(beats), "heartbeat_gaps_over_5s": sum(1 for g in gaps if g > 5.0),
        "max_heartbeat_gap_s": round(max(gaps), 1) if gaps else None,
        "degraded_details": dict(problems), "engines": dict(engines), "engine_switches": switches,
    }


def print_report(i, r):
    import time
    print(f"\n=== session {i}: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r['start']))}, "
          f"{r['minutes']:.1f} min, {sum(r['messages'].values())} messages")
    print(f"  captions {r['captions']}, retracted {r['retractions']} (typing dots only: {r['retractions_dots_only']}), "
          f"speech_start with no utterance {r['speech_start_without_utterance']}")
    print(f"  alerts {r['alerts'] or '-'}   sound events {r['sound_events'] or '-'}   tones {r['tones'] or '-'}")
    for name, s in r["latency_s"].items():
        if s:
            print(f"  {name:24s} median {s['median']:.2f} s   p90 {s['p90']:.2f} s   max {s['max']:.2f} s   (n={s['n']})")
        else:
            print(f"  {name:24s} -")
    print("  speech_start detect (s into the utterance's audio):")
    print("\n".join(histogram(r["raw"]["detect"], [0, 0.25, 0.4, 0.6, 0.8, 1.0, 1.5, 3.0])))
    print("  final after speech end:")
    print("\n".join(histogram(r["raw"]["final_lag"], [0, 0.6, 0.8, 1.0, 1.25, 1.5, 2.0, 4.0, 10.0])))
    print(f"  heartbeats {r['heartbeats']} (gaps > 5 s: {r['heartbeat_gaps_over_5s']}, longest {r['max_heartbeat_gap_s']} s); "
          f"engines {r['engines'] or '-'}, switches {r['engine_switches']}")
    for detail, n in sorted(r["degraded_details"].items(), key=lambda kv: -kv[1]):
        print(f"  degraded x{n}: {detail}")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("log", nargs="?", default=str(ROOT / "logs" / "laptop.jsonl"))
    p.add_argument("--last", type=int, help="only the newest N sessions")
    p.add_argument("--json", action="store_true", help="print JSON instead of text")
    a = p.parse_args(argv)
    ss = sessions(read_log(a.log))
    if a.last:
        ss = ss[-a.last:]
    reports = [analyze(s) for s in ss]
    if a.json:
        print(json.dumps([{k: v for k, v in r.items() if k != "raw"} for r in reports], indent=1))
        return reports
    print(f"{a.log}: {len(ss)} session(s)")
    for i, r in enumerate(reports, 1):
        print_report(i, r)
    return reports


if __name__ == "__main__":
    main()

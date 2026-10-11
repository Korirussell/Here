"""Insight metrics + wording, shared by the Databricks notebook and the local precomputed run.

Input: rows of the flat event export (`python -m extras.memory export`, extras/memory/export.py). The metrics:
events per hour, speech vs environmental sound by location, unknown rate, important rear-origin events, top
conversation partners, recurring topics, noise density, surfaced vs suppressed, replay scenario metrics.

`metrics(rows)` computes them in plain Python; the Databricks notebook computes the same tables with Spark and
passes them to `render()`, so the wording and ids are identical whichever produced them. Only `source` differs:
"databricks" or "precomputed".
"""

import re
from collections import Counter, defaultdict

SPEECHY = {"speech", "laughter"}
HAZARDS = {"car_horn", "siren", "alarm", "loud_bang", "baby_crying"}
STOP = set("""about after again also been before being could does doing done from have here just like more much
must only other over really should some than that their them then there these they this those through very want
what when where which while will with would your you're it's i'm that's don't can't we're let's okay sorry everyone
today tonight morning thing things going right still well yeah please first""".split())
WORD = re.compile(r"[a-z][a-z']+")
POLICIES = {
    "default": {"sensitivity": 0.5, "crowd_filter": True, "focus_mode": False, "hazard_priority": True,
                "speech_priority": False},
    "everything": {"sensitivity": 1.0, "crowd_filter": False, "focus_mode": False, "hazard_priority": True,
                   "speech_priority": False},
    "focus": {"sensitivity": 0.4, "crowd_filter": True, "focus_mode": True, "hazard_priority": True,
              "speech_priority": True},
    "no_hazard_priority": {"sensitivity": 0.3, "crowd_filter": True, "focus_mode": False, "hazard_priority": False,
                           "speech_priority": False},
}


def hour_of(row, tz_offset_h):
    return int(((row["event_time"] + tz_offset_h * 3600) % 86400) // 3600)


def metrics(rows, tz_offset_h=-5.0, replay_fn=None, exclude_words=()):
    """Plain-Python twin of the notebook's Spark aggregations. `exclude_words`: e.g. the wearer's own name."""
    exclude = {w.lower() for w in exclude_words}
    m = {}
    main = [r for r in rows if r["type"] in ("utterance", "sound_event", "alert")]
    m["events_per_hour"] = {str(h): n for h, n in sorted(Counter(hour_of(r, tz_offset_h) for r in main).items())}

    by_loc = defaultdict(lambda: {"speech": 0, "environment": 0})
    for r in rows:
        if r["type"] == "utterance" or (r["type"] == "sound_event" and r["label"] in SPEECHY):
            by_loc[r["location_id"] or "unknown"]["speech"] += 1
        elif r["type"] == "sound_event":
            by_loc[r["location_id"] or "unknown"]["environment"] += 1
    m["speech_vs_env_by_location"] = dict(by_loc)

    sounds = [r for r in rows if r["type"] == "sound_event"]
    unknown = [r for r in sounds if r["label"] == "unknown"]
    m["unknown_rate"] = {"unknown": len(unknown), "sounds": len(sounds),
                         "rate": round(len(unknown) / len(sounds), 3) if sounds else 0.0,
                         "ids": [r["ref_id"] for r in unknown]}

    m["rear_important"] = [
        {"event_id": r["ref_id"], "t": r["event_time"], "label": r["label"], "rel_bearing": r["rel_bearing"],
         "segment_id": r.get("segment_id")}
        for r in rows if r["type"] == "alert" and r["rel_bearing"] is not None and abs(r["rel_bearing"]) >= 120
        and (r["payload"].get("priority") or 0) >= 2]

    partners = Counter(r["person_label"] for r in rows if r["type"] == "utterance" and r["person_label"])
    m["partners"] = dict(partners.most_common())
    partner_segs = defaultdict(set)
    for r in rows:
        if r["type"] == "utterance" and r["person_label"]:
            partner_segs[r["person_label"]].add(r.get("segment_id"))
    m["partner_segments"] = {k: sorted(s for s in v if s) for k, v in partner_segs.items()}

    topics = defaultdict(lambda: {"count": 0, "segments": set(), "ids": []})
    for r in rows:
        if r["type"] != "utterance" or not r["text"]:
            continue
        for w in set(WORD.findall(r["text"].lower())):
            if len(w) >= 4 and w not in STOP and w not in exclude:
                topics[w]["count"] += 1
                topics[w]["segments"].add(r.get("segment_id"))
                topics[w]["ids"].append(r["ref_id"])
    m["topics"] = {w: {"count": v["count"], "segments": sorted(s for s in v["segments"] if s), "ids": v["ids"]}
                   for w, v in sorted(topics.items(), key=lambda kv: (-len(kv[1]["segments"]), -kv[1]["count"], kv[0]))
                   if len(v["segments"]) >= 2}

    seg_span = {}
    for r in main:
        sid = r.get("segment_id")
        if sid:
            lo, hi = seg_span.get(sid, (r["event_time"], r["event_time"]))
            seg_span[sid] = (min(lo, r["event_time"]), max(hi, r["event_time"]))
    density = {}
    for sid, (lo, hi) in seg_span.items():
        n = sum(1 for r in sounds if r.get("segment_id") == sid)
        density[sid] = round(n / max(1.0, (hi - lo) / 60.0), 2)
    m["noise_density_per_min"] = density

    attn = {r["ref_id"]: r["label"] for r in rows if r["type"] == "perception_enrichment"}
    ss = defaultdict(lambda: {"surfaced": 0, "suppressed": 0})
    for r in rows:
        if r["type"] in ("utterance", "sound_event") and r["ref_id"] in attn:
            ss[r.get("segment_id") or "unknown"]["surfaced" if attn[r["ref_id"]] != "ambient" else "suppressed"] += 1
    m["surfaced_vs_suppressed"] = dict(ss)

    if replay_fn is not None:
        scen = {}
        for sid in sorted({r.get("segment_id") for r in rows if r.get("segment_id")}):
            seg_rows = [r for r in rows if r.get("segment_id") == sid]
            scen[sid] = {name: {k: v for k, v in replay_fn(seg_rows, pol).items()
                                if k in ("surfaced", "suppressed", "kept_important", "missed_important")}
                         for name, pol in POLICIES.items()}
        m["replay_scenarios"] = scen
    return m


def render(m, source, segment_labels=None):
    """Metric tables -> dashboard insights [{id, category, title, text, score, evidence_ids, source, data}]."""
    lab = segment_labels or {}
    out = []

    def add(id_, category, title, text, score, evidence_ids, data):
        out.append({"id": id_, "category": category, "title": title, "text": text, "score": round(score, 2),
                    "evidence_ids": evidence_ids[:12], "source": source, "data": data})

    if m.get("rear_important"):
        rear = m["rear_important"]
        kinds = Counter(r["label"] for r in rear)
        hazards = [r for r in rear if r["label"] in HAZARDS]
        add("rear_hazards", "safety", f"{len(rear)} important sounds came from behind you",
            f"{len(hazards)} were hazards ({', '.join(sorted({r['label'].replace('_', ' ') for r in hazards}))}). "
            f"Behind is exactly where you can't see: the belt and the rear chevrons carried them.",
            0.95, [r["event_id"] for r in rear], {"by_kind": dict(kinds)})

    p = m.get("partners") or {}
    if p:
        top = list(p.items())[:3]
        segs = m.get("partner_segments", {})
        recurring = [k for k, v in segs.items() if len(v) >= 2]
        add("top_partners", "people", f"You talked most with {top[0][0]}",
            "Top conversation partners: " + ", ".join(f"{k} ({v} lines)" for k, v in top) + ". "
            + (f"{' and '.join(recurring)} showed up in {max(len(segs[k]) for k in recurring)} different parts of "
               f"your day." if recurring else ""),
            0.8, [], {"partners": p, "segments": segs})
        for k in recurring:
            places = [lab.get(s, s) for s in segs[k]]
            add(f"routine_{k.lower().replace(' ', '_')}", "routine", f"{k} is part of your routine",
                f"{k} appeared in {len(places)} settings: {', '.join(places)}.", 0.7, [], {"segments": segs[k]})

    t = m.get("topics") or {}
    if t:
        words = list(t.items())[:5]
        add("recurring_topics", "topics", "What kept coming up",
            "Recurring topics across settings: " + ", ".join(f"“{w}” ({v['count']}x in {len(v['segments'])})"
                                                            for w, v in words) + ".",
            0.75, [i for _, v in words for i in v["ids"]], {"topics": {w: {k: v[k] for k in ("count", "segments")}
                                                                       for w, v in words}})

    d = m.get("noise_density_per_min") or {}
    if d:
        loudest = max(d, key=d.get)
        quiet = min(d, key=d.get)
        add("noise_density", "environment", f"{lab.get(loudest, loudest)} was the noisiest part of the day",
            f"{d[loudest]} sound events per minute there vs {d[quiet]} in {lab.get(quiet, quiet).lower()}.",
            0.65, [], {"per_min": d})

    ss = m.get("surfaced_vs_suppressed") or {}
    if ss:
        tot_s = sum(v["surfaced"] for v in ss.values())
        tot_q = sum(v["suppressed"] for v in ss.values())
        worst = max(ss, key=lambda k: ss[k]["suppressed"])
        add("surfaced_suppressed", "attention", f"HERE kept {tot_q} things quiet",
            f"It surfaced {tot_s} events and suppressed {tot_q} as ambient; most of the quiet came from "
            f"{lab.get(worst, worst).lower()} ({ss[worst]['suppressed']} events).", 0.7, [], {"by_segment": ss})

    u = m.get("unknown_rate")
    if u and u["sounds"]:
        add("unknown_rate", "honesty", f"{round(100 * u['rate'])}% of sounds were honestly 'unknown'",
            f"{u['unknown']} of {u['sounds']} sounds had a direction but no confident label. HERE showed where they "
            f"came from instead of guessing.", 0.55, u["ids"], {k: u[k] for k in ("unknown", "sounds", "rate")})

    s = m.get("speech_vs_env_by_location") or {}
    if s:
        add("speech_vs_environment", "environment", "Speech vs environment by place",
            "; ".join(f"{lab.get(k, k)}: {v['speech']} speech / {v['environment']} other"
                      for k, v in sorted(s.items(), key=lambda kv: (-kv[1]["speech"] - kv[1]["environment"], kv[0])))
            + ".", 0.5, [], {"by_location": dict(sorted(s.items()))})

    e = m.get("events_per_hour") or {}
    if e:
        peak = max(e, key=e.get)
        add("events_per_hour", "timeline", f"Busiest hour: {peak}:00",
            f"{e[peak]} events between {peak}:00 and {int(peak) + 1}:00.", 0.45, [], {"per_hour": e})

    rs = m.get("replay_scenarios") or {}
    if rs:
        lunch = rs.get("lunch") or next(iter(rs.values()))
        everything, default = lunch["everything"], lunch["default"]
        add("replay_crowd_filter", "replay", "The crowd filter at lunch",
            f"Showing everything would have surfaced {everything['surfaced']} events at lunch; the default policy "
            f"surfaced {default['surfaced']} and still kept {default['kept_important']} important ones.",
            0.85, [], {"scenarios": rs})
        nh = sum(v["no_hazard_priority"]["missed_important"] for v in rs.values())
        add("replay_hazard_priority", "replay", "Why hazards bypass the sensitivity slider",
            f"With hazard priority off and low sensitivity, {nh} important events across the day would have been "
            f"missed.", 0.6, [], {})
    return sorted(out, key=lambda i: -i["score"])

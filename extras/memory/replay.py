"""Replay Lab backend (TASKS A3 G): what would HERE have surfaced under another attention policy?

Input: a segment's stored events + a policy {sensitivity 0..1, crowd_filter, focus_mode, hazard_priority,
speech_priority}. Output (INTEGRATION_CONTRACT §5.3):
    {surfaced, suppressed, kept_important, by_category:{cat:{surfaced, suppressed}}, decisions:[...]}

Deterministic: a base relevance per event from what was stored (alert priority, the attention enrichment,
directed-at-user, label), then the policy. Optional `model_score` from the Shared/ATTENTION.md zero-shot model
(HERE_REPLAY_MODEL=1, relevance.py) is reported alongside and blended in when already computed; missing scores are
queued for the background scorer and never waited for.
"""

from . import config
from .relevance import scorer as default_scorer

HAZARDS = {"car_horn", "siren", "alarm", "loud_bang", "baby_crying"}
ATTENTION_SCORE = {"ambient": 0.15, "notice": 0.45, "important": 0.75, "urgent": 0.95}
DEFAULT_POLICY = {"sensitivity": 0.5, "crowd_filter": True, "focus_mode": False, "hazard_priority": True,
                  "speech_priority": False}
MODEL_WEIGHT = 0.3                                        # share of the model score in the blended relevance


def clamp(x):
    return max(0.0, min(1.0, x))


def candidates(rows):
    """Group stored rows into replayable events: utterances, sound events and alerts, with their enrichment."""
    enrich = {r["ref_id"]: r["payload"] for r in rows if r["type"] == "perception_enrichment"}
    alerts_by_ref = {}
    for r in rows:
        if r["type"] == "alert":
            ref = r["payload"].get("ref") or {}
            alerts_by_ref[ref.get("utt_id") or ref.get("event_id")] = r
    stored = {r["ref_id"] for r in rows if r["type"] in ("utterance", "sound_event")}
    out = []
    for r in rows:
        if r["type"] not in ("utterance", "sound_event", "alert"):
            continue
        ref = r["payload"].get("ref") or {}
        if r["type"] == "alert" and (ref.get("utt_id") or ref.get("event_id")) in stored:
            continue                                    # alerts ride on the event they refer to
        out.append({"row": r, "enrichment": enrich.get(r["ref_id"]), "alert": alerts_by_ref.get(r["ref_id"])})
    return out


def classify(c):
    r, p = c["row"], c["row"]["payload"]
    if c["alert"] and c["alert"]["payload"]["kind"] == "name_called":
        return "name_called"
    if r["type"] == "utterance":
        return "speech"
    label = p.get("label") if r["type"] == "sound_event" else p.get("kind")
    if label in HAZARDS:
        return "hazard"
    if label == "unknown":
        return "unknown"
    if c["alert"] or r["type"] == "alert":
        return "alert"
    if c["enrichment"] and c["enrichment"]["attention"] == "ambient" and label in ("speech", "laughter", "other",
                                                                                  "music", "tv", "vehicle"):
        return "crowd"
    return "ambient"


def base_score(c, category):
    p = c["row"]["payload"]
    s = {"name_called": 0.95, "hazard": 0.85, "alert": 0.6, "speech": 0.45, "unknown": 0.35, "ambient": 0.25,
         "crowd": 0.1}[category]
    e = c["enrichment"]
    if e:
        s = 0.5 * s + 0.5 * ATTENTION_SCORE[e["attention"]]
        if e.get("directed_at_user") is not None and category == "speech":
            s = max(s, 0.3 + 0.6 * e["directed_at_user"])
    if c["alert"]:
        s = max(s, 0.45 + 0.15 * c["alert"]["payload"]["priority"])
    if category == "hazard" and (c["row"]["rel_bearing"] is not None and abs(c["row"]["rel_bearing"]) > 120):
        s = max(s, 0.95)                                 # hazards from behind
    if category == "speech" and c["row"]["person_label"]:
        s += 0.1                                         # someone you're facing: part of your conversation
    if category == "speech" and p.get("tone") == "whispering":
        s -= 0.05
    return clamp(s)


def important(c, category):
    e = c["enrichment"]
    return category in ("name_called", "hazard") or (c["alert"] and c["alert"]["payload"]["priority"] >= 2) or \
        bool(e and e["attention"] in ("important", "urgent"))


def replay(rows, policy=None, use_model=None, scorer=None):
    """use_model: None = HERE_REPLAY_MODEL decides, False = rules only. `scorer`: a RelevanceScorer (tests)."""
    pol = {**DEFAULT_POLICY, **(policy or {})}
    sens = clamp(float(pol["sensitivity"]))
    threshold = 0.85 - 0.75 * sens                       # sensitivity 0 -> 0.85, 0.5 -> 0.475, 1 -> 0.1
    sc = None if use_model is False else (scorer or default_scorer())
    cands = candidates(rows)
    if sc is not None:                                    # speech content only (relevance.py)
        sc.request(c["row"]["text"] for c in cands if c["row"]["type"] == "utterance")
    decisions, by_cat = [], {}
    surfaced = suppressed = kept = missed = 0
    for c in cands:
        r = c["row"]
        cat = classify(c)
        score = base_score(c, cat)
        ms = sc.cached(r["text"]) if sc is not None and r["type"] == "utterance" else None
        if ms is not None:
            score = (1 - MODEL_WEIGHT) * score + MODEL_WEIGHT * ms
        if pol["speech_priority"] and cat in ("speech", "name_called"):
            score += 0.2
        imp = important(c, cat)
        if pol["hazard_priority"] and cat in ("hazard", "name_called"):
            show, reason = True, "hazard priority" if cat == "hazard" else "your name"
        elif pol["crowd_filter"] and cat == "crowd":
            show, reason = False, "crowd filter"
        elif pol["focus_mode"] and cat in ("speech", "ambient", "unknown", "crowd") and not (
                cat == "speech" and r["person_label"] and score >= threshold) and not imp:
            show, reason = False, "focus mode"
        else:
            show = score >= threshold
            reason = f"score {score:.2f} {'>=' if show else '<'} threshold {threshold:.2f}"
        d = {"event_id": r["ref_id"], "type": r["type"], "category": cat, "t": r["event_time"],
             "text": r["text"], "surfaced": show, "important": bool(imp), "reason": reason,
             "score": round(min(score, 1.0), 3)}
        if ms is not None:
            d["model_score"] = ms
        decisions.append(d)
        b = by_cat.setdefault(cat, {"surfaced": 0, "suppressed": 0})
        if show:
            surfaced += 1; b["surfaced"] += 1
            kept += bool(imp)
        else:
            suppressed += 1; b["suppressed"] += 1
            missed += bool(imp)
    out = {"policy": pol, "surfaced": surfaced, "suppressed": suppressed, "kept_important": kept,
           "missed_important": missed, "by_category": by_cat, "decisions": decisions}
    if sc is not None:
        speech = sum(1 for d in decisions if d["type"] == "utterance")
        scored = sum(1 for d in decisions if "model_score" in d)
        out["model"] = {**sc.status(), "utterances_scored": scored, "utterances_unscored": speech - scored}
    return out

"""Build the SEEDED synthetic day: extras/memory/seed/synthetic_day.jsonl + segments.json.

Every line of synthetic_day.jsonl is one canonical v3 message (contracts/examples shapes), exactly what the memory
service would receive from the relay as role `memory` (+ `scene` for the wearer's heading), validated against
contracts/schemas. It is SEEDED, not captured: the session is `seeded-<date>`, ids start with `seed-`, and
segments.json says `"seeded": true`. Never present it as live capture.

Deterministic (fixed random seed), so the files only change when this script does.

    python -m extras.memory.seed.build_seed            # rewrite the seed files
    python -m extras.memory.seed.build_seed --check    # fail if the committed files are stale
"""

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from contracts.python import here_contract as hc  # noqa: E402
from laptop.sounds.labels import ALERT_DISPLAY, SOUND_DISPLAY  # noqa: E402

OUT = Path(__file__).resolve().parent
DATE = "2026-10-09"
TZ = timezone(timedelta(hours=-5))            # CDT, matches extras/memory/config.py TZ_OFFSET_H
SESSION = f"seeded-{DATE}"
USER = "Kori"
ZS = "MoritzLaurer/deberta-v3-base-zeroshot-v2.0"
GEM = "gemini-2.5-flash"
HF_SOUND = "MIT/ast-finetuned-audioset-10-10-0.4593"   # a plausible Hugging Face upgrade model for the seed
CAMERA_HALF_FOV = 50.0                        # phone attributes a face only within this relative bearing

LOCATIONS = [
    {"id": "home_kitchen", "name": "Home: kitchen", "environment": "home"},
    {"id": "university_ave_crosswalk", "name": "University Ave crosswalk", "environment": "street"},
    {"id": "shelby_1103", "name": "Shelby Hall 1103", "environment": "lecture"},
    {"id": "dining_hall", "name": "Dining hall", "environment": "noisy_lunch"},
    {"id": "eng_lab_214", "name": "Engineering lab 214", "environment": "meeting"},
    {"id": "hackathon_hall", "name": "Hackathon hall", "environment": "presentation"},
    {"id": "home_living_room", "name": "Home: living room", "environment": "home"},
]

# id, label, start, end, location, headings [(time, deg)], people in view [(label, world bearing)], roles
SEGMENTS = [
    ("morning", "Morning at home", "07:40", "08:20", "home_kitchen", [("07:40", 10), ("07:55", -20)],
     [("Person 3", -30)], {"Person 3": "roommate"}),
    ("commute", "Commute: University Ave crosswalk", "08:25", "08:55", "university_ave_crosswalk",
     [("08:25", 0), ("08:40", 90)], [], {}),
    ("lecture", "Signals lecture", "09:00", "10:15", "shelby_1103", [("09:00", 0)],
     [("Person 1", 5), ("Person 2", -30)], {"Person 1": "professor", "Person 2": "classmate"}),
    ("lunch", "Noisy lunch", "12:05", "12:50", "dining_hall", [("12:05", 0)],
     [("Person 2", 30), ("Person 3", -35)], {"Person 2": "classmate", "Person 3": "roommate"}),
    ("meeting", "Team meeting in the lab", "14:00", "15:00", "eng_lab_214", [("14:00", 0)],
     [("Person 2", -30), ("Person 3", 25), ("Person 5", 45)],
     {"Person 2": "teammate", "Person 3": "teammate", "Person 5": "teammate"}),
    ("hackathon", "Hackathon presentation", "16:00", "17:30", "hackathon_hall", [("16:00", 0)],
     [("Person 2", 10), ("Person 4", -20)], {"Person 2": "teammate", "Person 4": "judge"}),
    ("evening", "Evening at home", "18:45", "21:30", "home_living_room", [("18:45", 0), ("19:30", 40)],
     [("Person 3", -45)], {"Person 3": "roommate"}),
]

# ("say", time, person (None = stranger off camera), world bearing, text, tone, opts)
# ("sound", time, label, world bearing, confidence, opts)   opts: dur, alert, attention, context, upgrade, count
# ("crowd", start, end, n, labels)                          ambient background the crowd filter should hide
S = "say"
SCRIPT = {
    "morning": [
        ("sound", "07:41:05", "music", 90, 0.66, {"dur": 240, "attention": "ambient"}),
        (S, "07:43:10", "Person 3", -30, "Morning! There's coffee in the pot if you want some.", "neutral", {}),
        (S, "07:44:02", "Person 3", -30, "Did you finish the slides for the hackathon?", "questioning",
         {"directed": 0.82}),
        (S, "07:45:30", "Person 3", -30, "I can't believe it's Friday already.", "excited", {}),
        ("sound", "07:52:20", "unknown", 150, 0.31, {"dur": 1.5, "upgrade": ("door", 0.64, 2.0)}),
        (S, "07:58:15", "Person 3", 170, "Kori, your phone is buzzing on the counter!", "shouting", {"name": True}),
        ("sound", "07:58:40", "phone_ringing", 165, 0.72, {"dur": 6}),
        (S, "08:05:00", "Person 3", -30, "Are you taking the bus or walking today?", "questioning",
         {"directed": 0.78}),
        (S, "08:06:12", "Person 3", -30, "Remember the landlord is coming at seven tonight to look at the sink.",
         "neutral", {"directed": 0.7, "attention": "important",
                     "context": "Reminder for tonight: landlord visit at 7 pm"}),
        ("sound", "08:12:30", "doorbell", 175, 0.81, {"dur": 2, "alert": True}),
        (S, "08:12:50", "Person 3", -60, "That's probably the package I ordered.", "neutral", {}),
        ("sound", "08:15:10", "dog_barking", 120, 0.58, {"dur": 4, "attention": "notice"}),
    ],
    "commute": [
        ("vehicles", "08:25", "08:55", 14),
        ("sound", "08:31:40", "car_horn", 175, 0.87, {"dur": 1.2, "alert": True, "attention": "urgent",
                                                      "context": "Horn from behind while crossing the street"}),
        ("sound", "08:33:05", "unknown", -100, 0.28, {"dur": 3}),
        ("sound", "08:36:30", "unknown", 60, 0.33, {"dur": 2}),
        ("sound", "08:38:15", "siren", -150, 0.91, {"dur": 14, "alert": True, "attention": "urgent",
                                                    "context": "Emergency vehicle approaching from behind-left"}),
        (S, "08:44:20", None, 150, "Excuse me, is this the way to the engineering building?", "questioning",
         {"directed": 0.74}),
        ("sound", "08:50:05", "loud_bang", 100, 0.83, {"dur": 0.6, "alert": True,
                                                       "context": "Construction noise on the right"}),
        ("sound", "08:51:00", "unknown", 110, 0.30, {"dur": 5}),
    ],
    "lecture": [
        (S, "09:01:00", "Person 1", 5, "Good morning everyone, let's get started.", "neutral", {}),
        (S, "09:02:10", "Person 1", 5, "Today we finish chapter six, beamforming and direction of arrival.",
         "neutral", {}),
        ("sound", "09:04:30", "door", 178, 0.66, {"dur": 1}),
        (S, "09:12:40", "Person 1", 5, "A steering vector tells the array how much to delay each microphone.",
         "neutral", {}),
        (S, "09:20:05", "Person 1", 5, "Kori, can you tell us what the steering vector represents?",
         "questioning", {"name": True}),
        (S, "09:35:00", "Person 1", 5, "Quick reminder about the exam before I forget.", "neutral",
         {"attention": "important"}),
        (S, "09:35:20", "Person 1", 5, "The midterm exam is next Thursday, October 15th, here in this room.",
         "neutral", {"attention": "important", "context": "Deadline: midterm exam Thu Oct 15 in Shelby 1103"}),
        (S, "09:35:45", "Person 1", 5,
         "It covers chapters four through seven, and you can bring one page of handwritten notes.", "neutral",
         {"attention": "important", "context": "Exam scope: chapters 4-7, one page of notes allowed"}),
        (S, "09:36:10", "Person 2", -30, "Did he just say the exam is next Thursday?", "whispering",
         {"directed": 0.66}),
        (S, "09:36:40", "Person 1", 5,
         "And the project report is due Friday, October 16th at 11:59 pm on Canvas.", "neutral",
         {"attention": "important", "context": "Deadline: project report Fri Oct 16, 11:59 pm"}),
        (S, "09:37:05", "Person 1", 5, "Late reports lose ten percent per day, so please don't push it.",
         "neutral", {}),
        ("sound", "09:41:00", "phone_ringing", 120, 0.69, {"dur": 5, "attention": "ambient"}),
        (S, "09:52:30", "Person 1", 5, "Office hours move to Wednesday at two this week.", "neutral",
         {"attention": "notice"}),
        (S, "10:09:00", "Person 2", -30, "Want to study together Tuesday night?", "whispering", {"directed": 0.8}),
        (S, "10:12:30", "Person 1", 5, "That's it for today, see you Monday.", "neutral", {}),
    ],
    "lunch": [
        ("crowd", "12:05", "12:50", 34, ["speech", "speech", "speech", "laughter", "other", "music"]),
        (S, "12:07:10", "Person 2", 165, "Kori! Over here, we saved you a seat.", "shouting", {"name": True}),
        (S, "12:09:00", "Person 2", 30, "Okay so the internship fair is Wednesday in the student center.",
         "excited", {"directed": 0.7}),
        (S, "12:09:40", "Person 3", -35, "Wait, I thought it was Friday?", "questioning", {}),
        (S, "12:10:05", "Person 2", 30, "No, Wednesday! They moved it because of the game.", "shouting", {}),
        (S, "12:12:30", "Person 3", -35, "Are we still demoing the belt first or the glasses?", "questioning",
         {"directed": 0.6}),
        (S, "12:13:10", "Person 2", 30, "Glasses first, then the belt when someone talks from behind.", "neutral",
         {}),
        ("sound", "12:18:20", "loud_bang", 140, 0.84, {"dur": 0.8, "alert": True, "attention": "notice",
                                                       "context": "Dropped tray behind you; no action needed"}),
        (S, "12:18:30", "Person 3", -35, "Somebody just dropped an entire tray of pasta.", "laughing", {}),
        ("sound", "12:18:35", "laughter", -35, 0.77, {"dur": 3, "attention": "ambient"}),
        (S, "12:24:00", "Person 2", 30, "My mom keeps asking when I'm coming home for break.", "neutral", {}),
        (S, "12:31:15", "Person 3", -35, "We need to deploy the relay before the judges show up.", "neutral",
         {}),
        (S, "12:40:00", "Person 2", 30, "I'll grab more fries, do you want anything?", "questioning",
         {"directed": 0.75}),
    ],
    "meeting": [
        (S, "14:01:00", "Person 2", -30, "Okay, status round. Relay first.", "neutral", {}),
        (S, "14:02:10", "Person 3", 25, "The relay works locally, Vultr deploy is next.", "neutral", {}),
        (S, "14:03:00", "Person 5", 45, "The belt motors are all wired, motor three was loose.", "neutral", {}),
        (S, "14:05:30", "Person 2", -30, "Latency on the captions is around two hundred milliseconds.", "excited",
         {}),
        ("sound", "14:11:00", "knock", 175, 0.74, {"dur": 1.2, "alert": True}),
        (S, "14:11:20", None, 175, "Sorry, is this room booked at three?", "questioning", {}),
        (S, "14:20:00", "Person 3", 25, "We need the relay deployed by eight tonight, no excuses.", "shouting",
         {"attention": "important", "context": "Team deadline: relay deployed by 8 pm"}),
        (S, "14:22:40", "Person 5", 45, "Kori, can you own the memory dashboard demo?", "questioning",
         {"name": True}),
        ("sound", "14:30:00", "phone_ringing", -90, 0.71, {"dur": 5, "attention": "ambient"}),
        (S, "14:41:00", "Person 2", -30, "If the internet drops we switch to the local relay.", "neutral", {}),
        ("sound", "14:52:00", "door", 170, 0.62, {"dur": 1}),
    ],
    "hackathon": [
        ("sound", "16:00:30", "music", -120, 0.71, {"dur": 300, "attention": "ambient"}),
        (S, "16:10:00", "Person 2", 10, "Hi judges, this is HERE, it shows you who is talking and where.",
         "excited", {}),
        (S, "16:13:20", "Person 4", -20, "How fast does the caption show up?", "questioning", {"directed": 0.6}),
        (S, "16:13:50", "Person 2", 10, "The direction arrives in under a quarter of a second.", "neutral", {}),
        (S, "16:16:00", "Person 4", -20, "What happens if the internet drops?", "questioning", {"directed": 0.6}),
        ("sound", "16:18:40", "alarm", 170, 0.88, {"dur": 8, "alert": True, "attention": "urgent",
                                                   "context": "Building alarm test behind the stage"}),
        (S, "16:19:00", None, 160, "That's just the alarm test, everyone stay seated.", "shouting",
         {"attention": "important"}),
        ("sound", "16:24:00", "other", 0, 0.62, {"dur": 6, "attention": "ambient", "text": "Applause"}),
        (S, "16:25:00", "Person 4", -20, "Great demo, I'd love to see it on a busy street.", "excited", {}),
        ("sound", "16:40:00", "unknown", -60, 0.29, {"dur": 2}),
    ],
    "evening": [
        ("sound", "18:50:00", "tv", 60, 0.74, {"dur": 1800, "attention": "ambient"}),
        (S, "18:52:30", "Person 3", -45, "How did the presentation go?", "questioning", {"directed": 0.85}),
        (S, "18:53:30", "Person 3", -45, "My grandma's back in the hospital, I might fly home this weekend.",
         "sad", {}),
        ("sound", "18:58:00", "doorbell", 175, 0.83, {"dur": 2, "alert": True}),
        (S, "18:58:30", None, 175, "Hi, I'm here to look at the sink.", "neutral", {"attention": "notice"}),
        ("sound", "19:20:00", "unknown", 120, 0.34, {"dur": 4, "upgrade": ("baby_crying", 0.61, 3.0)}),
        (S, "19:42:00", "Person 3", 170, "Kori, dinner's ready!", "shouting", {"name": True}),
        ("sound", "20:15:00", "knock", 175, 0.7, {"dur": 1.5, "alert": True}),
        (S, "20:16:00", "Person 3", -45, "Good luck on the exam Thursday, you've got this.", "neutral",
         {"directed": 0.8}),
        ("sound", "20:30:00", "dog_barking", 140, 0.6, {"dur": 5, "attention": "notice"}),
        (S, "21:05:00", "Person 3", -45, "I'm heading to bed, night!", "neutral", {}),
    ],
}

ATTN_BY_PRIORITY = {1: "notice", 2: "important", 3: "urgent"}


def epoch(hms):
    parts = [int(p) for p in hms.split(":")] + [0]
    h, m, s = parts[:3]
    y, mo, d = (int(x) for x in DATE.split("-"))
    return datetime(y, mo, d, h, m, s, tzinfo=TZ).timestamp()


def wrap(deg):
    return round(hc.wrap(deg), 1)


class Day:
    def __init__(self):
        self.rng = random.Random(20261009)
        self.msgs = []
        self.n = {"u": 0, "s": 0, "a": 0}

    def next_id(self, kind):
        self.n[kind] += 1
        return f"seed-{kind}-{self.n[kind]:04d}"

    def add(self, msg):
        d = msg.to_dict()
        d["session"] = SESSION
        self.msgs.append(d)

    def heading_at(self, seg, t):
        h = seg["headings"][0][1]
        for hms, deg in seg["headings"]:
            if epoch(hms) <= t:
                h = deg
        return h

    def enrich(self, t, ref, attention, conf, model=ZS, directed=None, context=None):
        kw = {}
        if directed is not None:
            kw["directed_at_user"] = round(directed, 2)
        if context:
            kw["context"] = context
            kw["context_model"] = GEM
        self.add(hc.PerceptionEnrichment(src="laptop", t=round(t, 3), ref=ref, attention=attention,
                                         attention_confidence=round(conf, 2), attention_model=model, **kw))

    def say(self, seg, hms, person, bearing, text, tone, opts):
        t0 = epoch(hms) + self.rng.uniform(0, 0.9)
        dur = 0.4 + 0.3 * len(text.split())
        utt = self.next_id("u")
        b = wrap(bearing + self.rng.gauss(0, 2))
        self.add(hc.Utterance(src="laptop", t=round(t0 + dur + 0.6, 3), utt_id=utt, t_start=round(t0, 3),
                              t_end=round(t0 + dur, 3), text=text, pending="", final=True, bearing_deg=b,
                              bearing_spread_deg=round(self.rng.uniform(2, 7), 1), source_id=0,
                              overlap=False, tone=tone, tone_confidence=round(self.rng.uniform(0.55, 0.9), 2),
                              loudness=round(self.rng.uniform(0.75, 0.95) if tone == "shouting" else
                                             self.rng.uniform(0.15, 0.3) if tone == "whispering" else
                                             self.rng.uniform(0.4, 0.65), 2),
                              engine="scribe_realtime" if self.rng.random() < 0.85 else "faster_whisper"))
        rel = wrap(b - self.heading_at(seg, t0))
        on_camera = person is not None and abs(rel) <= CAMERA_HALF_FOV
        self.add(hc.Attribution(src="phone", t=round(t0 + dur + 0.7, 3), ref=hc.Ref(utt_id=utt),
                                person_label=person if on_camera else None,
                                method="bearing" if on_camera else "off_camera",
                                score=round(self.rng.uniform(0.75, 0.95) if on_camera else 0.0, 2)))
        if opts.get("name"):
            aid = self.next_id("a")
            label, emoji, prio = ALERT_DISPLAY["name_called"]
            for active, dt in ((True, 0.05), (False, 6.0)):
                self.add(hc.Alert(src="laptop", t=round(t0 + dur + dt, 3), alert_id=aid, kind="name_called",
                                  label=label, emoji=emoji, priority=prio, bearing_deg=b, active=active,
                                  confidence=0.9, ref=hc.Ref(utt_id=utt)))
            self.enrich(t0 + dur + 0.9, hc.Ref(utt_id=utt), "urgent" if abs(rel) > 90 else "important", 0.9,
                        directed=0.95)
            return
        directed = opts.get("directed", round(self.rng.uniform(0.1, 0.35), 2))
        attention = opts.get("attention") or ("important" if directed > 0.75 else
                                              "notice" if directed > 0.5 or person else "ambient")
        self.enrich(t0 + dur + 0.9, hc.Ref(utt_id=utt), attention, self.rng.uniform(0.6, 0.9), directed=directed,
                    context=opts.get("context"))

    def sound(self, seg, hms, label, bearing, conf, opts):
        t0 = epoch(hms) + self.rng.uniform(0, 0.9)
        dur = opts.get("dur", 1.0)
        ev = self.next_id("s")
        b = wrap(bearing + self.rng.gauss(0, 3))
        category, emoji, text = SOUND_DISPLAY[label]
        text = opts.get("text", text)
        first_end = min(t0 + dur, t0 + 2.0)
        self.add(hc.SoundEvent(src="laptop", t=round(first_end, 3), event_id=ev, label=label, category=category,
                               emoji=emoji, text=text, confidence=conf, bearing_deg=b, t_start=round(t0, 3),
                               t_end=round(first_end, 3), count=opts.get("count", 1), source_id=1,
                               model="panns_cnn14"))
        if "upgrade" in opts:                               # same event_id, better label (docs/INTEGRATION.md §4)
            new, new_conf, after = opts["upgrade"]
            category, emoji, text = SOUND_DISPLAY[new]
            label, conf = new, new_conf
            self.add(hc.SoundEvent(src="laptop", t=round(t0 + after, 3), event_id=ev, label=new, category=category,
                                   emoji=emoji, text=text, confidence=new_conf, bearing_deg=b, t_start=round(t0, 3),
                                   t_end=round(t0 + after, 3), count=1, source_id=1, model=HF_SOUND))
        alert_kind = label if opts.get("alert") and label in ALERT_DISPLAY else None
        prio = ALERT_DISPLAY[alert_kind][2] if alert_kind else 0
        if alert_kind:
            aid = self.next_id("a")
            a_label, a_emoji, _ = ALERT_DISPLAY[alert_kind]
            for active, dt in ((True, 0.1), (False, max(dur, 6.0))):
                self.add(hc.Alert(src="laptop", t=round(first_end + dt, 3), alert_id=aid, kind=alert_kind,
                                  label=a_label, emoji=a_emoji, priority=prio, bearing_deg=b, active=active,
                                  confidence=conf, ref=hc.Ref(event_id=ev)))
        rel = wrap(b - self.heading_at(seg, t0))
        attention = opts.get("attention") or (ATTN_BY_PRIORITY.get(prio) if prio else
                                              "notice" if label == "unknown" and abs(rel) > 90 else
                                              "ambient" if label == "unknown" else "notice")
        if prio >= 2 and abs(rel) > 120 and attention == "important":
            attention = "urgent"                            # hazards from behind escalate
        self.enrich(first_end + 0.3, hc.Ref(event_id=ev), attention, self.rng.uniform(0.6, 0.92),
                    model=ZS if label != "unknown" else "rules", context=opts.get("context"))

    def background(self, start, end, n, labels, kind):
        t0, t1 = epoch(start), epoch(end)
        for i in range(n):
            t = t0 + (t1 - t0) * (i + self.rng.random()) / n
            label = self.rng.choice(labels)
            ev = self.next_id("s")
            category, emoji, text = SOUND_DISPLAY[label]
            if label == "other":
                text = self.rng.choice(["Dishes clattering", "Chairs scraping", "Sound"])
            b = wrap(self.rng.uniform(-179, 180))
            dur = self.rng.uniform(1, 8) if kind == "crowd" else self.rng.uniform(2, 6)
            self.add(hc.SoundEvent(src="laptop", t=round(t + dur, 3), event_id=ev, label=label, category=category,
                                   emoji=emoji, text=text, confidence=round(self.rng.uniform(0.45, 0.85), 2),
                                   bearing_deg=b, t_start=round(t, 3), t_end=round(t + dur, 3),
                                   count=self.rng.randint(1, 6), source_id=1, model="panns_cnn14"))
            self.enrich(t + dur + 0.2, hc.Ref(event_id=ev), "ambient", self.rng.uniform(0.7, 0.95), model="rules",
                        context=None)

    def scenes(self, seg):
        t, end = seg["start"], seg["end"]
        while t < end:
            h = self.heading_at(seg, t) + self.rng.gauss(0, 4)
            people = [hc.ScenePerson(label=lbl, bearing_deg=wrap(b), speaking=False) for lbl, b in seg["people"]]
            self.add(hc.Scene(src="phone", t=round(t, 3), heading_deg=wrap(h), people=people))
            t += 60.0


def build():
    day = Day()
    segments = []
    for sid, label, start, end, loc, headings, people, roles in SEGMENTS:
        seg = {"id": sid, "label": label, "start": epoch(start), "end": epoch(end), "location": loc,
               "environment": next(lo["environment"] for lo in LOCATIONS if lo["id"] == loc),
               "headings": headings, "people": people, "roles": roles}
        day.scenes(seg)
        for item in SCRIPT[sid]:
            if item[0] == S:
                day.say(seg, *item[1:])
            elif item[0] == "sound":
                day.sound(seg, *item[1:])
            elif item[0] == "crowd":
                day.background(item[1], item[2], item[3], item[4], "crowd")
            elif item[0] == "vehicles":
                day.background(item[1], item[2], item[3], ["vehicle"] * 5 + ["other"], "street")
        segments.append({k: seg[k] for k in ("id", "label", "start", "end", "location", "environment", "roles")})
    msgs = sorted(day.msgs, key=lambda m: m["t"])
    for m in msgs:
        errs = hc.errors(m)
        if errs:
            raise SystemExit(f"invalid seed message {m}: {errs}")
    meta = {
        "seeded": True,
        "note": "SEEDED synthetic day in canonical v3 message shapes. Not live capture.",
        "date": DATE, "session": SESSION, "tz_offset_h": -5.0, "user_name": USER,
        "locations": LOCATIONS, "segments": segments,
    }
    jsonl = "".join(json.dumps(m, ensure_ascii=False, separators=(",", ":")) + "\n" for m in msgs)
    return jsonl, json.dumps(meta, ensure_ascii=False, indent=2) + "\n"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--check", action="store_true")
    args = p.parse_args()
    jsonl, meta = build()
    files = {OUT / "synthetic_day.jsonl": jsonl, OUT / "segments.json": meta}
    if args.check:
        stale = [f.name for f, text in files.items() if not f.exists() or f.read_text(encoding="utf-8") != text]
        if stale:
            raise SystemExit(f"stale: {stale}; run python -m extras.memory.seed.build_seed")
        print("seed up to date")
        return
    for f, text in files.items():
        f.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {len(jsonl.splitlines())} messages -> {OUT / 'synthetic_day.jsonl'}")


if __name__ == "__main__":
    main()

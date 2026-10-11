"""Builds the seeded synthetic day the dashboard falls back to when A3's memory API is down.

    python extras/dashboard/demo_data/make_day.py      # writes day.json, ask.json, insights.json next to it

Events use the canonical v3 message shapes unchanged, plus the stored-event metadata from
Shared/INTEGRATION_CONTRACT.md §5.3 (`person_label`, `location_id`, `session`, `seeded: true`). Deterministic.
"""

import json
import random
from datetime import datetime
from pathlib import Path

OUT = Path(__file__).resolve().parent
DATE = (2026, 10, 9)
SESSION = "seed-2026-10-09"
rng = random.Random(9)


def at(h, m, s=0.0):
    return round(datetime(*DATE, h).timestamp() + m * 60 + s, 3)


SEGMENTS = [
    ("home-am", "Morning at home", (7, 30), (8, 15), "Apartment kitchen", "quiet"),
    ("commute", "Walk to campus", (8, 15), (8, 50), "College Ave", "street"),
    ("lecture", "CS 341 lecture", (9, 0), (10, 30), "Hill Center 114", "quiet"),
    ("lunch", "Lunch at the café", (12, 0), (12, 45), "Student center café", "cafe"),
    ("library", "Library study", (14, 0), (16, 0), "Alexander Library, 3rd floor", "quiet"),
    ("hack", "Hackathon kickoff", (18, 0), (20, 0), "HackRU hall", "crowd"),
]

events = []
n = {"u": 0, "s": 0, "a": 0}
EMOJI = {"doorbell": "🔔", "dog_barking": "🐕", "baby_crying": "👶", "knock": "🚪", "alarm": "⏰", "music": "🎵",
         "tv": "📺", "laughter": "😄", "phone_ringing": "📱", "loud_bang": "💥", "car_horn": "📯", "vehicle": "🚗",
         "siren": "🚨", "door": "🚪", "unknown": "❔", "other": "🔊", "name_called": "🗣️"}
CAT = {"doorbell": "alert", "knock": "alert", "alarm": "alert", "loud_bang": "alert", "car_horn": "alert",
       "siren": "alert", "door": "alert", "dog_barking": "animal", "baby_crying": "alert", "music": "media",
       "tv": "media", "laughter": "other", "phone_ringing": "alert", "vehicle": "other", "unknown": "other",
       "other": "other"}
TEXT = {"doorbell": "Doorbell", "dog_barking": "Dog barking", "knock": "Knock", "alarm": "Alarm", "music": "Music",
        "tv": "TV", "laughter": "Laughter", "phone_ringing": "Phone ringing", "loud_bang": "Loud bang",
        "car_horn": "Car horn", "vehicle": "Vehicle", "siren": "Siren", "door": "Door", "unknown": "Unknown sound"}
HF = "MIT/ast-finetuned-audioset-10-10-0.4593"
ATT = "MoritzLaurer/deberta-v3-base-zeroshot-v2.0"


def wrap(b):
    b = ((b + 180) % 360) - 180
    return 180.0 if b == -180 else round(b, 1)


def base(mtype, t, loc):
    return {"type": mtype, "v": 3, "src": "laptop", "t": t, "session": SESSION, "location_id": loc, "seeded": True}


def enrich(t, ref, attention, conf, directed=None, context=None, model=ATT):
    e = {"type": "perception_enrichment", "v": 3, "src": "laptop", "t": round(t + 0.6, 3), "session": SESSION,
         "seeded": True, "ref": ref, "attention": attention, "attention_confidence": conf, "attention_model": model}
    if directed is not None:
        e["directed_at_user"] = directed
    if context:
        e["context"] = context
        e["context_model"] = "gemini-2.5-flash"
    events.append(e)


def say(t, loc, person, bearing, text, tone="neutral", attention="notice", directed=0.2, overlap=False,
        engine="scribe_realtime", context=None, name=False):
    n["u"] += 1
    uid = f"u-{n['u']:04d}"
    dur = max(1.2, len(text.split()) * 0.32)
    e = {**base("utterance", round(t + dur, 3), loc), "utt_id": uid, "t_start": t, "t_end": round(t + dur, 3),
         "text": text, "pending": "", "final": True, "bearing_deg": wrap(bearing), "bearing_spread_deg": 4.0,
         "source_id": 0, "overlap": overlap, "tone": tone, "tone_confidence": 0.7, "loudness": 0.55,
         "engine": engine, "stage_ms": {"asr": round(rng.uniform(90, 220), 1), "lag": round(rng.uniform(150, 320), 1)},
         "person_label": person}
    events.append(e)
    enrich(e["t"], {"utt_id": uid}, attention, round(rng.uniform(0.62, 0.93), 2), directed, context)
    if name:
        alert(e["t"], loc, "name_called", "Name called", bearing, {"utt_id": uid}, 2)
    return uid


def alert(t, loc, kind, label, bearing, ref, priority):
    n["a"] += 1
    a = {**base("alert", round(t + 0.05, 3), loc), "alert_id": f"a-{n['a']:04d}", "kind": kind, "label": label,
         "emoji": EMOJI.get(kind, "🔔"), "priority": priority, "bearing_deg": wrap(bearing) if bearing is not None
         else None, "active": True, "confidence": 0.8, "ref": ref}
    events.append(a)


def sound(t, loc, label, bearing, conf, attention, count=1, alert_kind=None, priority=2, context=None,
          model=HF, att_model=ATT):
    n["s"] += 1
    sid = f"s-{n['s']:04d}"
    e = {**base("sound_event", round(t + 1.0, 3), loc), "event_id": sid, "label": label, "category": CAT[label],
         "emoji": EMOJI[label], "text": TEXT[label], "confidence": conf, "bearing_deg": wrap(bearing),
         "t_start": t, "t_end": round(t + 1.0, 3), "count": count, "source_id": 1, "model": model}
    events.append(e)
    enrich(e["t"], {"event_id": sid}, attention, round(rng.uniform(0.6, 0.9), 2), context=context, model=att_model)
    if alert_kind:
        alert(e["t"], loc, alert_kind, TEXT[label], bearing, {"event_id": sid}, priority)
    return sid


# ---- 07:30 home -------------------------------------------------------------
L = "home-am"
sound(at(7, 31), L, "music", -60, 0.82, "ambient", count=4)
say(at(7, 34), L, "Mom", 120, "Morning! There's coffee in the pot.", "neutral", "notice", 0.8)
sound(at(7, 41), L, "unknown", 95, 0.31, "ambient", model="panns_cnn14", att_model="rules")
sound(at(7, 52), L, "phone_ringing", 30, 0.77, "important", alert_kind=None)
say(at(7, 58), L, "Mom", 175, "Kori, don't forget your badge for the hackathon tonight.", "neutral", "important",
    0.94, name=True, context="Reminder addressed to the wearer, from behind")
sound(at(8, 6), L, "door", 170, 0.68, "notice")

# ---- 08:15 commute ------------------------------------------------------------
L = "commute"
for i in range(9):
    sound(at(8, 16 + i * 3, rng.uniform(0, 50)), L, "vehicle", rng.choice([-80, -95, 85, 100]), 0.7, "ambient",
          count=rng.randint(1, 4))
sound(at(8, 22, 10), L, "car_horn", -150, 0.88, "urgent", alert_kind="car_horn", priority=3,
      context="Horn from behind-left while crossing")
sound(at(8, 31), L, "siren", 60, 0.9, "important", alert_kind="siren", priority=2)
sound(at(8, 37), L, "unknown", -120, 0.28, "notice", model="panns_cnn14", att_model="rules")
for i in range(5):
    say(at(8, 25 + i * 4), L, None, rng.choice([-140, 150, 110, -100]),
        rng.choice(["...yeah I told him that", "no way, really?", "is this the 8:40 bus?", "hold on, let me check",
                    "that's what she said last week"]), "neutral", "ambient", 0.05, overlap=True)
say(at(8, 44), L, "Sam", -30, "Kori! Wait up, are you going to the lecture?", "excited", "important", 0.92,
    name=True)

# ---- 09:00 lecture -------------------------------------------------------------
L = "lecture"
lines = ["Okay, let's get started. Today is dynamic programming.",
         "Remember, memoization trades space for time.",
         "The problem set is due Thursday at midnight.",
         "Who can tell me the recurrence for edit distance?",
         "Good. Now let's look at the knapsack problem.",
         "This will be on the midterm, so pay attention.",
         "Office hours move to Wednesday this week."]
for i, line in enumerate(lines):
    say(at(9, 2 + i * 12), L, "Prof. Lee", rng.uniform(-8, 8), line, "neutral",
        "important" if "due" in line or "midterm" in line or "Office" in line else "notice", 0.35,
        context="Deadline mentioned" if "due" in line else None)
say(at(9, 47), L, "Maya", -70, "Psst, did you get question three?", "whispering", "notice", 0.85)
sound(at(9, 49), L, "laughter", 150, 0.66, "ambient", count=3)
say(at(10, 5), L, "Prof. Lee", 3, "Kori, can you walk us through your answer?", "questioning", "urgent", 0.97,
    name=True, context="The professor asked the wearer a direct question")
sound(at(10, 21), L, "phone_ringing", 140, 0.74, "notice")

# ---- 12:00 lunch ---------------------------------------------------------------
L = "lunch"
sound(at(12, 1), L, "music", 90, 0.8, "ambient", count=12)
for i in range(14):
    say(at(12, 2 + i * 3, rng.uniform(0, 40)), L, None, rng.choice([140, -150, 100, -110, 175]),
        rng.choice(["two lattes please", "did you finish the lab?", "haha that's so true", "is anyone sitting here?",
                    "the line is huge today", "I'm so tired", "we should study together", "number forty-two!"]),
        rng.choice(["neutral", "laughing", "excited"]), "ambient", 0.04, overlap=True)
say(at(12, 8), L, "Maya", 25, "Did you see the email about the internship fair?", "questioning", "notice", 0.9)
say(at(12, 9), L, "Jordan", -35, "Yeah! It's on Friday. Are you going?", "excited", "notice", 0.88)
say(at(12, 10), L, "Maya", 25, "The fair is on Friday at ten, in the Busch campus center.", "neutral", "important",
    0.83, context="Event time and place: Friday 10:00, Busch campus center")
say(at(12, 18), L, "Jordan", -35, "Bring printed resumes, they said like twenty copies.", "neutral", "notice", 0.8)
sound(at(12, 24), L, "loud_bang", 160, 0.72, "important", alert_kind="loud_bang", priority=2,
      context="A tray dropped behind the wearer")
say(at(12, 31), L, None, 170, "Order for Kori! Kori?", "shouting", "important", 0.9, name=True,
    context="The wearer's order is ready at the counter")
sound(at(12, 38), L, "laughter", -40, 0.7, "ambient", count=5)

# ---- 14:00 library -------------------------------------------------------------
L = "library"
for i in range(7):
    sound(at(14, 5 + i * 15), L, "unknown", rng.uniform(-100, -80), round(rng.uniform(0.2, 0.35), 2), "ambient",
          count=rng.randint(2, 6), model="panns_cnn14", att_model="rules")
sound(at(14, 40), L, "knock", 0, 0.81, "important", alert_kind="knock", priority=2)
say(at(14, 41), L, "Maya", 5, "Hey, we got the study room for an hour.", "neutral", "notice", 0.9)
sound(at(15, 12), L, "phone_ringing", -20, 0.79, "notice")
say(at(15, 30), L, "Librarian", 160, "The library closes early today, at five.", "neutral", "important", 0.6,
    context="Schedule change")
sound(at(15, 50), L, "alarm", 90, 0.62, "important", alert_kind="alarm", priority=2,
      context="Short alarm, likely a test; no evacuation speech followed")

# ---- 18:00 hackathon -----------------------------------------------------------
L = "hack"
sound(at(18, 1), L, "music", 0, 0.85, "ambient", count=20)
for i in range(18):
    say(at(18, 3 + i * 6, rng.uniform(0, 50)), L, None, rng.uniform(-180, 180),
        rng.choice(["where's the Wi-Fi password?", "we need a fourth teammate", "that demo was sick",
                    "is the pizza here yet?", "what are you building?", "I love this API", "let's go, team!"]),
        rng.choice(["neutral", "excited", "laughing"]), "ambient", 0.05, overlap=True)
say(at(18, 10), L, "MC", 0, "Welcome to HackRU! Hacking starts in twenty minutes.", "excited", "important", 0.4)
say(at(18, 25), L, "Maya", 30, "Okay, let's split it: I'll do the phone, you do the laptop.", "neutral", "notice",
    0.9)
say(at(18, 26), L, "Adil", -40, "And I'll get the cloud running. Vultr box is up.", "excited", "notice", 0.85)
sound(at(18, 52), L, "doorbell", 165, 0.71, "important", alert_kind="doorbell", priority=2,
      context="Doorbell during a conversation; pizza delivery expected")
say(at(18, 53), L, "Sam", 180, "Hey Kori, the pizza is here!", "excited", "important", 0.95, name=True,
    context="Called by name from behind")
say(at(18, 54), L, "Sam", 180, "Why is nobody looking at the person holding all the pizza?", "questioning",
    "notice", 0.7)
sound(at(19, 15), L, "laughter", -120, 0.74, "ambient", count=8)
sound(at(19, 32), L, "unknown", 135, 0.42, "notice", model=HF)
sound(at(19, 44), L, "siren", -90, 0.83, "important", alert_kind="siren", priority=2,
      context="Siren outside; no change inside the hall")

events.sort(key=lambda e: e["t"])
segments = [{"id": sid, "label": label, "start": at(*s), "end": at(*e), "location": loc, "environment": env}
            for sid, label, s, e, loc, env in SEGMENTS]
day = {"date": f"{DATE[0]}-{DATE[1]:02d}-{DATE[2]:02d}", "seeded": True,
       "label": "Synthetic day (seeded)", "segments": segments, "events": events}


def find(pred):
    return [x.get("utt_id") or x.get("event_id") for x in events if pred(x)]


fair = find(lambda x: x["type"] == "utterance" and "fair" in x["text"].lower())
named = find(lambda x: x["type"] == "utterance" and "Kori" in x["text"])
horn = find(lambda x: x.get("label") == "car_horn")
missed = find(lambda x: x["type"] == "utterance" and abs(x["bearing_deg"]) > 120 and x.get("person_label")) + \
    find(lambda x: x["type"] == "sound_event" and x.get("label") in ("loud_bang", "car_horn", "doorbell"))
deadline = find(lambda x: x["type"] == "utterance" and ("due" in x["text"] or "midterm" in x["text"]))

ask = {"answers": [
    {"match": ["fair"], "q": "What did Maya say about the fair?",
     "answer": "Maya said the internship fair is on Friday at ten, in the Busch campus center. Jordan added "
               "to bring about twenty printed resumes.", "evidence_ids": fair},
    {"match": ["name"], "q": "When was my name called?",
     "answer": f"{len(named)} times: by Mom at home, Sam on the walk, Prof. Lee in lecture, the café counter, and "
               "Sam at the hackathon. Three of them came from behind you.", "evidence_ids": named},
    {"match": ["miss"], "q": "What did I miss today?",
     "answer": "Things that happened behind you: Mom's badge reminder, a car horn from behind-left on College Ave, "
               "a tray dropped at the café, your café order being called, and the pizza doorbell.",
     "evidence_ids": missed},
    {"match": ["horn"], "q": "Where did the car horn come from?",
     "answer": "Behind you on the left (about -150°), at 8:22 on College Ave, while you were crossing.",
     "evidence_ids": horn},
    {"match": ["due"], "q": "What's due this week?",
     "answer": "Prof. Lee said the problem set is due Thursday at midnight, and dynamic programming will be on "
               "the midterm.", "evidence_ids": deadline},
]}

speech_by_person = {}
for x in events:
    if x["type"] == "utterance" and x.get("person_label"):
        speech_by_person[x["person_label"]] = speech_by_person.get(x["person_label"], 0) + 1
lib_unknown = find(lambda x: x.get("location_id") == "library" and x.get("label") == "unknown")
insights = {"insights": [
    {"id": "i-partner", "category": "people", "title": "Maya is today's main conversation partner",
     "text": f"Maya spoke {speech_by_person.get('Maya', 0)} times across lecture, lunch, library and the hackathon, "
             "usually in front of you.", "score": 0.91,
     "evidence_ids": find(lambda x: x.get("person_label") == "Maya"), "source": "precomputed"},
    {"id": "i-behind", "category": "attention", "title": "Most name calls came from behind",
     "text": "3 of 5 name calls came from behind you (more than 150° off your front). The belt cue mattered most for these.",
     "score": 0.86, "evidence_ids": named, "source": "precomputed"},
    {"id": "i-noise", "category": "environment", "title": "The café and the hackathon were the loudest places",
     "text": "Both had overlapping crowd speech for most of the visit; the crowd filter hid 30+ background lines "
             "while keeping your name and your order.", "score": 0.78, "evidence_ids": [], "source": "precomputed"},
    {"id": "i-hvac", "category": "unknown", "title": "A recurring unknown sound on your left in the library",
     "text": "7 low-confidence unknown sounds, all from about -90°, every ~15 min. Likely the air handler: HERE kept "
             "them as ambient with their direction.", "score": 0.64, "evidence_ids": lib_unknown,
     "source": "precomputed"},
    {"id": "i-deadline", "category": "routine", "title": "Lecture is where deadlines happen",
     "text": "Both of today's deadlines were spoken in CS 341 from the front. Prof. Lee also moved office hours.",
     "score": 0.7, "evidence_ids": deadline, "source": "precomputed"},
]}

for name, obj in (("day.json", day), ("ask.json", ask), ("insights.json", insights)):
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print(f"wrote {len(events)} events, {len(segments)} segments, {len(ask['answers'])} answers, "
      f"{len(insights['insights'])} insights")

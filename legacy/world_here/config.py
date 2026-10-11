"""world_here tunables. Values marked (tune) in HERE_PLAN.md §9 are starting points."""

HOST = "0.0.0.0"
PORT = 8765
LOG_PATH = "logs/world_here.jsonl"
STATUS_EVERY_S = 5.0

# Speech attribution (§9.1)
DOA_MIN_CONFIDENCE = 0.4
DOA_WINDOW_PAD_S = 0.15          # widen the utterance window slightly when collecting DoA
OVERLAP_STD_DEG = 25.0
MATCH_VISIBLE_DEG = 15.0
MATCH_OFFCAM_DEG = 20.0
OFFCAM_REUSE_S = 30.0
MOVE_DEG = 30.0                  # re-attribute if DoA moves this far...
MOVE_HOLD_S = 1.0                # ...for this long
RECENT_SPEAKER_TIEBREAK_DEG = 5.0
LIPS_SPEAKING_MIN = 0.35         # detections.speaking at or above this counts as moving lips (tune)
LIPS_MAX_AGE_S = 1.0             # a lip score older than this (vs the utterance end) is ignored

# Entities (§9.3)
VISIBLE_TIMEOUT_S = 1.0          # a person not in detections for this long is "off camera"
ARCHIVE_S = 300.0

# Captions
CAPTION_HOLD_S = 6.0             # keep a final caption up this long
CAPTION_STALE_S = 10.0           # drop a partial that stopped updating
UNKNOWN_SPEAKER_ID = 0
OFFCAM_ID_START = 100000         # above any tracker id, so off-camera ids never collide

DEFAULT_CONFIG = {
    "profile": "deaf",
    "user_name": "",
    "name_aliases": [],
    "known_people": [],
    "features": {
        "captions": True, "tone": True, "sound_labels": True, "alerts": True,
        "belt": True, "memory": True, "looking_away_summary": False, "type_to_speak": False,
    },
    "alerts_enabled": {},
    "belt_strength": 180,
    "caption_scale": 1.0,
}

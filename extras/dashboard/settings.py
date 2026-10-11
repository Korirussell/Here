"""Every dashboard tunable in one place (CLAUDE.md: tunables in one file).

Env overrides: HERE_DASH_PORT, HERE_MEMORY_URL. The relay URL / session / token come from laptop/config.yaml
+ laptop/.env (the same place the laptop and tools read them); the token stays in this process and never
reaches the browser.
"""

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

HOST = "127.0.0.1"
PORT = int(os.environ.get("HERE_DASH_PORT", "8780"))

STATIC = HERE / "static"
DEMO_DATA = HERE / "demo_data"
MEMORY_SEED = ROOT / "extras" / "memory" / "seed"     # A3's seed, preferred over ours when it exists
SCENES = ROOT / "fixtures" / "events"                 # K1's scripted scenes (fake_events --scene)

# Memory API (Shared/INTEGRATION_CONTRACT.md §5.3). Falls back to the seeded JSON when it doesn't answer.
MEMORY_URL = os.environ.get("HERE_MEMORY_URL", "http://127.0.0.1:8790")
MEMORY_PROBE_S = 0.3           # TCP connect check before each call
MEMORY_TIMEOUT_S = 4.0         # a reachable API gets this long (Ask HERE may call Gemini)
MEMORY_RETRY_S = 10.0          # after a failure, go straight to the seed for this long
SEED_AS_TODAY = os.environ.get("HERE_DASH_SEED_AS_TODAY", "1") != "0"   # show a seeded day on today's date

# Local labelled replay: with no relay reachable (venue Wi-Fi down), Run demo plays K1's scenes from SCENES
# straight to the page, labelled as a replay. The core moments map onto scenes here.
LOCAL_MOMENTS = {
    "1": ["front_speech", "two_speakers"],
    "2": ["rear_speech"],
    "3": ["doorbell_rear", "name_called_rear"],
    "all": ["front_speech", "two_speakers", "rear_speech", "doorbell_rear", "name_called_rear", "unknown_sound"],
    "tour": ["rear_speech", "doorbell_rear", "name_called_rear"],          # the judge tour (T), ~26 s
}
LOCAL_GAP_S = 1.5              # pause between scenes in a local playlist

# Relay bridge (role dashboard, subscribes to everything)
RELAY_RECONNECT_MIN_S = 0.5
RELAY_RECONNECT_MAX_S = 5.0
HISTORY = 400                  # recent events replayed to a newly opened page
BROWSER_OUTBOX = 512           # per page; drops the oldest event if a tab stalls
LATEST_ONLY = {"sources", "scene", "heartbeat"}

# Sent to the page (/dash/config). Nothing secret in here.
PAGE = {
    "stream_max": 120,         # event stream rows kept in the DOM
    "captions_max": 6,
    "source_ttl_s": 1.6,       # a radar ray fades out after this without a sources update
    "sound_ttl_s": 6.0,        # a sound marker stays this long after its last sound_event
    "latency_window": 60,      # samples for median / p95
    "heartbeat_stale_s": 5.0,
    "messages_max": 600,       # event ids kept for the inspector (5x the stream)
    "moment_hold_s": 8.0,      # Moment view (F): an important moment stays this long before a lesser one replaces it
    # Measured on the real array, shown as a reference line under the live numbers (never mixed into them).
    # Source: HERE_36hr_vibecode_plan/WORKLOG_K1.md "Real-array latency", tools/doa_latency.py, 2026-10-10.
    "latency_reference": {
        "label": "Real array, measured by K1 (2026-10-10, laptop -> local relay, tools/doa_latency.py)",
        "direction_p50_ms": 31, "direction_p95_ms": 39, "n": 292,
        "target_ms": 250, "target_label": "sound -> phone target",
    },
}

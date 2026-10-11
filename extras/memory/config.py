"""Tunables for the memory service (README §17.1, Shared/INTEGRATION_CONTRACT.md §5.3). One file, env overrides.

Secrets (NEON_DATABASE_URL, HERE_RELAY_TOKEN, GEMINI_API_KEY) come from the environment or extras/memory/.env,
never from here.
"""

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SEED_DIR = HERE / "seed"
DATA_DIR = HERE / "data"                      # gitignored: local SQLite fallback lives here


def load_dotenv(path=HERE / ".env"):
    path = Path(path)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip()
                end = v.find(v[:1], 1) if v[:1] in ("'", '"') else -1
                if end > 0:                                          # quoted: up to the closing quote, '#' kept
                    v = v[1:end]
                else:
                    v = v.split(" #", 1)[0].split("\t#", 1)[0].strip()   # trailing comments, as in .env.example
                os.environ.setdefault(k.strip(), v)


def env(name, default=""):
    return os.environ.get(name, default)


# --- relay ---------------------------------------------------------------------------------------------
RELAY_URL = "ws://127.0.0.1:8080/ws"          # HERE_RELAY_URL overrides; wss://<host>/ws on Vultr
SESSION = "demo"                              # HERE_SESSION overrides
# role `memory` defaults (contract DEFAULT_SUBSCRIBE) plus `scene`, used only to know the wearer's heading so
# "behind me" can be answered server-side (Shared/INTEGRATION_CONTRACT.md §3). Never stored per frame.
EXTRA_SUBSCRIBE = ["scene"]
RECONNECT_MIN_S, RECONNECT_MAX_S = 0.5, 5.0

# --- ingest --------------------------------------------------------------------------------------------
QUEUE_MAX = 5000                              # bounded: drops the oldest if the database stalls
FLUSH_EVERY_S = 1.0
FLUSH_MAX = 200                               # rows per batch write
HEADING_MAX_AGE_S = 60.0                      # a scene older than this doesn't define "behind"
LIVE_LOCATION = "hackathon_hall"              # HERE_LOCATION overrides; where live capture is happening

# --- storage -------------------------------------------------------------------------------------------
SQLITE_PATH = DATA_DIR / "memory.sqlite"
NEON_CONNECT_TIMEOUT_S = 5
RETENTION_DAYS = 7.0                          # README §25 (HERE_RETENTION_DAYS overrides); seeded data is exempt
PURGE_EVERY_S = 3600

# --- seed ----------------------------------------------------------------------------------------------
SEED_DAY = SEED_DIR / "synthetic_day.jsonl"
SEED_SEGMENTS = SEED_DIR / "segments.json"
SEED_ON_START = True                          # load the seeded day into an empty store
TZ_OFFSET_H = -5.0                            # Central Daylight Time (Alabama in October); no tzdata on Windows

# --- API -----------------------------------------------------------------------------------------------
API_HOST = "127.0.0.1"                        # Caddy proxies /api on Vultr
API_PORT = 8790
ASK_EVIDENCE_MAX = 6
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_TIMEOUT_S = 4.0
GEMINI_PAUSE_DEFAULT_S = 60.0                 # after a quota (429) error with no retryDelay
GEMINI_PAUSE_MAX_S = 600.0                    # cap on the server's retryDelay: retry at least every 10 min
ASK_CACHE_S = 30.0                            # an answer is reused this long (live ingest no longer empties the cache)
SESSIONS_CACHE_S = 5.0                        # the session list (segments) is re-read at most this often

# --- replay --------------------------------------------------------------------------------------------
ZERO_SHOT_MODEL = "MoritzLaurer/deberta-v3-base-zeroshot-v2.0"   # Shared/ATTENTION.md tier 2
REPLAY_MODEL_SCORES = False                   # HERE_REPLAY_MODEL=1 adds zero-shot relevance (CPU, cached)
REPLAY_MODEL_THREADS = 4                      # more threads were far slower on the i7-1250U (P+E cores)

# --- server status (TASKS A3 H, status.py) -------------------------------------------------------------
RELAY_HEALTH_URL = "http://127.0.0.1:8080/health"   # HERE_RELAY_HEALTH overrides (same box on Vultr)
RELAY_LOG = ROOT / "logs" / "relay.jsonl"     # HERE_RELAY_LOG overrides; /var/log/here/relay.jsonl on Vultr
STATUS_WINDOW_S = 300                         # message rates and ages over the last 5 minutes
STATUS_TAIL_BYTES = 2_000_000                 # read at most this much of the relay log's end
STATUS_CACHE_S = 2.0

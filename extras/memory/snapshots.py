"""Static copies of the memory API's answers over the seeded day, for the dashboard when the API is down
(docs/INTEGRATION.md §5.3: "falls back to the seeded JSON in extras/memory/seed/").

    python -m extras.memory snapshots          # rewrite extras/memory/seed/api/*.json
    python -m extras.memory snapshots --check  # fail if stale (tests run this)

Same shapes as the live endpoints: day.json (= GET /api/day), ask.json ({question: POST /api/ask response}),
insights.json (= GET /api/insights), replay.json ({segment_id: POST /api/replay with the default policy}).
Built from a throwaway in-memory store, template phrasing only (no Gemini), so they're deterministic.
"""

import json

from . import config
from .api import insights
from .ask import DEMO_QUERIES, Asker
from .memory import Memory
from .replay import DEFAULT_POLICY, replay
from .store import open_sqlite

OUT = config.SEED_DIR / "api"


def build():
    mem = Memory(store=open_sqlite(":memory:"), log=lambda *a: None)
    mem.seed()
    asker = Asker(mem)
    ask = {}
    for q in DEMO_QUERIES:
        r = asker.ask(q, phrase=False)
        ask[q] = {**r, "cached": True}
    day = mem.day()
    rp = {s["id"]: {"segment_id": s["id"], **replay(mem.segment_rows(s), DEFAULT_POLICY, use_model=False)}
          for s in mem.segments() if s["seeded"]}
    files = {"day.json": day, "ask.json": ask, "insights.json": insights(mem), "replay.json": rp}
    return {name: json.dumps(body, ensure_ascii=False, indent=1) + "\n" for name, body in files.items()}


def write(check=False):
    files = build()
    if check:
        stale = [n for n, text in files.items() if not (OUT / n).exists()
                 or (OUT / n).read_text(encoding="utf-8") != text]
        return stale
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (OUT / name).write_text(text, encoding="utf-8", newline="\n")
    return list(files)

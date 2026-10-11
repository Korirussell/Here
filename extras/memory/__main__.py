"""python -m extras.memory [serve|seed|ask|replay|export]   (README §17.1, INTEGRATION_CONTRACT §5.3)

    python -m extras.memory                          # relay client (role memory) + API on :8790, seeds if empty
    python -m extras.memory --no-relay               # API only, over the seeded day
    python -m extras.memory seed [--force]           # (re)load the seeded day into the store
    python -m extras.memory ask "What happened at lunch?"
    python -m extras.memory replay lunch --sensitivity 0.8 --no-crowd-filter
    python -m extras.memory export out.jsonl         # flat event export for the Databricks job
    python -m extras.memory forget <session>         # README §25: delete a session now
    python -m extras.memory snapshots [--check]      # static API answers for the dashboard's offline fallback

Database: NEON_DATABASE_URL (extras/memory/.env) or local SQLite (extras/memory/data/memory.sqlite).
"""

import argparse
import asyncio
import json
import sys

from . import config


def main():
    config.load_dotenv()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    p = argparse.ArgumentParser(prog="python -m extras.memory", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", nargs="?", default="serve", choices=["serve", "seed", "ask", "replay", "export", "forget",
                                                                   "snapshots"])
    p.add_argument("arg", nargs="?")
    p.add_argument("--relay", default=config.env("HERE_RELAY_URL", config.RELAY_URL))
    p.add_argument("--session", default=config.env("HERE_SESSION", config.SESSION))
    p.add_argument("--host", default=config.env("HERE_MEMORY_HOST", config.API_HOST))
    p.add_argument("--port", type=int, default=int(config.env("HERE_MEMORY_PORT", config.API_PORT)))
    p.add_argument("--no-relay", action="store_true")
    p.add_argument("--no-seed", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--sensitivity", type=float, default=0.5)
    p.add_argument("--no-crowd-filter", action="store_true")
    p.add_argument("--focus", action="store_true")
    p.add_argument("--check", action="store_true", help="snapshots: fail if stale")
    args = p.parse_args()

    if args.cmd == "snapshots":
        from .snapshots import OUT, write
        out = write(check=args.check)
        if args.check and out:
            raise SystemExit(f"stale snapshots {out}: run python -m extras.memory snapshots")
        print(f"{'up to date' if args.check else 'wrote'}: {OUT}")
        return

    if args.cmd == "serve":
        from .service import run
        try:
            asyncio.run(run(args))
        except KeyboardInterrupt:
            pass
        return

    from .memory import Memory
    memory = Memory()
    if args.cmd == "seed":
        memory.seed(force=args.force)
        print(json.dumps(memory.health()))
        return
    memory.seed()
    if args.cmd == "ask":
        from .ask import Asker
        print(json.dumps(Asker(memory).ask(args.arg or "Summarize my day."), ensure_ascii=False, indent=2))
    elif args.cmd == "replay":
        from .replay import replay
        seg = memory.segment(args.arg or "lunch")
        r = replay(memory.segment_rows(seg), {"sensitivity": args.sensitivity,
                                               "crowd_filter": not args.no_crowd_filter, "focus_mode": args.focus})
        print(json.dumps({k: v for k, v in r.items() if k != "decisions"}, indent=2))
    elif args.cmd == "export":
        from .export import export
        n = export(memory, args.arg or "logs/events_export.jsonl")
        print(f"exported {n} events -> {args.arg or 'logs/events_export.jsonl'}")
    elif args.cmd == "forget":
        memory.store.delete_session(args.arg)
        print(f"deleted session {args.arg!r}")


if __name__ == "__main__":
    main()

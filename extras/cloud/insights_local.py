"""Compute insights without Databricks (the fallback, TASKS A3 F): same metrics + wording as the notebook,
tagged `source: "precomputed"`.

    python -m extras.cloud.insights_local                       # from the memory store (seeds if empty)
    python -m extras.cloud.insights_local --export events.jsonl # from a flat export
    python -m extras.cloud.insights_local --write-seed          # refresh extras/memory/seed/insights.json
    python -m extras.cloud.insights_local --push databricks_out.json   # store a Databricks result (source databricks)

The dashboard never waits for this: the memory API serves Databricks rows if any, else precomputed ones.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from extras.cloud.insights_core import metrics, render
from extras.memory import config


def compute(rows, source="precomputed", segment_labels=None, user_name=""):
    from extras.memory.replay import replay
    m = metrics(rows, float(config.env("HERE_TZ_OFFSET_H", config.TZ_OFFSET_H)),
                replay_fn=lambda seg_rows, pol: replay(seg_rows, pol, use_model=False),
                exclude_words=[user_name] if user_name else [])
    return render(m, source, segment_labels)


def labels_of(mem):
    """Display names for segment ids and location ids."""
    labels = {s["id"]: s["label"] for s in mem.segments()}
    labels.update({k: v["name"] for k, v in mem.store.locations().items()})
    return labels


def seed_rows():
    """The seeded day as export rows, from a throwaway in-memory store (deterministic)."""
    from extras.memory.export import rows_for_export
    from extras.memory.memory import Memory
    from extras.memory.store import open_sqlite
    mem = Memory(store=open_sqlite(":memory:"), log=lambda *a: None)
    mem.seed()
    return mem, list(rows_for_export(mem))


def main():
    config.load_dotenv()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--export", help="flat export JSONL (python -m extras.memory export)")
    p.add_argument("--write-seed", action="store_true")
    p.add_argument("--push", help="a Databricks insights JSON to store with source databricks")
    args = p.parse_args()
    if args.push:
        from extras.memory.memory import Memory
        data = json.loads(Path(args.push).read_text(encoding="utf-8"))
        ins = data["insights"] if isinstance(data, dict) else data
        Memory().store.put_insights(ins, "databricks")
        print(f"stored {len(ins)} databricks insights")
        return
    if args.write_seed:
        mem, rows = seed_rows()
        labels = labels_of(mem)
        ins = compute(rows, "precomputed", labels, mem.meta.get("user_name", ""))
        out = config.SEED_DIR / "insights.json"
        out.write_text(json.dumps({"seeded": True, "source": "precomputed", "generated_by":
                                   "python -m extras.cloud.insights_local --write-seed", "insights": ins},
                                  ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {len(ins)} insights -> {out}")
        return
    if args.export:
        rows = [json.loads(line) for line in open(args.export, encoding="utf-8")]
        labels = None
    else:
        from extras.memory.export import rows_for_export
        from extras.memory.memory import Memory
        mem = Memory()
        mem.seed()
        rows = list(rows_for_export(mem))
        labels = labels_of(mem)
    t0 = time.perf_counter()
    ins = compute(rows, "precomputed", labels, config.env("HERE_USER_NAME", "Kori"))
    print(json.dumps({"insights": ins, "ms": round(1000 * (time.perf_counter() - t0))}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

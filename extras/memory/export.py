"""Flat event export for the Databricks job (extras/cloud/): one JSON object per line, normalized columns plus
the canonical payload. Text and metadata only (README §25)."""

import json
from pathlib import Path

from .memory import local_date

COLS = ["session", "origin", "type", "ref_id", "source_id", "world_bearing", "rel_bearing", "label", "text",
        "confidence", "person_label", "location_id", "event_time", "ingest_time"]


def rows_for_export(memory):
    for r in memory.store.events():
        seg = memory.segment_of(r["event_time"], r["session"])
        yield {**{c: r[c] for c in COLS}, "date": local_date(r["event_time"]),
               "segment_id": seg["id"] if seg else None,
               "environment": seg["environment"] if seg else None, "payload": r["payload"]}


def export(memory, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for row in rows_for_export(memory):
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n

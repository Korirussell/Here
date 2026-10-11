"""Session export bundles: text and metadata only, never audio (SPEC §25).

    python -m extras.cloud.export_bundle                     # every live session -> logs/exports/<session>/<stamp>/
    python -m extras.cloud.export_bundle --session demo      # one session
    python -m extras.cloud.export_bundle --upload            # also PUT to Vultr Object Storage (VULTR_S3_*)

A bundle is three files: `events.jsonl` (the flat export rows Databricks reads, canonical payload included),
`insights.json` (what /api/insights serves) and `manifest.json` (counts, sha256 of each file, time range,
`contains: ["text", "metadata"]`, `audio: false`). The local copy is always written; the upload is skipped when the
four VULTR_S3_* variables aren't set, and a failed upload never deletes the local copy.
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from extras.memory import config
from extras.memory.api import insights
from extras.memory.export import rows_for_export

HERE = Path(__file__).resolve().parent
OUT_DIR = config.ROOT / "logs" / "exports"
PREFIX = "here/bundles"                      # object key prefix in the bucket


def live_sessions(memory):
    return [s["id"] for s in memory.store.sessions() if not str(s["id"]).startswith("seeded-")]


def build_bundle(memory, session, out_dir=OUT_DIR, now=None):
    """Write one session's bundle; returns (folder, manifest)."""
    now = time.time() if now is None else now
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
    folder = Path(out_dir) / session / stamp
    folder.mkdir(parents=True, exist_ok=True)
    rows = [r for r in rows_for_export(memory) if r["session"] == session]
    files = {
        "events.jsonl": "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode(),
        "insights.json": json.dumps(insights(memory), ensure_ascii=False, indent=1).encode(),
    }
    times = [r["event_time"] for r in rows if r.get("event_time") is not None]
    manifest = {
        "kind": "here_session_bundle", "v": 1, "session": session, "created": round(now, 3),
        "seeded": session.startswith("seeded-"),
        "events": len(rows), "types": sorted({r["type"] for r in rows}),
        "start": min(times) if times else None, "end": max(times) if times else None,
        "contains": ["text", "metadata"], "audio": False, "retention_days": float(
            config.env("HERE_RETENTION_DAYS", config.RETENTION_DAYS)),
        "files": {name: {"bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()} for name, b in files.items()},
    }
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=1).encode()
    for name, data in files.items():
        (folder / name).write_bytes(data)
    return folder, manifest


def upload_bundle(s3, folder, session):
    """PUT the three files; returns the object keys. Raises on the first failure."""
    keys = []
    for name, ctype in (("events.jsonl", "application/x-ndjson"), ("insights.json", "application/json"),
                        ("manifest.json", "application/json")):     # manifest last: its presence = complete bundle
        key = f"{PREFIX}/{session}/{folder.name}/{name}"
        s3.put(key, (folder / name).read_bytes(), ctype)
        keys.append(key)
    return keys


def main(argv=None):
    config.load_dotenv(HERE / ".env")
    config.load_dotenv()
    p = argparse.ArgumentParser(prog="python -m extras.cloud.export_bundle", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--session", action="append", help="session id (repeatable); default every live session")
    p.add_argument("--out", default=str(OUT_DIR))
    p.add_argument("--upload", action="store_true", help="also upload to Vultr Object Storage (VULTR_S3_*)")
    args = p.parse_args(argv)

    from extras.cloud.s3 import S3
    from extras.memory.memory import Memory
    memory = Memory(log=lambda *_: None)
    s3 = S3.from_env() if args.upload else None
    if args.upload and s3 is None:
        print("[export] VULTR_S3_ENDPOINT/BUCKET/ACCESS_KEY/SECRET_KEY not all set: local bundles only")
    sessions = args.session or live_sessions(memory)
    if not sessions:
        print("[export] no live sessions in memory (the seeded day is exported with --session seeded-2026-10-09)")
    failed = 0
    for session in sessions:
        folder, m = build_bundle(memory, session, args.out)
        line = f"[export] {session}: {m['events']} events -> {folder}"
        if s3 is not None:
            try:
                keys = upload_bundle(s3, folder, session)
                line += f" -> s3://{s3.bucket}/{keys[-1].rsplit('/', 1)[0]}/"
            except Exception as e:                    # keep going; the local copy stays
                failed += 1
                line += f" (upload failed: {type(e).__name__}: {str(e)[:120]})"
        print(line)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

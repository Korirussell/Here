"""Run the Databricks insights job end to end from the memory server (TASKS A3 F), with REST only (no SDK):

1. export the memory store (python -m extras.memory export) and upload it to the Unity Catalog volume;
2. trigger the `here-insights` job (extras/cloud/databricks/job.json) and wait for it;
3. download insights_databricks.json and store the rows with `source: "databricks"`.

Any failure leaves the precomputed insights in place (the dashboard never waits on Databricks).

    python -m extras.cloud.databricks_sync                 # needs DATABRICKS_HOST, DATABRICKS_TOKEN (extras/cloud/.env)
    python -m extras.cloud.databricks_sync --create-job    # upload the notebook + helpers, create or update the job
    python -m extras.cloud.databricks_sync --dry-run       # export only, show what would be uploaded
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from extras.memory import config

HERE = Path(__file__).resolve().parent
VOLUME_DIR = "/Volumes/workspace/here/exports"
WORKSPACE_DIR = "/Workspace/Shared/here"          # the GitHub repo is private: upload the code instead of Git source
UPLOAD = ["extras/cloud/__init__.py", "extras/cloud/insights_core.py", "extras/memory/__init__.py",
          "extras/memory/config.py", "extras/memory/replay.py", "extras/memory/relevance.py",
          "extras/memory/seed/segments.json"]
NOTEBOOK = "extras/cloud/databricks/here_insights.py"


def api(method, path, body=None, raw=None, timeout=60):
    host = os.environ["DATABRICKS_HOST"].rstrip("/")
    headers = {"Authorization": f"Bearer {os.environ['DATABRICKS_TOKEN']}"}
    data = raw
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    elif raw is not None:
        headers["Content-Type"] = "application/octet-stream"
    req = urllib.request.Request(f"{host}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = r.read()
    if not payload:
        return {}
    try:
        return json.loads(payload)
    except ValueError:
        return payload


def main():
    config.load_dotenv(HERE / ".env")
    config.load_dotenv()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--create-job", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--timeout", type=float, default=900)
    args = p.parse_args()

    if args.create_job:
        import base64
        dirs = sorted({Path(f).parent.as_posix() for f in UPLOAD + [NOTEBOOK]})
        for d in dirs:
            api("POST", "/api/2.0/workspace/mkdirs", {"path": f"{WORKSPACE_DIR}/{d}"})
        for f in UPLOAD + [NOTEBOOK]:
            content = base64.b64encode((config.ROOT / f).read_bytes()).decode()
            is_nb = f == NOTEBOOK
            api("POST", "/api/2.0/workspace/import", {
                "path": f"{WORKSPACE_DIR}/{f[:-3] if is_nb else f}", "overwrite": True, "content": content,
                **({"format": "SOURCE", "language": "PYTHON"} if is_nb else {"format": "AUTO"})})
            print(f"uploaded {f}")
        job = json.loads((HERE / "databricks" / "job.json").read_text(encoding="utf-8"))
        existing = api("GET", f"/api/2.1/jobs/list?name={job['name']}").get("jobs", [])
        if existing:                                  # re-running updates the job instead of duplicating it
            api("POST", "/api/2.1/jobs/reset", {"job_id": existing[0]["job_id"], "new_settings": job})
            print(f"updated job {existing[0]['job_id']}")
        else:
            print(api("POST", "/api/2.1/jobs/create", job))
        return

    from extras.memory.export import export
    from extras.memory.memory import Memory
    mem = Memory()
    mem.seed()
    out = config.ROOT / "logs" / "events_export.jsonl"
    n = export(mem, out)
    print(f"exported {n} events -> {out}")
    if args.dry_run:
        return
    try:
        api("PUT", f"/api/2.0/fs/files{VOLUME_DIR}/events.jsonl?overwrite=true", raw=out.read_bytes(), timeout=120)
        job_id = os.environ.get("DATABRICKS_JOB_ID")
        if not job_id:
            jobs = api("GET", "/api/2.1/jobs/list?name=here-insights").get("jobs", [])
            job_id = jobs[0]["job_id"] if jobs else None
        if not job_id:
            raise SystemExit("no here-insights job: run with --create-job first")
        run = api("POST", "/api/2.1/jobs/run-now", {"job_id": int(job_id)})
        print(f"run {run['run_id']} started")
        t0 = time.time()
        while True:
            st = api("GET", f"/api/2.1/jobs/runs/get?run_id={run['run_id']}")["state"]
            if st.get("life_cycle_state") in ("TERMINATED", "SKIPPED", "INTERNAL_ERROR"):
                break
            if time.time() - t0 > args.timeout:
                raise TimeoutError("job still running")
            time.sleep(10)
        if st.get("result_state") != "SUCCESS":
            raise RuntimeError(f"job ended {st}")
        data = api("GET", f"/api/2.0/fs/files{VOLUME_DIR}/insights_databricks.json")
        data = json.loads(data) if isinstance(data, (bytes, str)) else data
        mem.store.put_insights(data["insights"], "databricks")
        print(f"stored {len(data['insights'])} databricks insights in {mem.db_kind} ({time.time() - t0:.0f} s)")
    except Exception as e:
        print(f"databricks sync failed ({type(e).__name__}: {e}); precomputed insights stay", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

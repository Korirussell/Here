# extras/cloud: Databricks insights and other cloud roles

**Never blocks the dashboard:** the memory API serves Databricks insights when a run has been stored and the
precomputed ones (`extras/memory/seed/insights.json`, `source: "precomputed"`) otherwise.

## Insights

Metrics over the flat event export (`python -m extras.memory export`): events per hour, speech vs environment by
location, unknown rate, important rear-origin events, top conversation partners, recurring topics, noise density,
surfaced vs suppressed, replay scenario metrics (the Replay Lab engine under four policies).

| File | Job |
|---|---|
| `insights_core.py` | the metrics in plain Python + `render()` (wording, ids, scores), shared by both paths |
| `insights_local.py` | the fallback: same metrics without Databricks. `--write-seed` refreshes the precomputed file; `--push <json>` stores a Databricks result |
| `databricks/here_insights.py` | the Databricks notebook: Spark aggregations -> Delta tables `workspace.here.events` / `workspace.here.insights` -> `render(..., "databricks")` -> `insights_databricks.json` in the volume |
| `databricks/job.json` | the job (`here-insights`), notebook uploaded to `/Workspace/Shared/here` (the GitHub repo is private, so no Git source) |
| `databricks_sync.py` | from the memory server: export -> upload to the volume -> run the job -> store the insights (`source: "databricks"`) |

```powershell
python -m extras.cloud.insights_local                 # print insights from the current store
python -m extras.cloud.insights_local --write-seed    # refresh the precomputed fallback (tests check it's fresh)
python -m extras.cloud.databricks_sync --dry-run      # export only
python -m extras.cloud.databricks_sync --create-job   # once: uploads the notebook + helpers, creates the job
python -m extras.cloud.databricks_sync                # the full run
```

Databricks setup (Free Edition, approved 2026-10-10): schema `workspace.here` (Free Edition has one catalog), volume
`exports`; a personal access token in `extras/cloud/.env` (`DATABRICKS_HOST`, `DATABRICKS_TOKEN`). The repo
code is uploaded by `--create-job` (re-run it after changing the notebook or `insights_core.py`). Serverless
compute runs the notebook.

## Session bundles to Vultr Object Storage

```powershell
python -m extras.cloud.export_bundle                    # every live session -> logs/exports/<session>/<stamp>/
python -m extras.cloud.export_bundle --session demo --upload
```

A bundle = `events.jsonl` (the same flat rows Databricks reads), `insights.json` (what `/api/insights` serves) and
`manifest.json` (event count, types, time range, sha256 per file, `audio: false`). Text and metadata only (README
§25). The local copy is always written; `--upload` PUTs the three files to
`s3://$VULTR_S3_BUCKET/here/bundles/<session>/<stamp>/` (manifest last, so its presence means the bundle is
complete) when `VULTR_S3_ENDPOINT/BUCKET/ACCESS_KEY/SECRET_KEY` are set in `extras/cloud/.env`, and says so when
they aren't. `s3.py` is a stdlib SigV4 client (checked against AWS's published signature examples), so no boto3.
Memory is on Neon, so this runs from the laptop or the Vultr box alike.

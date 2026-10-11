# WORKLOG A3: cloud (Vultr relay, memory, Neon, synthetic day, Ask HERE, Databricks, replay)

Branch `adil/a3-cloud`, worktree `../shh-a3`. Python: `..\shh\.venv\Scripts\python.exe`.

## Status

| Task | State |
|---|---|
| A. Vultr relay | **Skipped for now (Adil's call, 2026-10-10).** Deploy prep done: Caddy `/api`, `here-memory.service`, DEPLOY.md. SSH key `~/.ssh/here_vultr` ready. |
| B. Event ingest (`extras/memory/`) | Done locally: role `memory` relay client, batched writes, bounded queue, never blocks |
| C. Neon schema | **Live on Neon** (project `winter-water-41821142`, branch production): migrations, seeded day (317 events), Ask answers identical to SQLite, live relay events land in Neon. Over HTTPS: this Wi-Fi blocks port 5432 |
| D. Synthetic day | Done: 838 canonical messages, validated, `seeded-2026-10-09` |
| E. Memory API | Done locally on :8790; `search_request` -> `search_result` over the relay; five demo questions answered with evidence |
| F. Databricks | **Live** (Free Edition): job `here-insights` (id 825393336820521), schema `workspace.here`, volume `exports`, tables `workspace.here.events` / `.insights`. `python -m extras.cloud.databricks_sync` = export from Neon -> volume -> serverless run -> 12 insights stored in Neon with `source: "databricks"` (~85 s). Identical to the precomputed fallback, which stays in the table |
| G. Replay backend | Done: deterministic rules + optional zero-shot relevance on utterances (`HERE_REPLAY_MODEL=1`, background, cached, never blocks) |
| H. Extra Vultr roles | **Code done, waiting on the server:** `/api/status` (+ `/api/status.html`): relay health, message rates and laptop->relay ages from the relay log, memory DB, insights source; session bundles to Vultr Object Storage (`extras/cloud/export_bundle.py`, stdlib SigV4 `s3.py`). Deploy script points status at the relay log. 11 tests, no network |

## Run commands

```powershell
$py = "..\shh\.venv\Scripts\python.exe"
& $py -m relay --port 8080                                   # local relay
& $py -m extras.memory                                       # memory service + API http://127.0.0.1:8790/api/health
& $py tools/fake_events.py --loop                            # live events -> they land in memory
& $py -m extras.memory ask "What did my professor say about the exam?"
& $py -m extras.memory replay lunch --sensitivity 0.9 --no-crowd-filter
& $py -m pytest extras -q                                    # 22 tests
& $py -m extras.memory.seed.build_seed                       # rebuild the seeded day
& $py -m extras.cloud.insights_local --write-seed; & $py -m extras.memory snapshots   # refresh derived seed files
```

## For K2 (dashboard)

- API: `extras/memory/README.md` (shapes = INTEGRATION_CONTRACT §5.3, plus a few additive fields).
- Offline fallback: `extras/memory/seed/api/{day,ask,insights,replay}.json` (same shapes as the live endpoints).
- `/api/day` with no date = the seeded day; `dates` lists live days too. Segment ids: `morning commute lecture lunch
  meeting hackathon evening live-<session>`.

## For K3

- `extras/memory/tests` isn't in `.\tasks.ps1 test` yet (K3 owns `tasks.ps1`). Proposed: add `extras` to the
  pytest line. Until then: `python -m pytest extras`.
- No relay routing change: the memory service subscribes to its contract defaults + `scene` in its own `hello`.
- The memory service sends no `heartbeat` on purpose: the phone subscribes to heartbeats and shouldn't see ours.

## Log

- 2026-10-10: memory service, store (Neon/SQLite), seed day, Ask HERE, replay, insights core/local, Databricks
  notebook/job/sync, API snapshots, tests. Local e2e: relay :8181 + memory + `fake_events` -> 8 live events stored,
  live "Hey Kori" found by "When did someone call my name?". Full suite: 206 passed, 1 skipped; extras: 22 passed.
- 2026-10-10: Neon connected. Port 5432 is blocked on Adil's network (even portquiz.net:5432 fails; 443 fine), so
  added `neon_http.py` (Neon SQL over HTTPS, batches = transactions) with auto-selection. Seed into Neon 10 s; Ask
  0.6-2 s uncached, identical answers to SQLite; day view 0.7 s. Live: relay + memory + fake_events (session
  `a3-test`, then forgotten) -> 8 events in Neon. Fixed: Ask grouped all unlabelled speakers as one person; `.env`
  loader read trailing comments as values.
- 2026-10-10: Databricks live. Failures fixed on the way: (1) serverless Spark Connect's `toJSON()` returns Rows,
  so the replay cell reads the export file instead; (2) insights were keyed by `id` alone, so Databricks rows
  collided with precomputed ones: key is now `(id, source)` with an automatic migration, plus a `data` column so
  charts get the numbers; (3) Spark returned locations in another order, so `render()` sorts. `--create-job` now
  updates the existing job instead of duplicating it.
- 2026-10-10: replay relevance. The transformers pipeline took 10-20 s per text here (default 10 threads on the
  i7-1250U's P+E cores); the model called directly with 4 threads takes ~0.3-0.5 s. Sound labels scored nonsense
  ("Speech" 0.985), so utterances only. Label sets compared on the seed's 51 utterances (weak labels): "casual small
  talk" set AUC 0.73 with the widest spread (0.79 vs 0.58), chosen. Effect: never misses an important event; at
  sensitivity 0.3 it surfaces informational lines (late-report policy, office hours, relay fallback) and hides small
  talk; it also hides a couple of lines a person would keep, so it stays opt-in. Databricks re-run OK.
- Finding: the GitHub repo is private, so the Databricks job uploads code to the workspace instead of Git source,
  and the Vultr deploy will copy the code (scp / git archive), not `git clone` anonymously.
- 2026-10-10: A3 H. `extras/memory/status.py` (read-only, relay.py untouched; every part fails alone, 2 s cache) +
  `/api/status` and a self-refreshing `/api/status.html`, token-gated through Caddy like the rest of `/api`.
  `extras/cloud/s3.py`: SigV4 PUT/GET with the stdlib; matches AWS's published examples (signing key and the S3 GET
  Object signature). `extras/cloud/export_bundle.py`: per-session bundle (events.jsonl + insights.json +
  manifest.json with sha256 and `audio: false`) always written locally, uploaded when `VULTR_S3_*` are set,
  manifest last. Tests use a fake bucket that re-checks every signature. Not tried against real Vultr Object
  Storage (no bucket yet; ADIL_TODO.md §2.7). `tools/deploy_vultr.py` writes `HERE_RELAY_LOG` for the memory
  service and `status` prints `/api/status`.

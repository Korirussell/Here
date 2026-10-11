# extras/memory: HERE's long-term memory (SPEC §17.1)

A relay client with role `memory` that stores canonical events in **Neon Postgres** (local **SQLite** when Neon is
missing or fails), plus the HTTP **memory API** the dashboard consumes (`docs/INTEGRATION.md` §5.3).
**Never on the live path:** the phone, the laptop and the relay don't wait for it, and LIVE works with it off.

```
laptop ─► relay ─► phone                         (live path: untouched)
             └──► memory service (role memory) ─► Neon | SQLite ─► /api/day, /api/ask, /api/insights, /api/replay
                                                       ▲
                       seed/synthetic_day.jsonl ───────┘  (SEEDED day, same canonical shapes)
```

## Run

```powershell
python -m extras.memory                      # relay client + API on http://127.0.0.1:8790/api/health (seeds if empty)
python -m extras.memory --no-relay           # API only, over the seeded day
python -m extras.memory ask "What did my professor say about the exam?"
python -m extras.memory replay lunch --sensitivity 0.9 --no-crowd-filter
python -m extras.memory export logs/events_export.jsonl     # flat export for Databricks
python -m extras.memory forget <session>     # SPEC §25: delete a session now
python -m pytest extras                      # seed shapes, ingest, Ask, replay, API, relay link, fallbacks
$env:HERE_TEST_MODEL="1"; python -m pytest extras -k relevance_model   # + the real zero-shot model (~15 s)
```

Local end-to-end: `python -m relay` + `python -m extras.memory` + `python tools/fake_events.py --loop`, then
`curl localhost:8790/api/health` (events go up) and ask "When did someone call my name?".

Config: `extras/memory/.env` (template `.env.example`): `HERE_RELAY_URL`, `HERE_RELAY_TOKEN`, `HERE_SESSION`,
`NEON_DATABASE_URL`, optional `GEMINI_API_KEY`. Tunables: `config.py`.

## The API (`:8790` locally, `https://<host>/api/...` on Vultr behind Caddy)

| Endpoint | Returns |
|---|---|
| `GET /api/health` | `{ok, db: "neon" \| "local", events, sessions, fallback_reason, retention_days, gemini:{state: "on" \| "off" \| "paused", reason?, retry_in_s?, model}}` |
| `GET /api/day?date=YYYY-MM-DD` | `{date, dates, seeded, note, locations, segments:[{id, label, start, end, location, environment, seeded, roles}], events:[canonical message + person_label, location_id, rel_bearing, origin]}`. No date = the seeded day; `dates` lists every day with memory (live days too). |
| `POST /api/ask {q}` | `{answer, evidence:[{utt_id \| event_id, type, t, text, person_label, bearing_deg, rel_bearing, location_id}], cached, phrased_by}` |
| `GET /api/insights` | `{insights:[{id, category, title, text, score, evidence_ids, source, data}], source}`: `databricks` rows when a Databricks run has been stored, else `precomputed` |
| `POST /api/replay {segment_id, policy}` | `{segment_id, policy, surfaced, suppressed, kept_important, missed_important, by_category:{cat:{surfaced, suppressed}}, decisions:[{event_id, type, category, t, text, surfaced, important, reason, score, model_score?}]}` |
| `GET /api/segments` | every segment: the seeded day's seven + one `live-<session>` per live session |
| `GET /api/status` | server status (`status.py`): `{ok, t, relay:{health, log:{ok, window_s, messages, by_type:{type:{count, per_s}}, by_role, laptop_age_ms:{p50, p95, n}, events_in_tail:{connect, disconnect, bad_message}, last_message_s_ago}}, memory:<health>, insights:{serving, by_source:{source:{count, computed_at}}}}`. Each part fails alone (`{ok: false, reason}`); cached 2 s |
| `GET /api/status.html` | the same as a small page that refreshes every 5 s (`https://<host>/api/status.html?token=<token>` on Vultr) |

Ask answers are cached per question for `ASK_CACHE_S` (30 s; live ingest no longer empties the cache) and the five demo
questions are warmed at startup. Uncached on Neon over HTTPS: about 0.3-0.5 s (one store call per session, the session
list reused for 5 s), about 1 s with Gemini phrasing (no thinking). A Gemini 429 (quota) or key error pauses Gemini for
the server's `retryDelay` (capped at 10 min): answers use the template meanwhile and `/api/health` `gemini` says why.

`/api/status` reads the relay's `/health` (`HERE_RELAY_HEALTH`, default `http://127.0.0.1:8080/health`) and the last 2 MB of its JSON-lines log (`HERE_RELAY_LOG`, default `logs/relay.jsonl`; `/var/log/here/relay.jsonl` on Vultr, set by `tools/deploy_vultr.py`). `laptop_age_ms` is `rx - t` for laptop messages other than heartbeats: server clock minus laptop clock, so it includes any clock offset; watch the trend, not the absolute value. relay.py is untouched.

Policy: `{sensitivity 0..1, crowd_filter, focus_mode, hazard_priority, speech_priority}` (defaults 0.5, true,
false, true, false).

**Model relevance (optional, `HERE_REPLAY_MODEL=1`):** the docs/ATTENTION.md zero-shot model
(`MoritzLaurer/deberta-v3-base-zeroshot-v2.0`, CPU, `relevance.py`) scores each utterance as 1 - P("casual small
talk"); replay blends it in at 30 % and reports it per decision as `model_score`, plus a `model` block
(`utterances_scored` / `utterances_unscored`). Replay never waits for it: unscored texts are queued for a background
thread (~0.5 s each on a laptop CPU; the seeded day's 51 utterances take ~40 s once) and cached in
`data/relevance_cache.json`. `"use_model": false` in the request turns it off. Sound events stay rules-only (the
model's scores on one-word labels were meaningless). Never causes an important event to be missed on the seeded
day; it does hide some lines a person would keep ("No, Wednesday!"), which is why it's opt-in. Segment ids: `morning, commute, lecture, lunch, meeting, hackathon, evening, live-<session>`.

**Offline fallback for the dashboard:** `seed/api/day.json`, `ask.json` (the five demo questions), `insights.json`,
`replay.json` (default policy per segment) are the same responses, frozen. Regenerate with
`python -m extras.memory snapshots` (a test fails if they're stale).

The contract's `search_request` (from any client) is answered with `search_result` over the relay.

## Storage (`store.py`)

Tables `sessions`, `locations`, `events`, `utterances`, `insights`. `events.payload` is the canonical message
unchanged; normalized columns: `type, ref_id, source_id, world_bearing, rel_bearing, label, text, confidence,
person_label, location_id, event_time, ingest_time`. Indexes on `event_time`, `type`, `session`; full-text search
over final utterances (Postgres `tsvector` + GIN, SQLite FTS5). Migrations run on start (`CREATE ... IF NOT
EXISTS`). Writes are batched (one transaction per second or per 200 rows) from a bounded queue that drops the
oldest. Neon is reached directly (psycopg, port 5432) when that port is open, otherwise over HTTPS (Neon's SQL
API on 443, `neon_http.py`): some networks, including campus Wi-Fi, block 5432. A Neon error at any point switches to SQLite for the rest of the run and `/api/health` says
`db: "local"` with the reason.

What is stored: final utterances (partials skipped), every `sound_event` / `alert` update (same id = same row, so
labels upgrade and alerts end), `perception_enrichment`, `attribution` (also sets `person_label` on the event it
refers to). `scene` is never stored: only the latest heading, so `rel_bearing` ("behind me") is computed
server-side from the phone's own data, never on the laptop.

## The seeded day (`seed/`)

`synthetic_day.jsonl`: 838 canonical v3 messages (session `seeded-2026-10-09`, ids `seed-*`), each validated
against `contracts/schemas`. Seven scenes (morning at home, crosswalk commute, lecture, noisy lunch, lab meeting,
hackathon presentation, evening), recurring Person 2 / Person 3, a professor's exam and report deadlines, a car horn
and a siren from behind, unknown sounds (two upgraded later on the same `event_id`), five name calls, crowd noise at
lunch marked ambient, varied tones. `segments.json` holds the scenes, locations and person roles and says
`"seeded": true`. **It is a seeded demonstration, never live capture.** Rebuild: `python -m
extras.memory.seed.build_seed`.

## Privacy (SPEC §25)

Text and metadata only; no audio ever reaches this service. Person labels only (`Person 2`); roles like
"professor" are seed metadata, never face recognition. Live memory auto-deletes after 7 days
(`HERE_RETENTION_DAYS`); `forget <session>` deletes now. The synthetic seed is exempt.

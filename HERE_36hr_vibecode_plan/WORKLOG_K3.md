# WORKLOG K3: integration

Branch `kori/k3-integration`, worktree `../shh-k3`. Python: `..\shh\.venv\Scripts\python.exe`.

## 2026-10-10

### Baseline
- `python -m pytest contracts relay laptop tests -q`: 207 passed on `main` @ 5b568c7.
- `fixtures/events/` doesn't exist yet (K1 E). Until it does, the end-to-end tests run on `fake_events`' core moments.

### K3 B: end-to-end tests (done for the core moments)
- New `tests/test_e2e_roles.py`: local relay + `fake_events` moments 1-3 at 25x speed, laptop role, plus a test-only
  enricher that sends `perception_enrichment` (`attention_model: "rules"`) 50 ms after each final utterance and sound
  event. The listeners are a phone, a dashboard, a memory client and a phone in another session.
- It checks:
  - each role gets exactly its §12.10 subscription;
  - every message is valid v3 with `session` set;
  - memory gets utterance/alert/sound_event/enrichment and no positional traffic, with no laptop change;
  - every enrichment reaches the phone, dashboard and memory after its event;
  - phone and memory see the shared types in the same order;
  - nothing crosses sessions.
- Mutation check: sending the enrichment before its event makes the test fail (`assert 229 < 228`).
- Gate: 209 passed.
- TODO: once K1 lands `fixtures/events/*.jsonl` + `fake_events --scene`, parametrize the test over every scene.

### K3 C: latency (local relay only so far)
Laptop `t` → client receive, local relay on the same machine, 271 messages per run:

| Client | p50 | p95 | max |
|---|---|---|---|
| phone | 1.0 ms | 1.5 ms | 2.5 ms |
| dashboard | 1.0 ms | 1.5 ms | 2.5 ms |
| memory (38 msgs) | 1.0 ms | 2.0 ms | 2.0 ms |

`test_local_relay_latency_is_well_under_budget` guards p95 < 100 ms. Vultr numbers (campus Wi-Fi, hotspot) are
waiting on A3's deploy.

### K3 E: demo harness (first cut)
- `tools/demo.py up | reset [--full] | down | status`, plus `.\tasks.ps1 demo-up | demo-reset | demo-down | demo-status | demo-check`
  (Makefile twins). Extra arguments pass through: `.\tasks.ps1 demo-up --source real --cloud`.
  - Sources: `fake` (default; `fake_events --loop`), `replay --fixture <name>`, `real`, `none`.
  - The local relay is on :8080, unless `--cloud` (then `HERE_RELAY_URL` from `laptop/.env`).
  - Children get the relay URL, session and token through the environment; the token is never printed. With no token set, the local relay runs with auth off and says so.
  - Stopping a `--source real` laptop runs `laptop/audio/xvf3800.py --reset`.
  - The dashboard and memory API start only if `extras/dashboard/__main__.py` / `extras/memory/__main__.py` exist. **K2 and A3 need to provide `python -m extras.dashboard` / `python -m extras.memory` entry points** (told Kori).
- `reset` (default) restarts the relay and dashboard and leaves the laptop warm; the laptop reconnects by itself. `--full` restarts the source too.
- Timed with the fake source:

  | Reset | Time |
  |---|---|
  | light | 6.2 s |
  | `--full` | 16.0 s |

  Not yet timed: `--full --source real`, which adds model load. Measure it when K1 isn't holding the array or GPU.
- `tools/check_gpu.py --demo` checks the running stack:
  - relay `/health`, token set, and the token accepted on a real WebSocket join;
  - a laptop heartbeat within 5 s (status, engine, doa source = "models warm");
  - dashboard `:8780` and memory `/api/health` `:8790`.

  It loads no models (no second copy on the GPU) and leaves the array to the laptop. The summary now counts warnings.
- `tasks.ps1` falls back to `..\shh\.venv` in worktrees (no per-worktree venv needed).
- Tests: `tests/test_demo_harness.py` (stack composition per source, cloud mode, extras detection, health URL). Gate: 214 passed.

### Integration preview (K1 + K2 + K3 + A1 + A3), round 2
- A local worktree `../shh-k3-preview`, detached and never pushed: my branch merged with `kori/k1-respeaker`,
  `kori/k2-dashboard`, `origin/adil/a1-speech` and `origin/adil/a3-cloud`.
- Conflicts were only in `CHANGELOG.md` and `IOS_INTEGRATION.md`, all additive (kept both sides).
- **K1's new `fake_events` broke my role test.** The cause was the test, not K1: the doorbell `sound_event` now
  comes on onset and on end, and the script sends its own enrichments, so the hard-coded count was wrong. The test
  now enriches once per event and checks that every final and sound event got one, with identical enrichment
  streams for phone, dashboard and memory. It passes on both `main` and the preview.
- New `tests/test_e2e_scenes.py`: each of K1's seven scenes through the relay to phone, dashboard and memory.
  - Every subscribed event message arrives exactly once and in order, valid v3.
  - Each enrichment arrives after its event, and `speech_start` before the first words.
  - Per-scene checks: front within 30° of 0, rear beyond 150°, doorbell plus both alerts behind, name_called, unknown keeps a bearing, two_speakers gives two tracks and two source ids, crowd_noise raises no alert.
  - It skips until K1 merges. On the preview: 9/9.
- **Preview suite:** 301 passed, 1 failed. The failure is a K2 + A3 seed mismatch (below).
- **Review of A3's relay change:** `relay.py` is unchanged; Caddy only adds `reverse_proxy /api/* 127.0.0.1:8790`. OK.

### Harness, round 2
- `demo.py up --scene <name|all>` plays K1's scenes on a loop.
- `--relay-port` / `--dashboard-port` (the dashboard gets `HERE_DASH_PORT`; `check_gpu --demo` honours it too).
- **Bug fixed:** with another relay already on :8080 (a worker's dev relay), `up` reported "relay up" while talking to that relay. It now refuses ("port 8080 is held by another process") and exits 1.
- Full stack on the preview, on ports 18080/18780: relay + K2 dashboard + `--scene all`.
  - The dashboard's `/dash/config` showed `connected: true`, 218 messages received; `/live` pushed 122 messages to the page in 6 s.
  - `demo-check`: all good (4 warnings: no token, no laptop heartbeat with the scripted source, no memory API).
  - Reset with the dashboard included: 17.5 s.
- The convention holds: K2 has `extras/dashboard/__main__.py` and A3 has `extras/memory/__main__.py`.

### K3 D: failure drills, so far
| Drill | Command | Observed |
|---|---|---|
| Neon / memory API down → seed | demo stack with no memory API; `curl :18780/api/health`, `/api/day`, `/api/insights`; Ask through `GET /api/ask?q=` | `{"db": "seed", "_source": "seed"}`, the seeded day and the insights served. OK |
| Relay restart mid-demo | `demo.py reset` with the source running | The source and dashboard reconnect by themselves. Reset 6.2 s without the dashboard, 17.5 s with it |
| Port clash at the booth | `demo.py up` with :8080 taken | Refuses and exits 1 (was a silent false "up") |

### Round 3: golden, drills, latency probe
**Golden** (this laptop, RTX 5000 Ada, GPU otherwise idle, array untouched):

| Tree | Tests | Golden |
|---|---|---|
| Preview: K1 + K2 + K3 + A1 + A3 | 302 passed, 1 failed (the K2/A3 seed mismatch) | 7/7 |
| `origin/adil/a1-speech` alone (c5f7e25) | 226 passed | **7/7** |
| `kori/k1-respeaker` alone (46cb8b6) | 232 (K1's WORKLOG) | 7/7 (K1's WORKLOG) |

The 7/7 golden numbers are identical in all three: mock_scene clean/+10 dB WER 0.00, array_tts clean 0.00 and +10 dB 0.02, babble_only 0 captions / 0 alerts, two_baby 0.00 (baby at -179.7), two_talkers 0.00; door and name hit everywhere.

**Drills (K3 D)**

| Drill | Command | Observed | Result |
|---|---|---|---|
| ElevenLabs down → whisper | in the preview: `HTTPS_PROXY=http://127.0.0.1:9 NO_PROXY=localhost,127.0.0.1 ELEVENLABS_API_KEY=drill-dummy python -m laptop --replay fixtures/mock_scene --engine scribe_realtime --relay ws://127.0.0.1:18082/ws` (Scribe's websocket goes through a dead proxy; ElevenLabs is never contacted) | `heartbeat` `status: degraded`, `engine: faster_whisper`, `detail: "...scribe_realtime unavailable: [WinError 10061] ... -> faster_whisper"`; 43 utterance messages, 7 finals, all whisper | PASS |
| Memory API down mid-demo → seed | full stack (`demo.py up --scene all`, memory API live at `db: local`, 323 events), then kill only the memory pid | the dashboard's `/api/health` switched to `db: "seed"` at once; Ask HERE answers from the seed | PASS |
| Vultr down → local relay | `HERE_RELAY_URL=wss://here-down.invalid/ws python tools/demo.py up --cloud` | exits 1: "cloud relay UNREACHABLE ... Fallback: `.\tasks.ps1 demo-up` (local relay) and set the phone to ws://10.37.49.5:8080/ws". Local `up` always prints the phone URL | PASS (the phone's URL switch is the iOS team's) |
| Relay restart | `demo.py reset` | the source, dashboard and memory reconnect | PASS |
| Internet off (airplane) | partial: everything above runs with no cloud (local relay, whisper, seed) | the real airplane test needs the network pulled on the demo laptop; not done, it would cut the other workers off | TODO with Kori |
| Hugging Face down → PANNs/unknown; attention slow → rules; Gemini down | waiting on A2's branch (not pushed yet) | | TODO |

**Latency (K3 C):** `tools/latency_probe.py` (`.\tasks.ps1 latency [--relay wss://host/ws]`) sends 20 Hz `sources`
through any relay in a private `latency-probe` session and measures one-way latency and loss. Local relay: p50 0.0,
p95 1.0, max 8.5 ms, 0/200 lost. Vultr waits on A3's deploy. Then run it from campus Wi-Fi and the hotspot.

### Round 4: A1 on main, the merge picture (2026-10-10)
`kori/k3-integration` is pushed (tracks `origin/kori/k3-integration`) and has `origin/main` (A1) merged in.

**Golden on a clean `origin/main` (bf21932, A1 merged)**, detached worktree, RTX 5000 Ada, array untouched: **7/7**,
numbers identical to round 3 (mock_scene clean/+10 dB WER 0.00, array_tts clean 0.00 / +10 dB 0.02, babble_only
0 captions / 0 alerts, two_baby 0.00 with the baby at -179.7, two_talkers 0.00; door and name hit). A1 does not regress golden.
Note: on main, `.	asks.ps1 golden` from a worktree with no `.venv` falls back to `py` (no torch) and dies at
`import torch`; the shared-venv fallback in `tasks.ps1` is on this branch only. I ran `stress_test.py --golden` with
the shared venv directly.

**Whisper on the GPU** (`python -m laptop.speech.bench fixtures/array_tts --engine faster_whisper`, main):

| line | start | engine | first | final | WER | text |
|---|---|---|---|---|---|---|
| 0 | 4.9 | faster_whisper | 0.27 | 0.93 | 0.00 | Hey, did you see the email about the internship fair? |
| 1 | 8.9 | faster_whisper | 0.22 | 0.81 | 0.00 | Yes. It's on Friday. Are you going? |
| 2 | 13.2 | faster_whisper | 0.33 | 0.83 | 0.12 | Definitely. I'm bringing 20 copies of my resume. |
| 3 | 17.9 | faster_whisper | 0.33 | 0.83 | 0.00 | Why is nobody looking at the person holding all the pizza? |
| 4 | 27.7 | faster_whisper | 0.61 | 0.87 | 0.17 | Hey Cory, the pizza is here. |
| 5 | 30.8 | faster_whisper | 0.20 | 0.84 | 0.00 | That was honestly the best pizza I have had all week. |
| 6 | 34.5 | faster_whisper | 0.59 | 0.88 | 0.00 | Can somebody please open the window? |

Median first words **0.33 s** after the line starts (Adil's CPU: 1.18 s), final **0.84 s** after it ends (CPU: 2.40 s),
mean WER 0.04, 10 utterances, 0 duplicate finals. "Cory" for "Kori" costs the WER, but the `name_called` alert
still fires (golden "name hit"); the 0.12 is "20" vs "twenty".

**Scribe: blocked, no key.** There is no `laptop/.env` in any HERE checkout on this laptop (`shh`, `shh-k1`, `shh-k3`,
the main worktree) and `ELEVENLABS_API_KEY` is not in the environment. Not run: the `scribe_realtime` bench, the
`--unplug 12:22 --retry-s 5` run on the real API, `stage_ms.first_partial`. Drop the key into `laptop/.env` and they are
three commands (WORKLOG_A1 "For Kori" 3-4).

**Switching without a key** (`--primary fake --unplug 12:22 --retry-s 5`, GPU whisper as fallback):
`scribe_realtime failed: unplugged -> faster_whisper` at fixture 12.2 s, `still down; next try in 5 s`, back on the
primary at 22.2 s (0.2 s after the outage window ends). Every line captioned, 0 duplicate finals; median first words 0.15 s and final 0.72 s
(the fake answers instantly). Line 1's WER 1.57 is the fake: it hands out scripted lines per utterance, and VAD splits that line
in two, so its text runs one line ahead. `FallbackTranscriber.decode` retries the same `utt_id` on whisper, so one utterance
can't get two finals.

**E2E over K1's scenes:** `tests/test_e2e_scenes.py` (in b5feab1) is parametrized over every `fixtures/events/*.jsonl`
scene, and `tools/demo.py up --scene <name|all>` plays them. On this branch they skip (no scenes until K1 merges). On a local
merge of `origin/kori/k1-respeaker` (not pushed): `test_e2e_scenes` + `test_e2e_roles` + `test_demo_harness` 17 passed,
0 skipped (all 7 scenes).

**Merge readiness (each branch merged onto `origin/main` bf21932 in a throwaway worktree; nothing pushed, nothing merged to main):**

| Branch (head) | Conflicts with main | Gate `pytest contracts relay laptop tests` | Golden | Verdict |
|---|---|---|---|---|
| `kori/k1-respeaker` (46cb8b6) | CHANGELOG.md, IOS_INTEGRATION.md: both sides added rows; keep both | 252 passed | **7/7** | **Ready** (resolve the two doc conflicts by keeping both sides) |
| `kori/k2-dashboard` (7df21e5) | CHANGELOG.md (keep both) | 226 passed; `extras/dashboard` 10 passed | n/a (only `extras/` + docs) | **Ready**, with a catch: fix the seed mismatch below **before A3 merges** |
| `adil/a2-sound-ai` (bc6f446) | none | 269 passed | **7/7** (still `sounds.model: panns_cnn14`) | **Ready to merge as code** (no regression). The bakeoff, the model pick, attention tier 2 / JEV-9B and Gemini with a real key are still open (`Adil/A2_ON_KORI_LAPTOP.md` jobs), so merging doesn't change the demo model yet |
| k1 + a2 together on main | none beyond k1's | 295 passed | **7/7** | A2's onset subscription to K1's `app.onsets` holds |

Suggested order: K1 first (it's what A2's onset windows are built for), then A2, then K2. K2 must take `seed/api/` as a set
before or with A3.

### Round 5: A3 review, K3's own readiness, the Scribe runbook (2026-10-10)

**A3 (`origin/adil/a3-cloud` 076060a) merged onto `origin/main` bf21932** in a throwaway worktree (nothing pushed):
- Conflict: `CHANGELOG.md` only, both sides added entries (keep both).
- Gate `pytest contracts relay laptop tests`: **226 passed**; `extras/memory`: **26 passed**. Golden: n/a (A3 touches only
  `extras/`, `relay/Caddyfile`, `relay/DEPLOY.md` and docs; nothing in `laptop/`).
- `python -m extras.memory` exists (`serve` is the default; `--port`/`HERE_MEMORY_PORT`, `HERE_RELAY_URL`,
  `HERE_SESSION`), so `demo.py up` starts it with no change.
- **Live, main + A3 + K2 + K3** (`demo.py up --relay-port 18080 --dashboard-port 18780`, fake source, memory on :8790):
  memory joined the relay as role `memory` and ingested live (323 -> 344 events in about a minute) with no laptop change.

| §5.3 endpoint | A3 direct (:8790) | Through K2's dashboard (:18780) |
|---|---|---|
| `GET /api/health` | 200, `{ok, db: "local", events, sessions}` + `transport`, `fallback_reason`, `retention_days` | 200, `_source: api` |
| `GET /api/day` | 200, 7 segments with every §5.3 key (`id, label, start, end, location, environment`), 266 events with `person_label`, `location_id`, `session`; `dates` lists both days | 200, `_source: api` |
| `POST /api/ask {q}` | 200 in 3 ms, `{answer, evidence, cached}`; evidence has `utt_id, t, text, person_label, bearing_deg` (+ `rel_bearing`); empty `q` gives 400 | the page's `GET ?q=` becomes a POST; `_source: api` |
| `GET /api/insights` | 200, 12 insights `{id, category, title, text, score, evidence_ids, source}` + `data`; every evidence id resolves to an event (alerts by `alert_id`) | 200, `_source: api` |
| `POST /api/replay` | 200, `{surfaced, suppressed, kept_important, by_category, decisions}` + `missed_important`, `policy`; unknown segment gives 404 | 200, `_source: api`, **but carries `error: "unknown segment morning"`** (below) |

Extra fields are additive; nothing in §5.3 is missing or renamed. The token is required only on proxied (`X-Forwarded-For`) requests, and `/health` stays open.

**Finding for K2 (same root cause as the seed mismatch):** `fallback.Seed` loads `day.json` from K2's own `demo_data`
(A3 has no top-level `seed/day.json`; it lives in `seed/api/`), so the dashboard's local replay doesn't know A3's segment
ids. `{**local, **up}` keeps local's `error` key, and the per-event detail the page draws comes from a day that isn't A3's.
`replay.js` ignores `error`, so nothing visible breaks today, but the merged K2+A3 tree fails
`test_seeded_answers_and_insights_point_at_real_events` (`rear_hazards`, 1 failed / 35 passed in
`extras/dashboard extras/memory`). One fix covers both: K2 reads `extras/memory/seed/api/` first and takes day, ask and
insights from one folder as a set. **A3 must not merge before K2 has that fix, or they merge together.**

`demo-check` on that stack: relay OK, dashboard OK, memory `db local`; warnings only for no token, auth off, and no laptop
heartbeat (scripted source). I added a **`memory database`** line: WARN with the reason when memory isn't on Neon
("Ask/Replay work, but the Neon story isn't live").
`demo.py up --scene <x>` now refuses (exit 1) when `fixtures/events` has no such scene; before, it started the stack
and only said "source NOT UP" (I hit that on this preview, which has no K1 scenes). Test in `test_demo_harness.py`.

**K3's own readiness (`kori/k3-integration` -> main):** the branch already has `origin/main` merged, so a merge adds only
K3's files (demo harness, check_gpu `--demo`, latency probe, e2e tests, the `tasks.ps1` / Makefile tasks, and **the
`tasks.ps1` worktree-venv fallback**, so `.	asks.ps1 golden` works from any worktree). `.	asks.ps1 test`: **235 passed,
3 skipped** (the scene tests, until K1 merges). No golden needed (no `laptop/` change).

**Merge readiness, updated (nothing merged; waiting for Kori's go per merge):**

| Branch (head) | Conflicts with main | Gate | Golden | Verdict |
|---|---|---|---|---|
| `kori/k1-respeaker` (46cb8b6) | CHANGELOG, IOS_INTEGRATION (keep both) | 252 passed | 7/7 | Ready |
| `adil/a2-sound-ai` (bc6f446) | none | 269 passed | 7/7 | Ready as code (model pick still open) |
| `kori/k2-dashboard` (7df21e5) | CHANGELOG (keep both) | 226 + dashboard 10 | n/a | Ready alone; **needs the seed-set fix before A3** |
| `adil/a3-cloud` (076060a) | CHANGELOG (keep both) | 226 + memory 26 | n/a | **Ready after K2's seed fix** (or merged with it) |
| `kori/k3-integration` (this) | none (has main) | 235 passed, 3 skipped | n/a | Ready; brings the golden venv fallback to main |

Suggested order: **K3, K1, A2, K2 (with the seed fix), A3.** K3 goes first so `.	asks.ps1 golden` works from every worktree
and the scene e2e tests are live when K1 lands.

**For Kori: the ElevenLabs drill, once `ELEVENLABS_API_KEY=...` is in `laptop/.env`** (no `.env` exists in any checkout
on this laptop yet). From `main` with A1 merged, shared venv, array untouched for 1-3:
1. `python -m laptop.speech.bench fixtures/array_tts --engine scribe_realtime`: paste the table; compare with GPU whisper
   (first 0.33 s, final 0.84 s, WER 0.04). Check `stage_ms.first_partial` against A1's "first 2 seconds" note.
2. `python -m laptop.speech.bench fixtures/array_tts --engine scribe_realtime --unplug 12:22 --retry-s 5`: expect
   `scribe_realtime failed: unplugged -> faster_whisper` near 12.2 s and back on Scribe near 22 s, 0 duplicate finals.
3. Real outage: `python -m laptop --replay fixtures/array_tts --offline --engine scribe_realtime`, turn Wi-Fi off mid-sentence:
   captions keep coming (`-> faster_whisper`), heartbeat `status: degraded`, `engine: faster_whisper`; Wi-Fi on gives `... back`.
   (`.	asks.ps1 tap` in another window shows the heartbeat.)
4. Only on the array (K1 or Kori): `python -m laptop --real --engine scribe_realtime`, "Hey Kori" from behind gives one
   `name_called` with a rear bearing.
5. `.	asks.ps1 golden` stays 7/7 (default engine whisper). Switch the demo to Scribe (`speech.engine: scribe_realtime`)
   only if 1-3 look good.
Paste the outputs here or in WORKLOG_A1; I'll fold them into the drill table.

### Round 6: Kori's merges K1 -> A2 -> K2 (2026-10-10)
Kori approved the order K1, A2, K2. Each merge goes onto a fresh `origin/main` in a detached worktree, with no force.

| Step | Head merged | Conflicts | Gate `pytest contracts relay laptop tests` | Golden | Pushed |
|---|---|---|---|---|---|
| K1 | `kori/k1-respeaker` 743b373 | none (K1 had main in it) | 252 passed | **7/7**, same numbers | **yes**: main bf21932 -> 23c83f9 |
| A2 | `adil/a2-sound-ai` bc6f446 | none | 295 passed, with A1's flake (below) in 2 of 6 runs | **7/7** on the merge | **yes**: bc5bffc, after Kori accepted the flake |
| K2 | `kori/k2-dashboard` 87d3902 | none | 313 passed, 1 skipped (+ `extras/dashboard`) | n/a (no `laptop/`) | **yes**: 283fa2f |
| K3 | `kori/k3-integration` 568a628 | CHANGELOG (kept both) | **332 passed**, 1 skipped; K1's scene e2e tests now run | n/a | **yes**: **43f1f02** |

**Why I stopped:** the gate failed intermittently on
`laptop/tests/test_transcribers.py::test_network_drop_mid_utterance_hands_the_whole_utterance_to_whisper` (A1's test).
**It isn't A2's**: on `origin/main` 23c83f9 (K1 merged, no A2) the same full suite failed 1 of 5 runs. The test alone
passes 30/30 on both trees, so it only fails under full-suite load. Log at the failure:
`[speech] scribe_realtime failed: received 1000 (OK); then sent 1000 (OK) -> faster_whisper`. Unverified guess: the fake
server's "drop" sometimes arrives as a clean close (1000), so `f.reason` lacks "connection" (line 173), or the < 2.0 s
check misses on a loaded machine. Note left for A1 in WORKLOG_A1. Kori accepted it as A1's (pre-existing) and said go; pushed A2, K2, K3 in order, each a fast-forward, no force.
**main = 43f1f02.** A3 is still waiting for its own go-ahead. K1 and K2 have been told to merge `origin/main`.
Then **K2 b47d7ae** (inspector: A2's `<base model>+enrolled` ids read as "Enrolled sounds"; 3 lines in `core.js`), Kori's go: no conflicts,
332 passed, 1 skipped, so **main = b5f0e89**.

### Round 7: main 8d78be3, drills, guards, the transformers question (2026-10-10)
**Golden on a clean `origin/main` 8d78be3** (A3, the relay mirror, A1's flaky-test fix, A2 tier 2, the SyntaxError fix):
**7/7**, the same numbers as every earlier run. The GPU was idle and K1 wasn't running the laptop. Gate on this branch with main
merged: 534 passed, 1 skipped.

**Entry-point guard** (`tests/test_entry_points.py`): `--help` in a fresh interpreter for every entry point `demo.py` starts
(`-m relay`, `-m laptop`, `-m extras.memory`, `-m extras.dashboard`, `tools/fake_events.py`) plus the K3 tools. The list
comes from `demo.commands()`, so a new service is covered without editing the test. 12 passed. Mutation check: a bad import at
the top of `laptop/__main__.py` makes it fail with the `ModuleNotFoundError`. `test_compiles` (main) only catches syntax errors.

**Attention tier in `demo-check`:** `check_gpu --demo` adds an `attention tier` line.
- It reads the running laptop's `[enrich] attention tiers: [...]` from `logs/demo/source.log`.
- With no laptop running, it predicts from what this venv, the cache and the GPU can load.
- WARN "RULES ONLY" when that's all there is. Today it says: JEV-9B missing transformers, peft, bitsandbytes; DeBERTa missing transformers.

**Finding: on this laptop attention has always been `rules`.** The shared `.venv` has no `transformers` (it isn't in
`laptop/requirements.txt`), so both tiers log "unavailable" at startup and every `perception_enrichment` says
`attention_model: "rules"`. Note for Adil in WORKLOG_A2.

**Why I didn't `pip install "transformers>=4.45,<5"` into the shared venv** (A2_ON_KORI_LAPTOP.md step 1):

| transformers | installs into the shared venv as it is? | imports with torch 2.5.1+cu121 / hub 1.33.0 / tokenizers 0.23.2? |
|---|---|---|
| 4.57.6 (A2's `<5`) | **no**: downgrades `huggingface_hub` 1.33.0 -> 0.36.2 and `tokenizers` 0.23.2 -> 0.22.2 (faster-whisper loads through both) | n/a |
| 5.0.0 to 5.14.1 | no: needs `tokenizers <= 0.23.0` | ImportError |
| **5.17.0, 5.18.0** | **yes**, adds only regex, safetensors (+ typer for the CLI) | **yes**: `pipeline`, `AutoModelForSequenceClassification`, `AutoModelForAudioClassification` import |
| 5.19.0 (latest) | pip says yes (transformers declares no torch pin) | **no**: needs `torch.accelerator` (torch >= 2.6) |

So the candidate for the shared venv is **`transformers==5.18.0`** (nothing shifts). It must be pinned: an unpinned `>=5`
picks 5.19 and breaks on import. JEV-9B needs `>=5`, so 5.18 could serve both tiers. That's for Kori + Adil, plus golden after.
For the benches I use private overlays outside the repo (`PYTHONPATH`): `../.overlay-tf4` (4.57.6 + hub 0.36.2 +
tokenizers 0.22.2, A2's versions) and `../.overlay-tf5` (5.18.0 on the venv's own hub/tokenizers). The shared venv is untouched
(hub 1.33.0, tokenizers 0.23.2 checked after). A2's `test_enrich` + `test_sound_ai`: 46 passed with none, tf4 and tf5 (they use
fakes, so this doesn't tell the versions apart; the GPU comparison is job 3 below).

**Failure drills (`tools/drills.py`)**: the real laptop on `fixtures/mock_scene`, a private relay, one dependency broken
(in-process: non-loopback DNS/connect refused, config attributes patched, a 2 s attention tier), compared with a clean run.
`python tools/drills.py [names]`, `--list`. Logs in `logs/drills/`.

| Drill | Broken | Observed | Events vs clean run |
|---|---|---|---|
| baseline | nothing | 8 finals, doorbell `sound_event` (`panns_cnn14+enrolled`), alerts doorbell + name_called, 9 enrichments `rules`, 0 invalid | n/a |
| hf_down | every non-loopback connection (`[drill] network off: ... huggingface.co`) | whisper and PANNs load from disk; same output | **unchanged** |
| hf_sound_model_down | `sounds.model` = AST (HF) with the network off | `[sounds] MIT/ast-... failed to load (...); falling back to panns_cnn14` | **unchanged** |
| gemini_down | `GEMINI_API_KEY` set, network off | `Gemini on (gemini-2.5-flash)`; calls fail quietly; no `context` | **unchanged** |
| attention_slow | a tier that answers in 2 s (timeout 0.3 s) ahead of the others | tiers `['drill/slow-tier', 'rules']`; every enrichment `attention_model: "rules"` | **unchanged** |
| attention_unloaded, relay_restart, airplane | | run after the bakeoff (GPU) | |

**Judge reset with the real array (K3 E)**, slot from K1 (array free, K1's laptop stopped; GPU otherwise idle), ports
18080/18780/18790, `demo.py up --source real`:

| Reset | Runs | Time | Budget 30 s |
|---|---|---|---|
| light (`demo-reset`: relay + dashboard + memory restart, the laptop stays warm and reconnects itself) | 3 | **17.8 / 18.6 / 17.7 s** | OK |
| `--full` (everything, the models reload) | 1 | 49.9 s by `demo.py`; the laptop is connected at **54.1 s** | **OVER**: never between judges |

After the full reset `demo-check` was all good: laptop `status ok, engine faster_whisper, doa builtin` (models warm),
`attention tier: running laptop loaded ['rules']: RULES ONLY`, memory `db local`. `demo-down` reset the array ("array reset"),
and I told K1 "array back".
**Harness bug found and fixed:** `up` / `reset --full` with a laptop source said "source NOT UP" although the laptop
came up. The wait was 15 s and model load takes about 35 s. Laptop sources now get `LAPTOP_START_TIMEOUT_S = 90`.
`--source replay` timing comes after the GPU bakeoff.

## Integration risks / open
- **K2 + A3 seed mismatch (K2 to fix):** `extras/dashboard/fallback.py` `_load_json` takes each file from
  `extras/memory/seed/<name>` if it exists, otherwise from its own `demo_data`. A3 ships `seed/insights.json` at the top level
  but `day.json` / `ask.json` under `seed/api/`. So once both are merged, the dashboard pairs A3's insights with
  K2's day, and `test_seeded_answers_and_insights_point_at_real_events` fails (`rear_hazards`). Suggested fix in
  K2: read `seed/api/` first and take day, insights and ask from one folder as a set. A3's own seed is consistent
  (0 missing evidence ids).
- **K1 Proposed:** `sources[].spread_deg` in `contracts/CHANGES.md`; waiting on Kori.
- A3 reviewed (round 5): §5.3 holds; blocked only on K2's seed-set fix.
- A1 is on main: golden 7/7 there. K1, A2 and K1+A2 on main: 7/7 (round 4). Scribe on the real API is waiting for a key.

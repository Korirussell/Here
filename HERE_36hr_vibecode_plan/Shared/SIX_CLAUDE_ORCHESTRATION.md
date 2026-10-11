# Six-Claude orchestration

Three Claude instances each: Kori runs K1-K3 on the demo laptop (it has the ReSpeaker), and Adil runs A1-A3 on
his machine. iOS is a separate team.

## iOS is a separate team

No worker edits `ios/`. Anything that changes what the phone receives or how it connects gets a row in
`IOS_INTEGRATION.md` (repo root) in the same commit. That file is the iOS team's integration list.

## Workers

| Worker | Job | Branch | Worktree |
|---|---|---|---|
| **K1** | ReSpeaker + event engine + scripted scenes | `kori/k1-respeaker` | `../shh-k1` |
| **K2** | Dashboard "HERE World" (LIVE / MEMORY / REPLAY LAB) | `kori/k2-dashboard` | `../shh-k2` |
| **K3** | Integration: contract, end-to-end tests, latency, failure drills, demo mode, merges | `kori/k3-integration` | `../shh-k3` |
| **A1** | Speech: ElevenLabs Scribe Realtime, whisper fallback, utterances, name called | `adil/a1-speech` | `../shh-a1` |
| **A2** | Sound AI: Hugging Face classifier + attention model, `unknown`, Gemini | `adil/a2-sound-ai` | `../shh-a2` |
| **A3** | Cloud: Vultr relay, memory service, Neon, synthetic day, Ask HERE, Databricks, replay backend | `adil/a3-cloud` | `../shh-a3` |

## Setup (once per machine, from the repo root, on an up-to-date `main`)

Kori (PowerShell, demo laptop):
```powershell
git checkout main; git pull
git worktree add ..\shh-k1 -b kori/k1-respeaker
git worktree add ..\shh-k2 -b kori/k2-dashboard
git worktree add ..\shh-k3 -b kori/k3-integration
```
Adil:
```powershell
git checkout main; git pull
git worktree add ..\shh-a1 -b adil/a1-speech
git worktree add ..\shh-a2 -b adil/a2-sound-ai
git worktree add ..\shh-a3 -b adil/a3-cloud
```

**Each worktree needs its own Python environment.**
- Simplest: point it at the main checkout's venv. Run `..\shh\.venv\Scripts\python.exe` from inside the worktree.
- Or create a fresh `.venv` per worktree and `pip install -r laptop/requirements.txt`.
- Model downloads (whisper, PANNs in `~/panns_data`, Hugging Face cache) are shared per user, so they download once.
- **`.env` files are not in git.** Copy them into each worktree that needs them.

Then open one Claude Code session in each worktree and paste its kickoff line (`KICKOFF.md`).

## File ownership (one owner per high-conflict area)

| Path | Owner | Others |
|---|---|---|
| `contracts/`, `IOS_INTEGRATION.md`, `tests/`, `tasks.ps1`, `Makefile` | **K3** | Propose in `contracts/CHANGES.md` |
| `laptop/audio/`, `laptop/app.py`, `laptop/__main__.py`, `laptop/client/`, `laptop/viz.*`, `laptop/dashboard.py`, `laptop/events/` (new), `fixtures/events/` (new), `tools/fake_events.py`, `tools/doa_*` | **K1** | A1/A2 may add a few lines of wiring in `app.py` / `__main__.py` for a new provider, noted in their WORKLOG |
| `extras/dashboard/` | **K2** | |
| `laptop/speech/` | **A1** | |
| `laptop/sounds/`, `laptop/alerts.py`, `laptop/enrich/` (new) | **A2** | |
| `relay/`, `extras/memory/`, `extras/cloud/` (new) | **A3** | K3 reviews any change to relay routing |
| `laptop/config.yaml` | **by section** | K1: `audio`, `viz`, `mock`, `relay`. A1: `speech`, `names`. A2: `sounds`, `alerts`, `enrich`. Only add keys in your own section. |
| `README.md`, `CHANGELOG.md` | anyone appends | README §12 only via K3 |

If a task needs a file someone else owns, prefer adding a new module and a small hook. Otherwise, ask that owner,
through the humans.

## Merge rhythm

- Workers commit small, push to **their own branch**, and pull `main` into it often (`git merge origin/main`).
- **Never** push to `main` directly. Never force-push.
- **Gate before anything reaches `main`:**
  - `.\tasks.ps1 test` passes;
  - `python tools/stress_test.py --golden` passes if anything in speech, sounds, segmentation or audio changed.
- **Adil's machine has no NVIDIA GPU,** so whisper falls back to the CPU `base.en` model and golden's WER numbers aren't meaningful there.
  - Adil's workers run the unit tests and their own CPU checks.
  - **Golden for Adil's branches runs on Kori's laptop** before the merge (K3 does it: `git fetch`, check out the branch in the K3 worktree, run golden, report).
- **Who merges:** each human merges their own workers' branches into `main` once they're green. Kori's K3 worker can do Kori's merges.
- **What to merge:** small vertical slices that work. In order:
  1. fixtures and contract compatibility;
  2. the event producer;
  3. providers;
  4. consumers (dashboard);
  5. cloud extras;
  6. polish.

  Don't wait for whole branches to be "done".

## Hardware rules (Kori's laptop)

- **Only K1 runs `--real`.** Only one process may hold the array's control interface.
- After a killed run: `python laptop/audio/xvf3800.py --reset`.
- Run audio and USB commands unsandboxed.
- K2 and K3 work from fixtures and the relay, unless K1 has stopped its live run.

## Shared fixtures (the decoupling mechanism)

K1 builds `fixtures/events/*.jsonl` early. Each line is one canonical message, with `t` relative to the start of
the scene, played through the relay by `tools/fake_events.py --scene <name>`. The scenes:
- `front_speech`
- `rear_speech`
- `doorbell_rear`
- `name_called_rear`
- `unknown_sound`
- `two_speakers`
- `crowd_noise`

A3 builds `extras/memory/seed/synthetic_day.jsonl` using the same shapes.

Audio fixtures (`fixtures/mock_scene`, `fixtures/array_tts`, `fixtures/rot_*`, `fixtures/sounds/`) let Adil's
workers run the real pipeline with `python -m laptop --replay fixtures/<name>`, without the array.

## Secrets (never in git; each `.env` stays on the machine that needs it)

| Variable | File | Used by |
|---|---|---|
| `HERE_RELAY_URL`, `HERE_SESSION`, `HERE_RELAY_TOKEN` | `laptop/.env` | laptop, tools |
| `HERE_RELAY_TOKEN` | `relay/.env` (server) | relay |
| `ELEVENLABS_API_KEY` | `laptop/.env` | A1 |
| `GEMINI_API_KEY` | `laptop/.env` | A2 (and A3 if Ask HERE phrases answers with Gemini: `extras/memory/.env`) |
| `HF_TOKEN` (only if a gated model needs it) | `laptop/.env` | A2 |
| `NEON_DATABASE_URL` | `extras/memory/.env` | A3 |
| `HERE_RELAY_URL`, `HERE_SESSION`, `HERE_RELAY_TOKEN` | `extras/memory/.env` | A3 (memory service joins the relay as role `memory`) |
| `HERE_LOCATION`, `HERE_RETENTION_DAYS`, `HERE_MEMORY_SQLITE`, `HERE_REPLAY_MODEL`, `HERE_REPLAY_MODEL_THREADS`, `HERE_NEON_TRANSPORT` (not secret, optional tunables) | `extras/memory/.env` | A3 |
| `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `DATABRICKS_JOB_ID` (optional) | `extras/cloud/.env` | A3 |
| `VULTR_S3_ENDPOINT`, `VULTR_S3_BUCKET`, `VULTR_S3_ACCESS_KEY`, `VULTR_S3_SECRET_KEY` (optional: session bundles to Object Storage) | `extras/cloud/.env` | A3 |
| `HERE_RELAY_LOG`, `HERE_RELAY_HEALTH` (optional, not secret: where `/api/status` reads the relay) | `extras/memory/.env` | A3 |

If you add a variable, add it here and to the matching `.env.example`.

## Ports

| Service | Address |
|---|---|
| Relay (local) | `:8080` |
| 3D sound view (`--viz`) | `:8770` |
| Dashboard | `:8780` |
| Memory API (local) | `:8790` |

On Vultr, Caddy serves `/ws` and `/health` (relay) and `/api` (memory).

## Escalation

A worker may ask for a contract change only when:
- its feature can't be built cleanly with the current ids, timestamps and types;
- it documents exactly what the consumer gains;
- Kori approves.

Otherwise it adapts locally.

## Demo fallbacks

| If this is missing | Fall back to |
|---|---|
| internet | local relay + fixtures, or real local processing |
| ElevenLabs | whisper |
| Hugging Face model | PANNs / unknown |
| Gemini | no enrichment |
| attention model | deterministic rules |
| Neon | local seeded JSON |
| Databricks | precomputed insights |
| belt | on-screen indicator (iOS) |
| real sound | scripted scenes |

A fallback is not failure. A dead demo is failure.

## Final rehearsal order

1. Reset all services and the array.
2. Calibrate.
3. Front caption.
4. Rear speaker, turn-around and belt.
5. Doorbell / name called.
6. Inspect that event in the dashboard (who produced which field).
7. Switch to MEMORY and ask the polished question.
8. Show a pattern insight.
9. Replay Lab counterfactual.
10. Explain the sponsor architecture, only after the product wow.

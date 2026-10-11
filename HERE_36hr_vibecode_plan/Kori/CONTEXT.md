# Kori's lane: context

## Mission

Kori owns **the sensor and the story**. That means:
- the ReSpeaker and the event engine that turns real sound into canonical HERE events;
- the dashboard that shows judges what the machine understands;
- the integration that makes all of it, including Adil's lane and the separate iOS team's app, work as one demo, every time.

Kori is also the **contract owner**. Every change to `contracts/` is Kori's call.

The ReSpeaker is plugged into Kori's laptop, which is also the demo laptop. Anything that needs the real array
happens in this lane.

## What this lane owns

**K1: ReSpeaker + event engine.**
- Array capture, built-in DoA / fixed beams, SRP tracks, the LED ring and the 3D view (`laptop/audio/`, `laptop/viz.*`).
- Source state: stable ids, smoothing without lag, expiry, two sources at once, overlap, confidence.
- Onset candidates for A2 (`Shared/INTEGRATION_CONTRACT.md` §5.1).
- `sources` publishing, `seq`, stage timings for capture and doa.
- Laptop wiring and the relay client (`laptop/app.py`, `laptop/client/`).
- Scripted event scenes (`fixtures/events/`, `tools/fake_events.py`).
- Live tuning on the real array.

**K2: dashboard, "HERE World"** (`extras/dashboard/`).
- LIVE world view, event inspector, latency panel, provider health.
- MEMORY timeline and Ask HERE.
- REPLAY LAB.
- It consumes the relay (role `dashboard`) and A3's memory API, with seeded JSON fallbacks.

**K3: integration.**
- `contracts/` changes (approved by Kori), `contracts/CHANGES.md`, `IOS_INTEGRATION.md`.
- End-to-end tests (`tests/`), latency measurement, failure drills.
- Demo mode: one-button scenes, reset.
- Merging green branches into `main`, the pre-demo checklist, and submission and pitch material.

## What this lane does NOT own

- `ios/`: a separate team. We log what they need in `IOS_INTEGRATION.md`.
- Transcription providers and utterance assembly (A1, `laptop/speech/`).
- Sound classification, the attention model and Gemini (A2, `laptop/sounds/`, `laptop/enrich/`).
- Relay deployment, Neon, Databricks and the memory service (A3, `relay/`, `extras/memory/`, `extras/cloud/`).

Kori's lane defines what it needs from those (the interfaces in `Shared/INTEGRATION_CONTRACT.md` §5) and doesn't rewrite them.

---

## What exists today (don't rebuild it)

- **The laptop runs on the real XVF3800** (firmware 2.1.1, 6 channels: 2 processed beams + 4 raw mics). Fixed beams at world 0 / 180 caption a talker in front through a baby crying behind.
  - Golden gate: `python tools/stress_test.py --golden`, 7/7.
  - Tests: `.\tasks.ps1 test`, 206 pass.
- **Direction:** built-in DoA is the demo default. SRP-PHAT on the raw mics drives the LED ring; it is right 88-95% of the time once vetoed by the free-running beam.
  - ODAS is opt-in (`--odas`). It tracks two sources better (60-69% vs 11-46%) but failed 4 of 5 demo gates; `laptop/audio/DOA_VS_ODAS.md` has the details. **Don't revisit ODAS unless it fixes a visible demo failure.**
- **3D view:** `python -m laptop --real --viz` serves a three.js hemisphere heat map, our tracks, ODAS tracks and the LEDs on `127.0.0.1:8770`. It's a starting point for the dashboard's LIVE orb.
- **Laptop client:** sends contract messages with `seq`. `utterance.stage_ms` exists.
- **Relay:** done and tested locally (routing by subscription, latest `config` replay, drops stale data, `/health`). Not deployed yet; that's A3's job.
- **Tools:** `tools/fake_events.py` (scripted core moments through the relay), `relay_tap.py --validate`, `replay.py`, `run_report.py`, `check_gpu.py`.
- **Contract v3** includes, since 2026-10-10: `car_horn`, `vehicle`, `siren`, `door` and `unknown` labels, and optional `sound_event.source_id` and `model`.

---

## Product principle

> HERE gives spatial understanding of sound, in the moment and across time.

LIVE answers: what happened, where, who, and what deserves my attention?
MEMORY answers: what happened today, what did I miss, what patterns exist, and what would my day have looked like under different attention settings?

## Demo priorities

| Tier | |
|---|---|
| **0: must work** | Laptop → relay → phone live messages, visible direction, streaming captions, a rear-origin event, scripted fallback |
| **1: grand-prize polish** | Two sources at once, fast and correct direction, a beautiful LIVE world view, an event inspector, obvious fallback states, one-button demo |
| **2: wow** | Synthetic-day timeline, Ask HERE, routine insights, Replay Lab counterfactuals, inspectable attention decisions |
| **3: sponsor breadth** | Vultr roles, Neon, Databricks, Hugging Face (sounds + attention), Gemini, all visible in the inspector |

**Never sacrifice Tier 0 for Tier 3.**

## Spatial assumptions

- The ReSpeaker sits fixed on the desk. The wearer stays within about 0.5 m and may rotate.
- We need the right direction to turn and a best guess for visual attachment. We don't need acoustic range.
- Calibration is allowed and encouraged.
- Deterministic geometry comes first.

## Unknown sounds

> Uncertainty reduces specificity; it doesn't erase reality.

An `unknown` keeps its direction. How prominent it is depends on rules (or the attention model); it is never hidden.

## Sponsor positioning (what the dashboard and pitch make legible)

| Sponsor | Role |
|---|---|
| ReSpeaker | The real sensor; direction is local and deterministic |
| ElevenLabs | Primary realtime captions; whisper is the fallback |
| Hugging Face | Open models: the sound classifier (`sound_event.model`) and the attention model (`perception_enrichment.attention_model`), `Shared/ATTENTION.md` |
| Gemini | Semantic context, never required for awareness |
| Vultr | The cloud nervous system, not the spinal reflex: relay, memory API, event ingest |
| Neon | Persistent memory: sessions, events, transcripts, insights |
| Databricks | Historical patterns: routines, sound density, interaction partners, replay metrics |

The best way to show this: when you inspect an event, show which provider produced which field. Avoid a wall of logos.

## Integrator discipline

- Protect `contracts/`. Keep one owner per high-conflict file. Prefer adding modules over rewriting.
- Fixtures first: every downstream feature can run from fixture events.
- Merge working vertical slices, not giant speculative branches.
- Never delete a known-working path until its replacement is proven.

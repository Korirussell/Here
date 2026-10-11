# Kori: TASKS

Three workers: **K1** (ReSpeaker + event engine), **K2** (dashboard), **K3** (integration). Kori is the human
integrator and contract owner.

## Rules for every Kori worker

- **Don't edit `ios/`.** Anything that changes what the phone receives or how it connects gets a row in `IOS_INTEGRATION.md`.
- **Don't edit Adil's areas:** `laptop/speech/`, `laptop/sounds/`, `laptop/alerts.py`, `laptop/enrich/`, `relay/`, `extras/memory/`, `extras/cloud/`. Define the interface you need and ask.
- **No silent contract changes.** Propose in `contracts/CHANGES.md`; K3 implements once Kori approves.
- **Nothing cloud-side on the live reaction path.**
- **Everything runs from fixtures.** Only K1 uses the real array, and only one process may hold it at a time.
- **Keep `WORKLOG_<worker>.md` up to date** in the plan folder: files changed, assumptions, TODOs, run commands, integration risks.
- **Commit small** to your own branch. Gate: `.\tasks.ps1 test`, plus golden for anything audio, speech or sounds. Never push to `main`; never force-push.
- **Never delete a known-working path** until its replacement is proven.

---

# K1: ReSpeaker + event engine

**Goal:** real sound becomes fast, stable canonical events, and fixture mode produces exactly the same downstream
messages as the real array.

## A. Keep the baseline
- [ ] Before refactoring anything: `.\tasks.ps1 test` is green, golden is 7/7, and a 2-minute `--real` smoke run works (`python -m laptop --real --offline --dashboard`).
- [ ] Freeze the known-good config in your WORKLOG (fixed beams 0/180, `doa_source: builtin`).

## B. Source state (`laptop/audio/`, new `laptop/events/` if useful)
- [ ] Stable integer source ids across a talker's turn. Beams mode uses ids 0 and 1; keep them stable through brief silences.
- [ ] Smoothing that doesn't lag: a new talker shows up within one or two `sources` frames.
- [ ] Expire sources; set an overlap flag when two are active.
- [ ] Propagate confidence. Add a bearing-spread estimate if it's cheap.
- [ ] `sources` at 10-30 Hz only while something is active; on change otherwise.

## C. Onset candidates for A2 (`Shared/INTEGRATION_CONTRACT.md` §5.1)
- [ ] Publish `(t, bearing_deg, energy, source_id)` when a non-speech track appears or its energy jumps, with debounce and cooldown.
- [ ] Give A2 a hook to subscribe to it. Document it in `laptop/CLAUDE.md`.
- [ ] Unit-test it with fixture audio: a doorbell gives one candidate, steady fan noise gives none.

## D. Instrumentation
- [ ] Stage timings for `capture`, `doa`, `serialize` and `socket_send`, on the messages where the contract allows (`stage_ms`) and in `logs/laptop.jsonl`.
- [ ] A rolling latency summary, dropped/suppressed counts and queue depth in the terminal dashboard (`--dashboard`).

## E. Scripted scenes (unblocks K2, K3, A3 and the iOS team)
- [ ] `fixtures/events/*.jsonl`: one canonical message per line, `t` relative to the scene start. Scenes:
  - `front_speech`
  - `rear_speech`
  - `doorbell_rear`
  - `name_called_rear`
  - `unknown_sound`
  - `two_speakers`
  - `crowd_noise`
- [ ] Record them from the real pipeline where possible (`logs/laptop.jsonl` → JSONL). Hand-write the rest.
- [ ] `tools/fake_events.py --scene <name> [--loop]` plays a scene through the relay. Keep the existing `--moment` behaviour.
- [ ] Every line passes `python tools/relay_tap.py --validate`.

## F. Live tuning on the real array (only when C-E are merged)
- [ ] Check direction at 0 / 90 / 180 / -90 with a person, not just the laptop speaker. Record the results in `laptop/audio/SPIKE.md`.
- [ ] Two talkers at once: both get a source and a caption.
- [ ] By-eye LED check (`python -m laptop.audio.ring --sweep`); calibrate `ring_led0_deg`.
- [ ] A real doorbell sound enrolled from the demo speaker (`python -m laptop.sounds.enroll doorbell`). Coordinate with A2: the file is A2's, the recording is yours.

**Done when:** Kori, the iOS team and Adil's cloud can build everything from `fake_events --scene`, and switching
to `--real` needs no downstream change.

---

# K2: dashboard, "HERE World" (`extras/dashboard/`)

**Goal:** the screen judges stare at while the iPhone shows the wearer's view. It shows the machine's understanding of
the room right now, and of the user's day.

**Build:**
- Plain web page plus a tiny Python server (stdlib or `websockets`) on `127.0.0.1:8780`.
- Live data comes from the relay as role `dashboard` (it gets every message type).
- Memory data comes from A3's API (`Shared/INTEGRATION_CONTRACT.md` §5.3), falling back to `extras/memory/seed/` JSON.
- Types: `contracts/ts/contract.ts`. The 3D orb can reuse ideas from `laptop/viz.html` (three.js 0.160.0 via an importmap, a world frame where the wearer looks along -Z).

**Develop against `fake_events --scene` and seeded JSON first. Don't wait for the real backends.**

## LIVE WORLD
- [ ] A 3D or top-down orb: active sources as rays, speech and non-speech visually distinct, labels anchored to their bearings.
- [ ] Latest captions (who, tone), active alerts, `unknown` shown with its direction.
- [ ] A connection and provider health strip, from heartbeats: relay, `engine`, `doa_source`.
- [ ] Latency panel: `stage_ms`, plus send-to-receive lag.
- [ ] Event stream / timeline with capped history.

## Event inspector (highest value: do it early)
Click any event to see:
- its raw source and bearing;
- its label, confidence and `model`;
- the caption `engine`;
- the related `utt_id` / `event_id` messages;
- stage timings;
- once enrichment exists, the attention decision (label probabilities, model id, or "rules") and Gemini context.

Show **which provider produced which field**: direction from ReSpeaker, caption from ElevenLabs or whisper, class
from Hugging Face, attention from a Hugging Face model, context from Gemini, storage in Neon, trend from Databricks, transport via Vultr.

## MEMORY
- [ ] Full-day timeline with location segments, recurring people, a noise summary and important-event cards.
- [ ] An Ask HERE box: answer plus clickable evidence events.
- [ ] Routine insights and "things you may have missed".

## REPLAY LAB
- [ ] Pick a segment. Sliders: sensitivity, crowd filtering, speech priority, hazard priority, focus mode.
- [ ] Actual vs alternative surfaced events, animated counts, important alerts kept under high focus, a per-event explanation.

## Demo mode and performance
- [ ] Keys or buttons for:
  - start a scripted LIVE scene;
  - open the seeded day;
  - load a known Ask answer;
  - reset;
  - a hidden panic/reset.
- [ ] Works offline: local fixtures, no CDN dependency at the booth (vendor three.js into `extras/dashboard/`).
- [ ] Cap the DOM and history. No memory leaks over a 20-minute expo session. Never display secrets.

**Done when:** a judge can tell, without explanation, where each sound is, what HERE thinks it is, who's speaking,
what HERE chose to surface, what it remembers, and what patterns it found.

---

# K3: integration

**Goal:** six workers' output becomes one demo that works every time. K3 owns the glue, the contract mechanics and the merges.

## A. Contract mechanics
- [ ] Watch `contracts/CHANGES.md` for "Proposed" entries. Bring them to Kori, then implement the approved ones the standard way (contracts/CLAUDE.md), with an `IOS_INTEGRATION.md` row in the same commit.
- [x] `perception_enrichment` decided and in the contract (2026-10-10): on the wire to phone, memory and dashboard. Record: `Shared/ATTENTION.md`.
- [ ] **Golden for Adil's branches:** his machine has no NVIDIA GPU. Before each of his merges, check out the branch in your worktree, run `python tools/stress_test.py --golden` (and `tools/sound_bench.py` / `python -m laptop.enrich.attention --bench` when A2 asks: the four JEV-9B gates in `Shared/ATTENTION.md`), and report the numbers in his WORKLOG and yours.
- [ ] Fix the known drift listed in `contracts/CHANGES.md` as it comes up.

## B. End-to-end tests (`tests/`)
- [ ] Each `fixtures/events` scene goes through the local relay to a fake phone and a fake dashboard client, validated against the schemas.
- [ ] The memory role receives `utterance`, `alert` and `sound_event` (A3's ingest path) without the laptop knowing.
- [ ] `.\tasks.ps1 test` runs everything.

## C. Latency
- [ ] End-to-end measurement: laptop observation `t` → relay → a client receive timestamp, through the local relay and (once A3 deploys) Vultr from campus Wi-Fi and a hotspot. Use `tools/run_report.py` and extend it if needed.
- [ ] Put the numbers in the WORKLOG and the pitch notes. Target ≤ 250 ms sound → phone.

## D. Failure drills (scripted, repeatable)
For each one, write down the command and the observed fallback:
- [ ] ElevenLabs down → whisper (`heartbeat.engine` changes);
- [ ] Hugging Face down → PANNs / unknown;
- [ ] Gemini down or the attention model slow / unloaded → events unchanged, `attention_model: "rules"`;
- [ ] Vultr down → local relay;
- [ ] Neon down → seeded JSON;
- [ ] reconnect during the demo;
- [ ] internet off (airplane test).

## E. Demo harness
- [ ] One command brings up the demo stack (local relay, laptop `--real` or `--replay`, dashboard) and one resets it. A PowerShell script in `tasks.ps1`.
- [ ] Pre-demo checklist script: extend `tools/check_gpu.py` with the relay, token, array, models warm, dashboard up and memory API up.
- [ ] Reset between judge groups in under 30 s.

## F. Merges and docs
- [ ] Merge Kori's green branches into `main`. Help Adil merge his when asked. Gate: tests plus golden.
- [ ] Keep README §11/§12/§17/§18 and the briefs (`*/CLAUDE.md`) honest about status. CHANGELOG entry per merge.
- [ ] Submission write-up and a one-page sponsor architecture diagram (after the product works).

**Done when:** the full rehearsal order (`Shared/SIX_CLAUDE_ORCHESTRATION.md`) runs three times in a row, and every
failure drill has a tested fallback.

---

# Kori's own checklist (the human)

| Gate | Checks |
|---|---|
| **1** | Scripted scenes frozen (K1 E). K2, K3, A3 and the iOS team all develop from them. |
| **2** | Real array → relay → phone (iOS team) and → dashboard. Adil's speech and sound providers live on the array. Memory ingest running independently. |
| **3** | Calibration plus real physical directions with people; two talkers; scripted demo end to end. |
| **4** | Seeded memory, Ask HERE, Replay Lab, sponsor inspector. |
| **Final** | Failure drills, 20-minute soak, reset between judges, every credential and env var documented, no secret on screen. |

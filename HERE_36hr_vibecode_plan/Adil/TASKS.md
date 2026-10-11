# Adil: TASKS

Three workers: **A1** (speech), **A2** (sound AI), **A3** (cloud). Adil integrates his lane and merges his green
branches into `main`.

## Rules for every Adil worker

- **Don't edit `ios/`, `laptop/audio/` or `extras/dashboard/`.** Anything that changes what the phone receives gets a row in `IOS_INTEGRATION.md`.
- **Don't compensate for the phone's heading.**
- **Keep the known-good path working** (whisper, PANNs, the local relay) until its replacement is proven.
- **Sponsor APIs are never blocking dependencies.** Every provider has a fake or stub and a fallback.
- **No unbounded queues.** Expensive AI runs on events only. Log stage timings.
- **Keep ids stable** (`utt_id`, `event_id`, `source_id`) so enrichment can attach later.
- **No silent contract changes.** Propose in `contracts/CHANGES.md`; Kori approves.
- **No ReSpeaker and no NVIDIA GPU here.** Develop with `python -m laptop --replay fixtures/<name>` (whisper runs CPU `base.en`) and the unit tests. GPU latency, golden and the final array checks run on Kori's laptop; K3 runs golden on your branch before it merges.
- **Keep `WORKLOG_<worker>.md` up to date** in the plan folder.
- **Commit small** to your own branch. Gate: `.\tasks.ps1 test`, plus golden for speech or sounds changes. Never push to `main`; never force-push.

---

# A1: speech (ElevenLabs Scribe + whisper)

**Goal:** speech onset gives an immediate direction cue, and captions stream as soon as they exist. ElevenLabs is
truthfully "primary when working", and pulling its plug doesn't kill captions.

## A. Baseline
- [x] `.\tasks.ps1 test` green on your machine (CPU). Golden runs on Kori's laptop. Ask K3 for a baseline run of `main` so you have numbers to compare against.
- [x] Measure Scribe's first-partial and final latency on `fixtures/array_tts` (`python -m laptop --replay ... --offline`); network-bound, so your machine is representative. Whisper's GPU numbers come from Kori's laptop.

## B. Provider interface (`laptop/speech/transcriber.py`)
- [x] One interface for all providers: start a session, feed audio, partial, committed/final, error, shutdown.
- [x] Providers: `ScribeRealtimeTranscriber`, `WhisperTranscriber`, and a deterministic `FakeTranscriber` for tests.

## C. ElevenLabs Scribe Realtime
- [x] Finish the stub against the current Scribe Realtime API. Read the official docs; key from `ELEVENLABS_API_KEY` in `laptop/.env`.
- [x] Streaming partials map to `pending`; committed text maps to `text`; finals map to `final: true`.
- [x] Reconnect behaviour; timeout; timing metrics in `stage_ms`; `engine: "scribe_realtime"`.
- [x] **Failure injection:** no key, bad key, network drop mid-utterance, slow responses. Each falls back to whisper without a restart, and the heartbeat `engine` and `detail` say so.
- [x] Never delay `speech_start` waiting on the remote transcript.

## D. Whisper fallback
- [x] Keep the current implementation working. Warm the model at startup; verify CUDA is used.
- [x] Fallback activates mid-session; switch back when Scribe recovers, if practical.

## E. Utterance assembly
- [x] Stable `utt_id`, `source_id` and bearing. Provisional `pending` and committed `text`. `final` exactly once.
- [x] `overlap`, `loudness`, `tone`, `engine`.
- [x] A late final never changes the next utterance. No duplicate finals across the two beams.

## F. Name called
- [x] User name and aliases come from `config` (phone) or `config.yaml` `names`. Fuzzy matching on partials and finals.
- [x] One alert per call (no spam), with the speaker's bearing. Demonstrate the rear name-call on a fixture.

## G. Tests
- Scribe normal, unavailable, and connection drop.
- Whisper fallback.
- Rapid speaker switch; overlap.
- Name alias; empty or garbage partial.

**Done when:** captions run on Scribe with `engine: scribe_realtime`, unplugging the network switches to whisper
within a second or two with no lost utterance, and golden stays 7/7.

---

# A2: sound AI (Hugging Face classifier + attention model + unknown + Gemini)

**Goal:** a judge triggers real sounds and sees an immediate direction, a meaningful open-source classification,
honest uncertainty, and attention / Gemini enrichment. No cloud model is ever needed for basic awareness.

## A. Hugging Face bakeoff
- [ ] Candidates: audio classification models on the Hugging Face Hub, for example AST fine-tuned on AudioSet, BEATs, CLAP zero-shot, or a YAMNet port. Check licences.
- [ ] Benchmark on **our** sounds: `fixtures/sounds/`, clips cut from the audio fixtures, and short recordings of doorbell, knock / bang, door, car horn, siren, baby, speech and ambient noise.
- [ ] Measure per-class accuracy, warmup, memory and confidence calibration on your CPU, and compare against current PANNs. Write `tools/sound_bench.py`, a single command that prints the table, so Kori's laptop can measure GPU latency for the finalists (K3 runs it). Put both tables in your WORKLOG. Prefer models that also run acceptably on CPU.
- [ ] Pick one model, or a very small ensemble. Document the repo id, licence and why.

## B. Wrapper and mapping (`laptop/sounds/`)
- [x] A local inference wrapper: warm at startup, GPU path, bounded queue (drop the oldest), event-triggered windows (K1's onset candidates, `Shared/INTEGRATION_CONTRACT.md` §5.1, plus the existing sliding window).
- [x] Map model labels to HERE `SoundLabel`s, in the `config.yaml` `sounds` section. Per-label thresholds.
- [x] `sound_event.model` = the Hugging Face repo id. Keep PANNs selectable and as the fallback.
- [ ] Publish the new labels (`car_horn`, `vehicle`, `siren`, `door`) once their thresholds are set on real clips. Golden must stay green.

## C. Unknown policy
- [x] Never force a known label below its threshold.
- [x] Emit `unknown` (with bearing, energy and confidence) when the event itself is meaningful: enough energy above the floor, and it lasts.
- [x] A later classification upgrades the label on the same `event_id`.
- [x] No `unknown` spam from steady ambience (fan, crowd): add a cooldown, and learn the ambience floor.

## D. Gemini enrichment (`laptop/enrich/`, async)
- [x] One narrow prompt with a strict output schema. Inputs: transcript, Hugging Face label and confidence, energy, recent event summary. **Not every frame.**
- [x] Outputs: a richer label, semantic category, directed-at-user, a one-line note.
- [x] Timeout, cache and debounce; drop on failure; log latency and cost.

## E. Attention model (`laptop/enrich/`; read `Shared/ATTENTION.md` first: the decisions are made)
- [x] **Three tiers** (`ATTENTION.md`):
  1. `autotrust/JEV-9B`, 4-bit, on Kori's laptop GPU;
  2. `MoritzLaurer/deberta-v3-base-zeroshot-v2.0` zero-shot, on CPU or GPU;
  3. deterministic rules.

  One `Decider` interface; each tier falls through to the next.
- [x] **Tier 2 first** (it runs on your CPU): the `transformers` `zero-shot-classification` pipeline, loaded once and warmed. Note the licence.
- [x] **Tier 1 (JEV-9B):** the model card's `transformers` + `peft` path (merge the adapter, apply the decision head), quantized at load time with `bitsandbytes` (NF4). No vLLM. It can't run on your CPU (18 GB, 9B parameters), so write it against the card, unit-test the prompt and probability code with a fake, and hand Kori a one-command check (`python -m laptop.enrich.attention --bench`). K3 runs the four gates in `ATTENTION.md` on Kori's laptop.
- [x] A state sentence for each event (label, confidence, energy, transcript snippet, repeats in the last minute, focus mode) → the attention labels; final transcripts → the directed-at-user hypothesis.
- [ ] Tune the label and hypothesis wording on real states from the fixtures. Compare against `facebook/bart-large-mnli` / ModernBERT zero-shot if time allows.
- [x] A worker thread, a queue of at most 4 that drops the oldest, a 5 s cache. Over 1.5 s (CPU) or 300 ms (GPU), an error, or a top probability under 0.5 → the rules table in `ATTENTION.md`, with `attention_model: "rules"`.
- [x] **It never suppresses or delays the canonical event.** No "behind the wearer" judgments on the laptop.
- [x] Tests use a fake classifier; `pytest` never downloads a model.

## F. Delivering enrichment
- [x] Send `perception_enrichment` (in the contract; `contracts/examples/perception_enrichment.json`) through the laptop's relay client, after the event, with `ref` set. It merges the attention decision with Gemini's `context` when both are ready, or sends whichever arrives first and updates later.
- [x] Log every decision (model versus rules, with label probabilities) to `logs/laptop.jsonl` for the dashboard inspector and `run_report`.

## G. Demo set (replayed here; checked live on Kori's laptop at the end)
Doorbell, knock / bang, door close, car horn, siren, speech, ambiguous sound → unknown, noisy ambience, and one deliberately weird sound.

**Done when:** each demo sound gives the right label, or an honest `unknown`, with a bearing, and `model` names the
Hugging Face model. Attention and Gemini add to it, and turning both off changes nothing else.

---

# A3: cloud (Vultr + memory + Neon + Databricks)

**Goal:** an ambitious persistent memory platform that ingests real events and can fully demo from a seeded day,
without ever being needed for LIVE.

## A. Vultr relay
- [ ] Deploy the relay with `relay/DEPLOY.md` (Atlanta, cheapest plan, Caddy TLS, systemd, ufw). Choose the hostname (domain or sslip.io).
- [ ] Check `/health`, TLS, token rejection and session isolation from outside.
- [ ] Hand the URL and token to Kori **out of band** (never in git). Write the hostname into README §27 and `relay/DEPLOY.md`. Add an `IOS_INTEGRATION.md` row with the URL format.
- [ ] Easy log inspection. Measure the round trip from campus Wi-Fi and a hotspot with `tools/relay_tap.py`, and send the numbers to K3.
- [x] The local relay fallback still works.

## B. Event ingest (`extras/memory/`)
- [x] A memory service that connects to the relay as role **`memory`** (it already receives `utterance`, `alert`, `sound_event`, `attribution` and `search_request`). No laptop change.
- [x] Store the raw canonical payload plus session, event time, ingest time and normalized searchable fields. Batch writes only on this persistence path.
- [x] A failure here never affects the relay or the phone.

## C. Neon schema
Tables:
- `sessions`
- `events`: `payload jsonb`, plus type, `source_id`, `world_bearing`, label, confidence, `person_label`, `location_id`, `event_time`, `ingest_time`
- `utterances`
- `locations`
- `insights`

Use pgvector only if it's straightforward.

- [x] Migrations, indexes (`event_time`, type, session), a read API.
- [x] Local fallback: SQLite or JSON when `NEON_DATABASE_URL` is missing or unreachable.

## D. Synthetic day (`extras/memory/seed/synthetic_day.jsonl`, canonical shapes)
**Scenes:**
- morning / home
- commute / crosswalk
- lecture
- noisy lunch
- lab / team meeting
- hackathon / presentation
- evening

**Seed into it:**
- recurring Person 2 and Person 3;
- repeated locations;
- a professor's deadline fact;
- a car horn / siren from the rear;
- unknown events;
- name called;
- a lunch where crowd noise was suppressed;
- varied tones.

There should be enough for a timeline, a heatmap, "what happened?", "what did my professor say?", a routine insight
and a counterfactual replay.

## E. Memory API (`Shared/INTEGRATION_CONTRACT.md` §5.3)
- [x] `/api/health`, `/api/day`, `/api/ask`, `/api/insights`, `/api/replay`, on `:8790` locally and behind Caddy at `/api` on Vultr.
- [x] Also answer the contract's `search_request` with `search_result` over the relay.
- [x] Ask HERE: Postgres full-text first, embeddings if easy, Gemini to phrase the answer if available. Return the answer plus evidence events. Cache the demo queries:
  - "What did my professor say about the exam?"
  - "What happened at lunch?"
  - "When did someone call my name?"
  - "What important thing happened behind me?"
  - "Summarize my day."

## F. Databricks (`extras/cloud/`)
- [x] A reproducible notebook or job over an event export. It computes:
  - events per hour;
  - speech vs environmental sound by location;
  - unknown rate;
  - important rear-origin events;
  - top conversation partners;
  - recurring topics;
  - noise density;
  - surfaced vs suppressed events;
  - replay scenario metrics.
- [x] Export to Neon `insights`, or a static JSON tagged `source: "precomputed"`. Never block the dashboard on it.

## G. Replay backend
- [x] Inputs: a historical event stream plus a policy (sensitivity, crowd filter, focus mode, hazard and speech priority).
- [x] Returns the surfaced, suppressed and kept-important counts, a per-category breakdown and per-event decisions.
- [x] Deterministic first. Then add relevance scores from the same Hugging Face zero-shot model (CPU is fine at replay rates).

## H. Low-risk extra Vultr roles (only after A-E)
Object storage for replay bundles or session exports; server-side health/metrics; a worker service.

**Done when:**
- LIVE works with A3 entirely offline;
- a real event from the laptop lands in Neon;
- the dashboard loads the seeded day;
- Ask HERE answers the five demo queries with evidence;
- Databricks visibly contributes one set of insights;
- Vultr has several defensible roles without being a fragile dependency.

---

# Adil's own checklist (the human)

| Gate | Checks |
|---|---|
| **1** | Baselines green on your machine (tests, golden). Fixtures replay. Contract examples validate. |
| **2** | Scribe captions with whisper fallback. Hugging Face labels plus `unknown` on fixtures. Relay live on Vultr; URL and token handed to Kori. |
| **3** | Provider resilience: Scribe → whisper, Hugging Face → PANNs / unknown, Gemini off, attention model off. All tested. |
| **4** | On Kori's laptop with the real array: front speech, rear speech, doorbell, name called, unknown sound, two talkers. |
| **Handoff to Kori** | Exact run commands, env template, fixture commands, known limitations, model warmup steps, debug/log commands. |

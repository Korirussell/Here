# Adil's lane: context

## Mission

Adil owns **meaning and memory**:
- the **words** (realtime captions: ElevenLabs Scribe first, local whisper as the fallback);
- the **sounds** (an open-source Hugging Face classifier, honest `unknown`s, a Hugging Face attention model, and Gemini context);
- the **cloud** that remembers (the Vultr relay, the memory service, Neon, the synthetic day, Ask HERE, Databricks insights and the Replay Lab backend).

None of it needs the ReSpeaker. That's on Kori's laptop, and Kori's K1 worker owns it. **Adil's machine has no NVIDIA
GPU:** whisper runs the CPU `base.en` model there, and Hugging Face models run on CPU. Accuracy work happens on Adil's
machine. GPU latency and golden are measured on Kori's laptop (RTX 5000 Ada), which runs the demo. Adil's workers develop
from **recorded fixtures**, which run the real laptop pipeline without the array:
`python -m laptop --replay fixtures/<name>`. Final verification on the real array happens on Kori's laptop, together.

## What this lane owns

| Worker | Owns |
|---|---|
| **A1: speech** | `laptop/speech/`: the transcription providers (Scribe Realtime, faster-whisper, a deterministic fake), utterance assembly, `speech_start` / `utterance`, tone, name called |
| **A2: sound AI** | `laptop/sounds/`, `laptop/alerts.py`, `laptop/enrich/` (new): Hugging Face classifier bakeoff and wrapper, label mapping, `unknown` policy, publishing the new labels, the attention model, Gemini enrichment |
| **A3: cloud** | `relay/` (deploy on Vultr), `extras/memory/` (memory service and API, Neon, seed, Ask HERE, replay backend), `extras/cloud/` (new: Databricks job, other Vultr roles) |

## What this lane does NOT own

- The ReSpeaker, DoA, source ids, `sources`, onset candidates, laptop wiring (K1).
- The dashboard UI (K2): A3 serves the memory API that K2 consumes.
- `contracts/`: Kori approves changes; propose them in `contracts/CHANGES.md`.
- `ios/`: a separate team. Anything that changes what the phone receives gets a row in `IOS_INTEGRATION.md`.

---

## What exists today (don't rebuild it)

- **Speech:**
  - `laptop/speech/pipeline.py`: Silero VAD with DoA gating, `speech_start` on onset, streaming partials, circular-mean bearing, overlap.
  - `transcriber.py`:
    - `WhisperTranscriber` (faster-whisper `large-v3-turbo` on CUDA, `base.en` on CPU);
    - a **`ScribeRealtimeTranscriber` stub** reading `ELEVENLABS_API_KEY`;
    - `FallbackTranscriber`, which switches engines automatically.
  - `tone.py` (including laughter from PANNs), `names.py` (fuzzy name called).
  - Two beams caption two talkers at once (crosstalk dedup).
  - Golden gate 7/7 (`python tools/stress_test.py --golden`): WER and phantom captions under babble.
- **Sounds:**
  - `laptop/sounds/classifier.py`: PANNs CNN14 (AudioSet) on 1 s windows every 0.5 s; GPU, drop-don't-queue.
  - AudioSet → contract label map in `config.yaml` `sounds.label_map`.
  - An enrolled-template matcher for the demo doorbell; `alerts.py` handles dedupe and expiry.
  - **Since 2026-10-10:**
    - the contract has `car_horn`, `vehicle`, `siren`, `door` and `unknown`, already mapped from AudioSet;
    - they are **not yet in `sounds.publish`**;
    - nothing emits `unknown` yet;
    - `sound_event.model` is sent as `panns_cnn14`.
- **Relay:**
  - `relay/relay.py` (single file, `websockets`): token auth, routing by subscription, latest `config` replay, newest-only for position streams, a bounded outbox, `/health`, JSON-lines logs.
  - **`relay/DEPLOY.md`** is a 15-minute Vultr + Caddy + systemd guide. **Not deployed yet.**
  - Role `memory` already subscribes to `utterance`, `alert`, `sound_event`, `attribution` and `search_request`. The memory service is just a relay client.
- **Extras:** `extras/memory/README.md` and `extras/dashboard/README.md` are plans only. The contract already has `search_request` / `search_result`.
- **Fixtures with audio, for replay:**
  - `fixtures/mock_scene` (synthetic TTS + doorbell);
  - `fixtures/array_tts` (TTS recorded through the real array);
  - `fixtures/rot_*` (real raw-mic scenes, including talker + baby). **Not in git; regenerate once with `python tools/make_raw_scene.py`** (from the committed `fixtures/raw_speaker_180`);
  - `fixtures/sounds/` (doorbell).
- **Tests:** `.\tasks.ps1 test`.

---

## Core principle

**Publish useful information immediately and enrich it later:**

```text
source -> speech_start -> partial caption -> final caption -> enrichment
```

Never make direction, `speech_start` or a sound's existence wait for transcription, classification, Gemini, the attention model or the cloud.

## Critical requirements

- World bearings come from K1. Never compensate for the phone's heading on the laptop. "Behind the wearer" is a phone-side or server-side fact.
- `unknown` is a valid output: it keeps its direction, and a later message can upgrade its label.
- Expensive inference runs on events (onsets, finals), never every frame.
- **Every sponsor API has a fallback,** and failure never kills the core path.
- No unbounded queues. Log stage timings.

## Model choices

| Job | Choice |
|---|---|
| Direction | K1's deterministic built-in DoA. Not this lane. |
| Speech | ElevenLabs Scribe Realtime is primary when it works. faster-whisper is the **mandatory** fallback. |
| Sound class | A Hugging Face model, chosen by a **bakeoff on our real demo sounds**, not by prestige. PANNs stays as the fallback. Targets: doorbell, baby crying, car horn / vehicle, door, bang, alarm / siren, speech, unknown. |
| Gemini | Semantic enrichment only: richer label, directed-at-user, context note. Send it the transcript plus acoustic metadata, not raw audio. |
| Attention | Open Hugging Face models in tiers (`Shared/ATTENTION.md`): **`autotrust/JEV-9B`** (an open Jev-style decision model, 4-bit, demo laptop GPU) → `MoritzLaurer/deberta-v3-base-zeroshot-v2.0` zero-shot (CPU, your development) → rules. Attention + directed-at-user, sent as `perception_enrichment`; replay relevance in the cloud (tier 2). Hosted TypeSafe Jev is out (not a sponsor). |

## Cloud philosophy (A3)

- Vultr is the cloud nervous system, not the spinal reflex. **The live demo works with Vultr unplugged** (local relay).
- The memory layer may be **seeded**. The synthetic day uses the **same canonical event shapes** as live data.
  - Pitch it honestly: "working architecture plus live capture, plus a seeded demonstration of long-term memory".
  - Never claim the whole day was captured live.
- Use breadth only where it's cheap and robust: compute, relay, ingest, object storage for replay bundles, health.
  - No Kubernetes or load balancers for logo count.
- Databricks can be precomputed if live setup is risky. Show the job/notebook and its output.

## Demo attitude

Audio is already "good enough". Focus on:
- captions that are fast and can't die;
- sound labels that are real and honest;
- a cloud that is impressive and never in the way.

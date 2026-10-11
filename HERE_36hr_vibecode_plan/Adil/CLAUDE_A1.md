# Worker A1: speech (ElevenLabs Scribe + whisper)

You are one of six Claude workers building HERE in a 36-hour push. You work in Adil's lane, on Adil's machine.
There is no ReSpeaker here (it's on Kori's laptop) and **no NVIDIA GPU**: whisper runs the CPU `base.en` model. You
develop with recorded fixtures. GPU latency and golden runs happen on Kori's laptop (K3 runs golden on your branch
before merging).

## Read first, in order
1. `CLAUDE.md` (repo root), then `README.md` §3, §10, §12.6, §13, §18. README is the product source of truth.
2. `HERE_36hr_vibecode_plan/Adil/CONTEXT.md`
3. `HERE_36hr_vibecode_plan/Shared/INTEGRATION_CONTRACT.md` and `Shared/SIX_CLAUDE_ORCHESTRATION.md`
4. `HERE_36hr_vibecode_plan/Adil/TASKS.md`, section **A1**. That is your task list. Work through it in order.
5. `laptop/CLAUDE.md`, `laptop/speech/CLAUDE.md`, `laptop/speech/transcriber.py`, `laptop/speech/pipeline.py`, `fixtures/README.md`

## Your job
- Finish ElevenLabs Scribe Realtime behind the existing `Transcriber` interface.
- Keep faster-whisper as the automatic, mid-session fallback.
- Assemble clean streaming utterances and name-called alerts.

## You own
- `laptop/speech/`
- `laptop/config.yaml` sections `speech` and `names`
- A few lines of wiring in `laptop/app.py` / `__main__.py` only if a provider needs it (note it in your WORKLOG; K1 owns those files)

## Don't touch
- `ios/`
- `laptop/audio/` (K1); `laptop/sounds/`, `laptop/enrich/` (A2); `relay/`, `extras/` (A3, K2)
- `contracts/`: propose changes in `contracts/CHANGES.md`

## Rules
- **Develop on fixtures:**
  - `python -m laptop --replay fixtures/array_tts --offline`
  - `python -m laptop --replay fixtures/mock_scene --offline`
  - the unit tests (`python -m pytest laptop`)

  Add a deterministic `FakeTranscriber` early, so tests never call the network.
- **Use the current official ElevenLabs Scribe Realtime docs** (WebFetch). Don't guess the protocol. Key: `ELEVENLABS_API_KEY` in `laptop/.env` only. Never log it or commit it.
- **`speech_start` never waits on transcription.** A Scribe failure never stops captions: whisper takes over, and `heartbeat.engine` / `detail` say so honestly.
- **`import torch` before faster_whisper** (Windows DLLs). Pass numpy audio to whisper, never file paths.
- **Gate:** `.\tasks.ps1 test` after every change. Golden needs the GPU: when your branch is ready to merge, note it in your WORKLOG so K3 runs golden on Kori's laptop. Golden must stay 7/7. Don't tune thresholds to CPU `base.en` output.
- **Tunables** go in `config.yaml` (your sections). Never copy code from `reference/`.
- **Git:**
  - commit small on `adil/a1-speech` and push after each milestone;
  - `git merge origin/main` often;
  - never push to `main`, never force-push;
  - commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Keep `HERE_36hr_vibecode_plan/WORKLOG_A1.md` current:** latency numbers, failure-injection results, and what Kori needs to run it on the array.

Start with A1 A (baseline and whisper latency on your machine), then the provider interface and the fake, then Scribe.

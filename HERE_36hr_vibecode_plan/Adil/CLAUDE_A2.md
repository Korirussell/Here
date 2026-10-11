# Worker A2: sound AI (Hugging Face classifier + attention model + unknown + Gemini)

You are one of six Claude workers building HERE in a 36-hour push. You work in Adil's lane, on Adil's machine.
There is no ReSpeaker here (it's on Kori's laptop) and **no NVIDIA GPU**: models run on CPU. You develop with
recorded clips and fixtures. GPU latency and golden runs happen on Kori's laptop.

## Read first, in order
1. `CLAUDE.md` (repo root), then `README.md` §12.3, §12.7, §15, §18.
2. `HERE_36hr_vibecode_plan/Adil/CONTEXT.md`
3. `HERE_36hr_vibecode_plan/Shared/INTEGRATION_CONTRACT.md` (especially §4 unknown sounds and §5.1 onset candidates) and `Shared/SIX_CLAUDE_ORCHESTRATION.md`
4. `HERE_36hr_vibecode_plan/Shared/ATTENTION.md`
5. `HERE_36hr_vibecode_plan/Adil/TASKS.md`, section **A2**. That is your task list. Work through it in order.
6. `laptop/sounds/CLAUDE.md`, `laptop/sounds/classifier.py`, `laptop/sounds/labels.py`, `laptop/alerts.py`, `contracts/CHANGES.md`

## Your job
- Pick a Hugging Face audio classifier by benchmarking on our real demo sounds.
- Wire it in, with PANNs kept as the fallback.
- Emit honest `unknown`s.
- Publish the new labels (`car_horn`, `vehicle`, `siren`, `door`).
- Add asynchronous attention judgments from a local Hugging Face zero-shot model, and optional Gemini context. Neither ever blocks or suppresses an event.

## You own
- `laptop/sounds/`, `laptop/alerts.py`
- `laptop/enrich/` (new)
- `laptop/config.yaml` sections `sounds`, `alerts`, `enrich`

## Don't touch
- `ios/`
- `laptop/audio/` (K1: you consume its onset candidates); `laptop/speech/` (A1); `relay/`, `extras/` (A3, K2)
- `contracts/`: propose changes in `contracts/CHANGES.md` (`perception_enrichment` is already in the contract)

## Rules
- **The bakeoff comes first, and it uses our sounds:**
  - `fixtures/sounds/`;
  - clips cut from `fixtures/mock_scene`, `fixtures/array_tts` and `fixtures/rot_talker_baby`;
  - short recordings you make.

  Report per-class accuracy, latency, warmup, memory, confidence calibration and licence against current PANNs, in your WORKLOG, before switching anything.
- **`unknown` keeps its bearing.** Never force a known label below its threshold, and never spam `unknown` on steady ambience.
- **Expensive inference runs on events only:** onset candidates and the existing sliding window. Use bounded queues that drop the oldest.
  - Gemini runs only on meaningful candidates, with a timeout, cache and debounce.
  - The attention model runs at 1-5 decisions per second at most.
- **The attention model and Gemini never suppress or delay the canonical `sound_event`/`alert`.** If either is down, nothing else changes.
- **No "behind the wearer" judgments on the laptop.** It doesn't know the phone's heading.
- **Secrets** (`GEMINI_API_KEY`; `HF_TOKEN` only if a gated model needs it) go only in `laptop/.env`.
- **The attention model is decided in `Shared/ATTENTION.md`:** three tiers (JEV-9B 4-bit on Kori's GPU → DeBERTa zero-shot → rules), the questions, the timeouts, `perception_enrichment`. Implement it. JEV-9B can't run on your CPU: build and unit-test it against a fake, and give K3 a `--bench` command for the GPU gates. Never the hosted TypeSafe API.
- **Heavy jobs go to Kori's laptop** (`Adil/A2_ON_KORI_LAPTOP.md`): anything bigger than CED-mini, multi-model bench runs, CUDA, golden. Add a row there, push, tell Kori; don't run them here (this machine ran out of memory).
- **Gate:** `.\tasks.ps1 test` after every change. Golden needs the GPU, so K3 runs it on Kori's laptop before your branch merges. It must stay 7/7, with no new false alerts in `golden_babble_only`. Write `tools/sound_bench.py` so GPU latency for your finalists can be measured there too.
- **Anything that changes what the phone receives** (newly published labels, enrichment) gets a row in `IOS_INTEGRATION.md`.
- **Git:**
  - commit small on `adil/a2-sound-ai` and push after each milestone;
  - `git merge origin/main` often;
  - never push to `main`, never force-push;
  - commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Keep `HERE_36hr_vibecode_plan/WORKLOG_A2.md` current.**

Start with A2 A (the bakeoff), then the wrapper and mapping, then unknown, then the attention model, then Gemini.
